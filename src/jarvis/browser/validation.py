"""Validation helpers for untrusted browser inputs and navigation targets."""

from __future__ import annotations

import asyncio
import inspect
import ipaddress
import re
import socket
import unicodedata
from collections.abc import Awaitable, Callable, Iterable
from itertools import islice
from typing import TypeAlias
from urllib.parse import unquote_to_bytes, urlsplit

from jarvis.browser.errors import BrowserValidationError

_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MALFORMED_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_SAFE_KEYS = frozenset(
    {
        "ArrowDown",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "Backspace",
        "Delete",
        "End",
        "Enter",
        "Escape",
        "Home",
        "PageDown",
        "PageUp",
        "Space",
        "Tab",
    }
)

AddressResolver: TypeAlias = Callable[[str], Iterable[str] | Awaitable[Iterable[str]]]
_MAX_RESOLVED_ADDRESSES = 64


def _contains_forbidden_characters(value: str, *, allow_newlines: bool = False) -> bool:
    for character in value:
        if allow_newlines and character in {"\n", "\t"}:
            continue
        category = unicodedata.category(character)
        if character.isspace() and character != " ":
            return True
        if category in {"Cc", "Cf", "Cs"} or ord(character) == 127:
            return True
    return False


def _normalized_hostname(hostname: str) -> str:
    """Normalize a URL hostname for policy checks and DNS resolution."""

    normalized = hostname.rstrip(".").casefold()
    if not normalized:
        raise BrowserValidationError("URL hostname cannot be empty")
    try:
        return normalized.encode("idna").decode("ascii")
    except UnicodeError:
        raise BrowserValidationError("URL hostname is invalid") from None


def _hostname_from_url(value: str) -> str:
    hostname = urlsplit(value).hostname
    if hostname is None:
        raise BrowserValidationError("URL hostname cannot be empty")
    return _normalized_hostname(hostname)


def _is_local_hostname(hostname: str) -> bool:
    return hostname in {"local", "localhost"} or hostname.endswith((".local", ".localhost"))


def _public_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise BrowserValidationError("DNS returned an invalid address") from None
    if not address.is_global or address.is_multicast or address.is_unspecified:
        raise BrowserValidationError("URL hostname resolved to a non-public address")
    return address


async def _default_address_resolver(hostname: str) -> tuple[str, ...]:
    """Resolve with the event loop so the default policy never blocks the UI thread."""

    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(
        hostname,
        None,
        family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM,
    )
    return tuple(str(record[4][0]) for record in records)


class PublicHostPolicy:
    """Fail-closed DNS policy that pins each hostname's public address set per session.

    The same resolver is consulted for every request. A hostname whose answer changes
    during the session is rejected; this is deliberately conservative protection
    against DNS rebinding. ``resolve_addresses`` exposes only the validated numeric
    pins so the browser egress proxy can connect without performing another DNS lookup.
    """

    def __init__(
        self,
        resolver: AddressResolver | None = None,
        *,
        timeout_ms: int = 15_000,
    ) -> None:
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or not 100 <= timeout_ms <= 120_000
        ):
            raise ValueError("timeout_ms must be between 100 and 120000")
        self._resolver = resolver or _default_address_resolver
        self._timeout_seconds = timeout_ms / 1000
        self._pins: dict[str, frozenset[str]] = {}
        self._lock = asyncio.Lock()

    async def _resolve(self, hostname: str) -> Iterable[str]:
        if inspect.iscoroutinefunction(self._resolver):
            return await self._resolver(hostname)
        result = await asyncio.to_thread(self._resolver, hostname)
        if inspect.isawaitable(result):
            return await result
        return result

    async def resolve_addresses(
        self,
        value: str,
        *,
        max_chars: int = 4096,
    ) -> tuple[str, ...]:
        """Return stable public numeric addresses for a structurally valid web URL."""

        validated = validate_web_url(value, max_chars=max_chars)
        hostname = _hostname_from_url(validated)
        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None:
            return (str(_public_address(str(literal))),)

        async with self._lock:
            try:
                result = await asyncio.wait_for(
                    self._resolve(hostname),
                    timeout=self._timeout_seconds,
                )
                if isinstance(result, (str, bytes)):
                    raise TypeError
                raw_addresses = list(islice(result, _MAX_RESOLVED_ADDRESSES + 1))
            except (BrowserValidationError, asyncio.CancelledError):
                raise
            except Exception:
                raise BrowserValidationError("URL hostname could not be safely resolved") from None
            if not raw_addresses or len(raw_addresses) > _MAX_RESOLVED_ADDRESSES:
                raise BrowserValidationError("URL hostname could not be safely resolved")
            addresses = frozenset(str(_public_address(str(item))) for item in raw_addresses)
            previous = self._pins.get(hostname)
            if previous is not None and previous != addresses:
                raise BrowserValidationError("URL hostname DNS answer changed during the session")
            self._pins[hostname] = addresses
        return tuple(
            sorted(
                addresses,
                key=lambda value: (
                    ipaddress.ip_address(value).version,
                    ipaddress.ip_address(value).packed,
                ),
            )
        )

    async def validate(self, value: str, *, max_chars: int = 4096) -> str:
        """Validate a web URL and require a stable, entirely public DNS answer."""

        validated = validate_web_url(value, max_chars=max_chars)
        await self.resolve_addresses(validated, max_chars=max_chars)
        return validated


def validate_web_url(value: str, *, max_chars: int = 4096) -> str:
    """Return an absolute HTTP(S) URL after strict structural validation."""

    if not isinstance(value, str) or not value or len(value) > max_chars:
        raise BrowserValidationError("URL must be a bounded absolute HTTP(S) URL")
    if (
        value != value.strip()
        or any(character.isspace() for character in value)
        or _contains_forbidden_characters(value)
        or "\\" in value
    ):
        raise BrowserValidationError("URL contains unsafe characters")
    if _MALFORMED_PERCENT_ESCAPE.search(value):
        raise BrowserValidationError("URL contains invalid percent encoding")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except (TypeError, ValueError):
        raise BrowserValidationError("URL must be a valid absolute HTTP(S) URL") from None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.netloc or hostname is None:
        raise BrowserValidationError("URL must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise BrowserValidationError("URL cannot contain user information")
    normalized_hostname = _normalized_hostname(hostname)
    if _is_local_hostname(normalized_hostname):
        raise BrowserValidationError("URL cannot target a local hostname")
    try:
        address = ipaddress.ip_address(normalized_hostname)
    except ValueError:
        address = None
    if address is not None:
        try:
            _public_address(str(address))
        except BrowserValidationError:
            raise BrowserValidationError(
                "URL cannot target a non-public IP address literal"
            ) from None
    try:
        decoded = unquote_to_bytes(value)
    except (UnicodeEncodeError, ValueError):
        raise BrowserValidationError("URL contains invalid percent encoding") from None
    if any(byte < 32 or byte == 127 for byte in decoded):
        raise BrowserValidationError("URL contains encoded control characters")
    return value


def validate_redirect_url(source_url: str, target_url: str, *, max_chars: int = 4096) -> str:
    """Validate a redirect and reject an HTTPS-to-HTTP downgrade."""

    source = validate_web_url(source_url, max_chars=max_chars)
    target = validate_web_url(target_url, max_chars=max_chars)
    if (
        urlsplit(source).scheme.casefold() == "https"
        and urlsplit(target).scheme.casefold() != "https"
    ):
        raise BrowserValidationError("HTTPS navigation cannot redirect to HTTP")
    return target


def validate_identifier(value: str, *, label: str) -> str:
    """Validate an opaque ID without interpreting it as a selector."""

    if not isinstance(value, str) or _SAFE_IDENTIFIER.fullmatch(value) is None:
        raise BrowserValidationError(f"{label} must be an opaque identifier")
    return value


def validate_find_query(value: str, *, max_chars: int) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > max_chars
        or value != value.strip()
        or _contains_forbidden_characters(value)
    ):
        raise BrowserValidationError("Find text must be bounded printable text")
    return value


def validate_typed_text(value: str, *, max_chars: int) -> str:
    if not isinstance(value, str) or len(value) > max_chars:
        raise BrowserValidationError("Typed text exceeds the configured limit")
    if _contains_forbidden_characters(value, allow_newlines=True):
        raise BrowserValidationError("Typed text contains unsafe control characters")
    return value


def validate_key(value: str) -> str:
    if not isinstance(value, str) or value not in _SAFE_KEYS:
        raise BrowserValidationError("Key is not in the browser-safe key allowlist")
    return value


def sanitize_page_text(value: object, *, max_chars: int) -> str:
    """Bound page-authored text and neutralize terminal/bidi control characters."""

    if not isinstance(value, str):
        return ""
    cleaned: list[str] = []
    for character in value:
        if character in {"\n", "\t"}:
            cleaned.append(character)
        elif character.isprintable() and unicodedata.category(character) != "Cf":
            cleaned.append(character)
        else:
            cleaned.append("�")
        if len(cleaned) >= max_chars:
            break
    return "".join(cleaned).strip()
