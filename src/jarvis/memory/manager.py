"""Application-facing coordination for explicit persistent memory."""

from __future__ import annotations

from jarvis.memory.models import MemoryRecord
from jarvis.memory.repository import MemoryCategoryLike, MemoryRepository


class MemoryManager:
    """Expose deliberate memory operations over an injected repository.

    This class has no conversation-ingestion hook and never writes implicitly. A
    caller must invoke :meth:`remember` after user intent and policy checks have
    explicitly approved persistence.
    """

    def __init__(self, repository: MemoryRepository) -> None:
        self._repository = repository

    @property
    def repository(self) -> MemoryRepository:
        """Return the injected repository."""

        return self._repository

    def remember(
        self,
        category: MemoryCategoryLike,
        key: str,
        value: str,
    ) -> MemoryRecord:
        """Explicitly create or update one approved memory."""

        return self._repository.upsert(category, key, value)

    def recall(self, category: MemoryCategoryLike, key: str) -> MemoryRecord | None:
        """Recall one memory by category and key."""

        return self._repository.get(category, key)

    def list(self, category: MemoryCategoryLike | None = None) -> list[MemoryRecord]:
        """List stored memories, optionally in one category."""

        return self._repository.list(category)

    def search(
        self,
        query: str,
        category: MemoryCategoryLike | None = None,
        *,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        """Search stored memory keys and values."""

        return self._repository.search(query, category, limit=limit)

    def forget(self, category: MemoryCategoryLike, key: str) -> bool:
        """Explicitly forget one memory."""

        return self._repository.delete(category, key)

    def clear(self, category: MemoryCategoryLike | None = None) -> int:
        """Explicitly clear a category or the entire memory store."""

        return self._repository.clear(category)

    def count(self, category: MemoryCategoryLike | None = None) -> int:
        """Count stored memories, optionally in one category."""

        return self._repository.count(category)
