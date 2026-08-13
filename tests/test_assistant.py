"""Tests for single-command and interactive assistant orchestration."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jarvis.core.assistant import Assistant, create_default_assistant
from jarvis.core.router import Router
from jarvis.skills.general import ExitSkill, GreetingSkill


class ScriptedInput:
    def __init__(self, commands: Iterator[str]) -> None:
        self._commands = commands
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return next(self._commands)


def test_process_routes_one_command_without_starting_cli() -> None:
    assistant = Assistant(Router([GreetingSkill()]))

    result = assistant.process("HELLO!")

    assert result.message == "Hello! How can I help you?"


def test_run_skips_blank_input_and_stops_after_exit() -> None:
    input_fn = ScriptedInput(iter(["  ", "hello", "bye", "never read"]))
    output: list[str] = []
    router = Router([ExitSkill(), GreetingSkill()])
    assistant = Assistant(router, input_fn=input_fn, output_fn=output.append)

    assistant.run()

    assert output == [
        "JARVIS",
        "\nJarvis > Hello! How can I help you?",
        "\nJarvis > Goodbye.",
    ]
    assert len(input_fn.prompts) == 3
    assert all(prompt == "\nYou > " for prompt in input_fn.prompts)


@pytest.mark.parametrize("error", [EOFError(), KeyboardInterrupt()])
def test_run_exits_cleanly_when_input_is_interrupted(error: BaseException) -> None:
    def interrupted_input(_prompt: str) -> str:
        raise error

    output: list[str] = []
    Assistant(Router(), input_fn=interrupted_input, output_fn=output.append).run()

    assert output == ["JARVIS", "\nJarvis > Goodbye."]


def test_default_assistant_has_all_phase_one_skills() -> None:
    assistant = create_default_assistant(
        input_fn=lambda _prompt: "exit",
        output_fn=lambda _text: None,
    )

    assert [skill.name for skill in assistant.router.skills] == [
        "exit",
        "greeting",
        "help",
        "current_time",
        "current_date",
        "system_information",
        "applications",
    ]
