"""Validated keyboard-control abstraction."""

from __future__ import annotations

import math
import string
from typing import Protocol

from jarvis.computer._pyautogui import PyAutoGUIAdapter
from jarvis.computer.errors import ComputerValidationError

MAX_TEXT_LENGTH = 500
MAX_PRESSES = 100
MAX_KEY_INTERVAL = 5.0
MAX_HOTKEY_KEYS = 6
FORBIDDEN_HOTKEYS = frozenset(
    {
        frozenset({"win", "r"}),
        frozenset({"win", "x"}),
        frozenset({"ctrl", "alt", "t"}),
    }
)

_NAMED_KEYS = {
    "backspace",
    "break",
    "capslock",
    "clear",
    "delete",
    "down",
    "end",
    "enter",
    "esc",
    "home",
    "insert",
    "left",
    "menu",
    "numlock",
    "pagedown",
    "pageup",
    "pause",
    "printscreen",
    "right",
    "scrolllock",
    "space",
    "tab",
    "up",
    "volumeup",
    "volumedown",
    "volumemute",
}
VALID_KEYS = frozenset(
    set(string.ascii_lowercase)
    | set(string.digits)
    | _NAMED_KEYS
    | {f"f{number}" for number in range(1, 25)}
    | {"ctrl", "shift", "alt", "win"}
)
KEY_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "escape": "esc",
    "return": "enter",
    "windows": "win",
    "command": "win",
    "option": "alt",
    "pgup": "pageup",
    "pgdn": "pagedown",
}


class KeyboardBackend(Protocol):
    """Mockable boundary implemented by :class:`PyAutoGUIAdapter`."""

    def write(self, text: str, *, interval: float) -> None: ...

    def press(self, key: str, *, presses: int, interval: float) -> None: ...

    def hotkey(self, *keys: str) -> None: ...


class KeyboardController:
    """Validate bounded text, keys, and chords before desktop input."""

    def __init__(self, backend: KeyboardBackend | None = None) -> None:
        self._backend = backend or PyAutoGUIAdapter()

    def type_text(self, text: str, *, interval: float = 0.0) -> None:
        if not isinstance(text, str):
            raise ComputerValidationError("text must be a string")
        if not text or len(text) > MAX_TEXT_LENGTH:
            raise ComputerValidationError(
                f"text must contain between 1 and {MAX_TEXT_LENGTH} characters"
            )
        if any(not character.isprintable() for character in text):
            raise ComputerValidationError("text contains unsupported non-printable characters")
        self._backend.write(text, interval=_validate_interval(interval))

    def press(self, key: str, *, presses: int = 1, interval: float = 0.0) -> None:
        normalized = normalize_key(key)
        if isinstance(presses, bool) or not isinstance(presses, int):
            raise ComputerValidationError("presses must be an integer")
        if not 1 <= presses <= MAX_PRESSES:
            raise ComputerValidationError(f"presses must be between 1 and {MAX_PRESSES}")
        self._backend.press(
            normalized,
            presses=presses,
            interval=_validate_interval(interval),
        )

    def hotkey(self, *keys: str) -> None:
        if not 2 <= len(keys) <= MAX_HOTKEY_KEYS:
            raise ComputerValidationError(
                f"hotkeys must contain between 2 and {MAX_HOTKEY_KEYS} keys"
            )
        normalized = tuple(normalize_key(key) for key in keys)
        if len(set(normalized)) != len(normalized):
            raise ComputerValidationError("a hotkey cannot contain duplicate keys")
        if frozenset(normalized) in FORBIDDEN_HOTKEYS:
            raise ComputerValidationError(
                "shortcuts that open a command launcher or terminal are not allowed"
            )
        self._backend.hotkey(*normalized)


def normalize_key(value: str) -> str:
    if not isinstance(value, str):
        raise ComputerValidationError("key must be text")
    key = value.casefold().strip()
    key = KEY_ALIASES.get(key, key)
    if key not in VALID_KEYS:
        raise ComputerValidationError(f"unsupported key: {value!r}")
    return key


def _validate_interval(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ComputerValidationError("interval must be a number")
    interval = float(value)
    if not math.isfinite(interval) or not 0 <= interval <= MAX_KEY_INTERVAL:
        raise ComputerValidationError(
            f"interval must be between 0 and {MAX_KEY_INTERVAL:g} seconds"
        )
    return interval
