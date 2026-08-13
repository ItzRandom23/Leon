"""Explicit permissioned reminder actions for the shared JARVIS runtime."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any

from jarvis.core.actions import ActionParameter, ActionRegistry, ActionResult
from jarvis.core.events import EventBus, EventName
from jarvis.skills.base import RiskLevel
from jarvis.tasks.errors import ReminderError
from jarvis.tasks.models import Reminder, ReminderStatus
from jarvis.tasks.service import ReminderService


@dataclass(slots=True)
class ReminderActionService:
    """Application-facing reminder service plus non-sensitive event metadata."""

    reminders: ReminderService
    timezone: str = "UTC"
    events: EventBus | None = None

    async def created(self, reminder: Reminder) -> None:
        if self.events is not None:
            await self.events.publish(
                EventName.TASK_CREATED,
                {"reminder_id": reminder.id, "recurrence": reminder.recurrence.value},
                source="tasks",
            )


def register_reminder_actions(
    registry: ActionRegistry,
    service: ReminderActionService,
) -> None:
    """Register persistence-backed reminder operations with strict schemas."""

    message = ActionParameter(
        "message",
        str,
        "Reminder text explicitly supplied by the user.",
        min_length=1,
        max_length=2_000,
    )
    timezone = ActionParameter(
        "timezone",
        str,
        "IANA timezone name, such as UTC or Asia/Kolkata.",
        required=False,
        max_length=100,
        default=service.timezone,
    )

    @registry.action(
        name="create_reminder",
        description="Persist a one-time reminder at an ISO 8601 date/time.",
        parameters=(
            message,
            ActionParameter(
                "scheduled_at",
                str,
                "ISO 8601 local or timezone-aware date/time.",
                max_length=80,
            ),
            timezone,
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def create_reminder(
        message: str,
        scheduled_at: str,
        timezone: str = service.timezone,
    ) -> ActionResult:
        try:
            reminder = service.reminders.remind_at(
                message,
                _parse_datetime(scheduled_at),
                timezone=timezone,
            )
            await service.created(reminder)
            return _created_result("create_reminder", reminder)
        except (ReminderError, ValueError):
            return _failure("create_reminder", "That reminder date or timezone is invalid.")

    @registry.action(
        name="create_relative_reminder",
        description="Persist a one-time reminder a bounded number of minutes from now.",
        parameters=(
            message,
            ActionParameter("delay_minutes", int, minimum=1, maximum=525_600),
            timezone,
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def create_relative_reminder(
        message: str,
        delay_minutes: int,
        timezone: str = service.timezone,
    ) -> ActionResult:
        try:
            reminder = service.reminders.remind_in(
                message,
                timedelta(minutes=delay_minutes),
                timezone=timezone,
            )
            await service.created(reminder)
            return _created_result("create_relative_reminder", reminder)
        except ReminderError:
            return _failure("create_relative_reminder", "That reminder could not be created.")

    @registry.action(
        name="create_daily_reminder",
        description="Persist a reminder that recurs every day at a local wall time.",
        parameters=(message, _time_parameter(), timezone),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def create_daily_reminder(
        message: str,
        at: str,
        timezone: str = service.timezone,
    ) -> ActionResult:
        try:
            reminder = service.reminders.remind_daily(
                message,
                _parse_time(at),
                timezone=timezone,
            )
            await service.created(reminder)
            return _created_result("create_daily_reminder", reminder)
        except (ReminderError, ValueError):
            return _failure("create_daily_reminder", "That recurring reminder is invalid.")

    @registry.action(
        name="create_weekly_reminder",
        description="Persist a reminder that recurs on one weekday at a local wall time.",
        parameters=(
            message,
            ActionParameter(
                "weekday",
                str,
                enum=(
                    "monday",
                    "tuesday",
                    "wednesday",
                    "thursday",
                    "friday",
                    "saturday",
                    "sunday",
                ),
            ),
            _time_parameter(),
            timezone,
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def create_weekly_reminder(
        message: str,
        weekday: str,
        at: str,
        timezone: str = service.timezone,
    ) -> ActionResult:
        try:
            reminder = service.reminders.remind_weekly(
                message,
                weekday,
                _parse_time(at),
                timezone=timezone,
            )
            await service.created(reminder)
            return _created_result("create_weekly_reminder", reminder)
        except (ReminderError, ValueError):
            return _failure("create_weekly_reminder", "That weekly reminder is invalid.")

    @registry.action(
        name="create_weekday_reminder",
        description="Persist a Monday-to-Friday reminder at a local wall time.",
        parameters=(message, _time_parameter(), timezone),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def create_weekday_reminder(
        message: str,
        at: str,
        timezone: str = service.timezone,
    ) -> ActionResult:
        try:
            reminder = service.reminders.remind_weekdays(
                message,
                _parse_time(at),
                timezone=timezone,
            )
            await service.created(reminder)
            return _created_result("create_weekday_reminder", reminder)
        except (ReminderError, ValueError):
            return _failure("create_weekday_reminder", "That weekday reminder is invalid.")

    @registry.action(
        name="list_reminders",
        description="Read persistent reminders; reminder text may be sensitive.",
        parameters=(
            ActionParameter(
                "status",
                str,
                required=False,
                enum=("all", "scheduled", "cancelled", "triggered"),
                default="all",
            ),
            ActionParameter("limit", int, required=False, minimum=1, maximum=200, default=50),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def list_reminders(status: str = "all", limit: int = 50) -> ActionResult:
        try:
            selected = None if status == "all" else ReminderStatus(status)
            reminders = service.reminders.list(selected, limit=limit)
            return ActionResult.succeeded(
                "list_reminders",
                message=f"Found {len(reminders)} reminders.",
                data={"reminders": [_reminder_data(item) for item in reminders]},
            )
        except (ReminderError, ValueError):
            return _failure("list_reminders", "Reminders could not be listed.")

    @registry.action(
        name="list_missed_reminders",
        description="Read reminders that were due while JARVIS was not running.",
        parameters=(
            ActionParameter("limit", int, required=False, minimum=1, maximum=200, default=50),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def list_missed_reminders(limit: int = 50) -> ActionResult:
        try:
            reminders = service.reminders.missed(limit=limit)
            return ActionResult.succeeded(
                "list_missed_reminders",
                message=f"Found {len(reminders)} missed reminders.",
                data={"reminders": [_reminder_data(item) for item in reminders]},
            )
        except ReminderError:
            return _failure("list_missed_reminders", "Missed reminders could not be listed.")

    @registry.action(
        name="edit_reminder",
        description=(
            "Edit the message and due time of the exact scheduled reminder the user reviewed. "
            "Timezone and recurrence are preserved."
        ),
        risk_level=RiskLevel.SENSITIVE,
        parameters=(
            ActionParameter("reminder_id", "integer", minimum=1),
            ActionParameter("expected_message", "string", min_length=1, max_length=2_000),
            ActionParameter("expected_due_at", "string", min_length=1, max_length=100),
            ActionParameter("message", "string", min_length=1, max_length=2_000),
            ActionParameter("scheduled_at", "string", min_length=1, max_length=100),
        ),
    )
    async def edit_reminder(
        reminder_id: int,
        expected_message: str,
        expected_due_at: str,
        message: str,
        scheduled_at: str,
    ) -> ActionResult:
        try:
            expected_due = datetime.fromisoformat(expected_due_at.replace("Z", "+00:00"))
            due = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
            reminder = service.edit_scheduled(
                reminder_id,
                message=message,
                due_at=due,
                expected_message=expected_message,
                expected_due_at=expected_due,
            )
            return ActionResult.succeeded(
                "edit_reminder",
                message=f"Updated reminder {reminder.id} for {reminder.due_at.isoformat()}.",
                data={
                    "id": reminder.id,
                    "message": reminder.message,
                    "due_at": reminder.due_at.isoformat(),
                    "timezone": reminder.timezone,
                    "recurrence": reminder.recurrence.value,
                    "status": reminder.status.value,
                },
            )
        except (ReminderError, ValueError):
            return _failure("edit_reminder", "That reminder could not be edited.")

    @registry.action(
        name="cancel_reminder",
        description="Cancel a scheduled reminder without deleting its history.",
        parameters=(
            ActionParameter("reminder_id", int, minimum=1),
            ActionParameter("expected_message", str, min_length=1, max_length=2_000),
        ),
        risk_level=RiskLevel.ACTION,
    )
    async def cancel_reminder(reminder_id: int, expected_message: str) -> ActionResult:
        try:
            current = service.reminders.get(reminder_id)
            if current is None or current.message != expected_message:
                return _failure(
                    "cancel_reminder",
                    "The reminder changed or does not match the confirmed message.",
                )
            reminder = service.reminders.cancel(reminder_id)
            if reminder is None:
                return _failure("cancel_reminder", "That reminder does not exist.")
            return ActionResult.succeeded(
                "cancel_reminder",
                message=f"Cancelled reminder {reminder_id}.",
                data={"reminder_id": reminder_id, "status": reminder.status.value},
            )
        except ReminderError:
            return _failure("cancel_reminder", "That reminder could not be cancelled.")

    @registry.action(
        name="delete_reminder",
        description="Permanently delete a reminder and its stored history.",
        parameters=(
            ActionParameter("reminder_id", int, minimum=1),
            ActionParameter("expected_message", str, min_length=1, max_length=2_000),
        ),
        risk_level=RiskLevel.DESTRUCTIVE,
    )
    async def delete_reminder(reminder_id: int, expected_message: str) -> ActionResult:
        try:
            current = service.reminders.get(reminder_id)
            if current is None or current.message != expected_message:
                return _failure(
                    "delete_reminder",
                    "The reminder changed or does not match the confirmed message.",
                )
            removed = service.reminders.delete(reminder_id)
            if not removed:
                return _failure("delete_reminder", "That reminder does not exist.")
            return ActionResult.succeeded(
                "delete_reminder",
                message=f"Deleted reminder {reminder_id}.",
                data={"reminder_id": reminder_id},
            )
        except ReminderError:
            return _failure("delete_reminder", "That reminder could not be deleted.")


def _time_parameter() -> ActionParameter:
    return ActionParameter(
        "at",
        str,
        "Local time in HH:MM or HH:MM:SS format.",
        pattern=r"^\d{2}:\d{2}(?::\d{2})?$",
        max_length=8,
    )


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("scheduled_at must be an ISO 8601 date/time") from None


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError("at must be a valid local time") from None


def _created_result(action: str, reminder: Reminder) -> ActionResult:
    return ActionResult.succeeded(
        action,
        message=f"Created reminder {reminder.id} for {reminder.due_at.isoformat()}.",
        data={"reminder": _reminder_data(reminder)},
    )


def _reminder_data(reminder: Reminder) -> dict[str, Any]:
    scheduled_action = reminder.scheduled_action
    return {
        "id": reminder.id,
        "message": reminder.message,
        "due_at": reminder.due_at.isoformat(),
        "timezone": reminder.timezone,
        "recurrence": reminder.recurrence.value,
        "status": reminder.status.value,
        "created_at": reminder.created_at.isoformat(),
        "updated_at": reminder.updated_at.isoformat(),
        "last_triggered_at": (
            None if reminder.last_triggered_at is None else reminder.last_triggered_at.isoformat()
        ),
        "scheduled_action": (
            None
            if scheduled_action is None
            else {
                "action_name": scheduled_action.action_name,
                "execution_enabled": scheduled_action.execution_enabled,
                "permission_required": scheduled_action.permission_required,
            }
        ),
    }


def _failure(action: str, message: str) -> ActionResult:
    return ActionResult.failed(
        action,
        "The reminder service reported a controlled failure.",
        message=message,
        error_code="reminder_error",
    )
