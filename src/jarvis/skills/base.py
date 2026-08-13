"""Contracts shared by all assistant skills."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RiskLevel(StrEnum):
    """Safety category attached to a skill's operation."""

    READ = "READ"
    ACTION = "ACTION"
    SENSITIVE = "SENSITIVE"
    DESTRUCTIVE = "DESTRUCTIVE"


@dataclass(frozen=True, slots=True)
class SkillResult:
    """The structured outcome returned by a skill."""

    message: str
    success: bool = True
    should_exit: bool = False
    data: Mapping[str, Any] = field(default_factory=dict)


class Skill(ABC):
    """An independently routable assistant capability."""

    name: str
    description: str
    risk_level: RiskLevel = RiskLevel.READ

    @abstractmethod
    def can_handle(self, command: str) -> bool:
        """Return whether this skill recognizes *command*."""

    @abstractmethod
    def execute(self, command: str) -> SkillResult:
        """Execute the recognized command and return its result."""
