"""Replaceable repository contract for reminders."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol, runtime_checkable

from jarvis.tasks.models import Recurrence, Reminder, ReminderStatus, ScheduledAction
from jarvis.tasks.recurrence import ReminderSchedule


@runtime_checkable
class ReminderRepository(Protocol):
    """Persistence operations required by the reminder scheduler."""

    @property
    def database_path(self) -> Path | None: ...

    @property
    def closed(self) -> bool: ...

    def create(
        self,
        message: str,
        due_at: datetime | ReminderSchedule,
        *,
        timezone: str = "UTC",
        recurrence: Recurrence | str = Recurrence.ONCE,
        idempotency_key: str | None = None,
        scheduled_action: ScheduledAction | None = None,
    ) -> Reminder: ...

    def get(self, reminder_id: int) -> Reminder | None: ...

    def list(
        self,
        status: ReminderStatus | str | None = None,
        *,
        limit: int | None = None,
    ) -> Sequence[Reminder]: ...

    def due(
        self, now: datetime | None = None, *, limit: int | None = None
    ) -> Sequence[Reminder]: ...

    def claim_due(
        self,
        now: datetime | None = None,
        *,
        owner: str,
        lease_until: datetime,
        limit: int | None = None,
    ) -> Sequence[Reminder]: ...

    def begin_delivery(
        self,
        reminder_id: int,
        *,
        owner: str,
        expected_due_at: datetime,
        started_at: datetime | None = None,
    ) -> bool: ...

    def release_claim(
        self,
        reminder_id: int,
        *,
        owner: str,
        expected_due_at: datetime,
    ) -> bool: ...

    def mark_claim_triggered(
        self,
        reminder_id: int,
        *,
        owner: str,
        triggered_at: datetime | None = None,
        expected_due_at: datetime,
    ) -> Reminder: ...

    def missed(
        self,
        now: datetime | None = None,
        *,
        limit: int | None = None,
    ) -> Sequence[Reminder]: ...

    def next_due(self) -> Reminder | None: ...

    def cancel(self, reminder_id: int) -> Reminder | None: ...

    def edit_scheduled(
        self,
        reminder_id: int,
        *,
        message: str,
        due_at: datetime,
        expected_message: str,
        expected_due_at: datetime,
    ) -> Reminder: ...

    def delete(self, reminder_id: int) -> bool: ...

    def mark_triggered(
        self,
        reminder_id: int,
        triggered_at: datetime | None = None,
        *,
        expected_due_at: datetime | None = None,
    ) -> Reminder: ...

    def count(self, status: ReminderStatus | str | None = None) -> int: ...

    def close(self) -> None: ...

    def __enter__(self) -> ReminderRepository: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...
