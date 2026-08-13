"""Replaceable repository contract for JARVIS memory."""

from __future__ import annotations

from pathlib import Path
from types import TracebackType
from typing import Protocol, runtime_checkable

from jarvis.memory.models import MemoryCategory, MemoryRecord

MemoryCategoryLike = MemoryCategory | str


@runtime_checkable
class MemoryRepository(Protocol):
    """Storage operations required by :class:`MemoryManager`."""

    @property
    def database_path(self) -> Path | None:
        """Return the database path, or ``None`` for an in-memory repository."""

        ...

    @property
    def closed(self) -> bool:
        """Return whether the repository has released its connection."""

        ...

    def upsert(
        self,
        category: MemoryCategoryLike,
        key: str,
        value: str,
    ) -> MemoryRecord:
        """Create or update the memory identified by category and normalized key."""

        ...

    def get(self, category: MemoryCategoryLike, key: str) -> MemoryRecord | None:
        """Return one memory, or ``None`` when it does not exist."""

        ...

    def list(self, category: MemoryCategoryLike | None = None) -> list[MemoryRecord]:
        """Return memories, optionally limited to a category."""

        ...

    def search(
        self,
        query: str,
        category: MemoryCategoryLike | None = None,
        *,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        """Search keys and values for a literal, case-insensitive substring."""

        ...

    def delete(self, category: MemoryCategoryLike, key: str) -> bool:
        """Delete one memory and report whether it existed."""

        ...

    def clear(self, category: MemoryCategoryLike | None = None) -> int:
        """Delete memories and return the number removed."""

        ...

    def count(self, category: MemoryCategoryLike | None = None) -> int:
        """Count memories, optionally in one category."""

        ...

    def close(self) -> None:
        """Release repository resources. Calling this repeatedly is safe."""

        ...

    def __enter__(self) -> MemoryRepository:
        """Return this repository as a context manager."""

        ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the repository when leaving a context."""

        ...
