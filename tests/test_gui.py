"""Deterministic tests for the optional, framework-neutral GUI foundation."""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from jarvis.core.events import EventBus
from jarvis.core.permissions import PermissionManager, PermissionRequest
from jarvis.gui import (
    ActivityState,
    ApplicationDataProvider,
    AssistantState,
    GuiBusyError,
    GuiController,
    GuiLogStore,
    GuiPermissionBroker,
    GuiUnavailableError,
    MemoryView,
    Page,
    Theme,
    is_gui_available,
)
from jarvis.gui.models import PermissionPrompt, bounded_ui_value, clean_text
from jarvis.gui.pyside import _configure_permission_buttons, create_main_window
from jarvis.skills.base import RiskLevel


@dataclass(frozen=True)
class _Response:
    message: str
    should_exit: bool = False


class _Runtime:
    def __init__(self, permission_broker: GuiPermissionBroker | None = None) -> None:
        self.events = EventBus()
        self.calls: list[str] = []
        broker = permission_broker or GuiPermissionBroker()
        self.permissions = PermissionManager(confirmer=broker.confirm)
        self.permission_broker = broker

    async def process(self, text: str) -> _Response:
        self.calls.append(text)
        payload = {"action": "demo_action", "request_id": "request-1"}
        await self.events.publish("action.requested", payload)
        await self.events.publish("action.started", payload)
        await self.events.publish("action.completed", {**payload, "success": True})
        return _Response(f"Handled: {text}")


class _BlockingRuntime:
    def __init__(self) -> None:
        self.events = EventBus()
        self.started = asyncio.Event()
        self.permission_broker = GuiPermissionBroker()
        self.permissions = PermissionManager(confirmer=self.permission_broker.confirm)

    async def process(self, _text: str) -> _Response:
        self.started.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class _UnstoppableSideEffectRuntime:
    """Model a to_thread-backed write that survives cancellation of its awaiter."""

    def __init__(self) -> None:
        self.events = EventBus()
        self.permission_broker = GuiPermissionBroker()
        self.permissions = PermissionManager(confirmer=self.permission_broker.confirm)
        self.started = asyncio.Event()
        self.release = threading.Event()
        self.completed = threading.Event()

    async def process(self, _text: str) -> _Response:
        payload = {"action": "remote_write", "request_id": "write-1"}
        await self.events.publish("action.requested", payload)
        await self.events.publish("action.started", payload)
        self.started.set()

        def commit() -> None:
            self.release.wait(timeout=2)
            self.completed.set()

        await asyncio.to_thread(commit)
        await self.events.publish("action.completed", payload)
        return _Response("Write completed")


class _PermissionRuntime:
    def __init__(self, broker: GuiPermissionBroker) -> None:
        self.events = EventBus()
        self.broker = broker
        self.permissions = PermissionManager(confirmer=broker.confirm)

    async def process(self, _text: str) -> _Response:
        allowed = await self.broker.confirm(
            PermissionRequest(
                RiskLevel.SENSITIVE,
                "browser_type",
                "Type into the selected field",
                {"target": "Email", "text": "hello\x00 world"},
            )
        )
        return _Response("Allowed" if allowed else "Denied")


def _app(runtime: object) -> SimpleNamespace:
    config = SimpleNamespace(
        ai=SimpleNamespace(enabled=True, provider="openai", base_url="https://api.openai.com/v1"),
        memory=SimpleNamespace(enabled=True),
        voice=SimpleNamespace(enabled=False),
        vision=SimpleNamespace(enabled=False),
    )
    return SimpleNamespace(runtime=runtime, config=config)


def test_gui_import_is_lazy_and_theme_values_are_stable() -> None:
    assert Theme.SYSTEM.value == "system"
    if not is_gui_available():
        assert not any(name == "PySide6" or name.startswith("PySide6.") for name in sys.modules)
        with pytest.raises(GuiUnavailableError, match="PySide6 and qasync"):
            create_main_window(object())  # type: ignore[arg-type]


def test_ui_values_are_bounded_sanitized_and_secret_safe() -> None:
    assert clean_text("one\x00two\x1bthree") == "onetwothree"
    assert clean_text("approve\u202edeny") == r"approve\u202edeny"
    safe = bounded_ui_value(
        {
            "api_token": {"nested": "value"},
            "normal": "x" * 5_000,
            "many": list(range(150)),
        }
    )
    assert safe["api_token"] == "***"  # type: ignore[index]
    assert str(safe["normal"]).endswith("…")  # type: ignore[index]
    assert len(safe["many"]) == 100  # type: ignore[arg-type,index]

    prompt = PermissionPrompt(
        id="p-1",
        risk_level="sensitive",
        action_name="browser_type",
        summary="Review this action",
        details={"text": "exact\x00 text", "target": "Email"},
        requested_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert prompt.details == {"text": "exact text", "target": "Email"}


def test_permission_dialog_makes_deny_the_only_default_button() -> None:
    class Button:
        def __init__(self) -> None:
            self.text = ""
            self.auto_default: bool | None = None
            self.default: bool | None = None
            self.focused = False

        def setText(self, value: str) -> None:  # noqa: N802
            self.text = value

        def setAutoDefault(self, value: bool) -> None:  # noqa: N802
            self.auto_default = value

        def setDefault(self, value: bool) -> None:  # noqa: N802
            self.default = value

        def setFocus(self) -> None:  # noqa: N802
            self.focused = True

    allow = Button()
    deny = Button()
    box = SimpleNamespace(button=lambda role: allow if role == "yes" else deny)
    qt = SimpleNamespace(QDialogButtonBox=SimpleNamespace(Yes="yes", No="no"))

    _configure_permission_buttons(box, qt)

    assert (allow.text, allow.auto_default, allow.default) == ("Allow once", False, False)
    assert (deny.text, deny.auto_default, deny.default, deny.focused) == (
        "Deny",
        True,
        True,
        True,
    )


def test_controller_rejects_a_broker_not_bound_to_runtime_permissions() -> None:
    bound = GuiPermissionBroker()
    visible = GuiPermissionBroker()
    runtime = _Runtime()
    runtime.permissions = PermissionManager(confirmer=bound.confirm)  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="same broker"):
        GuiController(_app(runtime), permission_broker=visible)

    controller = GuiController(_app(runtime), permission_broker=bound)
    controller.close()


def test_controller_requires_runtime_permission_engine_even_with_a_broker() -> None:
    runtime = SimpleNamespace(events=EventBus(), process=lambda _text: None)
    with pytest.raises(ValueError, match="same broker"):
        GuiController(_app(runtime), permission_broker=GuiPermissionBroker())


def test_run_gui_rejects_unbound_broker_before_importing_qt() -> None:
    from jarvis.gui import run_gui

    bound = GuiPermissionBroker()
    visible = GuiPermissionBroker()
    runtime = _Runtime()
    runtime.permissions = PermissionManager(confirmer=bound.confirm)  # type: ignore[attr-defined]

    with pytest.raises(ValueError, match="not bound"):
        run_gui(_app(runtime), visible)


def test_status_discloses_external_vision_when_ai_is_disabled() -> None:
    config = SimpleNamespace(
        ai=SimpleNamespace(enabled=False, provider="none", base_url=None),
        vision=SimpleNamespace(
            enabled=True,
            provider="openai-compatible",
            base_url="https://vision.example/v1",
        ),
        voice=SimpleNamespace(enabled=False, stt_provider="none"),
        memory=SimpleNamespace(enabled=False),
        integrations=SimpleNamespace(github_enabled=False),
    )
    runtime = _Runtime()
    controller = GuiController(
        SimpleNamespace(runtime=runtime, config=config),
        permission_broker=runtime.permission_broker,
    )

    assert controller.status.ai_provider == "disabled"
    assert controller.status.execution_label == "external data services active"
    controller.close()


def test_permission_broker_requires_one_explicit_decision() -> None:
    async def scenario() -> None:
        broker = GuiPermissionBroker(timeout_seconds=1)
        snapshots: list[int] = []
        broker.subscribe(lambda pending: snapshots.append(len(pending)))
        request = PermissionRequest(
            RiskLevel.ACTION,
            "open_application",
            "Open Calculator",
            {"application": "Calculator"},
        )
        decision = asyncio.create_task(broker.confirm(request))
        await asyncio.sleep(0)
        assert len(broker.pending) == 1
        prompt = broker.pending[0]
        assert prompt.details["application"] == "Calculator"
        assert broker.resolve(prompt.id, True)
        assert not broker.resolve(prompt.id, True)
        assert await decision is True
        assert broker.pending == ()
        assert snapshots == [1, 0]

    asyncio.run(scenario())


def test_permission_broker_times_out_and_close_fails_closed() -> None:
    async def scenario() -> None:
        request = PermissionRequest(RiskLevel.ACTION, "demo", "Demo action")
        timeout_broker = GuiPermissionBroker(timeout_seconds=0.01)
        assert await timeout_broker.confirm(request) is False

        broker = GuiPermissionBroker(timeout_seconds=None)
        pending = asyncio.create_task(broker.confirm(request))
        await asyncio.sleep(0)
        broker.close()
        assert await pending is False
        assert await broker.confirm(request) is False

    asyncio.run(scenario())


def test_controller_reuses_runtime_and_tracks_chat_actions_and_status() -> None:
    async def scenario() -> None:
        runtime = _Runtime()
        application = _app(runtime)
        controller = GuiController(application, permission_broker=runtime.permission_broker)
        updates = []
        working_busy = []

        def observe(update: object) -> None:
            updates.append(update)
            if (
                getattr(getattr(update, "kind", None), "value", None) == "status"
                and controller.status.state is AssistantState.WORKING
            ):
                working_busy.append(controller.busy)

        controller.subscribe(observe)  # type: ignore[arg-type]

        response = await controller.send_message("hello\x00 JARVIS")

        assert response == _Response("Handled: hello JARVIS")
        assert controller.application is application
        assert controller.runtime is runtime
        assert runtime.calls == ["hello JARVIS"]
        assert [message.role.value for message in controller.messages] == ["user", "assistant"]
        assert controller.messages[-1].text == "Handled: hello JARVIS"
        assert len(controller.activities) == 1
        assert controller.activities[0].state is ActivityState.COMPLETED
        assert controller.status.state is AssistantState.IDLE
        assert controller.status.ai_provider == "openai"
        assert controller.status.execution_label == "cloud"
        assert {update.kind.value for update in updates} >= {"chat", "activity", "status"}
        assert all(working_busy)
        controller.close()

    asyncio.run(scenario())


def test_controller_prevents_overlapping_turns_and_supports_cancellation() -> None:
    async def scenario() -> None:
        runtime = _BlockingRuntime()
        controller = GuiController(_app(runtime), permission_broker=runtime.permission_broker)
        active = asyncio.create_task(controller.send_message("wait"))
        await runtime.started.wait()

        with pytest.raises(GuiBusyError):
            await controller.send_message("second")
        assert controller.cancel_current()
        assert await active is None
        assert controller.messages[-1].text == "Request cancelled."
        assert controller.status.state is AssistantState.IDLE
        assert not controller.cancel_current()
        controller.close()

    asyncio.run(scenario())


def test_controller_reports_unknown_outcome_when_started_side_effect_survives_cancel() -> None:
    async def scenario() -> None:
        runtime = _UnstoppableSideEffectRuntime()
        controller = GuiController(_app(runtime), permission_broker=runtime.permission_broker)
        active = asyncio.create_task(controller.send_message("perform remote write"))
        await runtime.started.wait()

        assert controller.cancel_current()
        assert await active is None
        assert controller.activities[0].state is ActivityState.OUTCOME_UNKNOWN
        assert controller.activities[0].error_code == "outcome_unknown"
        assert "may still complete" in controller.messages[-1].text

        runtime.release.set()
        assert await asyncio.to_thread(runtime.completed.wait, 1)
        controller.close()

    asyncio.run(scenario())


def test_controller_async_close_keeps_fail_closed_stopped_state() -> None:
    async def scenario() -> None:
        runtime = _BlockingRuntime()
        controller = GuiController(_app(runtime), permission_broker=runtime.permission_broker)
        active = asyncio.create_task(controller.send_message("wait"))
        await runtime.started.wait()
        await controller.aclose()
        assert await active is None
        assert controller.closed
        assert controller.status.state is AssistantState.STOPPED

    asyncio.run(scenario())


def test_controller_permission_prompt_is_non_blocking_and_fail_closed() -> None:
    async def scenario() -> None:
        broker = GuiPermissionBroker(timeout_seconds=1)
        runtime = _PermissionRuntime(broker)
        controller = GuiController(_app(runtime), permission_broker=broker)
        active = asyncio.create_task(controller.send_message("type hello"))
        while not controller.pending_permissions:
            await asyncio.sleep(0)

        prompt = controller.pending_permissions[0]
        assert controller.status.state is AssistantState.AWAITING_PERMISSION
        assert prompt.details == {"target": "Email", "text": "hello world"}
        assert controller.resolve_permission(prompt.id, False)
        await active
        assert controller.messages[-1].text == "Denied"
        assert controller.pending_permissions == ()
        controller.close()

    asyncio.run(scenario())


class _Config:
    def redacted_dict(self) -> dict[str, object]:
        return {
            "ai": {"enabled": True, "api_key": "***"},
            "logging": {"level": "INFO"},
        }


def test_application_data_provider_exposes_bounded_dashboard_surfaces() -> None:
    memory = SimpleNamespace(
        list=lambda: [
            SimpleNamespace(
                id=1,
                category="facts",
                key="language",
                value="Python",
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]
    )
    reminders = SimpleNamespace(
        list=lambda: [
            SimpleNamespace(
                id=7,
                message="Stand up",
                due_at=datetime(2026, 1, 2, tzinfo=UTC),
                timezone="UTC",
                recurrence="daily",
                status="scheduled",
            )
        ]
    )
    integrations = SimpleNamespace(
        list_integrations=lambda: [
            SimpleNamespace(
                metadata=SimpleNamespace(
                    name="github",
                    display_name="GitHub",
                    description="Repository provider",
                ),
                status="connected",
            )
        ]
    )
    plugins = SimpleNamespace(
        list_plugins=lambda: [
            SimpleNamespace(
                plugin_id="hello-plugin",
                metadata=SimpleNamespace(name="Hello", version="1.0", description="Demo"),
                status="enabled",
            )
        ]
    )
    application = SimpleNamespace(
        memory_manager=memory,
        reminder_service=reminders,
        integration_registry=integrations,
        plugin_manager=plugins,
        config=_Config(),
    )
    logs = GuiLogStore(capacity=2)
    provider = ApplicationDataProvider(application, logs=logs)
    record = logging.LogRecord(
        "jarvis.test",
        logging.INFO,
        __file__,
        1,
        "token=super-secret\x00",
        (),
        None,
    )
    logs.handle(record)

    async def scenario() -> None:
        memories = await provider.load(Page.MEMORY)
        tasks = await provider.load(Page.TASKS)
        integration_rows = await provider.load(Page.INTEGRATIONS)
        plugin_rows = await provider.load(Page.PLUGINS)
        settings = await provider.load(Page.SETTINGS)
        log_rows = await provider.load(Page.LOGS)
        about = await provider.load(Page.ABOUT)

        assert memories[0].key == "language"  # type: ignore[index,union-attr]
        assert tasks[0].message == "Stand up"  # type: ignore[index,union-attr]
        assert integration_rows[0].name == "GitHub"  # type: ignore[index,union-attr]
        assert integration_rows[0].provider == "github"  # type: ignore[index,union-attr]
        assert plugin_rows[0].version == "1.0"  # type: ignore[index,union-attr]
        assert any(row.redacted for row in settings)  # type: ignore[union-attr]
        assert log_rows[0].message == "token=***"  # type: ignore[index,union-attr]
        assert about.name == "JARVIS"  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_controller_isolates_data_provider_failure() -> None:
    class BrokenProvider:
        async def load(self, _page: Page) -> object:
            raise RuntimeError("private provider detail")

    async def scenario() -> None:
        controller = GuiController(
            _app(runtime := _Runtime()),
            permission_broker=runtime.permission_broker,
            data_provider=BrokenProvider(),  # type: ignore[arg-type]
        )
        updates = []
        controller.subscribe(updates.append)
        assert await controller.load_page(Page.MEMORY) == ()
        assert updates[-1].payload == {"page": "memory", "error": "RuntimeError"}
        controller.close()

    asyncio.run(scenario())


def test_controller_bounds_and_sanitizes_injected_page_rows() -> None:
    class LargeProvider:
        async def load(self, _page: Page) -> object:
            return tuple(
                MemoryView(
                    id=str(index),
                    category="facts",
                    key=f"key-{index}\x00",
                    value="v" * 5_000,
                    updated_at="now",
                )
                for index in range(300)
            )

    async def scenario() -> None:
        controller = GuiController(
            _app(runtime := _Runtime()),
            permission_broker=runtime.permission_broker,
            data_provider=LargeProvider(),  # type: ignore[arg-type]
        )
        rows = await controller.load_page(Page.MEMORY)
        assert len(rows) == 250  # type: ignore[arg-type]
        assert rows[0].key == "key-0"  # type: ignore[index,union-attr]
        assert len(rows[0].value) == 4_000  # type: ignore[index,union-attr]
        assert rows[0].value.endswith("…")  # type: ignore[index,union-attr]
        controller.close()

    asyncio.run(scenario())
