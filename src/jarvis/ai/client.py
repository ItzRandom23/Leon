"""Language-model provider contracts and an OpenAI-compatible adapter."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.parse import urljoin, urlparse

from jarvis.ai.models import ChatMessage, LLMResponse, ToolCall

ToolSchema = Mapping[str, Any]
JsonObject = Mapping[str, Any]
HttpTransport = Callable[[str, Mapping[str, str], bytes, float], JsonObject]

_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_REQUEST_BYTES = 2 * 1024 * 1024


class LLMProviderError(RuntimeError):
    """Raised when a language-model provider cannot return a valid response."""


class LLMProvider(ABC):
    """Replaceable language-model interface used by JARVIS Core."""

    @abstractmethod
    async def complete_with_tools(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema],
    ) -> LLMResponse:
        """Complete a conversation with access only to the supplied tools."""


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
        raise LLMProviderError("The language-model provider returned an invalid response") from None
    if not isinstance(data, (bytes, bytearray)) or len(data) > _MAX_RESPONSE_BYTES:
        raise LLMProviderError("The language-model provider returned an invalid response")
    try:
        payload = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise LLMProviderError("The language-model provider returned an invalid response") from None
    if not isinstance(payload, dict):
        raise LLMProviderError("The language-model provider returned an invalid payload")
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
    except LLMProviderError:
        raise
    except (OSError, urllib.error.HTTPError, urllib.error.URLError, ValueError):
        raise LLMProviderError("The language-model request failed") from None


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
    except LLMProviderError:
        raise
    except Exception:
        raise LLMProviderError("The language-model request failed") from None
    if not isinstance(response, Mapping):
        raise LLMProviderError("The language-model provider returned an invalid payload")
    return response


def _encode_request_payload(payload: Mapping[str, Any]) -> bytes:
    try:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError):
        raise LLMProviderError("The language-model request was not serializable") from None
    if len(body) > _MAX_REQUEST_BYTES:
        raise LLMProviderError("The language-model request exceeded the safe size limit")
    return body


class OpenAICompatibleProvider(LLMProvider):
    """Call an OpenAI-compatible Chat Completions tool-calling endpoint."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        transport: HttpTransport = _default_transport,
    ) -> None:
        normalized_key = _normalize_api_key(api_key)
        validated_url = _validated_base_url(base_url, api_key=normalized_key)
        if not model.strip():
            raise ValueError("model cannot be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.model = model
        self._url = f"{validated_url}/chat/completions"
        self._api_key = normalized_key
        self._timeout = timeout_seconds
        self._transport = transport

    async def complete_with_tools(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema],
    ) -> LLMResponse:
        """Request one completion and parse validated tool-call envelopes."""

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [_serialize_message(message) for message in messages],
        }
        if tools:
            payload["tools"] = [_chat_completions_tool(tool) for tool in tools]
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        response = await _request_json(
            self._transport,
            self._url,
            headers,
            _encode_request_payload(payload),
            self._timeout,
        )
        return _parse_response(response)


class OpenAIResponsesProvider(LLMProvider):
    """Call the official OpenAI Responses API with custom function tools."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        timeout_seconds: float = 60.0,
        transport: HttpTransport = _default_transport,
    ) -> None:
        normalized_key = _normalize_api_key(api_key)
        validated_url = _validated_base_url(base_url, api_key=normalized_key)
        if not model.strip():
            raise ValueError("model cannot be empty")
        if normalized_key is None:
            raise ValueError("The OpenAI provider requires an API key")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.model = model
        self._url = f"{validated_url}/responses"
        self._api_key = normalized_key
        self._timeout = timeout_seconds
        self._transport = transport

    async def complete_with_tools(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSchema],
    ) -> LLMResponse:
        """Request one stateless response using caller-managed history."""

        payload: dict[str, Any] = {
            "model": self.model,
            "input": _responses_input(messages),
            "store": False,
        }
        if tools:
            payload["tools"] = [_responses_tool(tool) for tool in tools]
            payload["tool_choice"] = "auto"
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        response = await _request_json(
            self._transport,
            self._url,
            headers,
            _encode_request_payload(payload),
            self._timeout,
        )
        return _parse_responses_response(response)


def _serialize_message(message: ChatMessage) -> dict[str, Any]:
    serialized: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.name is not None:
        serialized["name"] = message.name
    if message.tool_call_id is not None:
        serialized["tool_call_id"] = message.tool_call_id
    if message.tool_calls:
        serialized["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(dict(call.arguments)),
                },
            }
            for call in message.tool_calls
        ]
    return serialized


def _canonical_function(tool: ToolSchema) -> dict[str, Any]:
    """Accept canonical or Chat-Completions-wrapped function definitions."""

    function = tool.get("function") if isinstance(tool.get("function"), Mapping) else tool
    name = function.get("name")
    description = function.get("description")
    parameters = function.get("parameters")
    if not isinstance(name, str) or not name:
        raise ValueError("A tool schema requires a function name")
    if not isinstance(description, str) or not description:
        raise ValueError(f"Tool {name!r} requires a description")
    if not isinstance(parameters, Mapping):
        raise ValueError(f"Tool {name!r} requires a parameter schema")
    canonical: dict[str, Any] = {
        "name": name,
        "description": description,
        "parameters": dict(parameters),
    }
    if "strict" in function:
        canonical["strict"] = bool(function["strict"])
    return canonical


def _chat_completions_tool(tool: ToolSchema) -> dict[str, Any]:
    return {"type": "function", "function": _canonical_function(tool)}


def _responses_tool(tool: ToolSchema) -> dict[str, Any]:
    return {"type": "function", **_canonical_function(tool)}


def _responses_input(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "tool":
            if not message.tool_call_id:
                raise ValueError("A tool output requires a tool_call_id")
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message.tool_call_id,
                    "output": message.content,
                }
            )
            continue
        items.append({"role": message.role, "content": message.content})
        for call in message.tool_calls:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call.id,
                    "name": call.name,
                    "arguments": json.dumps(dict(call.arguments)),
                }
            )
    return items


def _parse_response(payload: JsonObject) -> LLMResponse:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise LLMProviderError("The language-model response did not contain a message")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise LLMProviderError("The language-model response did not contain a message")

    content = message.get("content") or ""
    if not isinstance(content, str):
        raise LLMProviderError("The language-model response content was invalid")

    calls: list[ToolCall] = []
    raw_calls = message.get("tool_calls")
    if raw_calls is None:
        raw_calls = []
    if not isinstance(raw_calls, list):
        raise LLMProviderError("The provider returned malformed tool calls")
    for raw_call in raw_calls:
        if not isinstance(raw_call, Mapping):
            raise LLMProviderError("The provider returned a malformed tool call")
        function = raw_call.get("function")
        call_id = raw_call.get("id")
        if not isinstance(function, Mapping) or not isinstance(call_id, str) or not call_id:
            raise LLMProviderError("The provider returned a malformed tool call")
        name = function.get("name")
        raw_arguments = function.get("arguments") or "{}"
        if not isinstance(name, str) or not name or not isinstance(raw_arguments, str):
            raise LLMProviderError("The provider returned a malformed tool call")
        try:
            arguments = json.loads(raw_arguments)
        except json.JSONDecodeError:
            raise LLMProviderError("The provider returned a malformed tool call") from None
        if not isinstance(arguments, dict):
            raise LLMProviderError("The provider returned invalid tool arguments")
        calls.append(ToolCall(call_id, name, arguments))

    model = payload.get("model")
    return LLMResponse(content, tuple(calls), model if isinstance(model, str) else None)


def _parse_responses_response(payload: JsonObject) -> LLMResponse:
    raw_output = payload.get("output")
    if not isinstance(raw_output, list):
        raise LLMProviderError("The Responses API returned no output")
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    for item in raw_output:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") == "function_call":
            raw_arguments = item.get("arguments") or "{}"
            name = item.get("name")
            call_id = item.get("call_id")
            if (
                not isinstance(raw_arguments, str)
                or not isinstance(name, str)
                or not name
                or not isinstance(call_id, str)
                or not call_id
            ):
                raise LLMProviderError("The Responses API returned a malformed tool call")
            try:
                arguments = json.loads(raw_arguments)
            except json.JSONDecodeError:
                raise LLMProviderError("The Responses API returned a malformed tool call") from None
            if not isinstance(arguments, dict):
                raise LLMProviderError("The Responses API returned invalid tool arguments")
            calls.append(ToolCall(call_id, name, arguments))
        elif item.get("type") == "message":
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if isinstance(part, Mapping) and part.get("type") == "output_text":
                    text = part.get("text")
                    if isinstance(text, str):
                        text_parts.append(text)
    model = payload.get("model")
    return LLMResponse(
        "\n".join(part for part in text_parts if part).strip(),
        tuple(calls),
        model if isinstance(model, str) else None,
    )
