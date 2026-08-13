"""CalDAV calendar adapter with deterministic ICS mapping.

The provider talks to a real CalDAV server through the ``caldav`` library while
keeping the permissioned contract: create/update/delete require the caller's
confirmation and mutations bind to a stable event id (the ICS UID).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.integrations.auth import CredentialResolver, SecretCredential
from jarvis.integrations.calendar.models import (
    CalendarEvent,
    CalendarEventRequest,
    CalendarEventUpdate,
    CalendarSearch,
)
from jarvis.integrations.calendar.provider import CalendarProvider
from jarvis.integrations.errors import (
    IntegrationAuthError,
    IntegrationDataError,
    IntegrationError,
    IntegrationNotConnectedError,
    IntegrationTransportError,
    IntegrationValidationError,
)

ClientFactory = Callable[[], Any]


class CalDAVCalendarProvider(CalendarProvider):
    """Real CalDAV calendar connected at start using resolved credentials."""

    def __init__(
        self,
        credentials: CredentialResolver,
        *,
        credential_id: str = "calendar.password",
        url: str = "",
        username: str = "",
        client_factory: ClientFactory | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        super().__init__()
        self._credentials = credentials
        self._credential_id = credential_id
        self._url = url
        self._username = username
        self._client_factory = client_factory
        self._clock = clock
        self._calendar: Any = None

    @property
    def _connected_calendar(self) -> Any:
        if self._calendar is None:
            raise IntegrationNotConnectedError("Calendar provider is not connected")
        return self._calendar

    async def _connect(self) -> None:
        if not self._url or not self._username:
            raise IntegrationAuthError("CalDAV endpoint and username are not configured")
        secret = self._credentials.resolve(self._credential_id)
        client = self._build_client(secret)
        try:
            self._calendar = _resolve_calendar(client)
        except IntegrationError:
            raise
        except Exception:
            raise IntegrationAuthError(
                "Calendar credentials or endpoint could not be verified"
            ) from None

    async def _disconnect(self) -> None:
        self._calendar = None

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
        bounded = _bounded(limit)
        return await asyncio.to_thread(self._list, start, end, bounded)

    async def search_events(
        self, search: CalendarSearch, *, limit: int = 50
    ) -> tuple[CalendarEvent, ...]:
        self._ensure_connected()
        if not isinstance(search, CalendarSearch):
            raise TypeError("search must be CalendarSearch")
        bounded = _bounded(limit)
        return await asyncio.to_thread(self._search, search, bounded)

    async def upcoming_events(
        self, *, now: datetime | None = None, limit: int = 10
    ) -> tuple[CalendarEvent, ...]:
        self._ensure_connected()
        instant = _aware(self._clock() if now is None else now, "now")
        bounded = _bounded(limit)
        return await asyncio.to_thread(self._upcoming, instant, bounded)

    async def read_event(self, event_id: str) -> CalendarEvent:
        self._ensure_connected()
        _event_id(event_id)
        return await asyncio.to_thread(self._read, event_id)

    async def create_event(self, request: CalendarEventRequest) -> CalendarEvent:
        self._ensure_connected()
        if not isinstance(request, CalendarEventRequest):
            raise TypeError("request must be CalendarEventRequest")
        return await asyncio.to_thread(self._create, request)

    async def update_event(self, event_id: str, update: CalendarEventUpdate) -> CalendarEvent:
        self._ensure_connected()
        if not isinstance(update, CalendarEventUpdate):
            raise TypeError("update must be CalendarEventUpdate")
        _event_id(event_id)
        return await asyncio.to_thread(self._update, event_id, update)

    async def delete_event(self, event_id: str) -> bool:
        self._ensure_connected()
        _event_id(event_id)
        return await asyncio.to_thread(self._delete, event_id)

    def _build_client(self, secret: SecretCredential) -> Any:
        if self._client_factory is not None:
            return self._client_factory()
        import caldav

        return caldav.DAVClient(url=self._url, username=self._username, password=secret.reveal())

    def _list(
        self, start: datetime | None, end: datetime | None, limit: int
    ) -> tuple[CalendarEvent, ...]:
        events = self._remote_events()
        matched = [event for event in events if _overlaps(event, start, end)]
        matched.sort(key=lambda event: (event.start, event.id))
        return tuple(matched[:limit])

    def _search(self, search: CalendarSearch, limit: int) -> tuple[CalendarEvent, ...]:
        text = search.text.casefold()
        matched = [
            event
            for event in self._remote_events()
            if _overlaps(event, search.start, search.end)
            and text in f"{event.title}\n{event.description}\n{event.location}".casefold()
        ]
        matched.sort(key=lambda event: (event.start, event.id))
        return tuple(matched[:limit])

    def _upcoming(self, now: datetime, limit: int) -> tuple[CalendarEvent, ...]:
        matched = [event for event in self._remote_events() if event.end > now]
        matched.sort(key=lambda event: (event.start, event.id))
        return tuple(matched[:limit])

    def _read(self, event_id: str) -> CalendarEvent:
        event = _load_by_uid(self._connected_calendar, event_id)
        parsed = _parse_caldav_event(event)
        if parsed is None:
            raise IntegrationDataError("Calendar event could not be read")
        return parsed

    def _create(self, request: CalendarEventRequest) -> CalendarEvent:
        event_id = uuid4().hex
        ics = _render_ics(request, event_id, self._clock())
        try:
            created = self._connected_calendar.save_event(ics)
            created.load()
            remote_uid = str(created.vobject_instance.vevent.uid.value)
        except IntegrationError:
            raise
        except Exception:
            raise IntegrationTransportError("The calendar event could not be created") from None
        return CalendarEvent(
            remote_uid,
            request.title,
            request.start,
            request.end,
            request.timezone,
            request.description,
            request.location,
            request.attendees,
            self._clock(),
        )

    def _update(self, event_id: str, update: CalendarEventUpdate) -> CalendarEvent:
        event = _load_by_uid(self._connected_calendar, event_id)
        current = _parse_caldav_event(event)
        if current is None:
            raise IntegrationDataError("Calendar event could not be read")
        request = CalendarEventRequest(
            current.title if update.title is None else update.title,
            current.start if update.start is None else update.start,
            current.end if update.end is None else update.end,
            current.timezone if update.timezone is None else update.timezone,
            current.description if update.description is None else update.description,
            current.location if update.location is None else update.location,
            current.attendees if update.attendees is None else update.attendees,
        )
        ics = _render_ics(request, event_id, self._clock())
        try:
            event.data = ics
            event.save()
        except IntegrationError:
            raise
        except Exception:
            raise IntegrationTransportError("The calendar event could not be updated") from None
        return CalendarEvent(
            event_id,
            request.title,
            request.start,
            request.end,
            request.timezone,
            request.description,
            request.location,
            request.attendees,
            self._clock(),
        )

    def _delete(self, event_id: str) -> bool:
        event = _load_by_uid(self._connected_calendar, event_id)
        try:
            event.delete()
        except IntegrationError:
            raise
        except Exception:
            raise IntegrationTransportError("The calendar event could not be deleted") from None
        return True

    def _remote_events(self) -> list[CalendarEvent]:
        try:
            remote = self._connected_calendar.events()
        except IntegrationError:
            raise
        except Exception:
            raise IntegrationTransportError("The calendar could not be listed") from None
        events: list[CalendarEvent] = []
        for item in remote:
            parsed = _parse_caldav_event(item)
            if parsed is not None:
                events.append(parsed)
        return events

    def _ensure_connected(self) -> None:
        if self.status.value != "connected":
            raise IntegrationNotConnectedError("Calendar provider is not connected")


def _resolve_calendar(client: Any) -> Any:
    principal = client.principal()
    calendars = principal.calendars()
    if not calendars:
        raise IntegrationAuthError("No calendar collection is available")
    return calendars[0]


def _load_by_uid(calendar: Any, event_id: str) -> Any:
    try:
        return calendar.event_by_uid(event_id)
    except IntegrationError:
        raise
    except Exception:
        raise IntegrationValidationError("Calendar event was not found") from None


def _parse_caldav_event(event: Any) -> CalendarEvent | None:
    try:
        event.load()
        vevent = event.vobject_instance.vevent
        uid = _content_value(vevent, "uid")
        if not uid:
            return None
        dtstart = _property_value(vevent, "dtstart")
        if dtstart is None:
            return None
        tzid = _property_tzid(vevent, "dtstart")
        start = _as_aware(dtstart, tzid)
        dtend = _property_value(vevent, "dtend")
        end = start + timedelta(hours=1) if dtend is None else _as_aware(dtend, tzid)
        if end <= start:
            return None
        modified = _property_value(vevent, "lastmodified")
        updated_at = None if modified is None else _as_aware(modified, None)
        return CalendarEvent(
            uid,
            _content_value(vevent, "summary") or "",
            start,
            end,
            tzid or "UTC",
            _content_value(vevent, "description") or "",
            _content_value(vevent, "location") or "",
            _attendee_values(vevent),
            updated_at,
        )
    except (IntegrationValidationError, IntegrationDataError):
        return None
    except Exception:
        return None


def _content_value(vevent: Any, name: str) -> str | None:
    line = getattr(vevent, name, None)
    if line is None:
        return None
    value = getattr(line, "value", None)
    return None if value is None else str(value)


def _property_value(vevent: Any, name: str) -> datetime | date | None:
    line = getattr(vevent, name, None)
    if line is None:
        return None
    value = getattr(line, "value", None)
    if not isinstance(value, (datetime, date)):
        return None
    return value


def _property_tzid(vevent: Any, name: str) -> str | None:
    line = getattr(vevent, name, None)
    if line is None:
        return None
    params = getattr(line, "params", None)
    if not isinstance(params, dict):
        return None
    tzid = params.get("TZID")
    if isinstance(tzid, (list, tuple)):
        tzid = tzid[0] if tzid else None
    return None if tzid is None else str(tzid)


def _attendee_values(vevent: Any) -> tuple[str, ...]:
    contents = getattr(vevent, "contents", None)
    if not isinstance(contents, dict):
        return ()
    result: list[str] = []
    for line in contents.get("attendee", []):
        value = str(getattr(line, "value", "")).strip()
        if value.lower().startswith("mailto:"):
            value = value[7:]
        if value and "@" in value:
            result.append(value)
    return tuple(result)


def _as_aware(value: datetime | date, tzid: str | None) -> datetime:
    if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC)
    zone = _zone(tzid)
    if isinstance(value, datetime):
        value = value.replace(tzinfo=zone)
    else:
        value = datetime.combine(value, time.min, tzinfo=zone)
    return value.astimezone(UTC)


def _zone(tzid: str | None) -> ZoneInfo:
    if not tzid:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(tzid)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _render_ics(request: CalendarEventRequest, uid: str, stamp: datetime) -> str:
    zone = _zone(request.timezone)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//JARVIS Assistant//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{_utc(stamp)}",
    ]
    if zone.key == "UTC":
        lines.append(f"DTSTART:{_utc(request.start)}")
        lines.append(f"DTEND:{_utc(request.end)}")
    else:
        lines.append(f"DTSTART;TZID={request.timezone}:{_local(request.start, zone)}")
        lines.append(f"DTEND;TZID={request.timezone}:{_local(request.end, zone)}")
    lines.append(f"SUMMARY:{_escape(request.title)}")
    if request.description:
        lines.append(f"DESCRIPTION:{_escape(request.description)}")
    if request.location:
        lines.append(f"LOCATION:{_escape(request.location)}")
    for attendee in request.attendees:
        lines.append(f"ATTENDEE:mailto:{attendee}")
    lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _local(value: datetime, zone: ZoneInfo) -> str:
    return value.astimezone(zone).strftime("%Y%m%dT%H%M%S")


def _escape(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return escaped.replace("\r\n", "\\n").replace("\n", "\\n")


def _aware(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise IntegrationValidationError(f"Calendar {name} must be timezone-aware")
    return value.astimezone(UTC)


def _event_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 512
        or any(ord(char) < 32 for char in value)
    ):
        raise IntegrationValidationError("Calendar event id is invalid")
    return value


def _bounded(value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 100:
        raise IntegrationValidationError("Calendar result limit must be between 1 and 100")
    return value


def _overlaps(event: CalendarEvent, start: datetime | None, end: datetime | None) -> bool:
    return (start is None or event.end > start) and (end is None or event.start < end)
