"""A lightweight in-process event bus for observable JARVIS workflows."""

from __future__ import annotations

import copy
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, TypeAlias


class EventName(StrEnum):
    """Core event names. Extensions may publish their own dotted names."""

    ASSISTANT_STARTED = "assistant.started"
    ASSISTANT_STOPPED = "assistant.stopped"
    ASSISTANT_MESSAGE = "assistant.message"
    USER_MESSAGE = "user.message"
    AI_RESPONSE = "ai.response"
    ACTION_REQUESTED = "action.requested"
    ACTION_STARTED = "action.started"
    ACTION_COMPLETED = "action.completed"
    ACTION_FAILED = "action.failed"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_ALLOWED = "permission.allowed"
    PERMISSION_DENIED = "permission.denied"
    MEMORY_CREATED = "memory.created"
    MEMORY_DELETED = "memory.deleted"
    SCREENSHOT_CAPTURED = "screenshot.captured"
    BROWSER_STARTED = "browser.started"
    BROWSER_NAVIGATION_COMPLETED = "browser.navigation_completed"
    TASK_CREATED = "task.created"
    TASK_TRIGGERED = "task.triggered"
    TASK_COMPLETED = "task.completed"
    PLUGIN_LOADED = "plugin.loaded"
    PLUGIN_FAILED = "plugin.failed"
    INTEGRATION_CONNECTED = "integration.connected"
    INTEGRATION_FAILED = "integration.failed"


ASSISTANT_STARTED = EventName.ASSISTANT_STARTED
USER_MESSAGE = EventName.USER_MESSAGE
AI_RESPONSE = EventName.AI_RESPONSE
ACTION_REQUESTED = EventName.ACTION_REQUESTED
ACTION_STARTED = EventName.ACTION_STARTED
ACTION_COMPLETED = EventName.ACTION_COMPLETED
ACTION_FAILED = EventName.ACTION_FAILED
PERMISSION_REQUESTED = EventName.PERMISSION_REQUESTED
MEMORY_CREATED = EventName.MEMORY_CREATED
SCREENSHOT_CAPTURED = EventName.SCREENSHOT_CAPTURED


def _event_name(value: EventName | str) -> str:
    if isinstance(value, EventName):
        return value.value
    if not isinstance(value, str) or not value.strip():
        raise ValueError("event name cannot be empty")
    normalized = value.strip()
    if any(part == "" for part in normalized.split(".")):
        raise ValueError(f"invalid event name: {value!r}")
    return normalized


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable event record."""

    name: EventName | str
    payload: Mapping[str, Any] = field(default_factory=dict)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _event_name(self.name))
        if not isinstance(self.payload, Mapping):
            raise TypeError("event payload must be a mapping")
        if not all(isinstance(key, str) for key in self.payload):
            raise TypeError("event payload keys must be strings")
        object.__setattr__(self, "payload", MappingProxyType(copy.deepcopy(dict(self.payload))))
        if not isinstance(self.occurred_at, datetime):
            raise TypeError("event timestamp must be a datetime")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("event timestamp must be timezone-aware")
        if self.source is not None and (
            not isinstance(self.source, str) or not self.source.strip()
        ):
            raise ValueError("event source cannot be empty")

    @property
    def timestamp(self) -> datetime:
        """Compatibility alias for ``occurred_at``."""

        return self.occurred_at


EventHandler: TypeAlias = Callable[[Event], None | Awaitable[None]]


class EventBus:
    """Publish events to sync or async subscribers in registration order."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventHandler]] = {}

    def subscribe(self, name: EventName | str, handler: EventHandler) -> Callable[[], bool]:
        """Subscribe a handler and return a function that removes it."""

        normalized = "*" if name == "*" else _event_name(name)
        if not callable(handler):
            raise TypeError("event handler must be callable")
        handlers = self._subscribers.setdefault(normalized, [])
        if handler not in handlers:
            handlers.append(handler)

        def unsubscribe() -> bool:
            return self.unsubscribe(normalized, handler)

        return unsubscribe

    def unsubscribe(self, name: EventName | str, handler: EventHandler) -> bool:
        """Remove a subscription, returning whether it existed."""

        normalized = "*" if name == "*" else _event_name(name)
        handlers = self._subscribers.get(normalized)
        if handlers is None or handler not in handlers:
            return False
        handlers.remove(handler)
        if not handlers:
            del self._subscribers[normalized]
        return True

    def clear(self) -> None:
        """Remove all subscribers."""

        self._subscribers.clear()

    async def publish(
        self,
        event: Event | EventName | str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
        raise_exceptions: bool = False,
    ) -> tuple[Exception, ...]:
        """Deliver one event sequentially and return isolated handler errors."""

        if isinstance(event, Event):
            if payload is not None or source is not None:
                raise TypeError("payload and source cannot be supplied with an Event")
            record = event
        else:
            record = Event(event, {} if payload is None else payload, source=source)

        handlers = tuple(self._subscribers.get(str(record.name), ())) + tuple(
            self._subscribers.get("*", ())
        )
        errors: list[Exception] = []
        for handler in handlers:
            try:
                result = handler(record)
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                if raise_exceptions:
                    raise
                errors.append(exc)
        return tuple(errors)

    async def emit(
        self,
        name: EventName | str,
        payload: Mapping[str, Any] | None = None,
        *,
        source: str | None = None,
    ) -> Event:
        """Create, publish, and return an event record."""

        event = Event(name, {} if payload is None else payload, source=source)
        await self.publish(event)
        return event
