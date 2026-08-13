"""Strict bounded HTTPS transport shared by token-authenticated integrations."""

from __future__ import annotations

import asyncio
import json
import math
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any, TypeAlias
from urllib.parse import urlencode, urljoin, urlparse

from jarvis.integrations.errors import (
    IntegrationHTTPError,
    IntegrationTransportError,
    IntegrationValidationError,
)

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]
QueryValue: TypeAlias = str | int | bool | None
HTTPSRequester: TypeAlias = Callable[
    [str, str, Mapping[str, str], bytes | None, float, int], JSONValue
]

_METHODS = frozenset({"GET", "POST", "PATCH", "DELETE"})
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_FORBIDDEN_HEADERS = frozenset(
    {"host", "content-length", "transfer-encoding", "connection", "proxy-authorization"}
)
_MAX_CONFIGURED_RESPONSE = 16 * 1024 * 1024
_MAX_CONFIGURED_REQUEST = 2 * 1024 * 1024
_MAX_BASE_URL_LENGTH = 2048
_MAX_URL_LENGTH = 8192
_MAX_HEADER_VALUE_LENGTH = 16 * 1024
_MAX_HEADERS_LENGTH = 64 * 1024


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme != "https" or parsed.hostname is None:
        return None
    return "https", parsed.hostname.casefold(), 443 if port is None else port


class StrictHTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Permit redirects only within the exact original HTTPS origin."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> urllib.request.Request | None:
        target = urljoin(request.full_url, new_url)
        try:
            parsed = urlparse(target)
        except ValueError:
            parsed = None
        if (
            parsed is None
            or parsed.username is not None
            or parsed.password is not None
            or _origin(request.full_url) != _origin(target)
        ):
            raise urllib.error.HTTPError(
                request.full_url,
                code,
                "Credential redirect blocked",
                headers,
                file_pointer,
            )
        return super().redirect_request(request, file_pointer, code, message, headers, target)


def _read_bounded_json(response: Any, max_bytes: int) -> JSONValue:
    content_length = response.headers.get("Content-Length") if response.headers else None
    if content_length is not None:
        try:
            parsed_length = int(content_length)
            if parsed_length < 0:
                raise IntegrationTransportError("External service returned invalid headers")
            if parsed_length > max_bytes:
                raise IntegrationTransportError("External service response exceeded the size limit")
        except ValueError:
            raise IntegrationTransportError("External service returned invalid headers") from None
    try:
        raw = response.read(max_bytes + 1)
    except (OSError, TypeError, ValueError):
        raise IntegrationTransportError("External service returned an invalid response") from None
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > max_bytes:
        raise IntegrationTransportError("External service response exceeded the size limit")
    if not raw:
        return None
    content_type = response.headers.get("Content-Type", "") if response.headers else ""
    if content_type and "json" not in content_type.casefold():
        raise IntegrationTransportError("External service returned a non-JSON response")
    try:
        value = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise IntegrationTransportError("External service returned invalid JSON") from None
    try:
        return json_snapshot(value)
    except RecursionError:
        raise IntegrationTransportError("External service returned invalid JSON") from None


def _default_requester(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout: float,
    max_response_bytes: int,
) -> JSONValue:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    opener = urllib.request.build_opener(StrictHTTPSRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            return _read_bounded_json(response, max_response_bytes)
    except IntegrationTransportError:
        raise
    except urllib.error.HTTPError as exc:
        raise IntegrationHTTPError(int(exc.code)) from None
    except (OSError, urllib.error.URLError, ValueError):
        raise IntegrationTransportError("External service request failed") from None


class HTTPSJSONTransport:
    """Build and execute bounded JSON requests against one fixed HTTPS origin."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 4 * 1024 * 1024,
        max_request_bytes: int = 1024 * 1024,
        default_headers: Mapping[str, str] | None = None,
        requester: HTTPSRequester = _default_requester,
    ) -> None:
        self._base_url = _validate_base_url(base_url)
        if (
            not isinstance(timeout_seconds, (int, float))
            or isinstance(timeout_seconds, bool)
            or not 0 < timeout_seconds <= 120
        ):
            raise ValueError("timeout_seconds must be in the range (0, 120]")
        if (
            not isinstance(max_response_bytes, int)
            or isinstance(max_response_bytes, bool)
            or not 0 < max_response_bytes <= _MAX_CONFIGURED_RESPONSE
        ):
            raise ValueError("max_response_bytes is outside the supported range")
        if (
            not isinstance(max_request_bytes, int)
            or isinstance(max_request_bytes, bool)
            or not 0 < max_request_bytes <= _MAX_CONFIGURED_REQUEST
        ):
            raise ValueError("max_request_bytes is outside the supported range")
        if not callable(requester):
            raise TypeError("requester must be callable")
        self._timeout = float(timeout_seconds)
        self._max_response_bytes = max_response_bytes
        self._max_request_bytes = max_request_bytes
        self._default_headers = _validate_headers(default_headers or {})
        self._requester = requester

    async def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, QueryValue] | None = None,
        json_body: JSONValue = None,
        headers: Mapping[str, str] | None = None,
    ) -> JSONValue:
        normalized_method = method.upper() if isinstance(method, str) else ""
        if normalized_method not in _METHODS:
            raise IntegrationValidationError("Unsupported HTTP method")
        url = self._build_url(path, query)
        request_headers = dict(self._default_headers)
        request_headers.update(_validate_headers(headers or {}))
        request_headers.setdefault("Accept", "application/json")
        body: bytes | None = None
        if json_body is not None:
            snapshot = json_snapshot(json_body)
            try:
                body = json.dumps(
                    snapshot, ensure_ascii=False, allow_nan=False, separators=(",", ":")
                ).encode("utf-8")
            except (TypeError, ValueError):
                raise IntegrationValidationError("Request body must be JSON-compatible") from None
            if len(body) > self._max_request_bytes:
                raise IntegrationValidationError("Request body exceeds the size limit")
            request_headers.setdefault("Content-Type", "application/json")
        try:
            response = await asyncio.to_thread(
                self._requester,
                normalized_method,
                url,
                request_headers,
                body,
                self._timeout,
                self._max_response_bytes,
            )
        except (IntegrationHTTPError, IntegrationTransportError):
            raise
        except Exception:
            raise IntegrationTransportError("External service request failed") from None
        try:
            return json_snapshot(response)
        except RecursionError:
            raise IntegrationTransportError("External service returned invalid JSON") from None

    def _build_url(self, path: str, query: Mapping[str, QueryValue] | None) -> str:
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or "\\" in path
            or "?" in path
            or "#" in path
            or any(ord(char) < 32 or ord(char) == 127 for char in path)
        ):
            raise IntegrationValidationError("Request path is invalid")
        url = f"{self._base_url}{path}"
        if _origin(url) != _origin(self._base_url):
            raise IntegrationValidationError("Request path changed the configured origin")
        if query is not None and not isinstance(query, Mapping):
            raise TypeError("query must be a mapping")
        if query:
            encoded: list[tuple[str, str]] = []
            for key, value in query.items():
                if not isinstance(key, str) or not key or any(ord(char) < 32 for char in key):
                    raise IntegrationValidationError("Query parameter name is invalid")
                if value is None:
                    continue
                if isinstance(value, bool):
                    normalized = "true" if value else "false"
                elif isinstance(value, (str, int)) and not isinstance(value, bool):
                    normalized = str(value)
                else:
                    raise IntegrationValidationError("Query parameter value is invalid")
                encoded.append((key, normalized))
            if encoded:
                url = f"{url}?{urlencode(encoded)}"
        if len(url.encode("utf-8")) > _MAX_URL_LENGTH:
            raise IntegrationValidationError("Request URL exceeds the size limit")
        return url

    def __repr__(self) -> str:
        return f"HTTPSJSONTransport(base_url={self._base_url!r})"


def json_snapshot(value: Any) -> JSONValue:
    """Validate and copy a finite JSON value returned by an untrusted provider."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise IntegrationTransportError("External service returned invalid JSON values")
        return value
    if isinstance(value, (list, tuple)):
        return [json_snapshot(item) for item in value]
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise IntegrationTransportError("External service returned invalid JSON keys")
        return {key: json_snapshot(item) for key, item in value.items()}
    raise IntegrationTransportError("External service returned non-JSON data")


def _validate_base_url(value: str) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value.encode("utf-8")) > _MAX_BASE_URL_LENGTH
        or "\\" in value
        or any(char.isspace() or ord(char) == 127 for char in value)
    ):
        raise ValueError("base_url must be a valid absolute HTTPS URL")
    try:
        parsed = urlparse(value)
        _ = parsed.port
    except ValueError:
        raise ValueError("base_url must be a valid absolute HTTPS URL") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("base_url must be a valid absolute HTTPS URL")
    return value.rstrip("/")


def _validate_headers(headers: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(headers, Mapping):
        raise TypeError("headers must be a mapping")
    result: dict[str, str] = {}
    total_length = 0
    for name, value in headers.items():
        if (
            not isinstance(name, str)
            or len(name) > 128
            or not _HEADER_NAME.fullmatch(name)
            or name.casefold() in _FORBIDDEN_HEADERS
        ):
            raise IntegrationValidationError("Request header name is invalid")
        if (
            not isinstance(value, str)
            or len(value.encode("utf-8")) > _MAX_HEADER_VALUE_LENGTH
            or "\r" in value
            or "\n" in value
        ):
            raise IntegrationValidationError("Request header value is invalid")
        total_length += len(name.encode("ascii")) + len(value.encode("utf-8"))
        if total_length > _MAX_HEADERS_LENGTH:
            raise IntegrationValidationError("Request headers exceed the size limit")
        result[name] = value
    return result
