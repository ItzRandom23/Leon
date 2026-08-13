"""Windows-only visible-window inspection and exact-title focusing."""

from __future__ import annotations

import ctypes
import platform
from collections.abc import Iterable
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Protocol, cast

from jarvis.computer.errors import (
    ComputerValidationError,
    UnsupportedPlatformError,
    WindowFocusError,
    WindowNotFoundError,
)


@dataclass(frozen=True, slots=True)
class WindowBounds:
    left: int
    top: int
    right: int
    bottom: int

    def __post_init__(self) -> None:
        if self.right <= self.left or self.bottom <= self.top:
            raise ComputerValidationError("window bounds must have positive width and height")

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def as_bbox(self) -> tuple[int, int, int, int]:
        return self.left, self.top, self.right, self.bottom


@dataclass(frozen=True, slots=True)
class WindowInformation:
    handle: int
    title: str
    bounds: WindowBounds | None


class WindowsApi(Protocol):
    """Mockable high-level boundary around the handful of user32 calls used."""

    def foreground_window(self) -> int: ...

    def window_text(self, handle: int) -> str: ...

    def window_bounds(self, handle: int) -> WindowBounds: ...

    def visible_windows(self) -> Iterable[int]: ...

    def focus_window(self, handle: int) -> bool: ...


class CtypesWindowsApi:
    """Real user32 adapter; instantiate only after confirming Windows support."""

    def __init__(self) -> None:
        try:
            self._user32 = cast(Any, ctypes).windll.user32
        except AttributeError as exc:
            raise UnsupportedPlatformError("native window control requires Windows") from exc

    def foreground_window(self) -> int:
        return int(self._user32.GetForegroundWindow())

    def window_text(self, handle: int) -> str:
        length = int(self._user32.GetWindowTextLengthW(handle))
        if length <= 0:
            return ""
        buffer = ctypes.create_unicode_buffer(length + 1)
        self._user32.GetWindowTextW(handle, buffer, len(buffer))
        return buffer.value

    def window_bounds(self, handle: int) -> WindowBounds:
        rectangle = wintypes.RECT()
        if not self._user32.GetWindowRect(handle, ctypes.byref(rectangle)):
            raise OSError("GetWindowRect failed")
        return WindowBounds(
            int(rectangle.left),
            int(rectangle.top),
            int(rectangle.right),
            int(rectangle.bottom),
        )

    def visible_windows(self) -> tuple[int, ...]:
        handles: list[int] = []
        callback_type = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)(
            wintypes.BOOL,
            wintypes.HWND,
            wintypes.LPARAM,
        )

        @callback_type
        def collect(handle: int, _: int) -> bool:
            if self._user32.IsWindowVisible(handle):
                handles.append(int(handle))
            return True

        if not self._user32.EnumWindows(collect, 0):
            raise OSError("EnumWindows failed")
        return tuple(handles)

    def focus_window(self, handle: int) -> bool:
        self._user32.ShowWindow(handle, 9)  # SW_RESTORE
        return bool(self._user32.SetForegroundWindow(handle))


class WindowsController:
    """Expose native window operations with clean unsupported-platform errors."""

    def __init__(
        self,
        api: WindowsApi | None = None,
        *,
        platform_name: str | None = None,
    ) -> None:
        self._api = api
        self._platform_name = platform_name or platform.system()

    def active_window(self) -> WindowInformation:
        api = self._supported_api()
        handle = api.foreground_window()
        if not handle:
            raise WindowNotFoundError("there is no active window")
        title = api.window_text(handle)
        if not title:
            raise WindowNotFoundError("the active window has no visible title")
        return WindowInformation(handle, title, self._try_bounds(api, handle))

    def active_title(self) -> str:
        return self.active_window().title

    def list_visible_windows(self) -> tuple[WindowInformation, ...]:
        api = self._supported_api()
        windows: list[WindowInformation] = []
        for handle in api.visible_windows():
            title = api.window_text(handle)
            if title:
                windows.append(WindowInformation(handle, title, self._try_bounds(api, handle)))
        return tuple(windows)

    def focus_exact_title(self, requested_title: str) -> WindowInformation:
        """Focus a full case-insensitive title match; substrings never match."""

        if not isinstance(requested_title, str):
            raise ComputerValidationError("window title must be text")
        title = requested_title.strip()
        if not title or len(title) > 512:
            raise ComputerValidationError("window title must contain 1 to 512 characters")
        matches = tuple(
            window
            for window in self.list_visible_windows()
            if window.title.casefold() == title.casefold()
        )
        if not matches:
            raise WindowNotFoundError(f"no visible window has the exact title {title!r}")
        if len(matches) > 1:
            raise WindowFocusError(f"more than one visible window has the exact title {title!r}")
        match = matches[0]
        if not self._supported_api().focus_window(match.handle):
            raise WindowFocusError(f"Windows refused to focus {match.title!r}")
        return match

    def focus_window(self, requested_title: str) -> WindowInformation:
        """Compatibility alias emphasizing that matching remains exact-title."""

        return self.focus_exact_title(requested_title)

    def _supported_api(self) -> WindowsApi:
        if self._platform_name != "Windows":
            raise UnsupportedPlatformError(
                f"window inspection is not supported on {self._platform_name}"
            )
        if self._api is None:
            self._api = CtypesWindowsApi()
        return self._api

    @staticmethod
    def _try_bounds(api: WindowsApi, handle: int) -> WindowBounds | None:
        try:
            return api.window_bounds(handle)
        except (OSError, ComputerValidationError):
            return None
