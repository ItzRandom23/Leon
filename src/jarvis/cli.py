"""Professional terminal interface for JARVIS."""

from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import urlsplit

from jarvis import __version__
from jarvis.bootstrap import JarvisApplication, create_application
from jarvis.core.actions import ActionRequest
from jarvis.core.config import ConfigError, JarvisConfig, load_config
from jarvis.core.events import EventName
from jarvis.core.permissions import Confirmer, PermissionRequest
from jarvis.memory import MemoryStoreError
from jarvis.plugins import (
    TRUSTED_PLUGIN_WARNING,
    PluginError,
    PluginManager,
    SQLitePluginStateRepository,
)
from jarvis.skills.base import RiskLevel
from jarvis.voice.speech_to_text import VoiceInputError
from jarvis.voice.text_to_speech import VoiceOutputError

InputFunction = Callable[[str], str]
OutputFunction = Callable[[str], None]

_ANSI_ESCAPE = re.compile(
    r"(?:\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_])"
)
_UNSAFE_TERMINAL = re.compile(
    r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u2069]"
)


def build_parser() -> argparse.ArgumentParser:
    """Return the stable public CLI parser."""

    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="A modular, permissioned personal AI assistant foundation.",
    )
    parser.add_argument("--voice", action="store_true", help="use microphone input")
    parser.add_argument("--debug", action="store_true", help="enable debug diagnostics")
    parser.add_argument("--config", type=Path, metavar="PATH", help="load a specific TOML file")
    subcommands = parser.add_subparsers(dest="command")
    subcommands.add_parser("doctor", help="check runtime configuration and optional dependencies")
    subcommands.add_parser("config", help="print effective configuration with secrets redacted")
    subcommands.add_parser("version", help="print the JARVIS version")
    subcommands.add_parser("gui", help="start the optional PySide6 desktop interface")

    tasks = subcommands.add_parser("tasks", help="manage persistent reminders")
    task_commands = tasks.add_subparsers(dest="tasks_command")
    task_list = task_commands.add_parser("list", help="list reminders")
    task_list.add_argument(
        "--status",
        choices=("all", "scheduled", "cancelled", "triggered"),
        default="all",
    )
    task_commands.add_parser("missed", help="list reminders missed while JARVIS was offline")
    task_add = task_commands.add_parser("add", help="create a one-time reminder")
    task_add.add_argument("--message", required=True)
    schedule = task_add.add_mutually_exclusive_group(required=True)
    schedule.add_argument("--at", metavar="ISO_DATETIME")
    schedule.add_argument("--in-minutes", type=int, metavar="MINUTES")
    task_add.add_argument("--timezone")
    task_cancel = task_commands.add_parser("cancel", help="cancel a reminder")
    task_cancel.add_argument("reminder_id", type=int)
    task_cancel.add_argument("--message", required=True, help="exact reminder text to confirm")
    task_delete = task_commands.add_parser("delete", help="permanently delete a reminder")
    task_delete.add_argument("reminder_id", type=int)
    task_delete.add_argument("--message", required=True, help="exact reminder text to confirm")

    memory = subcommands.add_parser("memory", help="inspect or delete explicit memories")
    memory_commands = memory.add_subparsers(dest="memory_command")
    memory_list = memory_commands.add_parser("list", help="list stored memories")
    memory_list.add_argument("--category", choices=("preferences", "facts", "projects", "aliases"))
    memory_search = memory_commands.add_parser("search", help="search stored memories")
    memory_search.add_argument("query")
    memory_search.add_argument(
        "--category", choices=("preferences", "facts", "projects", "aliases")
    )
    memory_delete = memory_commands.add_parser("delete", help="delete one memory")
    memory_delete.add_argument("category", choices=("preferences", "facts", "projects", "aliases"))
    memory_delete.add_argument("key")
    memory_clear = memory_commands.add_parser("clear", help="delete a category or all memories")
    memory_clear.add_argument("--category", choices=("preferences", "facts", "projects", "aliases"))

    plugins = subcommands.add_parser("plugins", help="manage trusted Python plugins")
    plugin_commands = plugins.add_subparsers(dest="plugins_command")
    plugin_commands.add_parser("list", help="discover plugin entry points without importing them")
    plugin_info = plugin_commands.add_parser("info", help="inspect one plugin after confirmation")
    plugin_info.add_argument("plugin_id")
    plugin_enable = plugin_commands.add_parser("enable", help="enable and load one trusted plugin")
    plugin_enable.add_argument("plugin_id")
    plugin_disable = plugin_commands.add_parser("disable", help="disable and unload one plugin")
    plugin_disable.add_argument("plugin_id")
    return parser


def run_cli(
    argv: Sequence[str] | None = None,
    *,
    input_fn: InputFunction | None = None,
    output_fn: OutputFunction | None = None,
) -> int:
    """Parse a command and return a process exit status."""

    args = build_parser().parse_args(argv)
    output = output_fn or print
    if args.command == "version":
        output(f"JARVIS {__version__}")
        return 0
    try:
        config = load_config(args.config)
    except (ConfigError, FileNotFoundError, OSError) as error:
        output(f"Configuration error: {_terminal_safe(str(error))}")
        return 2

    if args.command == "config":
        output(json.dumps(config.redacted_dict(), indent=2, sort_keys=True))
        return 0
    if args.command == "doctor":
        return run_doctor(config, output_fn=output)

    if args.command == "gui":
        return _run_gui_command(config, output_fn=output)

    confirmer = make_console_confirmer(input_fn=input_fn, output_fn=output)
    if args.command in {"tasks", "memory", "plugins"}:
        try:
            application = create_application(config, confirmer=confirmer)
            return asyncio.run(_run_management_command(application, args, output))
        except (ConfigError, MemoryStoreError, PluginError, OSError) as error:
            output(f"Command error: {_terminal_safe(str(error))}")
            return 2
    try:
        with create_application(config, confirmer=confirmer, voice_mode=args.voice) as application:
            return asyncio.run(
                run_session(
                    application,
                    voice_mode=args.voice or config.voice.enabled,
                    input_fn=input_fn,
                    output_fn=output,
                )
            )
    except (
        ConfigError,
        MemoryStoreError,
        VoiceInputError,
        VoiceOutputError,
        OSError,
    ) as error:
        output(f"Startup error: {_terminal_safe(str(error))}")
        return 2


def _run_gui_command(config: JarvisConfig, *, output_fn: OutputFunction) -> int:
    from jarvis.gui import GuiPermissionBroker, GuiUnavailableError, run_gui

    broker = GuiPermissionBroker()
    try:
        application = create_application(config, confirmer=broker.confirm)
        return run_gui(
            application,
            broker,
            theme=config.gui.theme,
            minimize_to_tray=config.gui.minimize_to_tray,
        )
    except (ConfigError, MemoryStoreError, GuiUnavailableError, OSError) as error:
        output_fn(f"GUI startup error: {_terminal_safe(str(error))}")
        return 2


async def _run_management_command(
    application: JarvisApplication,
    args: argparse.Namespace,
    output: OutputFunction,
) -> int:
    try:
        if args.command == "plugins":
            return await _run_plugin_command(application, args, output)
        request = _management_action(args, application)
        results = await application.runtime.execute_requests((request,))
        result = results[0]
        output(_terminal_safe(result.message))
        return 0 if result.success else 1
    finally:
        await application.aclose()


def _management_action(args: argparse.Namespace, application: JarvisApplication) -> ActionRequest:
    config = application.config
    if args.command == "tasks":
        command = args.tasks_command or "list"
        if command == "list":
            return ActionRequest("list_reminders", {"status": getattr(args, "status", "all")})
        if command == "missed":
            return ActionRequest("list_missed_reminders")
        if command == "add":
            timezone = args.timezone or config.scheduler.timezone
            if args.in_minutes is not None:
                return ActionRequest(
                    "create_relative_reminder",
                    {
                        "message": args.message,
                        "delay_minutes": args.in_minutes,
                        "timezone": timezone,
                    },
                )
            return ActionRequest(
                "create_reminder",
                {"message": args.message, "scheduled_at": args.at, "timezone": timezone},
            )
        if command == "cancel":
            return ActionRequest(
                "cancel_reminder",
                {
                    "reminder_id": args.reminder_id,
                    "expected_message": args.message,
                },
            )
        return ActionRequest(
            "delete_reminder",
            {
                "reminder_id": args.reminder_id,
                "expected_message": args.message,
            },
        )
    command = args.memory_command or "list"
    if command == "list":
        values = {} if getattr(args, "category", None) is None else {"category": args.category}
        return ActionRequest("list_memories", values)
    if command == "search":
        values = {"query": args.query}
        if args.category is not None:
            values["category"] = args.category
        return ActionRequest("search_memories", values)
    if command == "delete":
        return ActionRequest("forget_memory", {"category": args.category, "key": args.key})
    values = {} if args.category is None else {"category": args.category}
    return ActionRequest("clear_memories", values)


async def _run_plugin_command(
    application: JarvisApplication,
    args: argparse.Namespace,
    output: OutputFunction,
) -> int:
    manager = application.plugin_manager
    if manager is None:
        manager = PluginManager(
            action_registry=application.runtime.registry,
            event_bus=application.runtime.events,
            integration_registry=application.integration_registry,
            state_repository=SQLitePluginStateRepository(application.config.plugins.state_path),
        )
        application.plugin_manager = manager
    manager.discover()
    command = args.plugins_command or "list"
    if command == "list":
        records = manager.list()
        if not records:
            output("No JARVIS plugin entry points are installed.")
        for info in records:
            output(f"{_terminal_safe(info.plugin_id)}\t{info.status.value}")
        return 0
    if command in {"info", "enable"}:
        permission = await application.runtime.permissions.check(
            RiskLevel.SENSITIVE,
            action_name="plugin_inspect" if command == "info" else "plugin_enable",
            summary=(
                "Import a trusted local Python plugin to inspect it."
                if command == "info"
                else "Import, initialize, and enable a trusted local Python plugin."
            ),
            details={"plugin_id": args.plugin_id, "warning": TRUSTED_PLUGIN_WARNING},
        )
        if not permission.allowed:
            output(f"Plugin operation denied: {_terminal_safe(permission.reason)}")
            return 1
    if command == "info":
        info = manager.inspect(args.plugin_id)
    elif command == "enable":
        info = await manager.enable(args.plugin_id)
    else:
        info = await manager.disable(args.plugin_id)
    metadata = info.metadata
    output(f"Plugin: {_terminal_safe(metadata.name if metadata else info.plugin_id)}")
    output(f"Status: {info.status.value}")
    output(f"Enabled: {'yes' if info.enabled else 'no'}")
    if metadata is not None:
        output(f"Version: {_terminal_safe(metadata.version)}")
        output(f"Author: {_terminal_safe(metadata.author)}")
        output(f"Permissions: {_terminal_safe(', '.join(metadata.permissions) or 'none declared')}")
    if info.error:
        output(f"Error: {_terminal_safe(info.error)}")
    output(_terminal_safe(TRUSTED_PLUGIN_WARNING))
    return 0 if info.error is None else 1


async def run_session(
    application: JarvisApplication,
    *,
    voice_mode: bool = False,
    input_fn: InputFunction | None = None,
    output_fn: OutputFunction | None = None,
) -> int:
    """Run one text or push-to-talk session until exit, EOF, or interrupt."""

    read = input_fn or input
    output = output_fn or print
    await application.start()
    await application.runtime.events.publish(EventName.ASSISTANT_STARTED)
    try:
        output("JARVIS is ready. Type 'help' for examples or 'exit' to quit.")
        while True:
            try:
                if voice_mode:
                    if application.speech_to_text is None:
                        output("Voice input is not configured.")
                        return 2
                    output("Listening…")
                    command = await application.speech_to_text.listen()
                    output(f"You > {_terminal_safe(command)}")
                else:
                    command = await asyncio.to_thread(read, "You > ")
            except EOFError:
                output("Jarvis > Goodbye.")
                return 0
            except KeyboardInterrupt:
                output("\nJarvis > Goodbye.")
                return 130
            except VoiceInputError as error:
                output(f"Voice input error: {_terminal_safe(str(error))}")
                continue

            response = await application.runtime.process(command)
            message = _terminal_safe(response.message)
            output(f"Jarvis > {message}")
            if application.text_to_speech is not None and message:
                try:
                    await application.text_to_speech.speak(message[:4_000])
                except VoiceOutputError as error:
                    output(f"Speech output error: {_terminal_safe(str(error))}")
            if response.should_exit:
                return 0
    finally:
        await application.runtime.events.publish(EventName.ASSISTANT_STOPPED)
        await application.aclose()


def make_console_confirmer(
    *,
    input_fn: InputFunction | None = None,
    output_fn: OutputFunction | None = None,
) -> Confirmer:
    """Create a fail-closed async terminal permission prompt."""

    read = input_fn or input
    output = output_fn or print

    async def confirm(request: PermissionRequest) -> bool:
        output("")
        output("Jarvis wants to perform:")
        output(f"Action: {_terminal_safe(request.action_name.replace('_', ' ').title())}")
        output(f"Risk: {request.risk_level.value}")
        output(f"Purpose: {_terminal_safe(request.summary)}")
        for key, value in request.details.items():
            serialized = json.dumps(value, ensure_ascii=False, default=str)
            output(f"{_terminal_safe(key.replace('_', ' ').title())}: {_terminal_safe(serialized)}")
        try:
            answer = await asyncio.to_thread(read, "Allow? [y/N] ")
        except (EOFError, KeyboardInterrupt):
            return False
        return answer.strip().casefold() in {"y", "yes"}

    return confirm


def run_doctor(config: JarvisConfig, *, output_fn: OutputFunction | None = None) -> int:
    """Run non-invasive readiness checks without calling external providers."""

    output = output_fn or print
    checks: list[tuple[str, str, bool]] = []
    checks.append(("ok", f"Python {platform.python_version()}", sys.version_info >= (3, 11)))
    checks.append(("ok", f"Platform: {platform.system()} {platform.release()}", True))
    checks.append(("ok", "Configuration parsed and validated", True))
    checks.extend(
        _writable_path_checks(
            (
                ("Memory storage parent", config.database.path),
                ("Reminder database", config.scheduler.database_path),
                ("Plugin state", config.plugins.state_path),
                ("Screenshots", config.screenshots.directory),
            )
        )
    )
    checks.extend(_provider_checks(config))
    checks.extend(_optional_dependency_checks(config))
    checks.extend(_integration_checks(config))
    checks.extend(_plugin_checks())
    for status, message, _ in checks:
        output(f"[{status}] {_terminal_safe(message)}")
    return 0 if all(passed or status == "warn" for status, _, passed in checks) else 1


def _provider_checks(config: JarvisConfig) -> list[tuple[str, str, bool]]:
    checks: list[tuple[str, str, bool]] = []
    for name, settings in (("AI", config.ai), ("Vision", config.vision)):
        if not settings.enabled:
            checks.append(("warn", f"{name} provider is disabled", True))
            continue
        supported = settings.provider.casefold() in {"openai", "openai-compatible"}
        checks.append(
            ("ok" if supported else "error", f"{name} provider: {settings.provider}", supported)
        )
        has_key = bool(settings.api_key) or settings.provider.casefold() == "openai-compatible"
        checks.append(
            (
                "ok" if has_key else "error",
                f"{name} credential {'is configured' if has_key else 'is missing'}",
                has_key,
            )
        )
    return checks


def _optional_dependency_checks(config: JarvisConfig) -> list[tuple[str, str, bool]]:
    checks: list[tuple[str, str, bool]] = []
    for module, feature, needed in (
        ("PIL", "screenshots", False),
        ("pyautogui", "mouse and keyboard control", False),
        ("speech_recognition", "speech-to-text", config.voice.enabled),
        ("pyaudio", "microphone capture", config.voice.enabled),
        ("pyttsx3", "text-to-speech", config.voice.tts_enabled),
        ("playwright", "browser automation package", config.browser.enabled),
        ("PySide6", "desktop GUI toolkit", False),
        ("qasync", "GUI async event-loop bridge", False),
        ("plyer", "desktop reminder notifications", config.scheduler.desktop_notifications),
    ):
        available = importlib.util.find_spec(module) is not None
        status = "ok" if available else ("error" if needed else "warn")
        checks.append(
            (
                status,
                f"{feature}: {'available' if available else 'not installed'}",
                available or not needed,
            )
        )
    if platform.system() != "Windows":
        checks.append(("warn", "Windows application/window control is unavailable", True))
    if config.browser.enabled and importlib.util.find_spec("playwright") is not None:
        binary = _playwright_browser_binary(config.browser.browser_type)
        checks.append(
            (
                "ok" if binary else "error",
                (
                    f"Playwright {config.browser.browser_type} binary: {binary}"
                    if binary
                    else "Playwright browser binary not found; run "
                    f"`playwright install {config.browser.browser_type}`"
                ),
                binary is not None,
            )
        )
    return checks


def _writable_path_checks(
    paths: Sequence[tuple[str, Path]],
) -> list[tuple[str, str, bool]]:
    checks: list[tuple[str, str, bool]] = []
    for label, path in paths:
        target = path if path.suffix == "" else path.parent
        parent = _nearest_existing_parent(target)
        safe_target = not path.is_symlink()
        writable = parent is not None and os.access(parent, os.W_OK) and safe_target
        checks.append(
            (
                "ok" if writable else "error",
                f"{label} path: {path}",
                writable,
            )
        )
    return checks


def _integration_checks(config: JarvisConfig) -> list[tuple[str, str, bool]]:
    checks: list[tuple[str, str, bool]] = []
    if config.integrations.github_enabled:
        parsed = urlsplit(config.integrations.github_base_url)
        secure = parsed.scheme == "https" and bool(parsed.hostname)
        checks.append(
            (
                "ok" if secure else "error",
                f"GitHub endpoint: {config.integrations.github_base_url}",
                secure,
            )
        )
        has_token = bool(config.integrations.github_token)
        checks.append(
            (
                "ok" if has_token else "error",
                f"GitHub token {'is configured' if has_token else 'is missing'}",
                has_token,
            )
        )
    else:
        checks.append(("warn", "GitHub integration is disabled", True))
    for label, provider in (
        ("Email", config.integrations.email_provider),
        ("Calendar", config.integrations.calendar_provider),
    ):
        supported = provider in {"none", "memory", "in-memory"}
        checks.append(
            (
                "ok" if supported and provider != "none" else ("warn" if supported else "error"),
                f"{label} provider: {provider}",
                supported,
            )
        )
    return checks


def _plugin_checks() -> list[tuple[str, str, bool]]:
    try:
        discovered = importlib.metadata.entry_points()
        selected = (
            discovered.select(group="jarvis.plugins")
            if hasattr(discovered, "select")
            else discovered.get("jarvis.plugins", ())
        )
        count = len(tuple(selected))
    except Exception:
        return [("error", "Plugin entry-point discovery failed", False)]
    return [("ok", f"Plugin entry points installed: {count}", True)]


def _playwright_browser_binary(browser_type: str) -> Path | None:
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured:
        roots = (Path(configured),)
    elif platform.system() == "Windows":
        roots = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / "ms-playwright",)
    elif platform.system() == "Darwin":
        roots = (Path.home() / "Library" / "Caches" / "ms-playwright",)
    else:
        roots = (Path.home() / ".cache" / "ms-playwright",)
    executable_names = {
        "chromium": {"chrome", "chrome.exe", "headless_shell", "headless_shell.exe"},
        "firefox": {"firefox", "firefox.exe"},
        "webkit": {"minibrowser", "minibrowser.exe", "pw_run.sh"},
    }[browser_type]
    for root in roots:
        if not root.is_dir():
            continue
        inspected = 0
        for candidate in root.rglob("*"):
            inspected += 1
            if inspected > 2_000:
                break
            if candidate.is_file() and candidate.name.casefold() in executable_names:
                return candidate
    return None


def _nearest_existing_parent(path: Path) -> Path | None:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate if candidate.exists() and candidate.is_dir() else None


def _terminal_safe(value: object, *, limit: int = 20_000) -> str:
    """Remove terminal control sequences while preserving tabs and newlines."""

    text = _ANSI_ESCAPE.sub("", str(value))
    return _UNSAFE_TERMINAL.sub("", text)[:limit]
