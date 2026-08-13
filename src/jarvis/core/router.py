"""Ordered intent routing for assistant skills."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from jarvis.skills.base import Skill, SkillResult

logger = logging.getLogger(__name__)


class Router:
    """Route commands to the first registered skill that accepts them."""

    def __init__(self, skills: Iterable[Skill] = ()) -> None:
        self._skills: list[Skill] = []
        for skill in skills:
            self.register(skill)

    @property
    def skills(self) -> tuple[Skill, ...]:
        """Return registered skills in routing order."""

        return tuple(self._skills)

    def register(self, skill: Skill) -> None:
        """Register *skill*, rejecting duplicate skill names."""

        if any(existing.name == skill.name for existing in self._skills):
            raise ValueError(f"A skill named {skill.name!r} is already registered")
        self._skills.append(skill)
        logger.info("skill_registered", extra={"skill": skill.name})

    def route(self, command: str) -> SkillResult:
        """Execute the first matching skill or return a friendly fallback."""

        normalized = command.strip()
        for skill in self._skills:
            try:
                matches = skill.can_handle(normalized)
            except Exception:
                logger.exception("skill_matching_failed", extra={"skill": skill.name})
                return SkillResult(
                    "I couldn't process that request.",
                    success=False,
                )
            if matches:
                logger.info(
                    "command_routed",
                    extra={"skill": skill.name, "risk_level": skill.risk_level.value},
                )
                try:
                    return skill.execute(normalized)
                except Exception:
                    logger.exception("skill_execution_failed", extra={"skill": skill.name})
                    return SkillResult(
                        "I couldn't complete that request.",
                        success=False,
                    )
        logger.info("command_unmatched")
        return SkillResult(
            "I don't understand that command. Type 'help' to see what I can do.",
            success=False,
        )


def create_default_router() -> Router:
    """Build a router containing the Phase 1 built-in skills."""

    from jarvis.skills.applications import ApplicationSkill
    from jarvis.skills.datetime_skill import DateSkill, TimeSkill
    from jarvis.skills.general import ExitSkill, GreetingSkill, HelpSkill
    from jarvis.skills.system import SystemInfoSkill

    return Router(
        [
            ExitSkill(),
            GreetingSkill(),
            HelpSkill(),
            TimeSkill(),
            DateSkill(),
            SystemInfoSkill(),
            ApplicationSkill(),
        ]
    )
