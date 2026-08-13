"""Tests for skill registration and deterministic command routing."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from jarvis.core.router import Router
from jarvis.skills.base import RiskLevel, Skill, SkillResult


class StubSkill(Skill):
    """Small configurable skill used to exercise the router contract."""

    description = "A skill used by router tests."
    risk_level = RiskLevel.READ

    def __init__(
        self,
        name: str,
        predicate: Callable[[str], bool],
        response: str,
    ) -> None:
        self.name = name
        self._predicate = predicate
        self._response = response
        self.seen_commands: list[str] = []

    def can_handle(self, command: str) -> bool:
        self.seen_commands.append(command)
        return self._predicate(command)

    def execute(self, command: str) -> SkillResult:
        return SkillResult(self._response, data={"command": command})


class FailingSkill(StubSkill):
    """Skill that simulates a failure at the execution boundary."""

    def execute(self, command: str) -> SkillResult:
        raise RuntimeError("simulated failure")


class FailingMatcherSkill(StubSkill):
    """Skill that simulates a failure during intent matching."""

    def can_handle(self, command: str) -> bool:
        raise RuntimeError("simulated matcher failure")


def test_route_uses_first_registered_matching_skill() -> None:
    router = Router()
    first = StubSkill("first", lambda _command: True, "first response")
    second = StubSkill("second", lambda _command: True, "second response")
    router.register(first)
    router.register(second)

    result = router.route("test command")

    assert result.message == "first response"
    assert result.data == {"command": "test command"}
    assert second.seen_commands == []


def test_register_rejects_duplicate_skill_names() -> None:
    router = Router()
    router.register(StubSkill("duplicate", lambda _command: False, "unused"))

    with pytest.raises(ValueError, match="duplicate"):
        router.register(StubSkill("duplicate", lambda _command: True, "unused"))


def test_unknown_command_returns_unsuccessful_result() -> None:
    router = Router()
    router.register(StubSkill("never", lambda _command: False, "unused"))

    result = router.route("please do something unavailable")

    assert isinstance(result, SkillResult)
    assert result.success is False
    assert result.should_exit is False
    assert result.message


def test_route_strips_outer_whitespace_before_matching_and_execution() -> None:
    skill = StubSkill("echo", lambda command: command == "hello", "matched")
    router = Router([skill])

    result = router.route("  hello\n")

    assert result.success is True
    assert skill.seen_commands == ["hello"]
    assert result.data == {"command": "hello"}


def test_skill_exception_becomes_safe_failed_result() -> None:
    router = Router([FailingSkill("failing", lambda _command: True, "unused")])

    result = router.route("run")

    assert result.success is False
    assert result.should_exit is False
    assert "couldn't complete" in result.message


def test_skill_matcher_exception_becomes_safe_failed_result() -> None:
    router = Router([FailingMatcherSkill("failing", lambda _command: True, "unused")])

    result = router.route("run")

    assert result.success is False
    assert result.should_exit is False
    assert "couldn't process" in result.message


def test_skills_property_is_an_immutable_registration_snapshot() -> None:
    skill = StubSkill("only", lambda _command: False, "unused")
    router = Router([skill])

    registered = router.skills

    assert registered == (skill,)
    assert isinstance(registered, tuple)


def test_skill_result_has_safe_non_exiting_defaults() -> None:
    result = SkillResult("done")

    assert result.success is True
    assert result.should_exit is False
