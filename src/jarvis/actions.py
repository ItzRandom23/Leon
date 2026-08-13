"""Composition of concrete Phase 1–6 capabilities into registered actions."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jarvis.computer import (
    ApplicationController,
    KeyboardController,
    MouseController,
    ScreenController,
    SystemInfoProvider,
    WindowsController,
)
from jarvis.computer.errors import ComputerError
from jarvis.core.actions import ActionParameter, ActionRegistry, ActionResult
from jarvis.core.events import EventBus, EventName
from jarvis.memory import MemoryManager, MemoryStoreError
from jarvis.skills.base import RiskLevel
from jarvis.vision import VisionAnalyzer
from jarvis.vision.providers import VisionProviderError


@dataclass(slots=True)
class ActionServices:
    """Injected adapters used to build the default action registry."""

    applications: ApplicationController
    system: SystemInfoProvider
    mouse: MouseController
    keyboard: KeyboardController
    screen: ScreenController
    windows: WindowsController
    memory: MemoryManager | None = None
    vision: VisionAnalyzer | None = None
    events: EventBus | None = None


def build_action_registry(services: ActionServices) -> ActionRegistry:
    """Register all available concrete capabilities with strict schemas."""

    registry = ActionRegistry()
    _register_application_actions(registry, services)
    _register_system_actions(registry, services)
    _register_input_actions(registry, services)
    _register_screen_and_window_actions(registry, services)
    if services.memory is not None:
        _register_memory_actions(registry, services)
    if services.vision is not None:
        _register_vision_actions(registry, services)
    return registry


def _register_application_actions(registry: ActionRegistry, services: ActionServices) -> None:
    application_parameter = ActionParameter(
        "application",
        str,
        "Approved application alias such as notepad, calculator, or vs code.",
        min_length=1,
        max_length=100,
    )

    @registry.action(
        name="open_application",
        description="Open an installed application from the trusted allowlist.",
        parameters=(application_parameter,),
        risk_level=RiskLevel.ACTION,
    )
    async def open_application(application: str) -> ActionResult:
        try:
            opened = await asyncio.to_thread(services.applications.open, application)
            return ActionResult.succeeded(
                "open_application",
                message=f"Started {opened.display_name}.",
                data={"application": opened.name, "display_name": opened.display_name},
            )
        except ComputerError as error:
            return _controlled_failure("open_application", error)

    @registry.action(
        name="close_application",
        description="Close running processes for an application from the trusted allowlist.",
        parameters=(application_parameter,),
        risk_level=RiskLevel.DESTRUCTIVE,
    )
    async def close_application(application: str) -> ActionResult:
        try:
            count = await asyncio.to_thread(services.applications.close, application)
            return ActionResult.succeeded(
                "close_application",
                message=(
                    f"Closed {count} approved process{'es' if count != 1 else ''}."
                    if count
                    else "That approved application isn't currently running."
                ),
                data={"closed_processes": count},
            )
        except ComputerError as error:
            return _controlled_failure("close_application", error)

    @registry.action(
        name="find_running_application",
        description="Find running processes for one approved application.",
        parameters=(application_parameter,),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def find_running_application(application: str) -> ActionResult:
        try:
            matches = await asyncio.to_thread(services.applications.find, application)
            names = [f"{item.application.display_name} (PID {item.pid})" for item in matches]
            return ActionResult.succeeded(
                "find_running_application",
                message="Running: " + ", ".join(names) if names else "It isn't currently running.",
                data={"processes": [_running_application_data(item) for item in matches]},
            )
        except ComputerError as error:
            return _controlled_failure("find_running_application", error)

    @registry.action(
        name="list_running_applications",
        description="List running applications that belong to the trusted catalog.",
        risk_level=RiskLevel.SENSITIVE,
    )
    async def list_running_applications() -> ActionResult:
        try:
            matches = await asyncio.to_thread(services.applications.list_running)
            names = [f"{item.application.display_name} (PID {item.pid})" for item in matches]
            return ActionResult.succeeded(
                "list_running_applications",
                message="Running approved applications: " + ", ".join(names)
                if names
                else "No approved applications are currently running.",
                data={"processes": [_running_application_data(item) for item in matches]},
            )
        except ComputerError as error:
            return _controlled_failure("list_running_applications", error)


def _register_system_actions(registry: ActionRegistry, services: ActionServices) -> None:
    @registry.action(
        name="get_cpu_usage",
        description="Read current CPU utilization and processor counts.",
        risk_level=RiskLevel.READ,
    )
    async def get_cpu_usage() -> ActionResult:
        cpu = await asyncio.to_thread(services.system.cpu)
        return ActionResult.succeeded(
            "get_cpu_usage",
            message=f"CPU usage is currently {cpu.percent:.1f}%.",
            data=asdict(cpu),
        )

    @registry.action(
        name="get_memory_usage",
        description="Read current RAM utilization.",
        risk_level=RiskLevel.READ,
    )
    async def get_memory_usage() -> ActionResult:
        memory = await asyncio.to_thread(services.system.memory)
        return ActionResult.succeeded(
            "get_memory_usage",
            message=f"Memory usage is currently {memory.percent:.1f}%.",
            data=asdict(memory),
        )

    @registry.action(
        name="get_storage_usage",
        description="Read mounted storage utilization.",
        risk_level=RiskLevel.SENSITIVE,
    )
    async def get_storage_usage() -> ActionResult:
        disks = await asyncio.to_thread(services.system.disks)
        message = (
            "Storage: " + ", ".join(f"{disk.mountpoint} {disk.percent:.1f}% used" for disk in disks)
            if disks
            else "No accessible storage volumes were found."
        )
        return ActionResult.succeeded(
            "get_storage_usage", message=message, data=[asdict(item) for item in disks]
        )

    @registry.action(
        name="get_battery_status",
        description="Read battery level and charging status when available.",
        risk_level=RiskLevel.READ,
    )
    async def get_battery_status() -> ActionResult:
        battery = await asyncio.to_thread(services.system.battery)
        if battery is None:
            return ActionResult.succeeded(
                "get_battery_status",
                message="No battery information is available on this computer.",
                data=None,
            )
        state = "plugged in" if battery.plugged_in else "on battery"
        return ActionResult.succeeded(
            "get_battery_status",
            message=f"Battery is at {battery.percent:.1f}% and {state}.",
            data=asdict(battery),
        )

    @registry.action(
        name="get_uptime",
        description="Read how long the operating system has been running.",
        risk_level=RiskLevel.READ,
    )
    async def get_uptime() -> ActionResult:
        seconds = await asyncio.to_thread(services.system.uptime)
        return ActionResult.succeeded(
            "get_uptime",
            message=f"System uptime is {_format_duration(seconds)}.",
            data={"seconds": seconds},
        )

    @registry.action(
        name="get_operating_system",
        description="Read operating-system version and machine architecture.",
        risk_level=RiskLevel.READ,
    )
    async def get_operating_system() -> ActionResult:
        information = await asyncio.to_thread(services.system.operating_system)
        return ActionResult.succeeded(
            "get_operating_system",
            message=f"This computer is running {information.system} {information.release}.",
            data=asdict(information),
        )

    @registry.action(
        name="get_top_processes",
        description="List the processes currently using the most resident memory.",
        parameters=(
            ActionParameter(
                "limit",
                int,
                "Maximum process count.",
                required=False,
                default=5,
                minimum=1,
                maximum=20,
            ),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def get_top_processes(limit: int = 5) -> ActionResult:
        processes = await asyncio.to_thread(services.system.top_processes, limit=limit)
        message = (
            "Top memory users: "
            + ", ".join(f"{item.name} ({_format_bytes(item.memory_bytes)})" for item in processes)
            if processes
            else "No process information is available."
        )
        return ActionResult.succeeded(
            "get_top_processes", message=message, data=[asdict(item) for item in processes]
        )

    @registry.action(
        name="get_network_information",
        description="Read local network-interface status and IP addresses.",
        risk_level=RiskLevel.SENSITIVE,
    )
    async def get_network_information() -> ActionResult:
        interfaces = await asyncio.to_thread(services.system.network_interfaces)
        message = (
            "Network interfaces: " + ", ".join(item.name for item in interfaces if item.is_up)
            if interfaces
            else "No network-interface information is available."
        )
        return ActionResult.succeeded(
            "get_network_information", message=message, data=[asdict(item) for item in interfaces]
        )

    @registry.action(
        name="get_system_information",
        description="Read a complete basic system-information snapshot.",
        risk_level=RiskLevel.SENSITIVE,
    )
    async def get_system_information() -> ActionResult:
        information = await asyncio.to_thread(services.system.collect)
        return ActionResult.succeeded(
            "get_system_information",
            message=(
                f"{information.operating_system.system} {information.operating_system.release}; "
                f"CPU {information.cpu.percent:.1f}%; memory {information.memory.percent:.1f}%; "
                f"uptime {_format_duration(information.uptime_seconds)}."
            ),
            data=asdict(information),
        )


def _register_input_actions(registry: ActionRegistry, services: ActionServices) -> None:
    coordinate_parameters = (
        ActionParameter("x", int, "Horizontal pixel coordinate.", minimum=0, maximum=1_000_000),
        ActionParameter("y", int, "Vertical pixel coordinate.", minimum=0, maximum=1_000_000),
    )

    @registry.action(
        name="move_mouse",
        description="Move the pointer to exact validated screen coordinates.",
        parameters=coordinate_parameters
        + (
            ActionParameter(
                "duration",
                float,
                "Movement duration in seconds.",
                required=False,
                default=0.2,
                minimum=0,
                maximum=30,
            ),
        ),
        risk_level=RiskLevel.ACTION,
    )
    async def move_mouse(x: int, y: int, duration: float = 0.2) -> ActionResult:
        try:
            point = await asyncio.to_thread(services.mouse.move, x, y, duration=duration)
            return ActionResult.succeeded(
                "move_mouse",
                message=f"Moved the pointer to {point.x}, {point.y}.",
                data=asdict(point),
            )
        except ComputerError as error:
            return _controlled_failure("move_mouse", error)

    def register_click(name: str, description: str, method_name: str) -> None:
        @registry.action(
            name=name,
            description=description,
            parameters=(
                ActionParameter("x", int, required=False, minimum=0, maximum=1_000_000),
                ActionParameter("y", int, required=False, minimum=0, maximum=1_000_000),
            ),
            risk_level=RiskLevel.ACTION,
        )
        async def click(x: int | None = None, y: int | None = None) -> ActionResult:
            try:
                method = getattr(services.mouse, method_name)
                await asyncio.to_thread(method, x, y)
                return ActionResult.succeeded(name, message="Done.")
            except ComputerError as error:
                return _controlled_failure(name, error)

    register_click(
        "click_mouse", "Click the left mouse button, optionally at coordinates.", "click"
    )
    register_click("double_click_mouse", "Double-click, optionally at coordinates.", "double_click")
    register_click("right_click_mouse", "Right-click, optionally at coordinates.", "right_click")

    @registry.action(
        name="scroll_mouse",
        description="Scroll the active interface by a bounded signed amount.",
        parameters=(ActionParameter("clicks", int, minimum=-10_000, maximum=10_000),),
        risk_level=RiskLevel.ACTION,
    )
    async def scroll_mouse(clicks: int) -> ActionResult:
        try:
            await asyncio.to_thread(services.mouse.scroll, clicks)
            return ActionResult.succeeded("scroll_mouse", message="Scrolled.")
        except ComputerError as error:
            return _controlled_failure("scroll_mouse", error)

    @registry.action(
        name="type_text",
        description="Type bounded text into the currently focused application.",
        parameters=(ActionParameter("text", str, min_length=1, max_length=500),),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def type_text(text: str) -> ActionResult:
        try:
            await asyncio.to_thread(services.keyboard.type_text, text)
            return ActionResult.succeeded("type_text", message=f"Typed {len(text)} characters.")
        except ComputerError as error:
            return _controlled_failure("type_text", error)

    @registry.action(
        name="press_key",
        description="Press one validated keyboard key.",
        parameters=(
            ActionParameter("key", str, min_length=1, max_length=32),
            ActionParameter("presses", int, required=False, default=1, minimum=1, maximum=100),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def press_key(key: str, presses: int = 1) -> ActionResult:
        try:
            await asyncio.to_thread(services.keyboard.press, key, presses=presses)
            return ActionResult.succeeded("press_key", message=f"Pressed {key}.")
        except ComputerError as error:
            return _controlled_failure("press_key", error)

    @registry.action(
        name="press_hotkey",
        description="Press a validated keyboard shortcut of two to six keys.",
        parameters=(
            ActionParameter(
                "keys",
                list,
                min_length=2,
                max_length=6,
                items={"type": "string"},
            ),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def press_hotkey(keys: list[str]) -> ActionResult:
        try:
            await asyncio.to_thread(services.keyboard.hotkey, *keys)
            return ActionResult.succeeded("press_hotkey", message=f"Pressed {'+'.join(keys)}.")
        except ComputerError as error:
            return _controlled_failure("press_hotkey", error)


def _register_screen_and_window_actions(registry: ActionRegistry, services: ActionServices) -> None:
    @registry.action(
        name="take_screenshot",
        description="Capture and persist a full-screen screenshot.",
        risk_level=RiskLevel.READ,
    )
    async def take_screenshot() -> ActionResult:
        try:
            screenshot = await asyncio.to_thread(services.screen.capture_screen)
            await _emit_screenshot(services, screenshot.path)
            return ActionResult.succeeded(
                "take_screenshot",
                message=f"Screenshot saved to {screenshot.path}.",
                data={"path": str(screenshot.path)},
            )
        except ComputerError as error:
            return _controlled_failure("take_screenshot", error)

    @registry.action(
        name="capture_active_window",
        description="Capture and persist a screenshot of the active window.",
        risk_level=RiskLevel.READ,
    )
    async def capture_active_window() -> ActionResult:
        try:
            screenshot = await asyncio.to_thread(services.screen.capture_active_window)
            await _emit_screenshot(services, screenshot.path)
            return ActionResult.succeeded(
                "capture_active_window",
                message=f"Active-window screenshot saved to {screenshot.path}.",
                data={"path": str(screenshot.path)},
            )
        except ComputerError as error:
            return _controlled_failure("capture_active_window", error)

    @registry.action(
        name="get_active_window",
        description="Read the title and bounds of the active window.",
        risk_level=RiskLevel.SENSITIVE,
    )
    async def get_active_window() -> ActionResult:
        try:
            window = await asyncio.to_thread(services.windows.active_window)
            return ActionResult.succeeded(
                "get_active_window",
                message=f"The active window is {window.title}.",
                data=asdict(window),
            )
        except ComputerError as error:
            return _controlled_failure("get_active_window", error)

    @registry.action(
        name="list_visible_windows",
        description="List titles and bounds for visible Windows desktop windows.",
        risk_level=RiskLevel.SENSITIVE,
    )
    async def list_visible_windows() -> ActionResult:
        try:
            windows = await asyncio.to_thread(services.windows.list_visible_windows)
            return ActionResult.succeeded(
                "list_visible_windows",
                message="Visible windows: " + ", ".join(item.title for item in windows)
                if windows
                else "No titled visible windows were found.",
                data=[asdict(item) for item in windows],
            )
        except ComputerError as error:
            return _controlled_failure("list_visible_windows", error)

    @registry.action(
        name="focus_window",
        description="Focus one visible window by an exact full title match.",
        parameters=(ActionParameter("title", str, min_length=1, max_length=512),),
        risk_level=RiskLevel.ACTION,
    )
    async def focus_window(title: str) -> ActionResult:
        try:
            window = await asyncio.to_thread(services.windows.focus_window, title)
            return ActionResult.succeeded(
                "focus_window", message=f"Focused {window.title}.", data=asdict(window)
            )
        except ComputerError as error:
            return _controlled_failure("focus_window", error)


def _register_memory_actions(registry: ActionRegistry, services: ActionServices) -> None:
    memory = services.memory
    assert memory is not None
    category = ActionParameter(
        "category",
        str,
        enum=("preferences", "facts", "projects", "aliases"),
    )
    key = ActionParameter("key", str, min_length=1, max_length=500)

    @registry.action(
        name="remember",
        description="Explicitly store one user-requested memory in SQLite.",
        parameters=(category, key, ActionParameter("value", str, max_length=50_000)),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def remember(category: str, key: str, value: str) -> ActionResult:
        try:
            record = await asyncio.to_thread(memory.remember, category, key, value)
            if services.events:
                await services.events.publish(
                    EventName.MEMORY_CREATED,
                    {"memory_id": record.id, "category": record.category.value, "key": record.key},
                )
            return ActionResult.succeeded(
                "remember",
                message=f"I'll remember {record.key}.",
                data=_memory_data(record),
            )
        except MemoryStoreError as error:
            return _controlled_failure("remember", error)

    @registry.action(
        name="recall_memory",
        description="Recall one explicitly stored memory by category and key.",
        parameters=(category, key),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def recall_memory(category: str, key: str) -> ActionResult:
        try:
            record = await asyncio.to_thread(memory.recall, category, key)
            if record is None:
                return ActionResult.succeeded(
                    "recall_memory", message=f"I don't have a stored memory for {key}."
                )
            return ActionResult.succeeded(
                "recall_memory",
                message=f"{record.key} is {record.value}.",
                data=_memory_data(record),
            )
        except MemoryStoreError as error:
            return _controlled_failure("recall_memory", error)

    @registry.action(
        name="list_memories",
        description="List explicitly stored memories, optionally in one category.",
        parameters=(
            ActionParameter(
                "category",
                str,
                required=False,
                enum=("preferences", "facts", "projects", "aliases"),
            ),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def list_memories(category: str | None = None) -> ActionResult:
        try:
            records = await asyncio.to_thread(memory.list, category)
            message = (
                "Memories:\n"
                + "\n".join(
                    f"- [{record.category.value}] {record.key}: {record.value}"
                    for record in records
                )
                if records
                else "No memories are stored."
            )
            return ActionResult.succeeded(
                "list_memories", message=message, data=[_memory_data(item) for item in records]
            )
        except MemoryStoreError as error:
            return _controlled_failure("list_memories", error)

    @registry.action(
        name="search_memories",
        description="Search explicitly stored memory keys and values.",
        parameters=(
            ActionParameter("query", str, min_length=1, max_length=500),
            ActionParameter(
                "category",
                str,
                required=False,
                enum=("preferences", "facts", "projects", "aliases"),
            ),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def search_memories(query: str, category: str | None = None) -> ActionResult:
        try:
            records = await asyncio.to_thread(memory.search, query, category)
            return ActionResult.succeeded(
                "search_memories",
                message=(
                    "Matching memories:\n"
                    + "\n".join(
                        f"- [{record.category.value}] {record.key}: {record.value}"
                        for record in records
                    )
                    if records
                    else "No memories matched that search."
                ),
                data=[_memory_data(item) for item in records],
            )
        except MemoryStoreError as error:
            return _controlled_failure("search_memories", error)

    @registry.action(
        name="forget_memory",
        description="Delete one explicitly stored memory.",
        parameters=(category, key),
        risk_level=RiskLevel.DESTRUCTIVE,
    )
    async def forget_memory(category: str, key: str) -> ActionResult:
        try:
            removed = await asyncio.to_thread(memory.forget, category, key)
            if removed and services.events:
                await services.events.publish(
                    EventName.MEMORY_DELETED,
                    {"category": category, "key": key},
                )
            return ActionResult.succeeded(
                "forget_memory",
                message=f"Forgot {key}." if removed else f"No stored memory matched {key}.",
                data={"removed": removed},
            )
        except MemoryStoreError as error:
            return _controlled_failure("forget_memory", error)

    @registry.action(
        name="clear_memories",
        description="Delete all stored memories or all memories in one category.",
        parameters=(
            ActionParameter(
                "category",
                str,
                required=False,
                enum=("preferences", "facts", "projects", "aliases"),
            ),
        ),
        risk_level=RiskLevel.DESTRUCTIVE,
    )
    async def clear_memories(category: str | None = None) -> ActionResult:
        try:
            count = await asyncio.to_thread(memory.clear, category)
            if count and services.events:
                await services.events.publish(
                    EventName.MEMORY_DELETED,
                    {"category": category, "count": count},
                )
            return ActionResult.succeeded(
                "clear_memories",
                message=f"Cleared {count} stored memories.",
                data={"removed": count},
            )
        except MemoryStoreError as error:
            return _controlled_failure("clear_memories", error)


def _register_vision_actions(registry: ActionRegistry, services: ActionServices) -> None:
    vision = services.vision
    assert vision is not None

    @registry.action(
        name="analyze_screen",
        description="Capture a temporary screenshot and semantically describe the visible screen.",
        parameters=(
            ActionParameter(
                "prompt", str, required=False, default="Describe the screen.", max_length=2_000
            ),
        ),
        risk_level=RiskLevel.SENSITIVE,
    )
    async def analyze_screen(prompt: str = "Describe the screen.") -> ActionResult:
        try:
            analysis = await vision.analyze_screen(prompt)
            return ActionResult.succeeded(
                "analyze_screen",
                message=analysis.description,
                data={
                    "description": analysis.description,
                    "visible_text": list(analysis.visible_text),
                    "targets": [asdict(target) for target in analysis.targets],
                    "model": analysis.model,
                },
            )
        except (VisionProviderError, ComputerError) as error:
            return _controlled_failure("analyze_screen", error)


async def _emit_screenshot(services: ActionServices, path: Path) -> None:
    if services.events:
        await services.events.publish(EventName.SCREENSHOT_CAPTURED, {"path": str(path)})


def _memory_data(record: Any) -> dict[str, Any]:
    return {
        "id": record.id,
        "category": record.category.value,
        "key": record.key,
        "value": record.value,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _running_application_data(item: Any) -> dict[str, Any]:
    """Expose only the process identity needed by the reasoning layer."""

    return {
        "application": item.application.name,
        "display_name": item.application.display_name,
        "pid": item.pid,
        "process_name": item.process_name,
    }


def _controlled_failure(action: str, error: Exception) -> ActionResult:
    return ActionResult.failed(
        action,
        "The capability could not complete the request.",
        message=f"I couldn't {action.replace('_', ' ')}.",
        error_code="capability_error",
    )


def _format_duration(seconds: int) -> str:
    days, remainder = divmod(max(0, seconds), 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, _ = divmod(remainder, 60)
    parts = [f"{days}d"] if days else []
    if hours or days:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _format_bytes(value: int) -> str:
    amount = float(max(0, value))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"
