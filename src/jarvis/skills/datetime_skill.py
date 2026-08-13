"""Current time and date skills."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime

from jarvis.skills.base import RiskLevel, Skill, SkillResult

Clock = Callable[[], datetime]


def _normalized(command: str) -> str:
    return re.sub(r"[.!?]+$", "", " ".join(command.casefold().split()))


class TimeSkill(Skill):
    """Report the machine's local time."""

    name = "current_time"
    description = "Show the current local time."
    risk_level = RiskLevel.READ
    _commands = frozenset(
        {"time", "current time", "what time is it", "what is the time", "what is the current time"}
    )

    def __init__(self, clock: Clock = datetime.now) -> None:
        self._clock = clock

    def can_handle(self, command: str) -> bool:
        return _normalized(command) in self._commands

    def execute(self, command: str) -> SkillResult:
        value = self._clock()
        display_time = value.strftime("%I:%M %p").lstrip("0")
        return SkillResult(
            f"The current time is {display_time}.",
            data={"iso_time": value.time().isoformat()},
        )


class DateSkill(Skill):
    """Report the machine's local date."""

    name = "current_date"
    description = "Show the current local date."
    risk_level = RiskLevel.READ
    _commands = frozenset(
        {
            "date",
            "current date",
            "today's date",
            "todays date",
            "what date is it",
            "what is the date",
            "what is today's date",
            "what is todays date",
        }
    )

    def __init__(self, clock: Clock = datetime.now) -> None:
        self._clock = clock

    def can_handle(self, command: str) -> bool:
        return _normalized(command) in self._commands

    def execute(self, command: str) -> SkillResult:
        value = self._clock()
        display_date = f"{value:%A, %B} {value.day}, {value:%Y}"
        return SkillResult(
            f"Today is {display_date}.",
            data={"iso_date": value.date().isoformat()},
        )
