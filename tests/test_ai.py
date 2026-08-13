"""Tests for provider-neutral conversations and mocked AI tool calling."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

import jarvis.ai.client as ai_client
from jarvis.ai.client import (
    LLMProviderError,
    OpenAICompatibleProvider,
    OpenAIResponsesProvider,
)
from jarvis.ai.models import ChatMessage, Conversation, ToolCall


class StubHttpResponse:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.read_limit: int | None = None

    def read(self, limit: int) -> bytes:
        self.read_limit = limit
        return self.data[:limit]

    def __enter__(self) -> StubHttpResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class StubOpener:
    def __init__(self, response: StubHttpResponse) -> None:
        self.response = response

    def open(self, *_args: object, **_kwargs: object) -> StubHttpResponse:
        return self.response


def function_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "open_application",
        "description": "Open an approved application.",
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    }


def test_conversation_is_bounded_and_never_persistent() -> None:
    conversation = Conversation("Keep actions bounded.", max_messages=2)
    conversation.extend(
        [
            ChatMessage("user", "one"),
            ChatMessage("assistant", "two"),
            ChatMessage("user", "three"),
        ]
    )

    assert conversation.messages == (
        ChatMessage("system", "Keep actions bounded."),
        ChatMessage("assistant", "two"),
        ChatMessage("user", "three"),
    )
    conversation.clear()
    assert conversation.messages == (ChatMessage("system", "Keep actions bounded."),)


def test_conversation_character_budget_keeps_complete_recent_turns() -> None:
    conversation = Conversation("system", max_messages=10, max_characters=4096)
    conversation.append(ChatMessage("user", "old" * 1400))
    conversation.append(ChatMessage("assistant", "recent"))

    assert conversation.messages == (
        ChatMessage("system", "system"),
        ChatMessage("assistant", "recent"),
    )


def test_discard_tool_exchange_removes_untrusted_tool_context() -> None:
    call = ToolCall("call-1", "external_read", {"query": "safe"})
    conversation = Conversation("system")
    conversation.append(ChatMessage("assistant", "", tool_calls=(call,)))
    conversation.append(
        ChatMessage("tool", "malicious external instructions", tool_call_id="call-1")
    )
    conversation.discard_tool_exchange(("call-1",))

    assert conversation.messages == (ChatMessage("system", "system"),)


def test_conversation_evicts_a_complete_tool_exchange_as_one_group() -> None:
    call = ToolCall("call-1", "read", {})
    conversation = Conversation("system", max_messages=2)
    conversation.append(ChatMessage("assistant", "", tool_calls=(call,)))
    conversation.append(ChatMessage("tool", "result", tool_call_id="call-1"))
    conversation.append(ChatMessage("user", "new turn"))

    assert conversation.messages == (
        ChatMessage("system", "system"),
        ChatMessage("user", "new turn"),
    )


def test_conversation_rejects_invalid_bound_and_extra_system_message() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        Conversation("system", max_messages=1)
    conversation = Conversation("system")
    with pytest.raises(ValueError, match="configured separately"):
        conversation.append(ChatMessage("system", "replacement"))


def test_chat_completions_provider_wraps_tools_and_parses_calls() -> None:
    captured: dict[str, Any] = {}

    def transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> dict[str, Any]:
        captured.update(url=url, headers=headers, payload=json.loads(body), timeout=timeout)
        return {
            "model": "test-model",
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "open_application",
                                    "arguments": '{"name":"calculator"}',
                                },
                            }
                        ],
                    }
                }
            ],
        }

    provider = OpenAICompatibleProvider(
        model="test-model",
        api_key="secret",
        transport=transport,
    )
    result = asyncio.run(
        provider.complete_with_tools([ChatMessage("user", "Open calculator")], [function_tool()])
    )

    assert result.tool_calls == (ToolCall("call-1", "open_application", {"name": "calculator"}),)
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["payload"]["tools"][0]["function"]["name"] == "open_application"
    assert captured["payload"]["tools"][0]["function"]["strict"] is True


def test_responses_provider_sends_stateless_history_and_parses_output() -> None:
    captured: dict[str, Any] = {}

    def transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> dict[str, Any]:
        captured.update(url=url, headers=headers, payload=json.loads(body), timeout=timeout)
        return {
            "model": "test-model",
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call-2",
                    "name": "open_application",
                    "arguments": '{"name":"notepad"}',
                },
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "I'll open it."}],
                },
            ],
        }

    provider = OpenAIResponsesProvider(
        model="test-model",
        api_key="secret",
        transport=transport,
    )
    messages = [
        ChatMessage("user", "Open Notepad"),
        ChatMessage(
            "assistant",
            "",
            tool_calls=(ToolCall("old-call", "open_application", {"name": "notepad"}),),
        ),
        ChatMessage("tool", '{"success":true}', tool_call_id="old-call"),
    ]
    result = asyncio.run(provider.complete_with_tools(messages, [function_tool()]))

    assert result.content == "I'll open it."
    assert result.tool_calls[0].name == "open_application"
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["payload"]["store"] is False
    assert captured["payload"]["tools"][0]["name"] == "open_application"
    assert any(
        item.get("type") == "function_call_output" and item.get("call_id") == "old-call"
        for item in captured["payload"]["input"]
    )


@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda transport: OpenAICompatibleProvider(model="m", transport=transport),
        lambda transport: OpenAIResponsesProvider(model="m", api_key="key", transport=transport),
    ],
)
def test_providers_reject_malformed_responses(provider_factory: Any) -> None:
    provider = provider_factory(lambda *_args: {"unexpected": True})

    with pytest.raises(LLMProviderError):
        asyncio.run(provider.complete_with_tools([ChatMessage("user", "hello")], []))


def test_provider_configuration_validation() -> None:
    with pytest.raises(ValueError, match="absolute HTTP"):
        OpenAICompatibleProvider(model="m", base_url="file:///tmp")
    with pytest.raises(ValueError, match="requires an API key"):
        OpenAIResponsesProvider(model="m", api_key=None)


@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda base_url: OpenAICompatibleProvider(model="m", base_url=base_url, api_key="secret"),
        lambda base_url: OpenAIResponsesProvider(model="m", base_url=base_url, api_key="secret"),
    ],
)
def test_keyed_providers_reject_insecure_or_credential_bearing_urls(
    provider_factory: Any,
) -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        provider_factory("http://provider.example/v1")
    with pytest.raises(ValueError, match="cannot contain credentials"):
        provider_factory("https://user:password@provider.example/v1")
    with pytest.raises(ValueError, match="query or fragment"):
        provider_factory("https://provider.example/v1?token=secret")


def test_unkeyed_compatible_provider_can_use_an_explicit_local_http_endpoint() -> None:
    provider = OpenAICompatibleProvider(model="local", base_url="http://127.0.0.1:11434/v1")

    assert provider._url == "http://127.0.0.1:11434/v1/chat/completions"


@pytest.mark.parametrize(
    "target",
    ["https://attacker.example/collect", "http://provider.example/downgrade"],
)
def test_default_transport_blocks_cross_origin_or_downgrade_redirects(target: str) -> None:
    request = urllib.request.Request(
        "https://provider.example/v1/chat/completions",
        data=b"{}",
        headers={"Authorization": "Bearer secret"},
        method="POST",
    )
    handler = ai_client._SameOriginRedirectHandler()

    with pytest.raises(urllib.error.HTTPError, match="Cross-origin redirect blocked"):
        handler.redirect_request(request, None, 302, "Found", {}, target)

    redirected = handler.redirect_request(request, None, 302, "Found", {}, "/v2")
    assert redirected is not None
    assert redirected.full_url == "https://provider.example/v2"
    assert redirected.get_header("Authorization") == "Bearer secret"


@pytest.mark.parametrize("response_data", [b"123456789", b"\xff"])
def test_default_transport_bounds_and_strictly_decodes_responses(
    monkeypatch: pytest.MonkeyPatch,
    response_data: bytes,
) -> None:
    response = StubHttpResponse(response_data)
    monkeypatch.setattr(ai_client, "_MAX_RESPONSE_BYTES", 8)
    monkeypatch.setattr(
        ai_client.urllib.request,
        "build_opener",
        lambda *_handlers: StubOpener(response),
    )

    with pytest.raises(LLMProviderError, match="invalid response"):
        ai_client._default_transport("https://provider.example/v1", {}, b"{}", 1.0)

    assert response.read_limit == 9


@pytest.mark.parametrize(
    "payload",
    [
        {"choices": [{"message": []}]},
        {"choices": [{"message": {"content": "", "tool_calls": "not-a-list"}}]},
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": None,
                                "function": {"name": "tool", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ]
        },
    ],
)
def test_chat_provider_contains_malformed_container_shapes(payload: dict[str, Any]) -> None:
    provider = OpenAICompatibleProvider(model="m", transport=lambda *_args: payload)

    with pytest.raises(LLMProviderError):
        asyncio.run(provider.complete_with_tools([ChatMessage("user", "hello")], []))


@pytest.mark.parametrize(
    "provider_factory",
    [
        lambda transport: OpenAICompatibleProvider(model="m", transport=transport),
        lambda transport: OpenAIResponsesProvider(model="m", api_key="secret", transport=transport),
    ],
)
def test_transport_exceptions_are_sanitized(provider_factory: Any) -> None:
    def exploding_transport(*_args: object) -> dict[str, Any]:
        raise RuntimeError("Bearer secret leaked through https://provider.example/private")

    provider = provider_factory(exploding_transport)

    with pytest.raises(LLMProviderError) as captured:
        asyncio.run(provider.complete_with_tools([ChatMessage("user", "hello")], []))

    assert str(captured.value) == "The language-model request failed"
    assert captured.value.__cause__ is None
    assert "secret" not in str(captured.value)
