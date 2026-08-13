"""Domain models for explicitly persisted assistant memory."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MemoryCategory(StrEnum):
    """Supported categories for user-approved persistent memories."""

    PREFERENCES = "preferences"
    FACTS = "facts"
    PROJECTS = "projects"
    ALIASES = "aliases"

    @classmethod
    def _missing_(cls, value: object) -> MemoryCategory | None:
        if isinstance(value, str):
            normalized = value.strip().casefold()
            for member in cls:
                if normalized in {member.value, member.name.casefold()}:
                    return member
        return None


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """An immutable memory returned by a repository."""

    id: int
    category: MemoryCategory
    key: str
    value: str
    created_at: datetime
    updated_at: datetime
