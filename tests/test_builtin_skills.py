"""Tests for deterministic conversation and date/time skills."""

from __future__ import annotations

from datetime import datetime

import pytest

from jarvis.core.router import create_default_router
from jarvis.skills.datetime_skill import DateSkill, TimeSkill
from jarvis.skills.general import ExitSkill, GreetingSkill, HelpSkill


@pytest.mark.parametrize("command", ["hello", " HI! ", "Good   morning."])
def test_greeting_variants(command: str) -> None:
    skill = GreetingSkill()

    assert skill.can_handle(command)
    assert skill.execute(command).message == "Hello! How can I help you?"


@pytest.mark.parametrize("command", ["exit", "QUIT!", " bye ", "goodbye"])
def test_exit_variants_request_session_end(command: str) -> None:
    skill = ExitSkill()

    assert skill.can_handle(command)
    assert skill.execute(command).should_exit is True


def test_help_describes_only_phase_one_capabilities() -> None:
    result = HelpSkill().execute("help")

    assert "system information" in result.message
    assert "approved application" in result.message
    assert "open notepad" in result.message


def test_time_skill_uses_injected_clock() -> None:
    value = datetime(2026, 8, 13, 6, 5, 4)
    skill = TimeSkill(clock=lambda: value)

    result = skill.execute("time")

    assert result.message == "The current time is 6:05 AM."
    assert result.data == {"iso_time": "06:05:04"}


def test_date_skill_uses_injected_clock() -> None:
    value = datetime(2026, 8, 13, 6, 5, 4)
    skill = DateSkill(clock=lambda: value)

    result = skill.execute("date")

    assert result.message == "Today is Thursday, August 13, 2026."
    assert result.data == {"iso_date": "2026-08-13"}


@pytest.mark.parametrize(
    ("skill", "accepted", "rejected"),
    [
        (TimeSkill(), "What TIME is it?", "what timezone is it"),
        (DateSkill(), "what is TODAY'S date?", "what happened today"),
        (HelpSkill(), "what can you do?", "please help me code"),
    ],
)
def test_builtin_matching_is_bounded(skill: object, accepted: str, rejected: str) -> None:
    assert skill.can_handle(accepted)  # type: ignore[attr-defined]
    assert not skill.can_handle(rejected)  # type: ignore[attr-defined]


def test_default_router_handles_conversation_commands_and_unknowns() -> None:
    router = create_default_router()

    assert router.route("hello").success is True
    assert router.route("exit").should_exit is True
    assert router.route("not a supported request").success is False
