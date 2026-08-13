"""Calendar provider contract and deterministic in-memory implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from uuid import uuid4

from jarvis.integrations.base import (
    IntegrationMetadata,
    IntegrationOperation,
    OperationKind,
    StatefulIntegration,
)
from jarvis.integrations.calendar.models import (
    CalendarEvent,
    CalendarEventRequest,
    CalendarEventUpdate,
    CalendarSearch,
)
from jarvis.integrations.errors import IntegrationNotConnectedError, IntegrationValidationError
from jarvis.skills.base import RiskLevel

CALENDAR_OPERATIONS = (
    IntegrationOperation(
        "calendar.list_events", OperationKind.READ, RiskLevel.SENSITIVE, "List events"
    ),
    IntegrationOperation(
        "calendar.search_events", OperationKind.READ, RiskLevel.SENSITIVE, "Search events"
    ),
    IntegrationOperation(
        "calendar.upcoming_events", OperationKind.READ, RiskLevel.SENSITIVE, "List upcoming events"
    ),
    IntegrationOperation(
        "calendar.create_event",
        OperationKind.WRITE,
        RiskLevel.SENSITIVE,
        "Create an event",
        confirmation_required=True,
    ),
    IntegrationOperation(
        "calendar.update_event",
        OperationKind.WRITE,
        RiskLevel.SENSITIVE,
        "Update an event",
        confirmation_required=True,
    ),
    IntegrationOperation(
        "calendar.delete_event",
        OperationKind.DELETE,
        RiskLevel.DESTRUCTIVE,
        "Delete an event",
        confirmation_required=True,
    ),
)

CALENDAR_METADATA = IntegrationMetadata(
    "calendar",
    "Calendar",
    "Provider-neutral calendar access with permission-ready mutations.",
    CALENDAR_OPERATIONS,
)


class CalendarProvider(StatefulIntegration, ABC):
    """Contract for future OAuth-backed calendar implementations."""

    def __init__(self, metadata: IntegrationMetadata = CALENDAR_METADATA) -> None:
        super().__init__(metadata)

    @abstractmethod
    async def list_events(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
    ) -> tuple[CalendarEvent, ...]:
        """List events overlapping an optional time range."""

    @abstractmethod
    async def search_events(
        self, search: CalendarSearch, *, limit: int = 50
    ) -> tuple[CalendarEvent, ...]:
        """Search event text and an optional time range."""

    @abstractmethod
    async def upcoming_events(
        self, *, now: datetime | None = None, limit: int = 10
    ) -> tuple[CalendarEvent, ...]:
        """Return events ending after the supplied instant."""

    @abstractmethod
    async def read_event(self, event_id: str) -> CalendarEvent:
        """Read one event so mutations can bind consent to a stable target."""

    @abstractmethod
    async def create_event(self, request: CalendarEventRequest) -> CalendarEvent:
        """Create an event after the caller's permission check."""

    @abstractmethod
    async def update_event(self, event_id: str, update: CalendarEventUpdate) -> CalendarEvent:
        """Update an event after the caller's permission check."""

    @abstractmethod
    async def delete_event(self, event_id: str) -> bool:
        """Delete an event after destructive-action confirmation."""


class InMemoryCalendarProvider(CalendarProvider):
    """Network-free calendar implementation for deterministic tests and demos."""

    def __init__(
        self,
        events: Sequence[CalendarEvent] = (),
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__()
        self._events = {event.id: event for event in events}
        if len(self._events) != len(events):
            raise IntegrationValidationError("Calendar event ids must be unique")
        self._clock = clock

    async def _connect(self) -> None:
        return None

    async def _disconnect(self) -> None:
        return None

    async def list_events(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 50,
    ) -> tuple[CalendarEvent, ...]:
        self._ensure_connected()
        start = None if start is None else _aware(start, "start")
        end = None if end is None else _aware(end, "end")
        if start is not None and end is not None and end <= start:
            raise IntegrationValidationError("Calendar range is invalid")
        values = [event for event in self._events.values() if _overlaps(event, start, end)]
        values.sort(key=lambda event: (event.start, event.id))
        return tuple(values[: _limit(limit)])

    async def search_events(
        self, search: CalendarSearch, *, limit: int = 50
    ) -> tuple[CalendarEvent, ...]:
        self._ensure_connected()
        if not isinstance(search, CalendarSearch):
            raise TypeError("search must be CalendarSearch")
        text = search.text.casefold()
        values = [
            event
            for event in self._events.values()
            if _overlaps(event, search.start, search.end)
            and text in f"{event.title}\n{event.description}\n{event.location}".casefold()
        ]
        values.sort(key=lambda event: (event.start, event.id))
        return tuple(values[: _limit(limit)])

    async def upcoming_events(
        self, *, now: datetime | None = None, limit: int = 10
    ) -> tuple[CalendarEvent, ...]:
        self._ensure_connected()
        instant = _aware(self._clock() if now is None else now, "now")
        values = [event for event in self._events.values() if event.end > instant]
        values.sort(key=lambda event: (event.start, event.id))
        return tuple(values[: _limit(limit)])

    async def read_event(self, event_id: str) -> CalendarEvent:
        self._ensure_connected()
        try:
            return self._events[event_id]
        except KeyError:
            raise IntegrationValidationError("Calendar event was not found") from None

    async def create_event(self, request: CalendarEventRequest) -> CalendarEvent:
        self._ensure_connected()
        if not isinstance(request, CalendarEventRequest):
            raise TypeError("request must be CalendarEventRequest")
        event = CalendarEvent(
            uuid4().hex,
            request.title,
            request.start,
            request.end,
            request.timezone,
            request.description,
            request.location,
            request.attendees,
            self._clock(),
        )
        self._events[event.id] = event
        return event

    async def update_event(self, event_id: str, update: CalendarEventUpdate) -> CalendarEvent:
        self._ensure_connected()
        if not isinstance(update, CalendarEventUpdate):
            raise TypeError("update must be CalendarEventUpdate")
        try:
            current = self._events[event_id]
        except KeyError:
            raise IntegrationValidationError("Calendar event was not found") from None
        event = CalendarEvent(
            current.id,
            current.title if update.title is None else update.title,
            current.start if update.start is None else update.start,
            current.end if update.end is None else update.end,
            current.timezone if update.timezone is None else update.timezone,
            current.description if update.description is None else update.description,
            current.location if update.location is None else update.location,
            current.attendees if update.attendees is None else update.attendees,
            self._clock(),
        )
        self._events[event.id] = event
        return event

    async def delete_event(self, event_id: str) -> bool:
        self._ensure_connected()
        return self._events.pop(event_id, None) is not None

    def _ensure_connected(self) -> None:
        if self.status.value != "connected":
            raise IntegrationNotConnectedError("Calendar provider is not connected")


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IntegrationValidationError(f"Calendar {name} must be timezone-aware")
    return value.astimezone(UTC)


def _limit(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise IntegrationValidationError("Calendar result limit must be between 1 and 100")
    return value


def _overlaps(event: CalendarEvent, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or event.end > start) and (end is None or event.start < end)
