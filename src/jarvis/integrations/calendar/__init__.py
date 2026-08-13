"""Calendar provider contracts and a network-free in-memory implementation."""

from jarvis.integrations.calendar.caldav_provider import CalDAVCalendarProvider
from jarvis.integrations.calendar.models import (
    CalendarEvent,
    CalendarEventRequest,
    CalendarEventUpdate,
    CalendarSearch,
)
from jarvis.integrations.calendar.provider import (
    CALENDAR_METADATA,
    CALENDAR_OPERATIONS,
    CalendarProvider,
    InMemoryCalendarProvider,
)

__all__ = [
    "CALENDAR_METADATA",
    "CALENDAR_OPERATIONS",
    "CalDAVCalendarProvider",
    "CalendarEvent",
    "CalendarEventRequest",
    "CalendarEventUpdate",
    "CalendarProvider",
    "CalendarSearch",
    "InMemoryCalendarProvider",
]
