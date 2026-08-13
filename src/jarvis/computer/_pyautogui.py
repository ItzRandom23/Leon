"""Lazy optional PyAutoGUI adapter shared by mouse and keyboard controllers."""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

from jarvis.computer.errors import AutomationUnavailableError


class PyAutoGUIAdapter:
    """Load PyAutoGUI on first use and enforce its corner fail-safe."""

    def __init__(self, module: Any | None = None) -> None:
        self._pyautogui: Any | None = module
        if module is not None:
            setattr(module, "FAILSAFE", True)

    def _backend(self) -> ModuleType:
        if self._pyautogui is None:
            try:
                self._pyautogui = importlib.import_module("pyautogui")
            except Exception as exc:
                raise AutomationUnavailableError(
                    "desktop input requires the optional 'pyautogui' dependency "
                    "and an interactive desktop"
                ) from exc
            setattr(self._pyautogui, "FAILSAFE", True)
        return self._pyautogui

    def size(self) -> tuple[int, int]:
        size = self._backend().size()
        return int(size[0]), int(size[1])

    def position(self) -> tuple[int, int]:
        position = self._backend().position()
        return int(position[0]), int(position[1])

    def move_to(self, x: int, y: int, *, duration: float) -> None:
        self._backend().moveTo(x, y, duration=duration)

    def click(self, *, x: int | None, y: int | None, button: str) -> None:
        self._backend().click(x=x, y=y, button=button)

    def double_click(self, *, x: int | None, y: int | None, button: str) -> None:
        self._backend().doubleClick(x=x, y=y, button=button)

    def scroll(self, clicks: int) -> None:
        self._backend().scroll(clicks)

    def write(self, text: str, *, interval: float) -> None:
        self._backend().write(text, interval=interval)

    def press(self, key: str, *, presses: int, interval: float) -> None:
        self._backend().press(key, presses=presses, interval=interval)

    def hotkey(self, *keys: str) -> None:
        self._backend().hotkey(*keys)
