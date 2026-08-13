"""Persistent reminders and deterministic scheduling for JARVIS Phase 8."""

from jarvis.tasks.actions import ReminderActionService, register_reminder_actions
from jarvis.tasks.errors import (
    NotificationError,
    ReminderClosedError,
    ReminderConflictError,
    ReminderError,
    ReminderRepositoryError,
    ReminderSchemaError,
    ReminderValidationError,
)
from jarvis.tasks.models import Recurrence, Reminder, ReminderStatus, ScheduledAction
from jarvis.tasks.notifiers import (
    DesktopNotifier,
    LazyDesktopNotifier,
    ReminderNotifier,
    TerminalNotifier,
)
from jarvis.tasks.recurrence import ReminderSchedule, next_occurrence
from jarvis.tasks.repository import ReminderRepository
from jarvis.tasks.scheduler import (
    NotificationFailure,
    ReminderScheduler,
    Scheduler,
    SchedulerPollResult,
)
from jarvis.tasks.service import ReminderService
from jarvis.tasks.storage import SQLiteReminderRepository, SQLiteTaskRepository

ReminderRecurrence = Recurrence
TaskRepository = ReminderRepository

__all__ = [
    "DesktopNotifier",
    "LazyDesktopNotifier",
    "NotificationError",
    "NotificationFailure",
    "Recurrence",
    "Reminder",
    "ReminderActionService",
    "ReminderClosedError",
    "ReminderConflictError",
    "ReminderError",
    "ReminderNotifier",
    "ReminderRecurrence",
    "ReminderRepository",
    "ReminderRepositoryError",
    "ReminderSchedule",
    "ReminderScheduler",
    "ReminderSchemaError",
    "ReminderService",
    "ReminderStatus",
    "ReminderValidationError",
    "SQLiteReminderRepository",
    "SQLiteTaskRepository",
    "ScheduledAction",
    "Scheduler",
    "SchedulerPollResult",
    "TaskRepository",
    "TerminalNotifier",
    "next_occurrence",
    "register_reminder_actions",
]
