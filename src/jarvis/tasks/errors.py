"""Domain exceptions for persistent reminders and scheduling."""

from __future__ import annotations


class ReminderError(Exception):
    """Base class for reminder subsystem failures."""


class ReminderValidationError(ReminderError, ValueError):
    """Raised when reminder input is invalid or unsafe."""


class ReminderRepositoryError(ReminderError):
    """Raised when reminder persistence cannot complete an operation."""


class ReminderSchemaError(ReminderRepositoryError):
    """Raised when a reminder database schema is incompatible."""


class ReminderClosedError(ReminderRepositoryError):
    """Raised when a closed repository is used."""


class ReminderConflictError(ReminderRepositoryError):
    """Raised for a conflicting idempotency key or stale occurrence update."""


class NotificationError(ReminderError):
    """Raised when a configured reminder notifier is unavailable or fails."""
