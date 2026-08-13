"""Execution-time safety checks for desktop input actions.

Permissions answer *whether* an action may run.  This module additionally binds
keyboard input to the window the user approved and rejects known shell-launch
sequences.  It deliberately does not try to infer arbitrary user intent.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from jarvis.computer import WindowInformation, WindowsController
from jarvis.computer.errors import ComputerError
from jarvis.core.actions import ActionRequest, ActionResult


class SafetyViolation(RuntimeError):
    """Raised when an action cannot be tied to a trustworthy desktop target."""


@dataclass(frozen=True, slots=True)
class GuardContext:
    """Opaque execution binding plus user-visible confirmation details."""

    action_name: str
    details: Mapping[str, Any] = field(default_factory=dict)
    window_handle: int | None = None
    window_title: str | None = None


class ExecutionGuard(Protocol):
    """Boundary used by the runtime immediately before desktop execution."""

    async def validate_sequence(self, requests: Sequence[ActionRequest]) -> None: ...

    async def prepare(
        self,
        action_name: str,
        arguments: Mapping[str, Any],
        previous_results: Sequence[ActionResult],
    ) -> GuardContext: ...

    async def verify(self, context: GuardContext) -> None: ...


class NoOpExecutionGuard:
    """Guard used by embedded runtimes that expose no desktop input actions."""

    async def validate_sequence(self, requests: Sequence[ActionRequest]) -> None:
        return None

    async def prepare(
        self,
        action_name: str,
        arguments: Mapping[str, Any],
        previous_results: Sequence[ActionResult],
    ) -> GuardContext:
        return GuardContext(action_name)

    async def verify(self, context: GuardContext) -> None:
        return None


_KEYBOARD_ACTIONS = frozenset({"type_text", "press_key", "press_hotkey"})
_POINTER_ACTIONS = frozenset(
    {"move_mouse", "click_mouse", "double_click_mouse", "right_click_mouse", "scroll_mouse"}
)
_FORBIDDEN_HOTKEYS = frozenset(
    {
        frozenset({"win", "r"}),
        frozenset({"win", "x"}),
        frozenset({"ctrl", "alt", "t"}),
    }
)
_TERMINAL_TITLES = (
    "command prompt",
    "powershell",
    "terminal",
    "cmd.exe",
    "bash",
    "shell",
    "console",
)


class DesktopExecutionGuard:
    """Bind keyboard actions to one active, non-terminal window.

    When an application was just launched, the guard waits briefly for that
    application's titled window to become active.  The exact foreground handle is
    then included in the confirmation and rechecked immediately before input.
    """

    def __init__(
        self,
        windows: WindowsController,
        *,
        focus_timeout_seconds: float = 4.0,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        if focus_timeout_seconds <= 0 or poll_interval_seconds <= 0:
            raise ValueError("desktop safety timeouts must be positive")
        self._windows = windows
        self._focus_timeout = focus_timeout_seconds
        self._poll_interval = poll_interval_seconds

    async def validate_sequence(self, requests: Sequence[ActionRequest]) -> None:
        for index, request in enumerate(requests):
            if request.name == "press_hotkey":
                raw_keys = request.arguments.get("keys", [])
                if isinstance(raw_keys, list):
                    keys = frozenset(str(key).casefold().strip() for key in raw_keys)
                    if keys in _FORBIDDEN_HOTKEYS:
                        raise SafetyViolation(
                            "Shortcuts that open a command launcher or terminal are not allowed."
                        )
            if request.name == "type_text":
                for following in requests[index + 1 :]:
                    if following.name == "press_key" and str(
                        following.arguments.get("key", "")
                    ).casefold() in {"enter", "return"}:
                        raise SafetyViolation(
                            "A type-then-Enter sequence is blocked because it could "
                            "execute a command."
                        )

    async def prepare(
        self,
        action_name: str,
        arguments: Mapping[str, Any],
        previous_results: Sequence[ActionResult],
    ) -> GuardContext:
        del arguments
        if action_name not in _KEYBOARD_ACTIONS | _POINTER_ACTIONS:
            return GuardContext(action_name)

        expected_title = _expected_application_title(previous_results)
        window = await self._wait_for_window(expected_title)
        normalized_title = window.title.casefold()
        if action_name in _KEYBOARD_ACTIONS and any(
            marker in normalized_title for marker in _TERMINAL_TITLES
        ):
            raise SafetyViolation("Keyboard input into terminals and command shells is disabled.")
        return GuardContext(
            action_name,
            {
                "target_window": window.title,
                "target_window_handle": window.handle,
            },
            window_handle=window.handle,
            window_title=window.title,
        )

    async def verify(self, context: GuardContext) -> None:
        if context.window_handle is None:
            return
        try:
            current = await asyncio.to_thread(self._windows.active_window)
        except ComputerError as exc:
            raise SafetyViolation("The approved target window is no longer available.") from exc
        if current.handle != context.window_handle or current.title != context.window_title:
            raise SafetyViolation(
                "The active window changed after confirmation, so desktop input was cancelled."
            )

    async def _wait_for_window(self, expected_title: str | None) -> WindowInformation:
        deadline = time.monotonic() + self._focus_timeout
        last_error: ComputerError | None = None
        while True:
            try:
                window = await asyncio.to_thread(self._windows.active_window)
                if expected_title is None or expected_title.casefold() in window.title.casefold():
                    return window
            except ComputerError as exc:
                last_error = exc
            if time.monotonic() >= deadline:
                detail = (
                    f"The launched {expected_title} window did not become the active target."
                    if expected_title
                    else "No active titled window was available for keyboard input."
                )
                raise SafetyViolation(detail) from last_error
            await asyncio.sleep(self._poll_interval)


def _expected_application_title(results: Sequence[ActionResult]) -> str | None:
    if not results:
        return None
    previous = results[-1]
    if not previous.success or previous.action != "open_application":
        return None
    if not isinstance(previous.data, Mapping):
        return None
    display_name = previous.data.get("display_name")
    return display_name if isinstance(display_name, str) and display_name.strip() else None
