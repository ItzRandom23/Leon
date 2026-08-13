"""Persistence for desired plugin enablement state."""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from jarvis.plugins.models import PluginStateError, validate_plugin_id

_SCHEMA_VERSION = 1


@runtime_checkable
class PluginStateRepository(Protocol):
    """Minimal injectable persistence contract used by :class:`PluginManager`."""

    def is_enabled(self, plugin_id: str) -> bool:
        """Return the desired enablement state, defaulting to disabled."""

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        """Persist the desired enablement state."""

    def enabled_plugins(self) -> tuple[str, ...]:
        """Return all explicitly enabled plugin identifiers."""


class InMemoryPluginStateRepository:
    """Small deterministic repository for embedded use and tests."""

    def __init__(self, initial: dict[str, bool] | None = None) -> None:
        self._states: dict[str, bool] = {}
        for plugin_id, enabled in (initial or {}).items():
            self.set_enabled(plugin_id, enabled)

    def is_enabled(self, plugin_id: str) -> bool:
        return self._states.get(validate_plugin_id(plugin_id), False)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("plugin enabled state must be a boolean")
        self._states[validate_plugin_id(plugin_id)] = enabled

    def enabled_plugins(self) -> tuple[str, ...]:
        return tuple(sorted(name for name, enabled in self._states.items() if enabled))


class SQLitePluginStateRepository:
    """Migration-versioned SQLite plugin state with private on-disk permissions."""

    def __init__(self, database: str | Path) -> None:
        self._database = Path(database).expanduser()
        _reject_symlink_target(self._database)
        self._lock = threading.RLock()
        self._initialize()

    @property
    def database(self) -> Path:
        """Return the configured database path."""

        return self._database

    def is_enabled(self, plugin_id: str) -> bool:
        normalized = validate_plugin_id(plugin_id)
        try:
            with self._connection() as connection:
                row = connection.execute(
                    "SELECT enabled FROM plugin_state WHERE plugin_id = ?",
                    (normalized,),
                ).fetchone()
        except (OSError, sqlite3.Error) as exc:
            raise PluginStateError("could not read plugin enablement state") from exc
        return bool(row[0]) if row is not None else False

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        normalized = validate_plugin_id(plugin_id)
        if not isinstance(enabled, bool):
            raise TypeError("plugin enabled state must be a boolean")
        updated_at = datetime.now(UTC).isoformat()
        try:
            with self._connection() as connection:
                connection.execute(
                    """
                    INSERT INTO plugin_state (plugin_id, enabled, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(plugin_id) DO UPDATE SET
                        enabled = excluded.enabled,
                        updated_at = excluded.updated_at
                    """,
                    (normalized, int(enabled), updated_at),
                )
                connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise PluginStateError("could not persist plugin enablement state") from exc
        self._harden_file()

    def enabled_plugins(self) -> tuple[str, ...]:
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    "SELECT plugin_id FROM plugin_state WHERE enabled = 1 ORDER BY plugin_id"
                ).fetchall()
        except (OSError, sqlite3.Error) as exc:
            raise PluginStateError("could not list enabled plugins") from exc
        return tuple(str(row[0]) for row in rows)

    def _initialize(self) -> None:
        try:
            self._database.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            _restrict_permissions(self._database.parent, 0o700)
            if not self._database.exists():
                self._database.touch(mode=0o600, exist_ok=False)
            self._harden_file()
            with self._connection() as connection:
                version = int(connection.execute("PRAGMA user_version").fetchone()[0])
                if version > _SCHEMA_VERSION:
                    raise PluginStateError(
                        f"plugin state schema {version} is newer than supported "
                        f"schema {_SCHEMA_VERSION}"
                    )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS plugin_state (
                        plugin_id TEXT PRIMARY KEY NOT NULL,
                        enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
                connection.commit()
            self._harden_file()
        except PluginStateError:
            raise
        except (OSError, sqlite3.Error) as exc:
            raise PluginStateError("could not initialize plugin state database") from exc

    def _connection(self) -> _LockedConnection:
        return _LockedConnection(self._database, self._lock)

    def _harden_file(self) -> None:
        _restrict_permissions(self._database, 0o600)


class _LockedConnection:
    def __init__(self, database: Path, lock: threading.RLock) -> None:
        self._database = database
        self._lock = lock
        self._connection: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self._lock.acquire()
        try:
            self._connection = sqlite3.connect(self._database, timeout=5.0)
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA busy_timeout = 5000")
            return self._connection
        except Exception:
            self._lock.release()
            raise

    def __exit__(self, *args: object) -> None:
        try:
            if self._connection is not None:
                self._connection.close()
        finally:
            self._lock.release()


def _restrict_permissions(path: Path, mode: int) -> None:
    """Best-effort POSIX permissions; Windows ACLs remain platform-managed."""

    try:
        os.chmod(path, mode)
    except OSError:
        if os.name != "nt":
            raise


def _reject_symlink_target(database: Path) -> None:
    """Reject a database path routed through a symbolic-link parent."""

    current = database
    while True:
        if current.is_symlink():
            raise PluginStateError("plugin state database path cannot use symbolic links")
        if current.parent == current:
            return
        current = current.parent
