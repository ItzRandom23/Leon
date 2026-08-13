"""Typed calendar event values with timezone-aware boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.integrations.errors import IntegrationValidationError


@dataclass(frozen=True, slots=True)
class CalendarEventRequest:
    title: str
    start: datetime
    end: datetime
    timezone: str = "UTC"
    description: str = field(default="", repr=False)
    location: str = ""
    attendees: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _text(self.title, "title", maximum=500, required=True)
        _text(self.description, "description", maximum=65536, allow_newlines=True)
        _text(self.location, "location", maximum=1000)
        zone = _zone(self.timezone)
        start = _aware(self.start, "start")
        end = _aware(self.end, "end")
        if end <= start:
            raise IntegrationValidationError("Calendar event end must be after start")
        attendees = _attendees(self.attendees)
        object.__setattr__(self, "timezone", zone.key)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        object.__setattr__(self, "attendees", attendees)

    def to_json(self) -> dict[str, object]:
        return {
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "timezone": self.timezone,
            "description": self.description,
            "location": self.location,
            "attendees": list(self.attendees),
        }


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    id: str
    title: str
    start: datetime
    end: datetime
    timezone: str = "UTC"
    description: str = field(default="", repr=False)
    location: str = ""
    attendees: tuple[str, ...] = ()
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _identifier(self.id)
        validated = CalendarEventRequest(
            self.title,
            self.start,
            self.end,
            self.timezone,
            self.description,
            self.location,
            self.attendees,
        )
        object.__setattr__(self, "start", validated.start)
        object.__setattr__(self, "end", validated.end)
        object.__setattr__(self, "timezone", validated.timezone)
        object.__setattr__(self, "attendees", validated.attendees)
        if self.updated_at is not None:
            object.__setattr__(self, "updated_at", _aware(self.updated_at, "updated_at"))

    def to_json(self) -> dict[str, object]:
        value = CalendarEventRequest(
            self.title,
            self.start,
            self.end,
            self.timezone,
            self.description,
            self.location,
            self.attendees,
        ).to_json()
        value.update(
            {
                "id": self.id,
                "updated_at": None if self.updated_at is None else self.updated_at.isoformat(),
            }
        )
        return value


@dataclass(frozen=True, slots=True)
class CalendarEventUpdate:
    title: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    timezone: str | None = None
    description: str | None = field(default=None, repr=False)
    location: str | None = None
    attendees: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        if all(
            value is None
            for value in (
                self.title,
                self.start,
                self.end,
                self.timezone,
                self.description,
                self.location,
                self.attendees,
            )
        ):
            raise IntegrationValidationError("Calendar update cannot be empty")
        if self.title is not None:
            _text(self.title, "title", maximum=500, required=True)
        if self.description is not None:
            _text(self.description, "description", maximum=65536, allow_newlines=True)
        if self.location is not None:
            _text(self.location, "location", maximum=1000)
        if self.start is not None:
            object.__setattr__(self, "start", _aware(self.start, "start"))
        if self.end is not None:
            object.__setattr__(self, "end", _aware(self.end, "end"))
        if self.timezone is not None:
            object.__setattr__(self, "timezone", _zone(self.timezone).key)
        if self.attendees is not None:
            object.__setattr__(self, "attendees", _attendees(self.attendees))

    def to_json(self) -> dict[str, object]:
        return {
            "title": self.title,
            "start": None if self.start is None else self.start.isoformat(),
            "end": None if self.end is None else self.end.isoformat(),
            "timezone": self.timezone,
            "description": self.description,
            "location": self.location,
            "attendees": None if self.attendees is None else list(self.attendees),
        }


@dataclass(frozen=True, slots=True)
class CalendarSearch:
    text: str
    start: datetime | None = None
    end: datetime | None = None

    def __post_init__(self) -> None:
        _text(self.text, "search text", maximum=500, required=True)
        if self.start is not None:
            object.__setattr__(self, "start", _aware(self.start, "start"))
        if self.end is not None:
            object.__setattr__(self, "end", _aware(self.end, "end"))
        if self.start is not None and self.end is not None and self.end <= self.start:
            raise IntegrationValidationError("Calendar search range is invalid")

    def to_json(self) -> dict[str, str | None]:
        return {
            "text": self.text,
            "start": None if self.start is None else self.start.isoformat(),
            "end": None if self.end is None else self.end.isoformat(),
        }


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or _controls(value):
        raise IntegrationValidationError("Calendar event id is invalid")
    return value


def _text(
    value: str,
    name: str,
    *,
    maximum: int,
    required: bool = False,
    allow_newlines: bool = False,
) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise IntegrationValidationError(f"Calendar {name} is invalid")
    if required and not value.strip():
        raise IntegrationValidationError(f"Calendar {name} cannot be empty")
    if _controls(value, allow_newlines=allow_newlines):
        raise IntegrationValidationError(f"Calendar {name} is invalid")
    return value


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IntegrationValidationError(f"Calendar {name} must be timezone-aware")
    return value.astimezone(UTC)


def _zone(value: str) -> ZoneInfo:
    if not isinstance(value, str) or not value.strip():
        raise IntegrationValidationError("Calendar timezone is invalid")
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise IntegrationValidationError("Calendar timezone is unknown") from None


def _attendees(values: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(values, tuple) or len(values) > 100:
        raise IntegrationValidationError("Calendar attendees are invalid")
    normalized: list[str] = []
    for value in values:
        if (
            not isinstance(value, str)
            or value.count("@") != 1
            or len(value) > 320
            or any(char.isspace() or ord(char) < 32 for char in value)
        ):
            raise IntegrationValidationError("Calendar attendee address is invalid")
        normalized.append(value)
    return tuple(normalized)


def _controls(value: str, *, allow_newlines: bool = False) -> bool:
    allowed = {"\n", "\r", "\t"} if allow_newlines else set()
    return any(ord(char) < 32 and char not in allowed for char in value)
