"""Tests for explicit, persistent, repository-backed memory."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from datetime import UTC
from pathlib import Path

import pytest

from jarvis.memory import (
    MemoryCategory,
    MemoryClosedError,
    MemoryManager,
    MemoryRepository,
    MemoryValidationError,
    SQLiteMemoryRepository,
)


def test_memory_categories_are_stable_string_values() -> None:
    assert set(MemoryCategory) == {
        MemoryCategory.PREFERENCES,
        MemoryCategory.FACTS,
        MemoryCategory.PROJECTS,
        MemoryCategory.ALIASES,
    }
    assert str(MemoryCategory.PREFERENCES) == "preferences"
    assert MemoryCategory("  FACTS ") is MemoryCategory.FACTS


def test_memory_record_is_immutable() -> None:
    with SQLiteMemoryRepository(":memory:") as repository:
        record = repository.upsert(MemoryCategory.FACTS, "answer", "42")

    with pytest.raises(FrozenInstanceError):
        record.value = "different"  # type: ignore[misc]


def test_memories_persist_across_repository_instances(tmp_path: Path) -> None:
    database = tmp_path / "memory.db"
    with SQLiteMemoryRepository(database) as first:
        saved = first.upsert(MemoryCategory.PROJECTS, "main directory", r"D:\Projects")
        assert saved.created_at.tzinfo is UTC
        assert saved.updated_at.tzinfo is UTC

    with SQLiteMemoryRepository(database) as second:
        recalled = second.get("projects", "MAIN DIRECTORY")

    assert recalled == saved


def test_normalized_category_and_key_upsert_without_cross_category_collision(
    tmp_path: Path,
) -> None:
    with SQLiteMemoryRepository(tmp_path / "memory.db") as repository:
        original = repository.upsert(" Facts ", " Favorite   Editor ", "Vim")
        updated = repository.upsert(MemoryCategory.FACTS, "favorite editor", "VS Code")
        preference = repository.upsert("PREFERENCES", "favorite editor", "dark theme")

        assert repository.count() == 2
        assert repository.count("facts") == 1
        assert repository.get("FACTS", "  FAVORITE  EDITOR ") == updated

    assert updated.id == original.id
    assert updated.created_at == original.created_at
    assert updated.updated_at >= original.updated_at
    assert updated.value == "VS Code"
    assert preference.category is MemoryCategory.PREFERENCES


def test_list_and_search_support_categories_literal_wildcards_and_limits(
    tmp_path: Path,
) -> None:
    manager = MemoryManager(SQLiteMemoryRepository(tmp_path / "memory.db"))
    try:
        manager.remember("aliases", "my_code%folder", r"D:\Projects")
        manager.remember("projects", "music bot", r"D:\Projects\MusicBot")
        manager.remember("facts", "under_score", "literal marker")

        assert [record.key for record in manager.list("projects")] == ["music bot"]
        assert {record.key for record in manager.search("projects")} == {
            "my_code%folder",
            "music bot",
        }
        assert [record.key for record in manager.search("%", limit=1)] == ["my_code%folder"]
        assert [record.key for record in manager.search("_", "facts")] == ["under_score"]
    finally:
        manager.repository.close()


def test_forget_and_clear_are_explicit_and_report_affected_rows(tmp_path: Path) -> None:
    with SQLiteMemoryRepository(tmp_path / "memory.db") as repository:
        manager = MemoryManager(repository)
        manager.remember("facts", "one", "1")
        manager.remember("facts", "two", "2")
        manager.remember("projects", "three", "3")

        assert manager.forget("facts", "one") is True
        assert manager.forget("facts", "one") is False
        assert manager.clear("facts") == 1
        assert manager.count() == 1
        assert manager.clear() == 1
        assert manager.clear() == 0


def test_bound_values_cannot_inject_sql(tmp_path: Path) -> None:
    payload = "x'); DROP TABLE memories; --"
    with SQLiteMemoryRepository(tmp_path / "memory.db") as repository:
        saved = repository.upsert("aliases", payload, payload)

        assert repository.get("aliases", payload) == saved
        assert repository.search("DROP TABLE") == [saved]
        assert repository.count() == 1


def test_database_parent_directory_is_created(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "jarvis" / "memory.db"

    with SQLiteMemoryRepository(database) as repository:
        assert repository.database_path == database
        repository.upsert("facts", "created", "yes")

    assert database.is_file()


def test_managers_use_injected_storage_without_global_state(tmp_path: Path) -> None:
    with (
        SQLiteMemoryRepository(tmp_path / "one.db") as first_repository,
        SQLiteMemoryRepository(tmp_path / "two.db") as second_repository,
    ):
        first = MemoryManager(first_repository)
        second = MemoryManager(second_repository)
        first.remember("facts", "owner", "first")
        second.remember("facts", "owner", "second")

        assert first.recall("facts", "owner").value == "first"  # type: ignore[union-attr]
        assert second.recall("facts", "owner").value == "second"  # type: ignore[union-attr]


def test_manager_does_not_save_without_explicit_remember(tmp_path: Path) -> None:
    with SQLiteMemoryRepository(tmp_path / "memory.db") as repository:
        manager = MemoryManager(repository)

        assert manager.recall("facts", "a conversation detail") is None
        assert manager.list() == []
        assert manager.count() == 0


def test_repository_is_thread_safe_for_one_shared_instance(tmp_path: Path) -> None:
    with SQLiteMemoryRepository(tmp_path / "memory.db") as repository:
        with ThreadPoolExecutor(max_workers=4) as executor:
            records = list(
                executor.map(
                    lambda index: repository.upsert("facts", f"key {index}", str(index)),
                    range(20),
                )
            )

        assert len(records) == 20
        assert repository.count() == 20


def test_repository_protocol_and_closed_lifecycle(tmp_path: Path) -> None:
    repository = SQLiteMemoryRepository(tmp_path / "memory.db")

    assert isinstance(repository, MemoryRepository)
    repository.close()
    repository.close()
    assert repository.closed is True
    with pytest.raises(MemoryClosedError):
        repository.list()


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda repository: repository.upsert("unknown", "key", "value"), "category"),
        (lambda repository: repository.upsert("facts", "   ", "value"), "key"),
        (lambda repository: repository.upsert("facts", "key", 1), "value"),
        (lambda repository: repository.search("  "), "query"),
        (lambda repository: repository.search("key", limit=0), "limit"),
    ],
)
def test_invalid_inputs_raise_domain_errors(
    tmp_path: Path,
    operation: object,
    message: str,
) -> None:
    with SQLiteMemoryRepository(tmp_path / "memory.db") as repository:
        with pytest.raises(MemoryValidationError, match=message):
            operation(repository)  # type: ignore[operator]
