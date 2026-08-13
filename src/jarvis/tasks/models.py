"""Typed domain models for persistent reminders."""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, TypeAlias
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from jarvis.tasks.errors import ReminderValidationError

JSONValue: TypeAlias = None | bool | int | float | str | list["JSONValue"] | dict[str, "JSONValue"]

_ACTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")


class ReminderStatus(StrEnum):
    """Lifecycle state for a persisted reminder."""

    SCHEDULED = "scheduled"
    CANCELLED = "cancelled"
    TRIGGERED = "triggered"

    @classmethod
    def _missing_(cls, value: object) -> ReminderStatus | None:
        if isinstance(value, str):
            normalized = value.strip().casefold()
            for member in cls:
                if normalized in {member.value, member.name.casefold()}:
                    return member
        return None


class Recurrence(StrEnum):
    """Supported reminder recurrence rules."""

    ONCE = "once"
    NONE = "once"
    DAILY = "daily"
    WEEKLY = "weekly"
    WEEKDAYS = "weekdays"

    @classmethod
    def _missing_(cls, value: object) -> Recurrence | None:
        if isinstance(value, str):
            normalized = value.strip().casefold().replace("_", "-")
            aliases = {
                "none": cls.ONCE,
                "one-time": cls.ONCE,
                "onetime": cls.ONCE,
                "weekday": cls.WEEKDAYS,
                "week-days": cls.WEEKDAYS,
            }
            if normalized in aliases:
                return aliases[normalized]
            for member in cls:
                if normalized in {member.value, member.name.casefold()}:
                    return member
        return None


def _is_json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool)):
        return True
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def validate_timezone(value: str | ZoneInfo) -> str:
    """Return a canonical IANA timezone key."""

    if isinstance(value, ZoneInfo):
        return value.key
    if not isinstance(value, str) or not value.strip():
        raise ReminderValidationError("A reminder timezone must be an IANA timezone name")
    name = value.strip()
    try:
        return ZoneInfo(name).key
    except ZoneInfoNotFoundError as exc:
        raise ReminderValidationError(f"Unknown reminder timezone: {name!r}") from exc


def utc_datetime(value: datetime, label: str = "datetime") -> datetime:
    """Validate an aware datetime and normalize it to UTC."""

    if not isinstance(value, datetime):
        raise ReminderValidationError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReminderValidationError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ScheduledAction:
    """Inert metadata for a possible future permissioned action.

    Phase 8 deliberately has no action executor. The scheduler can persist and
    display this metadata, but cannot enable or execute it.
    """

    action_name: str
    arguments: Mapping[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.action_name, str) or not _ACTION_NAME.fullmatch(self.action_name):
            raise ReminderValidationError(f"Invalid scheduled action name: {self.action_name!r}")
        if not isinstance(self.arguments, Mapping):
            raise ReminderValidationError("Scheduled action arguments must be a mapping")
        snapshot = copy.deepcopy(dict(self.arguments))
        if not _is_json_value(snapshot):
            raise ReminderValidationError("Scheduled action arguments must be JSON-compatible")
        object.__setattr__(self, "arguments", MappingProxyType(snapshot))

    @property
    def permission_required(self) -> bool:
        """Scheduled actions always require a future permission decision."""

        return True

    @property
    def execution_enabled(self) -> bool:
        """Action execution is intentionally unavailable in Phase 8."""

        return False


@dataclass(frozen=True, slots=True)
class Reminder:
    """An immutable persisted reminder; all timestamps are normalized to UTC."""

    id: int
    message: str
    due_at: datetime
    timezone: str = "UTC"
    recurrence: Recurrence = Recurrence.ONCE
    status: ReminderStatus = ReminderStatus.SCHEDULED
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_triggered_at: datetime | None = None
    idempotency_key: str | None = None
    scheduled_action: ScheduledAction | None = None

    def __post_init__(self) -> None:
        if isinstance(self.id, bool) or not isinstance(self.id, int) or self.id < 1:
            raise ReminderValidationError("A reminder id must be a positive integer")
        if not isinstance(self.message, str):
            raise ReminderValidationError("A reminder message must be text")
        message = " ".join(self.message.split())
        if not message or len(message) > 2_000:
            raise ReminderValidationError("A reminder message must contain 1 to 2000 characters")
        try:
            recurrence = Recurrence(self.recurrence)
            status = ReminderStatus(self.status)
        except (TypeError, ValueError) as exc:
            raise ReminderValidationError(
                "A reminder contains an unknown status or recurrence"
            ) from exc
        timezone = validate_timezone(self.timezone)
        due_at = utc_datetime(self.due_at, "due_at")
        created_at = utc_datetime(self.created_at, "created_at")
        updated_at = utc_datetime(self.updated_at, "updated_at")
        last_triggered = (
            None
            if self.last_triggered_at is None
            else utc_datetime(self.last_triggered_at, "last_triggered_at")
        )
        if updated_at < created_at:
            raise ReminderValidationError("updated_at cannot precede created_at")
        if self.idempotency_key is not None and (
            not isinstance(self.idempotency_key, str) or not self.idempotency_key.strip()
        ):
            raise ReminderValidationError("An idempotency key cannot be empty")
        if self.scheduled_action is not None and not isinstance(
            self.scheduled_action, ScheduledAction
        ):
            raise ReminderValidationError("scheduled_action must be ScheduledAction metadata")
        object.__setattr__(self, "message", message)
        object.__setattr__(self, "timezone", timezone)
        object.__setattr__(self, "recurrence", recurrence)
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "due_at", due_at)
        object.__setattr__(self, "created_at", created_at)
        object.__setattr__(self, "updated_at", updated_at)
        object.__setattr__(self, "last_triggered_at", last_triggered)
        if self.idempotency_key is not None:
            object.__setattr__(self, "idempotency_key", self.idempotency_key.strip())

    @property
    def title(self) -> str:
        """Compatibility/readability alias for the reminder message."""

        return self.message
