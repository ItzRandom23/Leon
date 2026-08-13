"""Conversation and session-control skills."""

from __future__ import annotations

import re

from jarvis.skills.base import RiskLevel, Skill, SkillResult


def _normalized(command: str) -> str:
    """Return case-insensitive command text without terminal punctuation."""

    return re.sub(r"[.!?]+$", "", " ".join(command.casefold().split()))


class GreetingSkill(Skill):
    """Respond to a small deterministic set of greetings."""

    name = "greeting"
    description = "Respond to greetings."
    risk_level = RiskLevel.READ
    _greetings = frozenset({"hello", "hi", "hey", "good morning", "good afternoon", "good evening"})

    def can_handle(self, command: str) -> bool:
        return _normalized(command) in self._greetings

    def execute(self, command: str) -> SkillResult:
        return SkillResult("Hello! How can I help you?")


class ExitSkill(Skill):
    """End the interactive assistant session."""

    name = "exit"
    description = "Exit JARVIS."
    risk_level = RiskLevel.READ
    _commands = frozenset({"exit", "quit", "bye", "goodbye"})

    def can_handle(self, command: str) -> bool:
        return _normalized(command) in self._commands

    def execute(self, command: str) -> SkillResult:
        return SkillResult("Goodbye.", should_exit=True)


class HelpSkill(Skill):
    """Describe representative live Phase 1–6 capabilities."""

    name = "help"
    description = "List available commands."
    risk_level = RiskLevel.READ
    _commands = frozenset({"help", "commands", "show commands", "what can you do"})

    def can_handle(self, command: str) -> bool:
        return _normalized(command) in self._commands

    def execute(self, command: str) -> SkillResult:
        return SkillResult(
            "I can report system information, open an approved application, "
            "manage explicit memories, capture or analyze the screen, and use "
            "permissioned mouse, keyboard, and window actions.\n"
            "Try: hello, time, date, system information, open notepad, "
            "remember that my code folder is D:\\Projects, take a screenshot, "
            "show memories, or exit."
        )
