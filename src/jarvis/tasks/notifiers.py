"""Extensible reminder notification boundaries."""

from __future__ import annotations

import asyncio
import importlib
import re
from collections.abc import Callable
from typing import Any, Protocol, runtime_checkable

from jarvis.tasks.errors import NotificationError
from jarvis.tasks.models import Reminder

OutputFunction = Callable[[str], None]
ModuleLoader = Callable[[str], Any]

_UNSAFE_TERMINAL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


@runtime_checkable
class ReminderNotifier(Protocol):
    """Deliver one reminder without changing its persistence state."""

    async def notify(self, reminder: Reminder) -> None: ...


class TerminalNotifier:
    """Write reminder notifications to an injected terminal output function."""

    def __init__(self, output_fn: OutputFunction | None = None) -> None:
        self._output = output_fn or print

    async def notify(self, reminder: Reminder) -> None:
        if not isinstance(reminder, Reminder):
            raise TypeError("notify expects a Reminder")
        safe_message = _UNSAFE_TERMINAL.sub("", reminder.message)[:2_000]
        self._output(f"Reminder: {safe_message}")


class DesktopNotifier:
    """Lazily use the optional ``plyer`` desktop-notification package."""

    def __init__(
        self,
        *,
        module_loader: ModuleLoader = importlib.import_module,
        title: str = "JARVIS Reminder",
    ) -> None:
        if not callable(module_loader):
            raise TypeError("module_loader must be callable")
        if not isinstance(title, str) or not title.strip():
            raise ValueError("notification title cannot be empty")
        self._module_loader = module_loader
        self._title = title.strip()
        self._backend: Any | None = None

    def _load_backend(self) -> Any:
        if self._backend is not None:
            return self._backend
        try:
            module = self._module_loader("plyer")
            backend = module.notification
            notify = backend.notify
        except (ImportError, AttributeError) as exc:
            raise NotificationError(
                "Desktop notifications require the optional 'plyer' dependency"
            ) from exc
        if not callable(notify):
            raise NotificationError("The desktop notification backend is invalid")
        self._backend = backend
        return backend

    async def notify(self, reminder: Reminder) -> None:
        if not isinstance(reminder, Reminder):
            raise TypeError("notify expects a Reminder")
        backend = self._load_backend()
        safe_message = _UNSAFE_TERMINAL.sub("", reminder.message)[:2_000]
        try:
            await asyncio.to_thread(
                backend.notify,
                title=self._title,
                message=safe_message,
                app_name="JARVIS",
            )
        except Exception as exc:
            raise NotificationError("The desktop notification could not be delivered") from exc


LazyDesktopNotifier = DesktopNotifier
