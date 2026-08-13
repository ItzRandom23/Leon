"""Vision provider contracts and an OpenAI-compatible image adapter."""

from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from jarvis.vision.models import BoundingBox, VisionAnalysis, VisionTarget

JsonObject = Mapping[str, Any]
HttpTransport = Callable[[str, Mapping[str, str], bytes, float], JsonObject]

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class VisionProviderError(RuntimeError):
    """Raised when an image cannot be analyzed safely."""


class VisionProvider(ABC):
    """Replaceable semantic image-analysis interface."""

    supports_grounding: bool = False

    @abstractmethod
    async def analyze_image(self, image_path: Path, prompt: str) -> VisionAnalysis:
        """Analyze one local image without taking follow-up actions."""


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only when credentials remain on the original origin."""

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
        if _origin(request.full_url) != _origin(target):
            raise urllib.error.HTTPError(
                request.full_url,
                code,
                "Cross-origin redirect blocked",
                headers,
                file_pointer,
            )
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            target,
        )


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """Return a normalized HTTP origin, or ``None`` for an invalid URL."""

    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if parsed.scheme not in {"http", "https"} or hostname is None:
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, hostname.casefold(), port


def _normalize_api_key(api_key: str | None) -> str | None:
    if api_key is None:
        return None
    if not isinstance(api_key, str):
        raise ValueError("api_key must be text")
    normalized = api_key.strip()
    return normalized or None


def _validated_base_url(base_url: str, *, api_key: str | None) -> str:
    """Validate a provider root before credentials can be attached to it."""

    if not isinstance(base_url, str):
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if (
        base_url != base_url.strip()
        or any(character.isspace() or ord(character) == 127 for character in base_url)
        or "\\" in base_url
    ):
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    try:
        parsed = urlparse(base_url)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError:
        raise ValueError("base_url must be an absolute HTTP(S) URL") from None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or hostname is None:
        raise ValueError("base_url must be an absolute HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url cannot contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url cannot contain a query or fragment")
    if api_key is not None and parsed.scheme != "https":
        raise ValueError("base_url must use HTTPS when an API key is configured")
    return base_url.rstrip("/")


def _read_json_response(response: Any) -> dict[str, Any]:
    """Decode one bounded UTF-8 JSON object from an HTTP response."""

    try:
        data = response.read(_MAX_RESPONSE_BYTES + 1)
    except (OSError, ValueError, TypeError):
        raise VisionProviderError("The vision provider returned an invalid response") from None
    if not isinstance(data, (bytes, bytearray)) or len(data) > _MAX_RESPONSE_BYTES:
        raise VisionProviderError("The vision provider returned an invalid response")
    try:
        payload = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise VisionProviderError("The vision provider returned an invalid response") from None
    if not isinstance(payload, dict):
        raise VisionProviderError("The vision provider returned an invalid payload")
    return payload


def _default_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> JsonObject:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method="POST")
    opener = urllib.request.build_opener(_SameOriginRedirectHandler())
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            return _read_json_response(response)
    except VisionProviderError:
        raise
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, ValueError):
        raise VisionProviderError("The vision request failed") from None


async def _request_json(
    transport: HttpTransport,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> JsonObject:
    """Run a provider transport while containing untrusted transport errors."""

    try:
        response = await asyncio.to_thread(transport, url, headers, body, timeout)
    except VisionProviderError:
        raise
    except Exception:
        raise VisionProviderError("The vision request failed") from None
    if not isinstance(response, Mapping):
        raise VisionProviderError("The vision provider returned an invalid payload")
    return response


class OpenAICompatibleVisionProvider(VisionProvider):
    """Analyze images through an OpenAI-compatible multimodal endpoint.

    The adapter requests structured JSON for stable parsing but deliberately
    advertises no grounding support. Coordinates are never inferred from prose.
    """

    supports_grounding = False

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        max_image_bytes: int = 20 * 1024 * 1024,
        transport: HttpTransport = _default_transport,
    ) -> None:
        normalized_key = _normalize_api_key(api_key)
        validated_url = _validated_base_url(base_url, api_key=normalized_key)
        if not model.strip():
            raise ValueError("model cannot be empty")
        if timeout_seconds <= 0 or max_image_bytes <= 0:
            raise ValueError("Vision limits must be positive")
        self.model = model
        self._url = f"{validated_url}/chat/completions"
        self._api_key = normalized_key
        self._timeout = timeout_seconds
        self._max_image_bytes = max_image_bytes
        self._transport = transport

    async def analyze_image(self, image_path: Path, prompt: str) -> VisionAnalysis:
        """Send one bounded local image and parse a semantic description."""

        image = Path(image_path)
        if not image.is_file():
            raise VisionProviderError("The image does not exist")
        data = _read_bounded_image(image, self._max_image_bytes)
        mime_type = mimetypes.guess_type(image.name)[0] or "image/png"
        encoded = base64.b64encode(data).decode("ascii")
        instruction = (
            "Analyze this screenshot. Return JSON with string 'description' and optional "
            "string-array 'visible_text'. Do not invent coordinates. User request: "
            f"{prompt.strip() or 'Describe the visible screen.'}"
        )
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": instruction},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
                        },
                    ],
                }
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        response = await _request_json(
            self._transport,
            self._url,
            headers,
            json.dumps(payload).encode("utf-8"),
            self._timeout,
        )
        return _parse_analysis(response)


def _parse_analysis(payload: JsonObject) -> VisionAnalysis:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise VisionProviderError("The vision response was malformed")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise VisionProviderError("The vision response was malformed")
    content = message.get("content")
    if not isinstance(content, str):
        raise VisionProviderError("The vision response was malformed")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        raise VisionProviderError("The vision response was malformed") from None
    if not isinstance(parsed, Mapping):
        raise VisionProviderError("The vision response was malformed")
    description = parsed.get("description")
    raw_text = parsed.get("visible_text", [])
    if not isinstance(description, str) or not description.strip():
        raise VisionProviderError("The vision response contained no description")
    if not isinstance(raw_text, list) or not all(isinstance(item, str) for item in raw_text):
        raise VisionProviderError("The vision response contained invalid visible text")
    model = payload.get("model")
    return VisionAnalysis(
        description.strip(),
        tuple(item for item in raw_text if item.strip()),
        model=str(model) if model is not None else None,
    )


def parse_grounded_analysis(payload: Mapping[str, Any], model: str | None = None) -> VisionAnalysis:
    """Validate structured output from a future provider that supports boxes."""

    description = payload.get("description")
    if not isinstance(description, str) or not description.strip():
        raise VisionProviderError("Grounded analysis requires a description")
    targets: list[VisionTarget] = []
    raw_targets = payload.get("targets", [])
    if not isinstance(raw_targets, list):
        raise VisionProviderError("Grounded targets must be a list")
    try:
        for raw_target in raw_targets:
            raw_bounds = raw_target.get("bounds")
            bounds = BoundingBox(**raw_bounds) if raw_bounds is not None else None
            targets.append(
                VisionTarget(
                    label=raw_target["label"],
                    confidence=raw_target.get("confidence"),
                    bounds=bounds,
                )
            )
    except (KeyError, TypeError, ValueError):
        raise VisionProviderError("A grounded target was invalid") from None
    return VisionAnalysis(description.strip(), targets=tuple(targets), model=model)


class OpenAIResponsesVisionProvider(VisionProvider):
    """Analyze images through the official OpenAI Responses API."""

    supports_grounding = False

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        max_image_bytes: int = 20 * 1024 * 1024,
        transport: HttpTransport = _default_transport,
    ) -> None:
        normalized_key = _normalize_api_key(api_key)
        validated_url = _validated_base_url(base_url, api_key=normalized_key)
        if not model.strip() or normalized_key is None:
            raise ValueError("The OpenAI vision provider requires a model and API key")
        if timeout_seconds <= 0 or max_image_bytes <= 0:
            raise ValueError("Vision limits must be positive")
        self.model = model
        self._url = f"{validated_url}/responses"
        self._api_key = normalized_key
        self._timeout = timeout_seconds
        self._max_image_bytes = max_image_bytes
        self._transport = transport

    async def analyze_image(self, image_path: Path, prompt: str) -> VisionAnalysis:
        image = Path(image_path)
        if not image.is_file():
            raise VisionProviderError("The image does not exist")
        data = _read_bounded_image(image, self._max_image_bytes)
        mime_type = mimetypes.guess_type(image.name)[0] or "image/png"
        encoded = base64.b64encode(data).decode("ascii")
        instruction = (
            "Describe this screenshot and include relevant visible text. "
            "Do not invent screen coordinates. User request: "
            f"{prompt.strip() or 'Describe the visible screen.'}"
        )
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": instruction},
                        {
                            "type": "input_image",
                            "image_url": f"data:{mime_type};base64,{encoded}",
                            "detail": "auto",
                        },
                    ],
                }
            ],
            "store": False,
        }
        response = await _request_json(
            self._transport,
            self._url,
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self._api_key}",
            },
            json.dumps(payload).encode("utf-8"),
            self._timeout,
        )
        text = _responses_output_text(response)
        model = response.get("model")
        return VisionAnalysis(text, model=model if isinstance(model, str) else None)


def _responses_output_text(payload: JsonObject) -> str:
    output = payload.get("output")
    if not isinstance(output, list):
        raise VisionProviderError("The Responses API returned no vision output")
    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, Mapping):
            raise VisionProviderError("The Responses API returned malformed vision output")
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            raise VisionProviderError("The Responses API returned malformed vision output")
        for part in content:
            if not isinstance(part, Mapping):
                raise VisionProviderError("The Responses API returned malformed vision output")
            if part.get("type") == "output_text":
                text = part.get("text")
                if not isinstance(text, str):
                    raise VisionProviderError("The Responses API returned malformed vision output")
                if text.strip():
                    text_parts.append(text.strip())
    if not text_parts:
        raise VisionProviderError("The Responses API returned no vision description")
    return "\n".join(text_parts)


def _read_bounded_image(image: Path, maximum_bytes: int) -> bytes:
    """Reject oversized input before allocating it, then enforce a read cap."""

    try:
        size = image.stat().st_size
        if size <= 0 or size > maximum_bytes:
            raise VisionProviderError("The image is empty or exceeds the configured size limit")
        with image.open("rb") as stream:
            data = stream.read(maximum_bytes + 1)
    except VisionProviderError:
        raise
    except OSError:
        raise VisionProviderError("The image could not be read") from None
    if not data or len(data) > maximum_bytes:
        raise VisionProviderError("The image is empty or exceeds the configured size limit")
    return data
