"""SQLite-backed persistent storage for JARVIS memory."""

from __future__ import annotations

import os
import sqlite3
import stat
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from threading import RLock
from types import TracebackType

from jarvis.memory.errors import (
    MemoryClosedError,
    MemoryRepositoryError,
    MemorySchemaError,
    MemoryValidationError,
)
from jarvis.memory.models import MemoryCategory, MemoryRecord
from jarvis.memory.repository import MemoryCategoryLike

_LATEST_SCHEMA_VERSION = 1

_CREATE_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS memory_schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""

_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL CHECK (
                category IN ('preferences', 'facts', 'projects', 'aliases')
            ),
            key TEXT NOT NULL,
            normalized_key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (category, normalized_key)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_memories_category
        ON memories (category)
        """,
    ),
}

_SELECT_COLUMNS = "id, category, key, value, created_at, updated_at"


class SQLiteMemoryRepository:
    """Persist individual memory records in a local SQLite database.

    A repository owns one connection. Calls on a repository instance are serialized
    with a re-entrant lock, and separate instances coordinate through SQLite's WAL and
    busy-timeout support.
    """

    def __init__(self, database: str | PathLike[str]) -> None:
        self._lock = RLock()
        self._closed = False
        self._database_path, connection_target = self._prepare_database(database)
        try:
            self._connection = sqlite3.connect(
                connection_target,
                timeout=10,
                check_same_thread=False,
            )
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA busy_timeout = 10000")
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._initialize_schema()
            self._harden_filesystem_permissions()
        except MemorySchemaError:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            self._closed = True
            raise
        except sqlite3.Error as exc:
            connection = getattr(self, "_connection", None)
            if connection is not None:
                connection.close()
            self._closed = True
            raise MemoryRepositoryError("Could not open the memory database") from exc

    @staticmethod
    def _prepare_database(database: str | PathLike[str]) -> tuple[Path | None, str]:
        raw_path = str(database)
        if not raw_path.strip():
            raise MemoryValidationError("The memory database path cannot be empty")
        if raw_path == ":memory:":
            return None, raw_path

        path = Path(database).expanduser()
        try:
            parent_existed = path.parent.exists()
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not path.parent.is_dir():
                raise OSError("database parent is not a directory")
            if not parent_existed:
                os.chmod(path.parent, 0o700)
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                if candidate.is_symlink():
                    raise OSError("database files cannot be symbolic links")
            if path.exists() and not path.is_file():
                raise OSError("database target is not a regular file")
            flags = os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                os.close(descriptor)
                raise OSError("database target is not a regular file")
            os.close(descriptor)
            os.chmod(path, 0o600)
        except OSError as exc:
            raise MemoryRepositoryError(
                f"Could not create the memory database directory: {path.parent}"
            ) from exc
        return path, str(path)

    def _harden_filesystem_permissions(self) -> None:
        """Keep database, WAL, and shared-memory files private where supported."""

        if self._database_path is None:
            return
        try:
            for candidate in (
                self._database_path,
                Path(f"{self._database_path}-wal"),
                Path(f"{self._database_path}-shm"),
            ):
                if candidate.exists():
                    if candidate.is_symlink() or not candidate.is_file():
                        raise OSError("database sidecar is not a regular file")
                    os.chmod(candidate, 0o600)
        except OSError as exc:
            raise MemoryRepositoryError("Could not secure the memory database files") from exc

    @property
    def database_path(self) -> Path | None:
        """Return the file-backed database path, or ``None`` for ``:memory:``."""

        return self._database_path

    @property
    def closed(self) -> bool:
        """Return whether this repository's connection has been closed."""

        with self._lock:
            return self._closed

    def _initialize_schema(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(_CREATE_VERSION_TABLE)
            row = self._connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM memory_schema_versions"
            ).fetchone()
            current_version = int(row["version"])
            if current_version > _LATEST_SCHEMA_VERSION:
                raise MemorySchemaError(
                    "The memory database was created by a newer JARVIS version "
                    f"(schema {current_version})"
                )

            for version in range(current_version + 1, _LATEST_SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[version]:
                    self._connection.execute(statement)
                self._connection.execute(
                    "INSERT INTO memory_schema_versions (version, applied_at) VALUES (?, ?)",
                    (version, _utc_timestamp()),
                )
            self._connection.commit()
        except (sqlite3.Error, MemorySchemaError):
            self._connection.rollback()
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise MemoryClosedError("The memory repository is closed")

    def upsert(
        self,
        category: MemoryCategoryLike,
        key: str,
        value: str,
    ) -> MemoryRecord:
        """Create or update a memory using a normalized category/key identity."""

        parsed_category = _parse_category(category)
        display_key, normalized_key = _prepare_key(key)
        stored_value = _prepare_value(value)
        timestamp = _utc_timestamp()

        with self._lock:
            self._ensure_open()
            try:
                with self._connection:
                    self._connection.execute(
                        """
                        INSERT INTO memories (
                            category, key, normalized_key, value, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT (category, normalized_key) DO UPDATE SET
                            key = excluded.key,
                            value = excluded.value,
                            updated_at = excluded.updated_at
                        """,
                        (
                            parsed_category.value,
                            display_key,
                            normalized_key,
                            stored_value,
                            timestamp,
                            timestamp,
                        ),
                    )
                    row = self._connection.execute(
                        f"SELECT {_SELECT_COLUMNS} FROM memories "
                        "WHERE category = ? AND normalized_key = ?",
                        (parsed_category.value, normalized_key),
                    ).fetchone()
            except sqlite3.Error as exc:
                raise MemoryRepositoryError("Could not save the memory") from exc

        if row is None:  # Defensive: the insert and select above are one transaction.
            raise MemoryRepositoryError("The saved memory could not be read back")
        return _record_from_row(row)

    def get(self, category: MemoryCategoryLike, key: str) -> MemoryRecord | None:
        """Return the memory identified by category/key, if present."""

        parsed_category = _parse_category(category)
        _, normalized_key = _prepare_key(key)
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM memories "
                    "WHERE category = ? AND normalized_key = ?",
                    (parsed_category.value, normalized_key),
                ).fetchone()
            except sqlite3.Error as exc:
                raise MemoryRepositoryError("Could not read the memory") from exc
        return None if row is None else _record_from_row(row)

    def list(self, category: MemoryCategoryLike | None = None) -> list[MemoryRecord]:
        """Return all memories in deterministic category/key order."""

        parsed_category = None if category is None else _parse_category(category)
        with self._lock:
            self._ensure_open()
            try:
                if parsed_category is None:
                    rows = self._connection.execute(
                        f"SELECT {_SELECT_COLUMNS} FROM memories ORDER BY category, normalized_key"
                    ).fetchall()
                else:
                    rows = self._connection.execute(
                        f"SELECT {_SELECT_COLUMNS} FROM memories "
                        "WHERE category = ? ORDER BY normalized_key",
                        (parsed_category.value,),
                    ).fetchall()
            except sqlite3.Error as exc:
                raise MemoryRepositoryError("Could not list memories") from exc
        return [_record_from_row(row) for row in rows]

    def search(
        self,
        query: str,
        category: MemoryCategoryLike | None = None,
        *,
        limit: int | None = None,
    ) -> list[MemoryRecord]:
        """Search keys and values for a literal, case-insensitive substring."""

        pattern = f"%{_escape_like(_prepare_query(query))}%"
        parsed_category = None if category is None else _parse_category(category)
        validated_limit = _prepare_limit(limit)

        with self._lock:
            self._ensure_open()
            try:
                if parsed_category is None:
                    sql = (
                        f"SELECT {_SELECT_COLUMNS} FROM memories "
                        "WHERE (key LIKE ? ESCAPE '\\' COLLATE NOCASE "
                        "OR value LIKE ? ESCAPE '\\' COLLATE NOCASE) "
                        "ORDER BY updated_at DESC, id DESC"
                    )
                    parameters: tuple[object, ...] = (pattern, pattern)
                else:
                    sql = (
                        f"SELECT {_SELECT_COLUMNS} FROM memories "
                        "WHERE category = ? AND "
                        "(key LIKE ? ESCAPE '\\' COLLATE NOCASE "
                        "OR value LIKE ? ESCAPE '\\' COLLATE NOCASE) "
                        "ORDER BY updated_at DESC, id DESC"
                    )
                    parameters = (parsed_category.value, pattern, pattern)

                if validated_limit is not None:
                    sql += " LIMIT ?"
                    parameters += (validated_limit,)
                rows = self._connection.execute(sql, parameters).fetchall()
            except sqlite3.Error as exc:
                raise MemoryRepositoryError("Could not search memories") from exc
        return [_record_from_row(row) for row in rows]

    def delete(self, category: MemoryCategoryLike, key: str) -> bool:
        """Delete one category/key memory and report whether it existed."""

        parsed_category = _parse_category(category)
        _, normalized_key = _prepare_key(key)
        with self._lock:
            self._ensure_open()
            try:
                with self._connection:
                    cursor = self._connection.execute(
                        "DELETE FROM memories WHERE category = ? AND normalized_key = ?",
                        (parsed_category.value, normalized_key),
                    )
            except sqlite3.Error as exc:
                raise MemoryRepositoryError("Could not delete the memory") from exc
        return cursor.rowcount > 0

    def clear(self, category: MemoryCategoryLike | None = None) -> int:
        """Delete all memories, or all in one category, returning the count."""

        parsed_category = None if category is None else _parse_category(category)
        with self._lock:
            self._ensure_open()
            try:
                with self._connection:
                    if parsed_category is None:
                        cursor = self._connection.execute("DELETE FROM memories")
                    else:
                        cursor = self._connection.execute(
                            "DELETE FROM memories WHERE category = ?",
                            (parsed_category.value,),
                        )
            except sqlite3.Error as exc:
                raise MemoryRepositoryError("Could not clear memories") from exc
        return cursor.rowcount

    def count(self, category: MemoryCategoryLike | None = None) -> int:
        """Return the number of stored memories, optionally in one category."""

        parsed_category = None if category is None else _parse_category(category)
        with self._lock:
            self._ensure_open()
            try:
                if parsed_category is None:
                    row = self._connection.execute(
                        "SELECT COUNT(*) AS total FROM memories"
                    ).fetchone()
                else:
                    row = self._connection.execute(
                        "SELECT COUNT(*) AS total FROM memories WHERE category = ?",
                        (parsed_category.value,),
                    ).fetchone()
            except sqlite3.Error as exc:
                raise MemoryRepositoryError("Could not count memories") from exc
        return int(row["total"])

    def close(self) -> None:
        """Close this repository. The operation is idempotent."""

        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> SQLiteMemoryRepository:
        """Return this open repository."""

        with self._lock:
            self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close the repository when leaving a context."""

        self.close()


def _parse_category(category: MemoryCategoryLike) -> MemoryCategory:
    try:
        return MemoryCategory(category)
    except (TypeError, ValueError) as exc:
        choices = ", ".join(member.value for member in MemoryCategory)
        raise MemoryValidationError(f"Unknown memory category; choose one of: {choices}") from exc


def _prepare_key(key: str) -> tuple[str, str]:
    if not isinstance(key, str):
        raise MemoryValidationError("A memory key must be text")
    display_key = " ".join(key.split())
    if not display_key:
        raise MemoryValidationError("A memory key cannot be empty")
    return display_key, display_key.casefold()


def _prepare_value(value: str) -> str:
    if not isinstance(value, str):
        raise MemoryValidationError("A memory value must be text")
    return value


def _prepare_query(query: str) -> str:
    if not isinstance(query, str):
        raise MemoryValidationError("A memory search query must be text")
    normalized = query.strip()
    if not normalized:
        raise MemoryValidationError("A memory search query cannot be empty")
    return normalized


def _prepare_limit(limit: int | None) -> int | None:
    if limit is None:
        return None
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise MemoryValidationError("A memory search limit must be a positive integer")
    return limit


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


def _record_from_row(row: sqlite3.Row) -> MemoryRecord:
    try:
        created_at = datetime.fromisoformat(row["created_at"])
        updated_at = datetime.fromisoformat(row["updated_at"])
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        return MemoryRecord(
            id=int(row["id"]),
            category=MemoryCategory(row["category"]),
            key=str(row["key"]),
            value=str(row["value"]),
            created_at=created_at.astimezone(UTC),
            updated_at=updated_at.astimezone(UTC),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MemoryRepositoryError("The memory database contains an invalid record") from exc
