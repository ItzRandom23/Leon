"""Provider-independent conversation and tool-call models."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """A model request to invoke one registered action."""

    id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """One provider-neutral conversation message."""

    role: Role
    content: str
    name: str | None = None
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """A natural-language response and optional bounded tool requests."""

    content: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    model: str | None = None


@dataclass(slots=True)
class Conversation:
    """Bounded in-memory conversation history for one active session."""

    system_prompt: str
    max_messages: int = 40
    max_characters: int = 128_000
    _messages: list[ChatMessage] = field(default_factory=list, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_messages < 2:
            raise ValueError("max_messages must be at least 2")
        if self.max_characters < 4_096:
            raise ValueError("max_characters must be at least 4096")

    @property
    def messages(self) -> tuple[ChatMessage, ...]:
        """Return a snapshot including the stable system instruction."""

        system = ChatMessage("system", self.system_prompt)
        remaining = max(0, self.max_characters - len(self.system_prompt))
        selected: list[tuple[ChatMessage, ...]] = []
        for group in reversed(_message_groups(self._messages)):
            cost = sum(_message_characters(message) for message in group)
            if cost > remaining:
                break
            selected.append(group)
            remaining -= cost
        history = tuple(message for group in reversed(selected) for message in group)
        return (system, *history)

    def append(self, message: ChatMessage) -> None:
        """Append a message and discard the oldest session-only entries."""

        if message.role == "system":
            raise ValueError("The system prompt is configured separately")
        self._messages.append(message)
        while len(self._messages) > self.max_messages:
            first_group = _message_groups(self._messages)[0]
            del self._messages[: len(first_group)]

    def extend(self, messages: Sequence[ChatMessage]) -> None:
        """Append multiple messages while preserving the same bound."""

        for message in messages:
            self.append(message)

    def clear(self) -> None:
        """Clear only active-session history, never persistent memory."""

        self._messages.clear()

    def discard_tool_exchange(self, tool_call_ids: Sequence[str]) -> None:
        """Remove one completed raw tool exchange from future provider context."""

        identifiers = frozenset(tool_call_ids)
        if not identifiers:
            return
        self._messages = [
            message
            for message in self._messages
            if not (
                message.role == "tool" and message.tool_call_id in identifiers
                or message.role == "assistant"
                and any(call.id in identifiers for call in message.tool_calls)
            )
        ]


def _message_groups(messages: Sequence[ChatMessage]) -> tuple[tuple[ChatMessage, ...], ...]:
    """Group assistant tool calls with every required tool response."""

    groups: list[tuple[ChatMessage, ...]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        if message.role == "assistant" and message.tool_calls:
            identifiers = {call.id for call in message.tool_calls}
            group = [message]
            index += 1
            while (
                index < len(messages)
                and messages[index].role == "tool"
                and messages[index].tool_call_id in identifiers
            ):
                group.append(messages[index])
                index += 1
            groups.append(tuple(group))
            continue
        groups.append((message,))
        index += 1
    return tuple(groups)


def _message_characters(message: ChatMessage) -> int:
    total = len(message.content) + len(message.name or "") + len(message.tool_call_id or "")
    for call in message.tool_calls:
        total += len(call.id) + len(call.name)
        try:
            total += len(json.dumps(dict(call.arguments), ensure_ascii=False))
        except (TypeError, ValueError):
            total += 1_024
    return total
