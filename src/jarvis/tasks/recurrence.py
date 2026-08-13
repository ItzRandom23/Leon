"""Timezone-aware reminder occurrence calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from jarvis.tasks.errors import ReminderValidationError
from jarvis.tasks.models import Recurrence, utc_datetime, validate_timezone

_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}


def _zone(value: str | ZoneInfo) -> ZoneInfo:
    return ZoneInfo(validate_timezone(value))


def _wall_time(value: time) -> time:
    if not isinstance(value, time):
        raise ReminderValidationError("A reminder wall time must be a time")
    if value.tzinfo is not None:
        raise ReminderValidationError("A wall time must not contain a timezone")
    return value


def _aware_now(value: datetime) -> datetime:
    return utc_datetime(value, "now")


def _localize(day: date, at: time, zone: ZoneInfo) -> datetime:
    """Create a real local instant, advancing through DST gaps when necessary."""

    candidate = datetime.combine(day, at, tzinfo=zone).replace(fold=at.fold)
    round_trip = candidate.astimezone(UTC).astimezone(zone)
    if (round_trip.date(), round_trip.timetz().replace(tzinfo=None)) == (day, at):
        return candidate

    # A nonexistent wall time (spring DST gap) advances to the first valid minute.
    naive = datetime.combine(day, at)
    for offset in range(1, 181):
        shifted = naive + timedelta(minutes=offset)
        localized = shifted.replace(tzinfo=zone)
        checked = localized.astimezone(UTC).astimezone(zone)
        if checked.replace(tzinfo=None) == shifted:
            return localized
    raise ReminderValidationError("The requested local reminder time does not exist")


def _weekday_number(value: int | str) -> int:
    if isinstance(value, bool):
        raise ReminderValidationError("A weekday must be Monday through Sunday")
    if isinstance(value, int) and 0 <= value <= 6:
        return value
    if isinstance(value, str):
        try:
            return _WEEKDAYS[value.strip().casefold()]
        except KeyError:
            pass
    raise ReminderValidationError("A weekday must be Monday through Sunday or 0 through 6")


@dataclass(frozen=True, slots=True)
class ReminderSchedule:
    """A calculated first occurrence and its recurrence rule."""

    due_at: datetime
    timezone: str = "UTC"
    recurrence: Recurrence = Recurrence.ONCE

    def __post_init__(self) -> None:
        object.__setattr__(self, "due_at", utc_datetime(self.due_at, "due_at"))
        object.__setattr__(self, "timezone", validate_timezone(self.timezone))
        try:
            object.__setattr__(self, "recurrence", Recurrence(self.recurrence))
        except (TypeError, ValueError) as exc:
            raise ReminderValidationError("Unknown reminder recurrence") from exc

    @classmethod
    def one_time(
        cls,
        when: datetime,
        *,
        timezone: str | ZoneInfo = "UTC",
    ) -> ReminderSchedule:
        zone = _zone(timezone)
        if not isinstance(when, datetime):
            raise ReminderValidationError("A one-time reminder requires a datetime")
        localized = when.replace(tzinfo=zone) if when.tzinfo is None else when
        return cls(localized.astimezone(UTC), zone.key, Recurrence.ONCE)

    @classmethod
    def relative(
        cls,
        delay: timedelta,
        *,
        now: datetime,
        timezone: str | ZoneInfo = "UTC",
    ) -> ReminderSchedule:
        if not isinstance(delay, timedelta) or delay < timedelta(0):
            raise ReminderValidationError("A relative reminder delay cannot be negative")
        zone = _zone(timezone)
        return cls(_aware_now(now) + delay, zone.key, Recurrence.ONCE)

    @classmethod
    def on_date(
        cls,
        day: date,
        at: time,
        *,
        timezone: str | ZoneInfo = "UTC",
    ) -> ReminderSchedule:
        if not isinstance(day, date) or isinstance(day, datetime):
            raise ReminderValidationError("A date reminder requires a calendar date")
        zone = _zone(timezone)
        return cls(_localize(day, _wall_time(at), zone), zone.key, Recurrence.ONCE)

    @classmethod
    def daily(
        cls,
        at: time,
        *,
        now: datetime,
        timezone: str | ZoneInfo = "UTC",
    ) -> ReminderSchedule:
        zone = _zone(timezone)
        local_now = _aware_now(now).astimezone(zone)
        candidate = _localize(local_now.date(), _wall_time(at), zone)
        if candidate < local_now:
            candidate = _localize(local_now.date() + timedelta(days=1), at, zone)
        return cls(candidate, zone.key, Recurrence.DAILY)

    @classmethod
    def weekly(
        cls,
        weekday: int | str,
        at: time,
        *,
        now: datetime,
        timezone: str | ZoneInfo = "UTC",
    ) -> ReminderSchedule:
        zone = _zone(timezone)
        local_now = _aware_now(now).astimezone(zone)
        target = _weekday_number(weekday)
        days = (target - local_now.weekday()) % 7
        candidate = _localize(local_now.date() + timedelta(days=days), _wall_time(at), zone)
        if candidate < local_now:
            candidate = _localize(candidate.date() + timedelta(days=7), at, zone)
        return cls(candidate, zone.key, Recurrence.WEEKLY)

    @classmethod
    def weekdays(
        cls,
        at: time,
        *,
        now: datetime,
        timezone: str | ZoneInfo = "UTC",
    ) -> ReminderSchedule:
        zone = _zone(timezone)
        local_now = _aware_now(now).astimezone(zone)
        day = local_now.date()
        while day.weekday() > 4:
            day += timedelta(days=1)
        candidate = _localize(day, _wall_time(at), zone)
        if candidate < local_now:
            day += timedelta(days=1)
            while day.weekday() > 4:
                day += timedelta(days=1)
            candidate = _localize(day, at, zone)
        return cls(candidate, zone.key, Recurrence.WEEKDAYS)

    weekday = weekdays

    def next_after(self, after: datetime | None = None) -> datetime | None:
        """Return the first recurring occurrence strictly after *after*."""

        return next_occurrence(
            self.due_at,
            self.recurrence,
            self.timezone,
            after=self.due_at if after is None else after,
        )


def next_occurrence(
    due_at: datetime,
    recurrence: Recurrence | str,
    timezone: str | ZoneInfo,
    *,
    after: datetime | None = None,
) -> datetime | None:
    """Calculate the first recurrence strictly after a UTC instant."""

    due = utc_datetime(due_at, "due_at")
    threshold = due if after is None else utc_datetime(after, "after")
    try:
        rule = Recurrence(recurrence)
    except (TypeError, ValueError) as exc:
        raise ReminderValidationError("Unknown reminder recurrence") from exc
    if rule is Recurrence.ONCE:
        return None

    zone = _zone(timezone)
    local_due = due.astimezone(zone)
    wall = local_due.timetz().replace(tzinfo=None)
    day = local_due.date()
    while True:
        if rule is Recurrence.DAILY:
            day += timedelta(days=1)
        elif rule is Recurrence.WEEKLY:
            day += timedelta(days=7)
        else:
            day += timedelta(days=1)
            while day.weekday() > 4:
                day += timedelta(days=1)
        candidate = _localize(day, wall, zone).astimezone(UTC)
        if candidate > threshold:
            return candidate
