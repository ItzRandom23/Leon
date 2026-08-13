"""Application-facing reminder scheduling service."""

from __future__ import annotations

import builtins
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta

from jarvis.tasks.models import Reminder, ReminderStatus, ScheduledAction, utc_datetime
from jarvis.tasks.recurrence import ReminderSchedule
from jarvis.tasks.repository import ReminderRepository

Clock = Callable[[], datetime]
ChangeCallback = Callable[[], None]


def _default_clock() -> datetime:
    return datetime.now(UTC)


class ReminderService:
    """Create typed schedules and coordinate repository changes."""

    def __init__(
        self,
        repository: ReminderRepository,
        *,
        clock: Clock = _default_clock,
        on_change: ChangeCallback | None = None,
    ) -> None:
        if not isinstance(repository, ReminderRepository):
            raise TypeError("repository must implement ReminderRepository")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if on_change is not None and not callable(on_change):
            raise TypeError("on_change must be callable")
        self.repository = repository
        self._clock = clock
        self._on_change = on_change

    def _now(self) -> datetime:
        return utc_datetime(self._clock(), "clock result")

    def _changed(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def create(
        self,
        message: str,
        schedule: ReminderSchedule,
        *,
        idempotency_key: str | None = None,
        scheduled_action: ScheduledAction | None = None,
    ) -> Reminder:
        if not isinstance(schedule, ReminderSchedule):
            raise TypeError("schedule must be a ReminderSchedule")
        reminder = self.repository.create(
            message,
            schedule,
            idempotency_key=idempotency_key,
            scheduled_action=scheduled_action,
        )
        self._changed()
        return reminder

    def remind_at(
        self,
        message: str,
        when: datetime,
        *,
        timezone: str = "UTC",
        idempotency_key: str | None = None,
        scheduled_action: ScheduledAction | None = None,
    ) -> Reminder:
        return self.create(
            message,
            ReminderSchedule.one_time(when, timezone=timezone),
            idempotency_key=idempotency_key,
            scheduled_action=scheduled_action,
        )

    def remind_in(
        self,
        message: str,
        delay: timedelta,
        *,
        timezone: str = "UTC",
        idempotency_key: str | None = None,
        scheduled_action: ScheduledAction | None = None,
    ) -> Reminder:
        return self.create(
            message,
            ReminderSchedule.relative(delay, now=self._now(), timezone=timezone),
            idempotency_key=idempotency_key,
            scheduled_action=scheduled_action,
        )

    def remind_on(
        self,
        message: str,
        day: date,
        at: time,
        *,
        timezone: str = "UTC",
        idempotency_key: str | None = None,
    ) -> Reminder:
        return self.create(
            message,
            ReminderSchedule.on_date(day, at, timezone=timezone),
            idempotency_key=idempotency_key,
        )

    def remind_daily(
        self,
        message: str,
        at: time,
        *,
        timezone: str = "UTC",
        idempotency_key: str | None = None,
    ) -> Reminder:
        return self.create(
            message,
            ReminderSchedule.daily(at, now=self._now(), timezone=timezone),
            idempotency_key=idempotency_key,
        )

    def remind_weekly(
        self,
        message: str,
        weekday: int | str,
        at: time,
        *,
        timezone: str = "UTC",
        idempotency_key: str | None = None,
    ) -> Reminder:
        return self.create(
            message,
            ReminderSchedule.weekly(weekday, at, now=self._now(), timezone=timezone),
            idempotency_key=idempotency_key,
        )

    def remind_weekdays(
        self,
        message: str,
        at: time,
        *,
        timezone: str = "UTC",
        idempotency_key: str | None = None,
    ) -> Reminder:
        return self.create(
            message,
            ReminderSchedule.weekdays(at, now=self._now(), timezone=timezone),
            idempotency_key=idempotency_key,
        )

    daily = remind_daily
    weekly = remind_weekly
    weekdays = remind_weekdays

    def get(self, reminder_id: int) -> Reminder | None:
        return self.repository.get(reminder_id)

    def list(
        self,
        status: ReminderStatus | str | None = None,
        *,
        limit: int | None = None,
    ) -> builtins.list[Reminder]:
        return list(self.repository.list(status, limit=limit))

    def due(
        self, now: datetime | None = None, *, limit: int | None = None
    ) -> builtins.list[Reminder]:
        return list(self.repository.due(now, limit=limit))

    def missed(
        self,
        now: datetime | None = None,
        *,
        limit: int | None = None,
    ) -> builtins.list[Reminder]:
        return list(self.repository.missed(now, limit=limit))

    def cancel(self, reminder_id: int) -> Reminder | None:
        reminder = self.repository.cancel(reminder_id)
        if reminder is not None:
            self._changed()
        return reminder

    def edit_scheduled(
        self,
        reminder_id: int,
        *,
        message: str,
        due_at: datetime,
        expected_message: str,
        expected_due_at: datetime,
    ) -> Reminder:
        """Edit the exact still-scheduled occurrence previously shown to a user."""

        reminder = self.repository.edit_scheduled(
            reminder_id,
            message=message,
            due_at=due_at,
            expected_message=expected_message,
            expected_due_at=expected_due_at,
        )
        self._changed()
        return reminder

    reschedule = edit_scheduled

    def delete(self, reminder_id: int) -> bool:
        removed = self.repository.delete(reminder_id)
        if removed:
            self._changed()
        return removed
