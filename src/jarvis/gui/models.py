"""Framework-neutral view models for the optional JARVIS desktop interface."""

from __future__ import annotations

import copy
import math
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, TypeAlias

UIValue: TypeAlias = None | bool | int | float | str | list["UIValue"] | dict[str, "UIValue"]

MAX_CHAT_CHARACTERS = 8_000
MAX_DISPLAY_CHARACTERS = 4_000
MAX_ROWS = 250
MAX_COLLECTION_ITEMS = 100
MAX_VALUE_DEPTH = 5

_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SECRET_KEYS = re.compile(
    r"(?:api[_-]?key|authorization|cookie|credential|password|secret|session|token)",
    re.IGNORECASE,
)


class Theme(StrEnum):
    """Supported UI color modes."""

    SYSTEM = "system"
    LIGHT = "light"
    DARK = "dark"


class AssistantState(StrEnum):
    """High-level assistant lifecycle shown in the status area."""

    IDLE = "idle"
    WORKING = "working"
    AWAITING_PERMISSION = "awaiting_permission"
    CANCELLING = "cancelling"
    ERROR = "error"
    STOPPED = "stopped"


class ChatRole(StrEnum):
    """Visual chat roles; tool payloads are represented as activity cards instead."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class ActivityState(StrEnum):
    """Lifecycle state for one explicit action request."""

    REQUESTED = "requested"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    OUTCOME_UNKNOWN = "outcome_unknown"


class Page(StrEnum):
    """Stable navigation identifiers used by all GUI adapters."""

    CHAT = "chat"
    MEMORY = "memory"
    TASKS = "tasks"
    INTEGRATIONS = "integrations"
    PLUGINS = "plugins"
    SETTINGS = "settings"
    LOGS = "logs"
    ABOUT = "about"


class GuiUpdateKind(StrEnum):
    """Small update vocabulary for presentation adapters."""

    CHAT = "chat"
    ACTIVITY = "activity"
    STATUS = "status"
    PERMISSION = "permission"
    DATA = "data"


def utc_now() -> datetime:
    """Return an aware timestamp, kept as a function for deterministic injection."""

    return datetime.now(UTC)


def clean_text(
    value: object,
    *,
    limit: int = MAX_DISPLAY_CHARACTERS,
    collapse_whitespace: bool = False,
) -> str:
    """Return display-safe bounded text without terminal/control sequences."""

    if isinstance(value, str):
        text = value
    elif value is None:
        text = ""
    else:
        text = str(value)
    text = _CONTROL_CHARACTERS.sub("", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        f"\\u{ord(character):04x}" if unicodedata.category(character) == "Cf" else character
        for character in text
    )
    if collapse_whitespace:
        text = " ".join(text.split())
    if len(text) > limit:
        text = f"{text[: limit - 1]}…"
    return text


def bounded_ui_value(
    value: Any,
    *,
    redact_secrets: bool = True,
    _depth: int = 0,
) -> UIValue:
    """Convert arbitrary provider data into bounded JSON-like local UI data."""

    if _depth >= MAX_VALUE_DEPTH:
        return "…"
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else clean_text(value)
    if isinstance(value, (str, bytes)):
        if isinstance(value, bytes):
            return f"<{len(value)} bytes>"
        return clean_text(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        result: dict[str, UIValue] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= MAX_COLLECTION_ITEMS:
                result["…"] = f"{len(value) - MAX_COLLECTION_ITEMS} more items"
                break
            key = clean_text(raw_key, limit=100, collapse_whitespace=True) or "(unnamed)"
            if redact_secrets and _SECRET_KEYS.search(key):
                result[key] = (
                    None if item is None or (isinstance(item, str) and not item) else "***"
                )
            else:
                result[key] = bounded_ui_value(
                    item,
                    redact_secrets=redact_secrets,
                    _depth=_depth + 1,
                )
        return result
    if isinstance(value, Sequence):
        return [
            bounded_ui_value(
                item,
                redact_secrets=redact_secrets,
                _depth=_depth + 1,
            )
            for item in value[:MAX_COLLECTION_ITEMS]
        ]
    return clean_text(value)


def _aware(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return value


def _identifier(value: str, label: str) -> str:
    result = clean_text(value, limit=128, collapse_whitespace=True)
    if not result:
        raise ValueError(f"{label} cannot be empty")
    return result


@dataclass(frozen=True, slots=True)
class ChatMessageView:
    """One local transcript entry."""

    role: ChatRole
    text: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", ChatRole(self.role))
        object.__setattr__(self, "id", _identifier(self.id, "message id"))
        object.__setattr__(self, "created_at", _aware(self.created_at, "created_at"))
        text = clean_text(self.text, limit=MAX_CHAT_CHARACTERS)
        if not text.strip():
            raise ValueError("chat message text cannot be empty")
        object.__setattr__(self, "text", text)


@dataclass(frozen=True, slots=True)
class ActionActivity:
    """Auditable, secret-safe presentation of an action lifecycle."""

    action_name: str
    request_id: str
    state: ActivityState
    summary: str = ""
    error_code: str | None = None
    started_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "action_name", _identifier(self.action_name, "action name"))
        object.__setattr__(self, "request_id", _identifier(self.request_id, "request id"))
        object.__setattr__(self, "state", ActivityState(self.state))
        object.__setattr__(self, "summary", clean_text(self.summary, limit=500))
        if self.error_code is not None:
            object.__setattr__(
                self,
                "error_code",
                clean_text(self.error_code, limit=100, collapse_whitespace=True),
            )
        object.__setattr__(self, "started_at", _aware(self.started_at, "started_at"))
        if self.finished_at is not None:
            object.__setattr__(self, "finished_at", _aware(self.finished_at, "finished_at"))


@dataclass(frozen=True, slots=True)
class PermissionPrompt:
    """Local, bounded confirmation request displayed before an action runs."""

    id: str
    risk_level: str
    action_name: str
    summary: str
    details: Mapping[str, UIValue] = field(default_factory=dict)
    requested_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _identifier(self.id, "permission id"))
        object.__setattr__(self, "risk_level", _identifier(self.risk_level, "risk level"))
        object.__setattr__(self, "action_name", _identifier(self.action_name, "action name"))
        summary = clean_text(self.summary, limit=1_000)
        if not summary.strip():
            raise ValueError("permission summary cannot be empty")
        object.__setattr__(self, "summary", summary)
        # Permission details intentionally remain visible: they are the user's
        # last chance to inspect target, URL, text, or identifiers. They are
        # sanitized and bounded but not silently replaced with optimistic prose.
        safe = bounded_ui_value(self.details, redact_secrets=False)
        if not isinstance(safe, dict):
            raise TypeError("permission details must be a mapping")
        object.__setattr__(self, "details", MappingProxyType(copy.deepcopy(safe)))
        object.__setattr__(self, "requested_at", _aware(self.requested_at, "requested_at"))


@dataclass(frozen=True, slots=True)
class StatusView:
    """Current assistant and capability status."""

    state: AssistantState = AssistantState.IDLE
    message: str = "Ready"
    ai_provider: str = "disabled"
    execution_label: str = "local"
    enabled_components: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", AssistantState(self.state))
        object.__setattr__(self, "message", clean_text(self.message, limit=500))
        object.__setattr__(
            self,
            "ai_provider",
            clean_text(self.ai_provider, limit=100, collapse_whitespace=True) or "disabled",
        )
        object.__setattr__(
            self,
            "execution_label",
            clean_text(self.execution_label, limit=50, collapse_whitespace=True) or "local",
        )
        object.__setattr__(
            self,
            "enabled_components",
            tuple(
                clean_text(item, limit=50, collapse_whitespace=True)
                for item in self.enabled_components[:32]
                if clean_text(item, limit=50, collapse_whitespace=True)
            ),
        )


@dataclass(frozen=True, slots=True)
class GuiUpdate:
    """Notification sent from a framework-neutral controller to a view adapter."""

    kind: GuiUpdateKind
    payload: UIValue = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", GuiUpdateKind(self.kind))
        object.__setattr__(self, "payload", bounded_ui_value(self.payload))


@dataclass(frozen=True, slots=True)
class MemoryView:
    id: str
    category: str
    key: str
    value: str
    updated_at: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", clean_text(self.id, limit=64, collapse_whitespace=True))
        object.__setattr__(
            self, "category", clean_text(self.category, limit=100, collapse_whitespace=True)
        )
        object.__setattr__(self, "key", clean_text(self.key, limit=500))
        object.__setattr__(self, "value", clean_text(self.value, limit=MAX_DISPLAY_CHARACTERS))
        object.__setattr__(self, "updated_at", clean_text(self.updated_at, limit=100))


@dataclass(frozen=True, slots=True)
class ReminderView:
    id: str
    message: str
    due_at: str
    timezone: str
    recurrence: str
    status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", clean_text(self.id, limit=64, collapse_whitespace=True))
        object.__setattr__(self, "message", clean_text(self.message, limit=2_000))
        object.__setattr__(self, "due_at", clean_text(self.due_at, limit=100))
        object.__setattr__(
            self, "timezone", clean_text(self.timezone, limit=100, collapse_whitespace=True)
        )
        object.__setattr__(
            self,
            "recurrence",
            clean_text(self.recurrence, limit=100, collapse_whitespace=True),
        )
        object.__setattr__(
            self, "status", clean_text(self.status, limit=100, collapse_whitespace=True)
        )


@dataclass(frozen=True, slots=True)
class IntegrationView:
    name: str
    provider: str
    status: str
    detail: str = ""

    def __post_init__(self) -> None:
        for name in ("name", "provider", "status"):
            object.__setattr__(
                self,
                name,
                clean_text(getattr(self, name), limit=100, collapse_whitespace=True),
            )
        object.__setattr__(self, "detail", clean_text(self.detail, limit=1_000))


@dataclass(frozen=True, slots=True)
class PluginView:
    name: str
    version: str
    status: str
    description: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", clean_text(self.name, limit=100))
        object.__setattr__(
            self, "version", clean_text(self.version, limit=50, collapse_whitespace=True)
        )
        object.__setattr__(
            self, "status", clean_text(self.status, limit=100, collapse_whitespace=True)
        )
        object.__setattr__(self, "description", clean_text(self.description, limit=1_000))


@dataclass(frozen=True, slots=True)
class SettingView:
    section: str
    key: str
    value: str
    redacted: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "section", clean_text(self.section, limit=100, collapse_whitespace=True)
        )
        object.__setattr__(self, "key", clean_text(self.key, limit=100, collapse_whitespace=True))
        object.__setattr__(self, "value", clean_text(self.value, limit=2_000))
        if not isinstance(self.redacted, bool):
            raise TypeError("setting redacted flag must be a boolean")


@dataclass(frozen=True, slots=True)
class LogView:
    timestamp: str
    level: str
    logger: str
    message: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", clean_text(self.timestamp, limit=100))
        object.__setattr__(
            self, "level", clean_text(self.level, limit=20, collapse_whitespace=True)
        )
        object.__setattr__(
            self, "logger", clean_text(self.logger, limit=100, collapse_whitespace=True)
        )
        object.__setattr__(self, "message", clean_text(self.message, limit=MAX_DISPLAY_CHARACTERS))


@dataclass(frozen=True, slots=True)
class AboutView:
    name: str
    version: str
    description: str
    python_version: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", clean_text(self.name, limit=100))
        object.__setattr__(
            self, "version", clean_text(self.version, limit=50, collapse_whitespace=True)
        )
        object.__setattr__(self, "description", clean_text(self.description, limit=1_000))
        object.__setattr__(
            self,
            "python_version",
            clean_text(self.python_version, limit=100, collapse_whitespace=True),
        )


PageData: TypeAlias = (
    tuple[MemoryView, ...]
    | tuple[ReminderView, ...]
    | tuple[IntegrationView, ...]
    | tuple[PluginView, ...]
    | tuple[SettingView, ...]
    | tuple[LogView, ...]
    | AboutView
    | tuple[()]
)
