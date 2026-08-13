"""Authenticated loopback proxy that enforces browser DNS pins at the socket boundary."""

from __future__ import annotations

import asyncio
import base64
import hmac
import ipaddress
import re
import secrets
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeAlias
from urllib.parse import SplitResult, urlsplit

from jarvis.browser.errors import BrowserSessionError, BrowserValidationError
from jarvis.browser.validation import PublicHostPolicy, validate_web_url

_MAX_HEADER_BYTES = 32 * 1024
_MAX_HEADERS = 100
_MAX_REQUEST_BODY_BYTES = 8 * 1024 * 1024
_MAX_CONNECTIONS = 32
_READ_CHUNK_BYTES = 64 * 1024
_MAX_HTTP_RESPONSE_SECONDS = 5 * 60
_MAX_CONNECT_TUNNEL_SECONDS = 60 * 60
_HEADER_NAME = re.compile(rb"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
_METHOD = re.compile(rb"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,32}$")

UpstreamConnection: TypeAlias = tuple[asyncio.StreamReader, asyncio.StreamWriter]
ConnectionFactory: TypeAlias = Callable[[str, int], Awaitable[UpstreamConnection]]


@dataclass(frozen=True, slots=True)
class _ProxyRequest:
    method: str
    target: str
    version: str
    headers: tuple[tuple[str, str], ...]

    def values(self, name: str) -> tuple[str, ...]:
        normalized = name.casefold()
        return tuple(value for key, value in self.headers if key.casefold() == normalized)


async def _open_numeric_connection(address: str, port: int) -> UpstreamConnection:
    """Open a TCP socket to a numeric address without hostname resolution."""

    parsed = ipaddress.ip_address(address)
    family = socket.AF_INET if parsed.version == 4 else socket.AF_INET6
    return await asyncio.open_connection(address, port, family=family)


async def _close_writer(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    writer.close()
    try:
        await writer.wait_closed()
    except (ConnectionError, OSError, RuntimeError):
        pass


class AuthenticatedLoopbackProxy:
    """One-session HTTP CONNECT proxy with DNS-pin-enforced upstream sockets.

    The proxy listens only on IPv4 loopback, requires an unguessable Basic credential,
    and never forwards that credential. Every upstream connection is made to a numeric
    address returned by the session's ``PublicHostPolicy``. The browser therefore has
    no opportunity to resolve an approved hostname a second time.
    """

    def __init__(
        self,
        host_policy: PublicHostPolicy,
        *,
        timeout_ms: int,
        max_url_chars: int,
        max_connections: int = _MAX_CONNECTIONS,
        connection_factory: ConnectionFactory | None = None,
    ) -> None:
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or not 100 <= timeout_ms <= 120_000
        ):
            raise ValueError("timeout_ms must be between 100 and 120000")
        if (
            isinstance(max_url_chars, bool)
            or not isinstance(max_url_chars, int)
            or not 1 <= max_url_chars <= 16_384
        ):
            raise ValueError("max_url_chars must be between 1 and 16384")
        if (
            isinstance(max_connections, bool)
            or not isinstance(max_connections, int)
            or not 1 <= max_connections <= 256
        ):
            raise ValueError("max_connections must be between 1 and 256")
        self._host_policy = host_policy
        self._timeout_seconds = timeout_ms / 1000
        self._max_url_chars = max_url_chars
        self._max_connections = max_connections
        self._connection_factory = connection_factory or _open_numeric_connection
        self._username = "jarvis"
        self._password = secrets.token_urlsafe(32)
        token = base64.b64encode(f"{self._username}:{self._password}".encode("ascii"))
        self._expected_authorization = b"Basic " + token
        self._server: asyncio.AbstractServer | None = None
        self._port: int | None = None
        self._client_tasks: set[asyncio.Task[None]] = set()
        self._closed = False

    @property
    def playwright_proxy(self) -> dict[str, str]:
        """Return a Playwright proxy config with no target bypass list."""

        if self._port is None or self._server is None:
            raise BrowserSessionError("The browser egress proxy is not running")
        return {
            "server": f"http://127.0.0.1:{self._port}",
            "username": self._username,
            "password": self._password,
        }

    @property
    def endpoint(self) -> tuple[str, int]:
        """Return the loopback endpoint without exposing authentication material."""

        if self._port is None or self._server is None:
            raise BrowserSessionError("The browser egress proxy is not running")
        return ("127.0.0.1", self._port)

    async def start(self) -> None:
        if self._closed:
            raise BrowserSessionError("The browser egress proxy is closed")
        if self._server is not None:
            return
        try:
            server = await asyncio.wait_for(
                asyncio.start_server(
                    self._accept,
                    host="127.0.0.1",
                    port=0,
                    limit=_MAX_HEADER_BYTES + 1,
                    start_serving=True,
                ),
                timeout=self._timeout_seconds,
            )
        except (OSError, TimeoutError):
            raise BrowserSessionError("The browser egress proxy could not be started") from None
        sockets = server.sockets or ()
        if len(sockets) != 1:
            server.close()
            await server.wait_closed()
            raise BrowserSessionError("The browser egress proxy could not be started")
        socket_name = sockets[0].getsockname()
        if not isinstance(socket_name, tuple) or len(socket_name) < 2:
            server.close()
            await server.wait_closed()
            raise BrowserSessionError("The browser egress proxy could not be started")
        self._server = server
        self._port = int(socket_name[1])

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        server = self._server
        self._server = None
        self._port = None
        if server is not None:
            server.close()
            await server.wait_closed()
        tasks = tuple(self._client_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._client_tasks.clear()

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_address = peer[0] if isinstance(peer, tuple) and peer else ""
        try:
            is_loopback = ipaddress.ip_address(peer_address).is_loopback
        except ValueError:
            is_loopback = False
        if self._closed or not is_loopback or len(self._client_tasks) >= self._max_connections:
            writer.close()
            return
        task = asyncio.create_task(self._handle_client(reader, writer))
        self._client_tasks.add(task)
        task.add_done_callback(self._client_tasks.discard)

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            try:
                request = await self._read_request(reader)
            except (ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                await self._send_status(writer, 400, "Bad Request")
                return
            authorization = request.values("proxy-authorization")
            if len(authorization) != 1 or not hmac.compare_digest(
                authorization[0].encode("latin-1"), self._expected_authorization
            ):
                await self._send_status(
                    writer,
                    407,
                    "Proxy Authentication Required",
                    extra_headers=(("Proxy-Authenticate", 'Basic realm="JARVIS"'),),
                )
                return
            try:
                if request.method == "CONNECT":
                    await self._handle_connect(request, reader, writer)
                else:
                    await self._handle_http(request, reader, writer)
            except BrowserValidationError:
                await self._send_status(writer, 403, "Forbidden")
            except (ConnectionError, OSError, TimeoutError):
                await self._send_status(writer, 502, "Bad Gateway")
        except asyncio.CancelledError:
            raise
        except Exception:
            try:
                await self._send_status(writer, 502, "Bad Gateway")
            except (ConnectionError, OSError, RuntimeError):
                pass
        finally:
            await _close_writer(writer)

    async def _read_request(self, reader: asyncio.StreamReader) -> _ProxyRequest:
        raw = await asyncio.wait_for(
            reader.readuntil(b"\r\n\r\n"),
            timeout=self._timeout_seconds,
        )
        if len(raw) > _MAX_HEADER_BYTES:
            raise ValueError
        lines = raw[:-4].split(b"\r\n")
        if not lines or len(lines) > _MAX_HEADERS + 1:
            raise ValueError
        request_parts = lines[0].split(b" ")
        if len(request_parts) != 3 or _METHOD.fullmatch(request_parts[0]) is None:
            raise ValueError
        try:
            method = request_parts[0].decode("ascii").upper()
            target = request_parts[1].decode("ascii")
            version = request_parts[2].decode("ascii")
        except UnicodeDecodeError:
            raise ValueError from None
        if version != "HTTP/1.1" or not target or len(target) > self._max_url_chars:
            raise ValueError
        headers: list[tuple[str, str]] = []
        for line in lines[1:]:
            if not line or line[:1] in {b" ", b"\t"} or b":" not in line:
                raise ValueError
            name_bytes, value_bytes = line.split(b":", 1)
            if _HEADER_NAME.fullmatch(name_bytes) is None:
                raise ValueError
            value_bytes = value_bytes.strip(b" \t")
            if any((byte < 32 and byte != 9) or byte == 127 for byte in value_bytes):
                raise ValueError
            headers.append(
                (name_bytes.decode("ascii"), value_bytes.decode("latin-1"))
            )
        return _ProxyRequest(method, target, version, tuple(headers))

    async def _handle_connect(
        self,
        request: _ProxyRequest,
        downstream_reader: asyncio.StreamReader,
        downstream_writer: asyncio.StreamWriter,
    ) -> None:
        port, policy_url = self._connect_target(request.target)
        upstream_reader, upstream_writer = await self._open_pinned(policy_url, port)
        try:
            downstream_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await asyncio.wait_for(downstream_writer.drain(), timeout=self._timeout_seconds)
            try:
                await self._relay_tunnel(
                    downstream_reader,
                    downstream_writer,
                    upstream_reader,
                    upstream_writer,
                )
            except (BrowserValidationError, ConnectionError, OSError, TimeoutError):
                pass
        finally:
            await _close_writer(upstream_writer)

    async def _handle_http(
        self,
        request: _ProxyRequest,
        downstream_reader: asyncio.StreamReader,
        downstream_writer: asyncio.StreamWriter,
    ) -> None:
        validated = validate_web_url(request.target, max_chars=self._max_url_chars)
        parsed = urlsplit(validated)
        if parsed.scheme.casefold() != "http" or parsed.hostname is None:
            raise BrowserValidationError("Normal proxy requests must use HTTP")
        port = parsed.port or 80
        upstream_reader, upstream_writer = await self._open_pinned(validated, port)
        try:
            body_length = self._content_length(request)
            body = b""
            if body_length:
                body = await asyncio.wait_for(
                    downstream_reader.readexactly(body_length),
                    timeout=self._timeout_seconds,
                )
            path = parsed.path or "/"
            if parsed.query:
                path = f"{path}?{parsed.query}"
            if len(path) > self._max_url_chars:
                raise BrowserValidationError("Proxy request target is too long")
            upstream_writer.write(self._upstream_request_head(request, parsed, path) + body)
            await asyncio.wait_for(upstream_writer.drain(), timeout=self._timeout_seconds)
            await asyncio.wait_for(
                self._relay_response(upstream_reader, downstream_writer),
                timeout=_MAX_HTTP_RESPONSE_SECONDS,
            )
        finally:
            await _close_writer(upstream_writer)

    def _connect_target(self, target: str) -> tuple[int, str]:
        if not target.isascii() or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in target
        ) or any(character in target for character in "/?#@"):
            raise BrowserValidationError("CONNECT target is invalid")
        try:
            parsed = urlsplit(f"//{target}")
            host = parsed.hostname
            port = parsed.port
        except ValueError:
            raise BrowserValidationError("CONNECT target is invalid") from None
        if host is None or port is None or not 1 <= port <= 65_535:
            raise BrowserValidationError("CONNECT target is invalid")
        bracketed = f"[{host}]" if ":" in host else host
        policy_url = validate_web_url(
            f"https://{bracketed}:{port}/",
            max_chars=self._max_url_chars,
        )
        return port, policy_url

    async def _open_pinned(self, policy_url: str, port: int) -> UpstreamConnection:
        addresses = await self._host_policy.resolve_addresses(
            policy_url,
            max_chars=self._max_url_chars,
        )
        if not addresses:
            raise BrowserValidationError("No approved upstream address is available")
        # Selection order is deterministic. Crucially, the connector receives numeric
        # pins only, never the hostname inspected by the resolver.
        last_error: BaseException | None = None
        for address in addresses:
            try:
                return await asyncio.wait_for(
                    self._connection_factory(address, port),
                    timeout=self._timeout_seconds,
                )
            except asyncio.CancelledError:
                raise
            except (ConnectionError, OSError, TimeoutError) as error:
                last_error = error
        if last_error is not None:
            raise last_error
        raise BrowserValidationError("No approved upstream address is available")

    def _content_length(self, request: _ProxyRequest) -> int:
        if request.values("transfer-encoding"):
            raise BrowserValidationError("Chunked proxy requests are not supported")
        values = request.values("content-length")
        if not values:
            return 0
        if len(values) != 1 or not values[0].isdigit():
            raise BrowserValidationError("Request body length is invalid")
        length = int(values[0])
        if length > _MAX_REQUEST_BODY_BYTES:
            raise BrowserValidationError("Request body is too large")
        return length

    @staticmethod
    def _authority(parsed: SplitResult) -> str:
        if parsed.hostname is None:
            raise BrowserValidationError("URL hostname cannot be empty")
        try:
            hostname = parsed.hostname.encode("idna").decode("ascii")
        except UnicodeError:
            raise BrowserValidationError("URL hostname is invalid") from None
        if ":" in hostname:
            hostname = f"[{hostname}]"
        port = parsed.port
        if port is not None and port != 80:
            return f"{hostname}:{port}"
        return hostname

    def _upstream_request_head(
        self,
        request: _ProxyRequest,
        parsed: SplitResult,
        path: str,
    ) -> bytes:
        blocked = {
            "connection",
            "host",
            "keep-alive",
            "proxy-authenticate",
            "proxy-authorization",
            "proxy-connection",
            "te",
            "trailer",
            "transfer-encoding",
            "upgrade",
        }
        lines = [
            f"{request.method} {path} HTTP/1.1",
            f"Host: {self._authority(parsed)}",
        ]
        lines.extend(
            f"{name}: {value}"
            for name, value in request.headers
            if name.casefold() not in blocked
        )
        lines.extend(("Connection: close", "", ""))
        return "\r\n".join(lines).encode("latin-1")

    async def _relay_response(
        self,
        upstream_reader: asyncio.StreamReader,
        downstream_writer: asyncio.StreamWriter,
    ) -> None:
        while True:
            chunk = await upstream_reader.read(_READ_CHUNK_BYTES)
            if not chunk:
                return
            downstream_writer.write(chunk)
            await asyncio.wait_for(downstream_writer.drain(), timeout=self._timeout_seconds)

    async def _relay_tunnel(
        self,
        downstream_reader: asyncio.StreamReader,
        downstream_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
    ) -> None:
        downstream_to_upstream = asyncio.create_task(
            self._pump(downstream_reader, upstream_writer)
        )
        upstream_to_downstream = asyncio.create_task(
            self._pump(upstream_reader, downstream_writer)
        )
        tasks = {downstream_to_upstream, upstream_to_downstream}
        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=_MAX_CONNECT_TUNNEL_SECONDS,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        if not done:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            raise TimeoutError
        errors: list[BaseException] = []
        for task in done:
            try:
                task.result()
            except BaseException as error:
                errors.append(error)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if errors:
            raise errors[0]

    async def _pump(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        while True:
            chunk = await reader.read(_READ_CHUNK_BYTES)
            if not chunk:
                return
            writer.write(chunk)
            await asyncio.wait_for(writer.drain(), timeout=self._timeout_seconds)

    @staticmethod
    async def _send_status(
        writer: asyncio.StreamWriter,
        status: int,
        reason: str,
        *,
        extra_headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        lines = [
            f"HTTP/1.1 {status} {reason}",
            "Content-Length: 0",
            "Connection: close",
        ]
        lines.extend(f"{name}: {value}" for name, value in extra_headers)
        lines.extend(("", ""))
        writer.write("\r\n".join(lines).encode("ascii"))
        await writer.drain()
