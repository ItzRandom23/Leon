"""Focused runtime integration tests for guard and fallback containment."""

from __future__ import annotations

import asyncio

import pytest

from jarvis.computer import WindowInformation
from jarvis.core.actions import ActionParameter, ActionRegistry, ActionRequest
from jarvis.core.permissions import PermissionManager
from jarvis.core.router import Router
from jarvis.core.runtime import JarvisRuntime
from jarvis.core.safety import DesktopExecutionGuard
from jarvis.skills.base import RiskLevel, Skill, SkillResult


class NeverCalledWindows:
    def active_window(self):  # type: ignore[no-untyped-def]
        raise AssertionError("sequence validation must run before desktop inspection")


class ChangingWindows:
    def __init__(self) -> None:
        self.calls = 0

    def active_window(self) -> WindowInformation:
        self.calls += 1
        return WindowInformation(self.calls, f"Window {self.calls}", None)


class UnsafeFallbackSkill(Skill):
    name = "unsafe-fallback"
    description = "An unsafe fallback used only by this test."
    risk_level = RiskLevel.ACTION

    def can_handle(self, command: str) -> bool:
        return True

    def execute(self, command: str) -> SkillResult:
        raise AssertionError("unsafe fallback must never execute")


def test_desktop_guard_blocks_command_launcher_hotkey_before_handler() -> None:
    registry = ActionRegistry()
    calls: list[list[str]] = []

    @registry.action(
        name="press_hotkey",
        description="Press a keyboard shortcut",
        parameters=(ActionParameter("keys", list, items={"type": "string"}),),
        risk_level=RiskLevel.SENSITIVE,
    )
    def press_hotkey(keys: list[str]) -> None:
        calls.append(keys)

    runtime = JarvisRuntime(
        registry,
        PermissionManager({RiskLevel.SENSITIVE: "allow"}, confirmer=lambda _request: True),
        execution_guard=DesktopExecutionGuard(NeverCalledWindows()),  # type: ignore[arg-type]
    )
    results = asyncio.run(
        runtime.execute_requests((ActionRequest("press_hotkey", {"keys": ["win", "r"]}),))
    )

    assert results[0].error_code == "safety_violation"
    assert calls == []


def test_desktop_guard_blocks_type_then_enter_sequence_before_handlers() -> None:
    registry = ActionRegistry()
    calls: list[str] = []

    @registry.action(
        name="type_text",
        description="Type text",
        parameters=(ActionParameter("text", str),),
        risk_level=RiskLevel.SENSITIVE,
    )
    def type_text(text: str) -> None:
        calls.append(text)

    @registry.action(
        name="press_key",
        description="Press a key",
        parameters=(ActionParameter("key", str),),
        risk_level=RiskLevel.SENSITIVE,
    )
    def press_key(key: str) -> None:
        calls.append(key)

    runtime = JarvisRuntime(
        registry,
        PermissionManager({RiskLevel.SENSITIVE: "allow"}, confirmer=lambda _request: True),
        execution_guard=DesktopExecutionGuard(NeverCalledWindows()),  # type: ignore[arg-type]
    )
    results = asyncio.run(
        runtime.execute_requests(
            (
                ActionRequest("type_text", {"text": "whoami"}),
                ActionRequest("press_key", {"key": "enter"}),
            )
        )
    )

    assert results[0].error_code == "safety_violation"
    assert calls == []


def test_runtime_rejects_non_read_fallback_skills() -> None:
    with pytest.raises(ValueError, match="fallback_router may contain only READ skills"):
        JarvisRuntime(
            ActionRegistry(),
            PermissionManager(),
            fallback_router=Router([UnsafeFallbackSkill()]),
        )


def test_desktop_guard_rechecks_foreground_window_before_pointer_click() -> None:
    registry = ActionRegistry()
    calls: list[tuple[int, int]] = []

    @registry.action(
        name="click_mouse",
        description="Click at exact coordinates",
        parameters=(ActionParameter("x", int), ActionParameter("y", int)),
        risk_level=RiskLevel.ACTION,
    )
    def click_mouse(x: int, y: int) -> None:
        calls.append((x, y))

    prompts = []
    runtime = JarvisRuntime(
        registry,
        PermissionManager(
            {RiskLevel.ACTION: "ask"},
            confirmer=lambda request: prompts.append(request) or True,
        ),
        execution_guard=DesktopExecutionGuard(ChangingWindows()),  # type: ignore[arg-type]
    )

    results = asyncio.run(
        runtime.execute_requests((ActionRequest("click_mouse", {"x": 25, "y": 40}),))
    )

    assert prompts[0].details["target_window"] == "Window 1"
    assert results[0].error_code == "safety_violation"
    assert calls == []
