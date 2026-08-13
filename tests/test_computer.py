"""Portable tests for computer adapters; every OS/desktop side effect is mocked."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from jarvis.computer._pyautogui import PyAutoGUIAdapter
from jarvis.computer.applications import (
    ApplicationController,
    ApplicationDefinition,
    ApplicationResolver,
    ResolvedApplication,
)
from jarvis.computer.errors import (
    ApplicationLaunchError,
    ApplicationNotFoundError,
    ComputerValidationError,
    ScreenshotError,
    UnsupportedPlatformError,
    WindowFocusError,
    WindowNotFoundError,
)
from jarvis.computer.keyboard import KeyboardController, normalize_key
from jarvis.computer.mouse import MouseController, Point
from jarvis.computer.screen import ScreenController, ScreenshotStore
from jarvis.computer.system import SystemInfoProvider
from jarvis.computer.windows import WindowBounds, WindowInformation, WindowsController

TEST_APP = ApplicationDefinition(
    "editor",
    "Trusted Editor",
    ("editor", "trusted editor"),
    ("editor.exe",),
)


class StaticPaths:
    def __init__(self, path: Path) -> None:
        self.path = path

    def candidates(self, application: ApplicationDefinition) -> tuple[Path, ...]:
        assert application is TEST_APP
        return (self.path,)


def make_resolver(executable: Path) -> ApplicationResolver:
    return ApplicationResolver(
        platform_name="TestOS",
        path_provider=StaticPaths(executable),
        definitions=(TEST_APP,),
    )


@pytest.mark.parametrize("alias", ["editor", "EDITOR", " trusted   editor "])
def test_application_resolver_accepts_only_exact_aliases(tmp_path: Path, alias: str) -> None:
    executable = tmp_path / "editor.exe"
    executable.touch()

    result = make_resolver(executable).resolve(alias)

    assert result.name == "editor"
    assert result.executable == executable.resolve()


@pytest.mark.parametrize(
    "value",
    ["editor --flag", "editor && calc", "editor; calc", "", "unknown"],
)
def test_application_resolver_rejects_non_alias_input(tmp_path: Path, value: str) -> None:
    executable = tmp_path / "editor.exe"
    executable.touch()

    with pytest.raises((ApplicationNotFoundError, ComputerValidationError)):
        make_resolver(executable).resolve(value)


def test_application_controller_opens_with_a_sanitized_process_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executable = tmp_path / "editor.exe"
    executable.touch()
    monkeypatch.setenv("JARVIS_AI_API_KEY", "jarvis-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("PATH", "safe-path")
    monkeypatch.setenv("JARVIS_TEST_SAFE", "unknown-variable")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("DATABASE_URL", "database-secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "agent-socket")
    process_factory = Mock()
    controller = ApplicationController(
        make_resolver(executable),
        process_factory=process_factory,
    )

    result = controller.open("editor")

    assert result.executable == executable.resolve()
    process_factory.assert_called_once()
    args, kwargs = process_factory.call_args
    assert args == ([str(executable.resolve())],)
    assert kwargs["shell"] is False
    assert kwargs["cwd"] == str(executable.resolve().parent)
    assert kwargs["env"]["PATH"] == "safe-path"
    for excluded in (
        "JARVIS_AI_API_KEY",
        "OPENAI_API_KEY",
        "JARVIS_TEST_SAFE",
        "GITHUB_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "DATABASE_URL",
        "SSH_AUTH_SOCK",
    ):
        assert excluded not in kwargs["env"]


def test_application_controller_refuses_to_launch_while_elevated_on_windows(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "editor.exe"
    executable.touch()
    resolver = ApplicationResolver(
        platform_name="Windows",
        path_provider=StaticPaths(executable),
        definitions=(TEST_APP,),
    )
    process_factory = Mock()
    controller = ApplicationController(
        resolver,
        process_factory=process_factory,
        elevation_checker=lambda: True,
    )

    with pytest.raises(ApplicationLaunchError, match="disabled while JARVIS is elevated"):
        controller.open("editor")

    process_factory.assert_not_called()


class FakeProcess:
    def __init__(self, pid: int, name: str, executable: Path) -> None:
        self.pid = pid
        self.info = {"pid": pid, "name": name, "exe": str(executable)}
        self.terminated = False
        self.wait_timeout: float | None = None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.wait_timeout = timeout
        return 0


class FakeProcessApi:
    def __init__(self, processes: list[FakeProcess]) -> None:
        self.processes = processes
        self.attrs: tuple[str, ...] | None = None

    def process_iter(self, attrs: tuple[str, ...]) -> list[FakeProcess]:
        self.attrs = attrs
        return self.processes


def test_application_find_and_close_match_trusted_absolute_path_only(tmp_path: Path) -> None:
    executable = tmp_path / "editor.exe"
    executable.touch()
    trusted = FakeProcess(2, "editor.exe", executable.resolve())
    impostor_path = tmp_path / "other" / "editor.exe"
    impostor = FakeProcess(1, "editor.exe", impostor_path)
    process_api = FakeProcessApi([impostor, trusted])
    controller = ApplicationController(
        make_resolver(executable),
        psutil_api=process_api,
    )

    running = controller.find("editor")
    closed = controller.close("editor", timeout=2)

    assert [item.pid for item in running] == [2]
    assert closed == 1
    assert trusted.terminated is True
    assert trusted.wait_timeout == 2.0
    assert impostor.terminated is False
    assert process_api.attrs == ("pid", "name", "exe")


def test_resolved_application_rejects_relative_executable() -> None:
    with pytest.raises(ComputerValidationError, match="absolute"):
        ResolvedApplication("bad", "Bad", ("bad",), Path("bad.exe"))


class FakeSystemPsutil:
    def cpu_percent(self, interval: float | None = None) -> float:
        assert interval == 0.1
        return 12.5

    def cpu_count(self, logical: bool = True) -> int:
        return 8 if logical else 4

    def virtual_memory(self) -> SimpleNamespace:
        return SimpleNamespace(total=1000, available=400, used=600, percent=60.0)

    def disk_partitions(self, all: bool = False) -> list[SimpleNamespace]:
        assert all is False
        return [SimpleNamespace(device="disk", mountpoint="root", fstype="safe-fs")]

    def disk_usage(self, path: str) -> SimpleNamespace:
        assert path == "root"
        return SimpleNamespace(total=2000, used=500, free=1500, percent=25.0)

    def sensors_battery(self) -> SimpleNamespace:
        return SimpleNamespace(percent=75.0, power_plugged=False, secsleft=3600)

    def boot_time(self) -> float:
        return 100.0

    def process_iter(self, attrs: tuple[str, ...]) -> list[SimpleNamespace]:
        assert attrs == ("pid", "name", "memory_info", "memory_percent")
        return [
            SimpleNamespace(
                info={
                    "pid": 1,
                    "name": "small",
                    "memory_info": SimpleNamespace(rss=10),
                    "memory_percent": 1.0,
                }
            ),
            SimpleNamespace(
                info={
                    "pid": 2,
                    "name": "large",
                    "memory_info": SimpleNamespace(rss=20),
                    "memory_percent": 2.0,
                }
            ),
        ]

    def net_if_addrs(self) -> dict[str, list[SimpleNamespace]]:
        return {
            "wifi": [
                SimpleNamespace(family=socket.AF_INET, address="192.0.2.1"),
                SimpleNamespace(family=socket.AF_INET6, address="fe80::1%3"),
                SimpleNamespace(family="link", address="00:11:22:33:44:55"),
            ]
        }

    def net_if_stats(self) -> dict[str, SimpleNamespace]:
        return {"wifi": SimpleNamespace(isup=True, speed=100)}


class FakePlatform:
    @staticmethod
    def system() -> str:
        return "TestOS"

    @staticmethod
    def release() -> str:
        return "1"

    @staticmethod
    def version() -> str:
        return "1.2"

    @staticmethod
    def machine() -> str:
        return "test64"


def test_system_provider_collects_safe_snapshot() -> None:
    provider = SystemInfoProvider(
        psutil_api=FakeSystemPsutil(),
        clock=lambda: 190.0,
        platform_module=FakePlatform,
    )

    result = provider.collect(top_process_limit=1)

    assert result.cpu.percent == 12.5
    assert result.memory.used_bytes == 600
    assert result.disks[0].filesystem == "safe-fs"
    assert result.battery is not None and result.battery.seconds_remaining == 3600
    assert result.uptime_seconds == 90
    assert result.operating_system.system == "TestOS"
    assert [process.name for process in result.top_processes] == ["large"]
    assert result.network_interfaces[0].addresses == ("192.0.2.1", "fe80::1")


class FakeMouse:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def size(self) -> tuple[int, int]:
        return 1920, 1080

    def position(self) -> tuple[int, int]:
        return 10, 20

    def move_to(self, x: int, y: int, *, duration: float) -> None:
        self.calls.append(("move", x, y, duration))

    def click(self, *, x: int | None, y: int | None, button: str) -> None:
        self.calls.append(("click", x, y, button))

    def double_click(self, *, x: int | None, y: int | None, button: str) -> None:
        self.calls.append(("double", x, y, button))

    def scroll(self, clicks: int) -> None:
        self.calls.append(("scroll", clicks))


def test_mouse_controller_validates_then_delegates() -> None:
    backend = FakeMouse()
    mouse = MouseController(backend)

    assert mouse.position() == Point(10, 20)
    assert mouse.move(100, 200, duration=0.5) == Point(100, 200)
    mouse.right_click(300, 400)
    mouse.double_click(button="middle")
    mouse.scroll(-3)

    assert backend.calls == [
        ("move", 100, 200, 0.5),
        ("click", 300, 400, "right"),
        ("double", None, None, "middle"),
        ("scroll", -3),
    ]


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("move", (-1, 1)),
        ("move", (1920, 1)),
        ("move", (True, 1)),
        ("click", (1, None)),
        ("scroll", (0,)),
        ("scroll", (10_001,)),
    ],
)
def test_mouse_rejects_invalid_input_without_side_effect(
    method: str, args: tuple[object, ...]
) -> None:
    backend = FakeMouse()
    mouse = MouseController(backend)

    with pytest.raises(ComputerValidationError):
        getattr(mouse, method)(*args)

    assert backend.calls == []


class FakeKeyboard:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    def write(self, text: str, *, interval: float) -> None:
        self.calls.append(("write", text, interval))

    def press(self, key: str, *, presses: int, interval: float) -> None:
        self.calls.append(("press", key, presses, interval))

    def hotkey(self, *keys: str) -> None:
        self.calls.append(("hotkey", *keys))


def test_keyboard_controller_normalizes_and_delegates() -> None:
    backend = FakeKeyboard()
    keyboard = KeyboardController(backend)

    keyboard.type_text("hello world", interval=0.1)
    keyboard.press("Escape", presses=2)
    keyboard.hotkey("Control", "S")

    assert backend.calls == [
        ("write", "hello world", 0.1),
        ("press", "esc", 2, 0.0),
        ("hotkey", "ctrl", "s"),
    ]
    assert normalize_key(" pgdn ") == "pagedown"


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("type_text", ("bad\ntext",)),
        ("type_text", ("bad\x00text",)),
        ("type_text", ("",)),
        ("press", ("ctrl+s",)),
        ("hotkey", ("ctrl",)),
        ("hotkey", ("ctrl", "control")),
    ],
)
def test_keyboard_rejects_invalid_input_without_side_effect(
    method: str, args: tuple[object, ...]
) -> None:
    backend = FakeKeyboard()
    keyboard = KeyboardController(backend)

    with pytest.raises(ComputerValidationError):
        getattr(keyboard, method)(*args)

    assert backend.calls == []


def test_pyautogui_adapter_enables_fail_safe_and_maps_calls() -> None:
    module = Mock()
    module.size.return_value = (800, 600)
    adapter = PyAutoGUIAdapter(module)

    assert module.FAILSAFE is True
    assert adapter.size() == (800, 600)
    adapter.move_to(1, 2, duration=0.2)
    adapter.hotkey("ctrl", "s")

    module.moveTo.assert_called_once_with(1, 2, duration=0.2)
    module.hotkey.assert_called_once_with("ctrl", "s")


class FakeImage:
    def __init__(self) -> None:
        self.closed = False

    def save(self, file_path: str | Path, format: str) -> None:
        assert format == "PNG"
        Path(file_path).write_bytes(b"fake png")

    def close(self) -> None:
        self.closed = True


class FakeCaptureProvider:
    def __init__(self) -> None:
        self.images: list[FakeImage] = []
        self.bounds: list[WindowBounds | None] = []

    def capture(self, bbox: WindowBounds | None = None) -> FakeImage:
        image = FakeImage()
        self.images.append(image)
        self.bounds.append(bbox)
        return image


def test_temporary_screenshot_is_unique_and_always_deleted(tmp_path: Path) -> None:
    provider = FakeCaptureProvider()
    store = ScreenshotStore(
        tmp_path.resolve(),
        clock=lambda: 0,
        unique_id=lambda: "abc123",
    )

    with pytest.raises(RuntimeError, match="consumer failed"):
        with store.temporary(provider) as screenshot:
            assert screenshot.path.exists()
            assert screenshot.persistent is False
            raise RuntimeError("consumer failed")

    assert not screenshot.path.exists()
    assert provider.images[0].closed is True


class FakeWindows:
    def __init__(self, window: WindowInformation) -> None:
        self.window = window

    def active_window(self) -> WindowInformation:
        return self.window


def test_screen_controller_passes_active_window_bounds_and_removes_capture(
    tmp_path: Path,
) -> None:
    bounds = WindowBounds(10, 20, 110, 220)
    provider = FakeCaptureProvider()
    controller = ScreenController(
        ScreenshotStore(tmp_path.resolve()),
        provider=provider,
        windows=FakeWindows(WindowInformation(1, "Editor", bounds)),
    )

    with controller.temporary_active_window() as screenshot:
        assert screenshot.path.exists()
        assert screenshot.bounds == bounds

    assert not screenshot.path.exists()
    assert provider.bounds == [bounds]


def test_screen_controller_rejects_active_window_without_bounds(tmp_path: Path) -> None:
    controller = ScreenController(
        ScreenshotStore(tmp_path.resolve()),
        provider=FakeCaptureProvider(),
        windows=FakeWindows(WindowInformation(1, "Editor", None)),
    )

    with pytest.raises(ScreenshotError, match="bounds"):
        controller.capture_active_window()


@dataclass
class FakeWindowsApi:
    focused: int | None = None
    allow_focus: bool = True

    def foreground_window(self) -> int:
        return 2

    def window_text(self, handle: int) -> str:
        return {1: "Editor - file.txt", 2: "Calculator", 3: ""}[handle]

    def window_bounds(self, handle: int) -> WindowBounds:
        return WindowBounds(handle, handle, handle + 100, handle + 50)

    def visible_windows(self) -> tuple[int, ...]:
        return 1, 2, 3

    def focus_window(self, handle: int) -> bool:
        self.focused = handle
        return self.allow_focus


def test_windows_controller_reads_and_focuses_exact_title() -> None:
    api = FakeWindowsApi()
    windows = WindowsController(api, platform_name="Windows")

    assert windows.active_title() == "Calculator"
    assert [window.title for window in windows.list_visible_windows()] == [
        "Editor - file.txt",
        "Calculator",
    ]
    assert windows.focus_exact_title("calculator").handle == 2
    assert api.focused == 2

    with pytest.raises(WindowNotFoundError):
        windows.focus_exact_title("Editor")


def test_windows_controller_reports_unsupported_platform_without_touching_api() -> None:
    windows = WindowsController(platform_name="Linux")

    with pytest.raises(UnsupportedPlatformError, match="Linux"):
        windows.active_window()


def test_windows_controller_reports_focus_refusal() -> None:
    api = FakeWindowsApi(allow_focus=False)

    with pytest.raises(WindowFocusError, match="refused"):
        WindowsController(api, platform_name="Windows").focus_exact_title("Calculator")
