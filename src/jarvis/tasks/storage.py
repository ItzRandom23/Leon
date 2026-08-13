"""Versioned SQLite persistence for reminders."""

from __future__ import annotations

import builtins
import json
import os
import sqlite3
import stat
from collections.abc import Callable
from datetime import UTC, datetime
from os import PathLike
from pathlib import Path
from threading import RLock
from types import TracebackType
from typing import Any

from jarvis.tasks.errors import (
    ReminderClosedError,
    ReminderConflictError,
    ReminderRepositoryError,
    ReminderSchemaError,
    ReminderValidationError,
)
from jarvis.tasks.models import (
    Recurrence,
    Reminder,
    ReminderStatus,
    ScheduledAction,
    utc_datetime,
    validate_timezone,
)
from jarvis.tasks.recurrence import ReminderSchedule, next_occurrence

Clock = Callable[[], datetime]

_LATEST_SCHEMA_VERSION = 2
_CREATE_VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS reminder_schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""
_MIGRATIONS: dict[int, tuple[str, ...]] = {
    1: (
        """
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message TEXT NOT NULL,
            normalized_message TEXT NOT NULL,
            due_at TEXT NOT NULL,
            timezone TEXT NOT NULL,
            recurrence TEXT NOT NULL CHECK (
                recurrence IN ('once', 'daily', 'weekly', 'weekdays')
            ),
            status TEXT NOT NULL CHECK (
                status IN ('scheduled', 'cancelled', 'triggered')
            ),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_triggered_at TEXT,
            idempotency_key TEXT UNIQUE,
            scheduled_action_name TEXT,
            scheduled_action_arguments TEXT,
            CHECK (
                (scheduled_action_name IS NULL AND scheduled_action_arguments IS NULL)
                OR
                (scheduled_action_name IS NOT NULL AND scheduled_action_arguments IS NOT NULL)
            )
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_reminders_due
        ON reminders (status, due_at, id)
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_reminders_active_identity
        ON reminders (normalized_message, due_at, timezone, recurrence)
        WHERE status = 'scheduled'
        """,
    ),
    2: (
        "ALTER TABLE reminders ADD COLUMN claim_owner TEXT",
        "ALTER TABLE reminders ADD COLUMN claim_expires_at TEXT",
        "ALTER TABLE reminders ADD COLUMN delivery_started_at TEXT",
        """
        CREATE INDEX IF NOT EXISTS idx_reminders_claimable
        ON reminders (status, due_at, claim_expires_at, id)
        """,
    ),
}
_SELECT_COLUMNS = """
id, message, due_at, timezone, recurrence, status, created_at, updated_at,
last_triggered_at, idempotency_key, scheduled_action_name, scheduled_action_arguments
""".strip()


def _default_clock() -> datetime:
    return datetime.now(UTC)


class SQLiteReminderRepository:
    """Persist reminders in an isolated, private, versioned SQLite schema."""

    def __init__(
        self,
        database: str | PathLike[str],
        *,
        clock: Clock = _default_clock,
    ) -> None:
        if not callable(clock):
            raise TypeError("clock must be callable")
        self._clock = clock
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
            if self._database_path is not None:
                self._connection.execute("PRAGMA journal_mode = WAL")
            self._initialize_schema()
            self._harden_database_files()
        except ReminderSchemaError:
            self._close_after_failed_init()
            raise
        except sqlite3.Error as exc:
            self._close_after_failed_init()
            raise ReminderRepositoryError("Could not open the reminder database") from exc
        except OSError as exc:
            self._close_after_failed_init()
            raise ReminderRepositoryError("Could not secure the reminder database") from exc

    def _close_after_failed_init(self) -> None:
        connection = getattr(self, "_connection", None)
        if connection is not None:
            connection.close()
        self._closed = True

    @staticmethod
    def _prepare_database(database: str | PathLike[str]) -> tuple[Path | None, str]:
        raw = str(database)
        if not raw.strip():
            raise ReminderValidationError("The reminder database path cannot be empty")
        if raw == ":memory:":
            return None, raw

        path = Path(database).expanduser().absolute()
        parent = path.parent
        try:
            _reject_symlink_components(parent)
            missing = _missing_directories(parent)
            parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            for directory in reversed(missing):
                if directory.is_symlink() or not directory.is_dir():
                    raise OSError("database parent cannot contain symbolic links")
                os.chmod(directory, 0o700)
            _reject_symlink_components(parent)
            if not parent.is_dir():
                raise OSError("database parent is not a directory")

            for candidate in _database_files(path):
                if candidate.is_symlink():
                    raise OSError("database files cannot be symbolic links")
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode):
                    raise OSError("database target is not a regular file")
                secure_descriptor = getattr(os, "fchmod", None)
                if callable(secure_descriptor):
                    secure_descriptor(descriptor, 0o600)
            finally:
                os.close(descriptor)
            if path.is_symlink() or not path.is_file():
                raise OSError("database target is not a regular file")
        except OSError as exc:
            raise ReminderRepositoryError(
                f"Could not create a private reminder database at {path}"
            ) from exc
        return path, str(path)

    def _harden_database_files(self) -> None:
        if self._database_path is None:
            return
        for candidate in _database_files(self._database_path):
            if candidate.is_symlink():
                raise OSError("database sidecars cannot be symbolic links")
            if candidate.exists():
                metadata = candidate.stat(follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    raise OSError("database sidecar is not a regular file")
                os.chmod(candidate, 0o600, follow_symlinks=False)

    @property
    def database_path(self) -> Path | None:
        return self._database_path

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def _now(self) -> datetime:
        try:
            return utc_datetime(self._clock(), "clock result")
        except ReminderValidationError:
            raise
        except Exception as exc:
            raise ReminderValidationError("The reminder clock failed") from exc

    def _initialize_schema(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(_CREATE_VERSION_TABLE)
            row = self._connection.execute(
                "SELECT COALESCE(MAX(version), 0) AS version FROM reminder_schema_versions"
            ).fetchone()
            current = int(row["version"])
            if current > _LATEST_SCHEMA_VERSION:
                raise ReminderSchemaError(
                    "The reminder database was created by a newer JARVIS version "
                    f"(schema {current})"
                )
            for version in range(current + 1, _LATEST_SCHEMA_VERSION + 1):
                for statement in _MIGRATIONS[version]:
                    self._connection.execute(statement)
                self._connection.execute(
                    "INSERT INTO reminder_schema_versions (version, applied_at) VALUES (?, ?)",
                    (version, _timestamp(self._now())),
                )
            self._connection.commit()
        except (sqlite3.Error, ReminderSchemaError):
            self._connection.rollback()
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise ReminderClosedError("The reminder repository is closed")

    def create(
        self,
        message: str,
        due_at: datetime | ReminderSchedule,
        *,
        timezone: str = "UTC",
        recurrence: Recurrence | str = Recurrence.ONCE,
        idempotency_key: str | None = None,
        scheduled_action: ScheduledAction | None = None,
    ) -> Reminder:
        """Create a reminder, returning an existing identical request idempotently."""

        stored_message, normalized_message = _prepare_message(message)
        if isinstance(due_at, ReminderSchedule):
            if timezone != "UTC" or _parse_recurrence(recurrence) is not Recurrence.ONCE:
                raise ReminderValidationError(
                    "timezone and recurrence cannot override a ReminderSchedule"
                )
            due = due_at.due_at
            zone_name = due_at.timezone
            rule = due_at.recurrence
        else:
            due = utc_datetime(due_at, "due_at")
            zone_name = validate_timezone(timezone)
            rule = _parse_recurrence(recurrence)
        key = _prepare_idempotency_key(idempotency_key)
        if scheduled_action is not None and not isinstance(scheduled_action, ScheduledAction):
            raise ReminderValidationError("scheduled_action must be ScheduledAction metadata")
        action_name, action_arguments = _serialize_action(scheduled_action)
        now = self._now()
        parameters = (
            stored_message,
            normalized_message,
            _timestamp(due),
            zone_name,
            rule.value,
            ReminderStatus.SCHEDULED.value,
            _timestamp(now),
            _timestamp(now),
            key,
            action_name,
            action_arguments,
        )

        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                if key is not None:
                    existing = self._select_by_idempotency_key(key)
                    if existing is not None:
                        reminder = _reminder_from_row(existing)
                        if _same_create_request(
                            reminder, stored_message, due, zone_name, rule, scheduled_action
                        ):
                            self._connection.commit()
                            return reminder
                        raise ReminderConflictError(
                            "The idempotency key already belongs to a different reminder"
                        )
                duplicate = self._connection.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM reminders "
                    "WHERE normalized_message = ? AND due_at = ? AND timezone = ? "
                    "AND recurrence = ? AND status = 'scheduled'",
                    (normalized_message, _timestamp(due), zone_name, rule.value),
                ).fetchone()
                if duplicate is not None:
                    reminder = _reminder_from_row(duplicate)
                    if scheduled_action == reminder.scheduled_action:
                        self._connection.commit()
                        return reminder
                    raise ReminderConflictError(
                        "An active reminder already exists for the same occurrence"
                    )
                cursor = self._connection.execute(
                    """
                    INSERT INTO reminders (
                        message, normalized_message, due_at, timezone, recurrence,
                        status, created_at, updated_at, idempotency_key,
                        scheduled_action_name, scheduled_action_arguments
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    parameters,
                )
                created_id = cursor.lastrowid
                if created_id is None:
                    self._connection.rollback()
                    raise ReminderRepositoryError("The created reminder has no database identifier")
                row = self._select_by_id(int(created_id))
                self._connection.commit()
            except ReminderConflictError:
                self._connection.rollback()
                raise
            except sqlite3.IntegrityError as exc:
                self._connection.rollback()
                raise ReminderConflictError("A duplicate reminder was not created") from exc
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ReminderRepositoryError("Could not create the reminder") from exc
        if row is None:
            raise ReminderRepositoryError("The new reminder could not be read back")
        return _reminder_from_row(row)

    add = create
    create_reminder = create

    def _select_by_id(self, reminder_id: int) -> sqlite3.Row | None:
        return self._connection.execute(
            f"SELECT {_SELECT_COLUMNS} FROM reminders WHERE id = ?",
            (reminder_id,),
        ).fetchone()

    def _select_by_idempotency_key(self, key: str) -> sqlite3.Row | None:
        return self._connection.execute(
            f"SELECT {_SELECT_COLUMNS} FROM reminders WHERE idempotency_key = ?",
            (key,),
        ).fetchone()

    def get(self, reminder_id: int) -> Reminder | None:
        identifier = _prepare_id(reminder_id)
        with self._lock:
            self._ensure_open()
            try:
                row = self._select_by_id(identifier)
            except sqlite3.Error as exc:
                raise ReminderRepositoryError("Could not read the reminder") from exc
        return None if row is None else _reminder_from_row(row)

    get_reminder = get

    def list(
        self,
        status: ReminderStatus | str | None = None,
        *,
        limit: int | None = None,
    ) -> builtins.list[Reminder]:
        parsed_status = None if status is None else _parse_status(status)
        bounded_limit = _prepare_limit(limit)
        sql = f"SELECT {_SELECT_COLUMNS} FROM reminders"
        parameters: tuple[object, ...] = ()
        if parsed_status is not None:
            sql += " WHERE status = ?"
            parameters = (parsed_status.value,)
        sql += " ORDER BY due_at, id"
        if bounded_limit is not None:
            sql += " LIMIT ?"
            parameters += (bounded_limit,)
        with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(sql, parameters).fetchall()
            except sqlite3.Error as exc:
                raise ReminderRepositoryError("Could not list reminders") from exc
        return [_reminder_from_row(row) for row in rows]

    list_reminders = list

    def due(
        self, now: datetime | None = None, *, limit: int | None = None
    ) -> builtins.list[Reminder]:
        threshold = self._now() if now is None else utc_datetime(now, "now")
        return self._scheduled_before(threshold, inclusive=True, limit=limit)

    due_reminders = due

    def claim_due(
        self,
        now: datetime | None = None,
        *,
        owner: str,
        lease_until: datetime,
        limit: int | None = None,
    ) -> builtins.list[Reminder]:
        """Atomically lease due occurrences for one scheduler runner."""

        threshold = self._now() if now is None else utc_datetime(now, "now")
        expires = utc_datetime(lease_until, "lease_until")
        if expires <= threshold:
            raise ReminderValidationError("A reminder claim lease must expire in the future")
        claim_owner = _prepare_claim_owner(owner)
        bounded_limit = _prepare_limit(limit)
        sql = (
            "SELECT id FROM reminders "
            "WHERE status = ? AND due_at <= ? "
            "AND delivery_started_at IS NULL "
            "AND (claim_expires_at IS NULL OR claim_expires_at <= ?) "
            "ORDER BY due_at, id"
        )
        parameters: tuple[object, ...] = (
            ReminderStatus.SCHEDULED.value,
            _timestamp(threshold),
            _timestamp(threshold),
        )
        if bounded_limit is not None:
            sql += " LIMIT ?"
            parameters += (bounded_limit,)

        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                identifiers = [
                    int(row["id"]) for row in self._connection.execute(sql, parameters).fetchall()
                ]
                if not identifiers:
                    self._connection.commit()
                    return []
                placeholders = ", ".join("?" for _ in identifiers)
                self._connection.execute(
                    f"""
                    UPDATE reminders
                    SET claim_owner = ?, claim_expires_at = ?
                    WHERE id IN ({placeholders}) AND status = ?
                      AND delivery_started_at IS NULL
                      AND (claim_expires_at IS NULL OR claim_expires_at <= ?)
                    """,
                    (
                        claim_owner,
                        _timestamp(expires),
                        *identifiers,
                        ReminderStatus.SCHEDULED.value,
                        _timestamp(threshold),
                    ),
                )
                rows = self._connection.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM reminders "
                    f"WHERE id IN ({placeholders}) AND claim_owner = ? "
                    "ORDER BY due_at, id",
                    (*identifiers, claim_owner),
                ).fetchall()
                self._connection.commit()
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ReminderRepositoryError("Could not claim due reminders") from exc
        return [_reminder_from_row(row) for row in rows]

    def begin_delivery(
        self,
        reminder_id: int,
        *,
        owner: str,
        expected_due_at: datetime,
        started_at: datetime | None = None,
    ) -> bool:
        """Linearize cancellation against the start of an external notification."""

        identifier = _prepare_id(reminder_id)
        claim_owner = _prepare_claim_owner(owner)
        expected = utc_datetime(expected_due_at, "expected_due_at")
        started = self._now() if started_at is None else utc_datetime(started_at, "started_at")
        with self._lock:
            self._ensure_open()
            try:
                with self._connection:
                    cursor = self._connection.execute(
                        """
                        UPDATE reminders
                        SET delivery_started_at = ?, updated_at = ?
                        WHERE id = ? AND status = ? AND due_at = ?
                          AND claim_owner = ? AND claim_expires_at > ?
                          AND delivery_started_at IS NULL
                        """,
                        (
                            _timestamp(started),
                            _timestamp(started),
                            identifier,
                            ReminderStatus.SCHEDULED.value,
                            _timestamp(expected),
                            claim_owner,
                            _timestamp(started),
                        ),
                    )
            except sqlite3.Error as exc:
                raise ReminderRepositoryError("Could not begin reminder delivery") from exc
        return cursor.rowcount == 1

    def release_claim(
        self,
        reminder_id: int,
        *,
        owner: str,
        expected_due_at: datetime,
    ) -> bool:
        """Release a failed or abandoned occurrence for a later retry."""

        identifier = _prepare_id(reminder_id)
        claim_owner = _prepare_claim_owner(owner)
        expected = utc_datetime(expected_due_at, "expected_due_at")
        with self._lock:
            self._ensure_open()
            try:
                with self._connection:
                    cursor = self._connection.execute(
                        """
                        UPDATE reminders
                        SET claim_owner = NULL, claim_expires_at = NULL, updated_at = ?
                        WHERE id = ? AND status = ? AND due_at = ? AND claim_owner = ?
                          AND delivery_started_at IS NULL
                        """,
                        (
                            _timestamp(self._now()),
                            identifier,
                            ReminderStatus.SCHEDULED.value,
                            _timestamp(expected),
                            claim_owner,
                        ),
                    )
            except sqlite3.Error as exc:
                raise ReminderRepositoryError("Could not release reminder claim") from exc
        return cursor.rowcount == 1

    def mark_claim_triggered(
        self,
        reminder_id: int,
        *,
        owner: str,
        triggered_at: datetime | None = None,
        expected_due_at: datetime,
    ) -> Reminder:
        """Complete one successfully delivered, scheduler-owned occurrence."""

        return self._mark_triggered(
            reminder_id,
            triggered_at,
            expected_due_at=expected_due_at,
            claim_owner=_prepare_claim_owner(owner),
        )

    def missed(
        self,
        now: datetime | None = None,
        *,
        limit: int | None = None,
    ) -> builtins.list[Reminder]:
        threshold = self._now() if now is None else utc_datetime(now, "now")
        return self._scheduled_before(threshold, inclusive=False, limit=limit)

    missed_reminders = missed

    def _scheduled_before(
        self,
        threshold: datetime,
        *,
        inclusive: bool,
        limit: int | None,
    ) -> builtins.list[Reminder]:
        bounded_limit = _prepare_limit(limit)
        operator = "<=" if inclusive else "<"
        sql = (
            f"SELECT {_SELECT_COLUMNS} FROM reminders "
            f"WHERE status = ? AND due_at {operator} ? ORDER BY due_at, id"
        )
        parameters: tuple[object, ...] = (
            ReminderStatus.SCHEDULED.value,
            _timestamp(threshold),
        )
        if bounded_limit is not None:
            sql += " LIMIT ?"
            parameters += (bounded_limit,)
        with self._lock:
            self._ensure_open()
            try:
                rows = self._connection.execute(sql, parameters).fetchall()
            except sqlite3.Error as exc:
                raise ReminderRepositoryError("Could not query due reminders") from exc
        return [_reminder_from_row(row) for row in rows]

    def next_due(self) -> Reminder | None:
        with self._lock:
            self._ensure_open()
            try:
                row = self._connection.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM reminders "
                    "WHERE status = ? ORDER BY due_at, id LIMIT 1",
                    (ReminderStatus.SCHEDULED.value,),
                ).fetchone()
            except sqlite3.Error as exc:
                raise ReminderRepositoryError("Could not read the next reminder") from exc
        return None if row is None else _reminder_from_row(row)

    def cancel(self, reminder_id: int) -> Reminder | None:
        identifier = _prepare_id(reminder_id)
        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._select_by_id(identifier)
                if row is None:
                    self._connection.commit()
                    return None
                current = _reminder_from_row(row)
                if current.status is ReminderStatus.TRIGGERED:
                    raise ReminderConflictError("A triggered reminder cannot be cancelled")
                if current.status is ReminderStatus.SCHEDULED:
                    claim = self._connection.execute(
                        "SELECT delivery_started_at FROM reminders WHERE id = ?",
                        (identifier,),
                    ).fetchone()
                    now = self._now()
                    if claim is not None and claim["delivery_started_at"] is not None:
                        raise ReminderConflictError("The reminder notification has already started")
                    self._connection.execute(
                        """
                        UPDATE reminders
                        SET status = ?, updated_at = ?, claim_owner = NULL,
                            claim_expires_at = NULL, delivery_started_at = NULL
                        WHERE id = ?
                        """,
                        (
                            ReminderStatus.CANCELLED.value,
                            _timestamp(now),
                            identifier,
                        ),
                    )
                updated = self._select_by_id(identifier)
                self._connection.commit()
            except ReminderConflictError:
                self._connection.rollback()
                raise
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ReminderRepositoryError("Could not cancel the reminder") from exc
        return None if updated is None else _reminder_from_row(updated)

    cancel_reminder = cancel

    def edit_scheduled(
        self,
        reminder_id: int,
        *,
        message: str,
        due_at: datetime,
        expected_message: str,
        expected_due_at: datetime,
    ) -> Reminder:
        """Atomically edit an unstarted scheduled occurrence.

        The expected values bind consent to the exact reminder the caller saw.
        Timezone, recurrence, idempotency, and inert scheduled-action metadata are
        deliberately preserved.
        """

        identifier = _prepare_id(reminder_id)
        stored_message, normalized_message = _prepare_message(message)
        # Validate without normalizing: consent is bound to the exact displayed
        # message, not merely a whitespace-equivalent value.
        _prepare_message(expected_message)
        expected_stored_message = expected_message
        new_due = utc_datetime(due_at, "due_at")
        expected_due = utc_datetime(expected_due_at, "expected_due_at")
        now = self._now()

        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._select_by_id(identifier)
                if row is None:
                    raise ReminderValidationError(f"No reminder exists with id {identifier}")
                current = _reminder_from_row(row)
                if current.status is not ReminderStatus.SCHEDULED:
                    raise ReminderConflictError("Only a scheduled reminder can be edited")
                delivery = self._connection.execute(
                    "SELECT delivery_started_at FROM reminders WHERE id = ?",
                    (identifier,),
                ).fetchone()
                if delivery is not None and delivery["delivery_started_at"] is not None:
                    raise ReminderConflictError("The reminder notification has already started")
                if current.message != expected_stored_message or current.due_at != expected_due:
                    raise ReminderConflictError(
                        "The reminder changed after it was selected; review it again"
                    )

                cursor = self._connection.execute(
                    """
                    UPDATE reminders
                    SET message = ?, normalized_message = ?, due_at = ?, updated_at = ?,
                        claim_owner = NULL, claim_expires_at = NULL,
                        delivery_started_at = NULL
                    WHERE id = ? AND status = ? AND message = ? AND due_at = ?
                      AND delivery_started_at IS NULL
                    """,
                    (
                        stored_message,
                        normalized_message,
                        _timestamp(new_due),
                        _timestamp(now),
                        identifier,
                        ReminderStatus.SCHEDULED.value,
                        expected_stored_message,
                        _timestamp(expected_due),
                    ),
                )
                if cursor.rowcount != 1:
                    raise ReminderConflictError(
                        "The reminder changed after it was selected; review it again"
                    )
                updated = self._select_by_id(identifier)
                self._connection.commit()
            except (ReminderConflictError, ReminderValidationError):
                self._connection.rollback()
                raise
            except sqlite3.IntegrityError as exc:
                self._connection.rollback()
                raise ReminderConflictError(
                    "An active reminder already exists for the edited occurrence"
                ) from exc
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ReminderRepositoryError("Could not edit the reminder") from exc
        if updated is None:
            raise ReminderRepositoryError("The edited reminder could not be read back")
        return _reminder_from_row(updated)

    reschedule = edit_scheduled

    def delete(self, reminder_id: int) -> bool:
        identifier = _prepare_id(reminder_id)
        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                claim = self._connection.execute(
                    "SELECT delivery_started_at FROM reminders WHERE id = ?",
                    (identifier,),
                ).fetchone()
                if claim is not None and claim["delivery_started_at"] is not None:
                    raise ReminderConflictError("The reminder notification has already started")
                cursor = self._connection.execute(
                    "DELETE FROM reminders WHERE id = ?", (identifier,)
                )
                self._connection.commit()
            except ReminderConflictError:
                self._connection.rollback()
                raise
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ReminderRepositoryError("Could not delete the reminder") from exc
        return cursor.rowcount > 0

    delete_reminder = delete

    def mark_triggered(
        self,
        reminder_id: int,
        triggered_at: datetime | None = None,
        *,
        expected_due_at: datetime | None = None,
    ) -> Reminder:
        return self._mark_triggered(
            reminder_id,
            triggered_at,
            expected_due_at=expected_due_at,
            claim_owner=None,
        )

    def _mark_triggered(
        self,
        reminder_id: int,
        triggered_at: datetime | None = None,
        *,
        expected_due_at: datetime | None = None,
        claim_owner: str | None,
    ) -> Reminder:
        identifier = _prepare_id(reminder_id)
        triggered = (
            self._now() if triggered_at is None else utc_datetime(triggered_at, "triggered_at")
        )
        expected = (
            None if expected_due_at is None else utc_datetime(expected_due_at, "expected_due_at")
        )
        with self._lock:
            self._ensure_open()
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                row = self._select_by_id(identifier)
                if row is None:
                    raise ReminderValidationError(f"No reminder exists with id {identifier}")
                reminder = _reminder_from_row(row)
                if reminder.status is ReminderStatus.CANCELLED:
                    raise ReminderConflictError("A cancelled reminder cannot be triggered")
                if reminder.status is ReminderStatus.TRIGGERED:
                    self._connection.commit()
                    return reminder
                if expected is not None and reminder.due_at != expected:
                    if reminder.last_triggered_at == triggered:
                        self._connection.commit()
                        return reminder
                    raise ReminderConflictError("The reminder occurrence has already changed")
                if triggered < reminder.due_at:
                    raise ReminderValidationError(
                        "A reminder cannot be marked triggered before it is due"
                    )

                claim = self._connection.execute(
                    "SELECT claim_owner, claim_expires_at, delivery_started_at "
                    "FROM reminders WHERE id = ?",
                    (identifier,),
                ).fetchone()
                assert claim is not None
                stored_owner = claim["claim_owner"]
                if claim_owner is None:
                    if stored_owner is not None:
                        raise ReminderConflictError("The reminder occurrence is claimed")
                elif stored_owner != claim_owner or claim["delivery_started_at"] is None:
                    raise ReminderConflictError("The reminder claim is no longer active")

                next_due = next_occurrence(
                    reminder.due_at,
                    reminder.recurrence,
                    reminder.timezone,
                    after=max(reminder.due_at, triggered),
                )
                status = ReminderStatus.TRIGGERED if next_due is None else ReminderStatus.SCHEDULED
                stored_due = reminder.due_at if next_due is None else next_due
                self._connection.execute(
                    """
                    UPDATE reminders
                    SET status = ?, due_at = ?, last_triggered_at = ?, updated_at = ?,
                        claim_owner = NULL, claim_expires_at = NULL,
                        delivery_started_at = NULL
                    WHERE id = ? AND status = ?
                    """,
                    (
                        status.value,
                        _timestamp(stored_due),
                        _timestamp(triggered),
                        _timestamp(triggered),
                        identifier,
                        ReminderStatus.SCHEDULED.value,
                    ),
                )
                updated = self._select_by_id(identifier)
                self._connection.commit()
            except (ReminderConflictError, ReminderValidationError):
                self._connection.rollback()
                raise
            except sqlite3.IntegrityError as exc:
                self._connection.rollback()
                raise ReminderConflictError(
                    "The next occurrence conflicts with another active reminder"
                ) from exc
            except sqlite3.Error as exc:
                self._connection.rollback()
                raise ReminderRepositoryError("Could not mark the reminder triggered") from exc
        if updated is None:
            raise ReminderRepositoryError("The triggered reminder could not be read back")
        return _reminder_from_row(updated)

    def count(self, status: ReminderStatus | str | None = None) -> int:
        parsed_status = None if status is None else _parse_status(status)
        with self._lock:
            self._ensure_open()
            try:
                if parsed_status is None:
                    row = self._connection.execute(
                        "SELECT COUNT(*) AS total FROM reminders"
                    ).fetchone()
                else:
                    row = self._connection.execute(
                        "SELECT COUNT(*) AS total FROM reminders WHERE status = ?",
                        (parsed_status.value,),
                    ).fetchone()
            except sqlite3.Error as exc:
                raise ReminderRepositoryError("Could not count reminders") from exc
        return int(row["total"])

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._connection.close()
                self._closed = True

    def __enter__(self) -> SQLiteReminderRepository:
        with self._lock:
            self._ensure_open()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()


SQLiteTaskRepository = SQLiteReminderRepository


def _database_files(path: Path) -> tuple[Path, Path, Path]:
    return path, Path(f"{path}-wal"), Path(f"{path}-shm")


def _missing_directories(parent: Path) -> list[Path]:
    missing: list[Path] = []
    current = parent
    while not current.exists():
        if current.is_symlink():
            raise OSError("database parents cannot be symbolic links")
        missing.append(current)
        if current == current.parent:
            break
        current = current.parent
    return missing


def _reject_symlink_components(path: Path) -> None:
    chain = (path, *path.parents)
    for component in reversed(chain):
        if component.is_symlink():
            raise OSError("database paths cannot contain symbolic links")


def _prepare_id(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ReminderValidationError("A reminder id must be a positive integer")
    return value


def _prepare_message(value: str) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ReminderValidationError("A reminder message must be text")
    message = " ".join(value.split())
    if not message or len(message) > 2_000:
        raise ReminderValidationError("A reminder message must contain 1 to 2000 characters")
    return message, message.casefold()


def _prepare_idempotency_key(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReminderValidationError("An idempotency key must be text")
    key = value.strip()
    if not key or len(key) > 200:
        raise ReminderValidationError("An idempotency key must contain 1 to 200 characters")
    return key


def _prepare_claim_owner(value: str) -> str:
    if not isinstance(value, str):
        raise ReminderValidationError("A reminder claim owner must be text")
    owner = value.strip()
    if not owner or len(owner) > 128:
        raise ReminderValidationError("A reminder claim owner must contain 1 to 128 characters")
    if any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in owner):
        raise ReminderValidationError("A reminder claim owner cannot contain control characters")
    return owner


def _prepare_limit(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10_000:
        raise ReminderValidationError("A reminder limit must be between 1 and 10000")
    return value


def _parse_status(value: ReminderStatus | str) -> ReminderStatus:
    try:
        return ReminderStatus(value)
    except (TypeError, ValueError) as exc:
        raise ReminderValidationError(f"Unknown reminder status: {value!r}") from exc


def _parse_recurrence(value: Recurrence | str) -> Recurrence:
    try:
        return Recurrence(value)
    except (TypeError, ValueError) as exc:
        raise ReminderValidationError(f"Unknown reminder recurrence: {value!r}") from exc


def _timestamp(value: datetime) -> str:
    return utc_datetime(value).isoformat(timespec="microseconds")


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ReminderRepositoryError(f"The reminder database contains an invalid {label}")
    try:
        parsed = datetime.fromisoformat(value)
        return utc_datetime(parsed, label)
    except (ValueError, ReminderValidationError) as exc:
        raise ReminderRepositoryError(f"The reminder database contains an invalid {label}") from exc


def _serialize_action(action: ScheduledAction | None) -> tuple[str | None, str | None]:
    if action is None:
        return None, None
    return action.action_name, json.dumps(
        dict(action.arguments), ensure_ascii=False, sort_keys=True
    )


def _parse_action(row: sqlite3.Row) -> ScheduledAction | None:
    name = row["scheduled_action_name"]
    raw_arguments = row["scheduled_action_arguments"]
    if name is None and raw_arguments is None:
        return None
    if not isinstance(name, str) or not isinstance(raw_arguments, str):
        raise ReminderRepositoryError("The reminder database contains an invalid scheduled action")
    try:
        arguments = json.loads(raw_arguments)
    except json.JSONDecodeError as exc:
        raise ReminderRepositoryError(
            "The reminder database contains malformed scheduled action arguments"
        ) from exc
    if not isinstance(arguments, dict):
        raise ReminderRepositoryError("Scheduled action arguments are not an object")
    try:
        return ScheduledAction(name, arguments)
    except ReminderValidationError as exc:
        raise ReminderRepositoryError(
            "The reminder database contains an unsafe scheduled action"
        ) from exc


def _reminder_from_row(row: sqlite3.Row) -> Reminder:
    try:
        return Reminder(
            id=int(row["id"]),
            message=str(row["message"]),
            due_at=_parse_timestamp(row["due_at"], "due_at"),
            timezone=str(row["timezone"]),
            recurrence=Recurrence(row["recurrence"]),
            status=ReminderStatus(row["status"]),
            created_at=_parse_timestamp(row["created_at"], "created_at"),
            updated_at=_parse_timestamp(row["updated_at"], "updated_at"),
            last_triggered_at=(
                None
                if row["last_triggered_at"] is None
                else _parse_timestamp(row["last_triggered_at"], "last_triggered_at")
            ),
            idempotency_key=(
                None if row["idempotency_key"] is None else str(row["idempotency_key"])
            ),
            scheduled_action=_parse_action(row),
        )
    except (KeyError, TypeError, ValueError, ReminderValidationError) as exc:
        if isinstance(exc, ReminderRepositoryError):
            raise
        raise ReminderRepositoryError("The reminder database contains an invalid record") from exc


def _same_create_request(
    reminder: Reminder,
    message: str,
    due_at: datetime,
    timezone: str,
    recurrence: Recurrence,
    scheduled_action: ScheduledAction | None,
) -> bool:
    return (
        reminder.message == message
        and reminder.due_at == due_at
        and reminder.timezone == timezone
        and reminder.recurrence is recurrence
        and reminder.scheduled_action == scheduled_action
    )
