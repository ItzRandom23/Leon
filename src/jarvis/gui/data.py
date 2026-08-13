"""Bounded application data adapters for the framework-neutral GUI."""

from __future__ import annotations

import asyncio
import inspect
import logging
import platform
import threading
from collections import deque
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from importlib import metadata
from typing import Any, Protocol, runtime_checkable

from jarvis.gui.models import (
    MAX_ROWS,
    AboutView,
    IntegrationView,
    LogView,
    MemoryView,
    Page,
    PageData,
    PluginView,
    ReminderView,
    SettingView,
    bounded_ui_value,
    clean_text,
)
from jarvis.utils.logging import redact


@runtime_checkable
class GuiDataProvider(Protocol):
    """Replaceable async data surface used by the controller."""

    async def load(self, page: Page) -> PageData:
        """Return immutable, presentation-safe data for one page."""


class GuiLogStore(logging.Handler):
    """Thread-safe in-memory log tail with redaction and fixed memory use."""

    def __init__(self, *, capacity: int = 500) -> None:
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, int)
            or not 1 <= capacity <= 5_000
        ):
            raise ValueError("log capacity must be between 1 and 5000")
        super().__init__(logging.NOTSET)
        self._records: deque[LogView] = deque(maxlen=capacity)
        self._lock = threading.RLock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            timestamp = datetime.fromtimestamp(record.created, UTC).isoformat()
            item = LogView(
                timestamp=timestamp,
                level=clean_text(record.levelname, limit=20, collapse_whitespace=True),
                logger=clean_text(record.name, limit=100, collapse_whitespace=True),
                message=clean_text(redact(record.getMessage()), limit=4_000),
            )
            with self._lock:
                self._records.append(item)
        except Exception:
            # Logging is diagnostic only. A malformed third-party LogRecord must
            # not crash the UI or fall back to an unredacted error rendering.
            return

    def snapshot(self, *, limit: int = MAX_ROWS) -> tuple[LogView, ...]:
        """Return the newest bounded records in chronological order."""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("log limit must be a positive integer")
        with self._lock:
            return tuple(self._records)[-min(limit, MAX_ROWS) :]


class ApplicationDataProvider:
    """Adapt one supplied application container without constructing services.

    The adapter deliberately uses narrow duck-typed seams so the GUI can be
    added to both the current application object and future richer composition
    roots. Synchronous repository access is moved off the UI event loop.
    """

    def __init__(self, application: object, *, logs: GuiLogStore | None = None) -> None:
        self.application = application
        self.logs = logs or GuiLogStore()

    async def load(self, page: Page) -> PageData:
        page = Page(page)
        if page is Page.MEMORY:
            return await self._memories()
        if page is Page.TASKS:
            return await self._reminders()
        if page is Page.INTEGRATIONS:
            return await self._integrations()
        if page is Page.PLUGINS:
            return await self._plugins()
        if page is Page.SETTINGS:
            return await self._settings()
        if page is Page.LOGS:
            return self.logs.snapshot()
        if page is Page.ABOUT:
            return _about()
        return ()

    async def _memories(self) -> tuple[MemoryView, ...]:
        source = _first_attribute(
            self.application,
            "memory_manager",
            "memory",
            "memory_repository",
        )
        records = await _records(source, ("list", "list_memories", "all"))
        result: list[MemoryView] = []
        for record in records[:MAX_ROWS]:
            result.append(
                MemoryView(
                    id=_display(_field(record, "id"), limit=64),
                    category=_display(_field(record, "category"), limit=100),
                    key=_display(_field(record, "key", "name"), limit=500),
                    value=_display(_field(record, "value", "content"), limit=4_000),
                    updated_at=_display(_field(record, "updated_at", "created_at"), limit=100),
                )
            )
        return tuple(result)

    async def _reminders(self) -> tuple[ReminderView, ...]:
        source = _first_attribute(
            self.application,
            "reminder_service",
            "tasks",
            "task_service",
            "reminder_repository",
        )
        records = await _records(source, ("list", "list_reminders", "all"))
        result: list[ReminderView] = []
        for record in records[:MAX_ROWS]:
            result.append(
                ReminderView(
                    id=_display(_field(record, "id"), limit=64),
                    message=_display(_field(record, "message", "title"), limit=2_000),
                    due_at=_display(_field(record, "due_at", "scheduled_at"), limit=100),
                    timezone=_display(_field(record, "timezone"), limit=100),
                    recurrence=_display(_field(record, "recurrence"), limit=100),
                    status=_display(_field(record, "status"), limit=100),
                )
            )
        return tuple(result)

    async def _integrations(self) -> tuple[IntegrationView, ...]:
        source = _first_attribute(
            self.application,
            "integration_registry",
            "integrations",
            "integration_manager",
        )
        records = await _records(
            source,
            ("list_integrations", "list_metadata", "statuses", "list", "all"),
        )
        result: list[IntegrationView] = []
        for record in records[:MAX_ROWS]:
            metadata = _field(record, "metadata")
            result.append(
                IntegrationView(
                    name=_display(
                        _first_value(
                            _field(metadata, "display_name", "name"),
                            _field(record, "name", "id", "key"),
                        ),
                        limit=100,
                    ),
                    provider=_display(
                        _first_value(
                            _field(record, "provider", "kind", "integration_type"),
                            _field(metadata, "name"),
                        ),
                        limit=100,
                    ),
                    status=_display(_field(record, "status", "state", "enabled"), limit=100),
                    detail=_display(
                        _first_value(
                            _field(record, "detail", "description", "error"),
                            _field(metadata, "description"),
                        ),
                        limit=1_000,
                    ),
                )
            )
        return tuple(result)

    async def _plugins(self) -> tuple[PluginView, ...]:
        source = _first_attribute(
            self.application,
            "plugin_manager",
            "plugins",
            "plugin_registry",
        )
        records = await _records(
            source,
            ("list_plugins", "list_metadata", "statuses", "discover", "list", "all"),
        )
        result: list[PluginView] = []
        for record in records[:MAX_ROWS]:
            metadata = _field(record, "metadata")
            result.append(
                PluginView(
                    name=_display(
                        _first_value(
                            _field(record, "plugin_id", "name", "id", "key"),
                            _field(metadata, "name"),
                        ),
                        limit=100,
                    ),
                    version=_display(
                        _first_value(
                            _field(metadata, "version"),
                            _field(record, "version"),
                        ),
                        limit=50,
                    ),
                    status=_display(_field(record, "status", "state", "enabled"), limit=100),
                    description=_display(
                        _first_value(
                            _field(record, "description", "summary", "error"),
                            _field(metadata, "description"),
                        ),
                        limit=1_000,
                    ),
                )
            )
        return tuple(result)

    async def _settings(self) -> tuple[SettingView, ...]:
        config = getattr(self.application, "config", None)
        if config is None:
            return ()

        def serialize() -> Any:
            method = getattr(config, "redacted_dict", None)
            if callable(method):
                return method()
            method = getattr(config, "to_dict", None)
            if callable(method):
                try:
                    return method(redact_secrets=True)
                except TypeError:
                    return method()
            return {}

        raw = await asyncio.to_thread(serialize)
        safe = bounded_ui_value(raw, redact_secrets=True)
        if not isinstance(safe, dict):
            return ()
        result: list[SettingView] = []
        for section, values in safe.items():
            if isinstance(values, dict):
                entries = values.items()
            else:
                entries = (("value", values),)
            for key, value in entries:
                rendered = _display(value, limit=2_000)
                result.append(
                    SettingView(
                        section=clean_text(section, limit=100, collapse_whitespace=True),
                        key=clean_text(key, limit=100, collapse_whitespace=True),
                        value=rendered,
                        redacted=rendered == "***",
                    )
                )
                if len(result) >= MAX_ROWS:
                    return tuple(result)
        return tuple(result)


async def _records(source: object | None, methods: tuple[str, ...]) -> list[Any]:
    if source is None:
        return []
    for name in methods:
        candidate = getattr(source, name, None)
        if candidate is None:
            continue
        try:
            if callable(candidate):
                result = await asyncio.to_thread(candidate)
                if inspect.isawaitable(result):
                    result = await result
            else:
                result = candidate
        except TypeError:
            continue
        if result is None:
            return []
        if isinstance(result, Mapping):
            return [_with_mapping_key(key, value) for key, value in list(result.items())[:MAX_ROWS]]
        if isinstance(result, Iterable) and not isinstance(result, (str, bytes)):
            return list(result)[:MAX_ROWS]
        return [result]
    return []


def _with_mapping_key(key: object, value: object) -> object:
    if isinstance(value, Mapping):
        result = dict(value)
        result.setdefault("key", key)
        return result
    return {"key": key, "value": value}


def _first_attribute(value: object, *names: str) -> object | None:
    for name in names:
        candidate = getattr(value, name, None)
        if candidate is not None:
            return candidate
    return None


def _field(record: object, *names: str) -> object:
    for name in names:
        if isinstance(record, Mapping) and name in record:
            return record[name]
        if hasattr(record, name):
            return getattr(record, name)
    return ""


def _first_value(*values: object) -> object:
    for value in values:
        if value is not None and value != "":
            return value
    return ""


def _display(value: object, *, limit: int) -> str:
    raw = getattr(value, "value", value)
    safe = bounded_ui_value(raw)
    if isinstance(safe, (dict, list)):
        import json

        return clean_text(json.dumps(safe, ensure_ascii=False, sort_keys=True), limit=limit)
    return clean_text(safe, limit=limit)


def _about() -> AboutView:
    try:
        version = metadata.version("jarvis-assistant")
    except metadata.PackageNotFoundError:
        try:
            from jarvis import __version__

            version = __version__
        except (ImportError, AttributeError):
            version = "development"
    return AboutView(
        name="JARVIS",
        version=clean_text(version, limit=50),
        description="A modular, permissioned personal AI assistant.",
        python_version=platform.python_version(),
    )
