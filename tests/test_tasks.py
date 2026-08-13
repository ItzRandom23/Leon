"""Deterministic tests for persistent reminders and scheduling."""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import stat
import threading
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from jarvis.tasks import (
    DesktopNotifier,
    NotificationError,
    Recurrence,
    Reminder,
    ReminderClosedError,
    ReminderConflictError,
    ReminderRepository,
    ReminderSchedule,
    ReminderSchemaError,
    ReminderService,
    ReminderStatus,
    ReminderValidationError,
    ScheduledAction,
    Scheduler,
    SQLiteReminderRepository,
    TerminalNotifier,
    next_occurrence,
)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class RecordingNotifier:
    def __init__(self, *, fail: bool = False) -> None:
        self.reminders: list[Reminder] = []
        self.fail = fail
        self.called = asyncio.Event()

    async def notify(self, reminder: Reminder) -> None:
        self.reminders.append(reminder)
        self.called.set()
        if self.fail:
            raise NotificationError("simulated notification failure")


NOW = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


def make_repository(tmp_path: Path, clock: MutableClock | None = None) -> SQLiteReminderRepository:
    return SQLiteReminderRepository(
        tmp_path / "reminders.sqlite3", clock=clock or MutableClock(NOW)
    )


def test_reminder_model_normalizes_utc_and_scheduled_action_is_inert() -> None:
    arguments = {"application": "notepad", "nested": [1, {"safe": True}]}
    action = ScheduledAction("open_application", arguments)
    arguments["nested"][1]["safe"] = False  # type: ignore[index]
    reminder = Reminder(
        1,
        "  Review   release  ",
        datetime(2026, 1, 3, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        timezone="Asia/Kolkata",
        scheduled_action=action,
        created_at=NOW,
        updated_at=NOW,
    )

    assert reminder.message == "Review release"
    assert reminder.due_at == datetime(2026, 1, 3, 3, 30, tzinfo=UTC)
    assert dict(action.arguments) == {
        "application": "notepad",
        "nested": [1, {"safe": True}],
    }
    assert action.permission_required is True
    assert action.execution_enabled is False


def test_models_reject_naive_times_unknown_zones_and_non_json_actions() -> None:
    with pytest.raises(ReminderValidationError, match="timezone-aware"):
        Reminder(1, "test", datetime(2026, 1, 1), created_at=NOW, updated_at=NOW)
    with pytest.raises(ReminderValidationError, match="Unknown reminder timezone"):
        ReminderSchedule.relative(timedelta(minutes=1), now=NOW, timezone="Mars/Olympus")
    with pytest.raises(ReminderValidationError, match="JSON-compatible"):
        ScheduledAction("open_application", {"unsafe": {"set"}})


def test_one_time_relative_and_date_schedule_calculation() -> None:
    local = ReminderSchedule.one_time(
        datetime(2026, 1, 5, 9, 0),
        timezone="Asia/Kolkata",
    )
    relative = ReminderSchedule.relative(
        timedelta(minutes=90),
        now=NOW,
        timezone="Asia/Kolkata",
    )
    dated = ReminderSchedule.on_date(
        date(2026, 1, 6),
        time(8, 15),
        timezone="Asia/Kolkata",
    )

    assert local.due_at == datetime(2026, 1, 5, 3, 30, tzinfo=UTC)
    assert relative.due_at == NOW + timedelta(minutes=90)
    assert dated.due_at == datetime(2026, 1, 6, 2, 45, tzinfo=UTC)
    assert local.recurrence is Recurrence.ONCE


def test_daily_weekly_and_weekday_schedules_use_local_calendar_rules() -> None:
    # 2026-01-02 is Friday; local time in Kolkata is 17:30.
    daily = ReminderSchedule.daily(time(18, 0), now=NOW, timezone="Asia/Kolkata")
    weekly = ReminderSchedule.weekly("monday", time(9, 0), now=NOW, timezone="Asia/Kolkata")
    weekdays = ReminderSchedule.weekdays(time(9, 0), now=NOW, timezone="Asia/Kolkata")

    assert daily.due_at == datetime(2026, 1, 2, 12, 30, tzinfo=UTC)
    assert weekly.due_at == datetime(2026, 1, 5, 3, 30, tzinfo=UTC)
    assert weekdays.due_at == datetime(2026, 1, 5, 3, 30, tzinfo=UTC)
    assert daily.recurrence is Recurrence.DAILY
    assert weekly.recurrence is Recurrence.WEEKLY
    assert weekdays.recurrence is Recurrence.WEEKDAYS


def test_recurrence_preserves_wall_time_across_daylight_saving_change() -> None:
    zone = ZoneInfo("America/New_York")
    first = datetime(2026, 3, 7, 9, 0, tzinfo=zone).astimezone(UTC)

    following = next_occurrence(first, Recurrence.DAILY, zone.key, after=first)

    assert following == datetime(2026, 3, 8, 13, 0, tzinfo=UTC)
    assert following.astimezone(zone).hour == 9


def test_repository_persists_utc_values_in_isolated_versioned_tables(tmp_path: Path) -> None:
    database = tmp_path / "private" / "reminders.sqlite3"
    repository = SQLiteReminderRepository(database, clock=MutableClock(NOW))
    created = repository.create(
        "Call the team",
        datetime(2026, 1, 4, 9, 0, tzinfo=ZoneInfo("Asia/Kolkata")),
        timezone="Asia/Kolkata",
    )
    repository.close()

    reopened = SQLiteReminderRepository(database, clock=MutableClock(NOW))
    try:
        persisted = reopened.get(created.id)
        assert persisted is not None
        assert persisted.due_at == datetime(2026, 1, 4, 3, 30, tzinfo=UTC)
        assert persisted.timezone == "Asia/Kolkata"
    finally:
        reopened.close()

    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        due_at = connection.execute("SELECT due_at FROM reminders").fetchone()[0]
        version = connection.execute(
            "SELECT MAX(version) FROM reminder_schema_versions"
        ).fetchone()[0]
    finally:
        connection.close()
    assert {"reminders", "reminder_schema_versions"} <= tables
    assert "memories" not in tables
    assert due_at.endswith("+00:00")
    assert version == 2


def test_repository_migrates_a_legacy_v1_database_in_place(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE reminder_schema_versions "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO reminder_schema_versions VALUES (1, ?)",
            (NOW.isoformat(),),
        )
        connection.execute(
            """
            CREATE TABLE reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message TEXT NOT NULL,
                normalized_message TEXT NOT NULL,
                due_at TEXT NOT NULL,
                timezone TEXT NOT NULL,
                recurrence TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_triggered_at TEXT,
                idempotency_key TEXT UNIQUE,
                scheduled_action_name TEXT,
                scheduled_action_arguments TEXT
            )
            """
        )

    repository = SQLiteReminderRepository(database, clock=MutableClock(NOW))
    repository.close()

    with sqlite3.connect(database) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(reminders)").fetchall()}
        version = connection.execute(
            "SELECT MAX(version) FROM reminder_schema_versions"
        ).fetchone()[0]
    assert {"claim_owner", "claim_expires_at", "delivery_started_at"} <= columns
    assert version == 2


def test_repository_protocol_lifecycle_and_future_schema_rejection(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    assert isinstance(repository, ReminderRepository)
    repository.close()
    repository.close()
    with pytest.raises(ReminderClosedError):
        repository.list()

    future_database = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(future_database)
    connection.execute(
        "CREATE TABLE reminder_schema_versions "
        "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO reminder_schema_versions (version, applied_at) VALUES (?, ?)",
        (999, NOW.isoformat()),
    )
    connection.commit()
    connection.close()
    with pytest.raises(ReminderSchemaError, match="newer JARVIS version"):
        SQLiteReminderRepository(future_database)


def test_repository_does_not_chmod_preexisting_directory_and_files_are_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = tmp_path / "existing"
    parent.mkdir()
    real_chmod = os.chmod
    chmod_targets: list[Path] = []

    def recording_chmod(path, mode, *args, **kwargs):  # type: ignore[no-untyped-def]
        chmod_targets.append(Path(path))
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr("jarvis.tasks.storage.os.chmod", recording_chmod)
    database = parent / "reminders.sqlite3"
    repository = SQLiteReminderRepository(database)
    repository.close()

    assert parent not in chmod_targets
    if os.name != "nt":
        assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_repository_rejects_database_or_parent_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.sqlite3"
    target.touch()
    link = tmp_path / "link.sqlite3"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable in this environment")

    with pytest.raises(Exception, match="private reminder database"):
        SQLiteReminderRepository(link)


def test_create_prevents_duplicates_and_honors_idempotency_keys(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    due = NOW + timedelta(hours=1)
    try:
        first = repository.create("Submit report", due, idempotency_key="request-1")
        duplicate = repository.create("  submit   REPORT ", due)
        replay = repository.create("Submit report", due, idempotency_key="request-1")

        assert duplicate.id == first.id
        assert replay.id == first.id
        assert repository.count() == 1
        with pytest.raises(ReminderConflictError, match="different reminder"):
            repository.create(
                "Different report",
                due + timedelta(minutes=5),
                idempotency_key="request-1",
            )
    finally:
        repository.close()


def test_sql_values_are_bound_and_scheduled_action_round_trips_inertly(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    malicious = "remind'); DROP TABLE reminders; --"
    action = ScheduledAction("open_application", {"application": "notepad"})
    try:
        reminder = repository.create(
            malicious,
            NOW + timedelta(minutes=10),
            scheduled_action=action,
        )
        loaded = repository.get(reminder.id)

        assert repository.count() == 1
        assert loaded is not None
        assert loaded.message == malicious
        assert loaded.scheduled_action == action
        assert loaded.scheduled_action is not None
        assert loaded.scheduled_action.execution_enabled is False
    finally:
        repository.close()


def test_due_missed_list_get_cancel_and_delete_are_deterministic(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    try:
        missed = repository.create("Missed", NOW - timedelta(minutes=1))
        exact = repository.create("Due now", NOW)
        future = repository.create("Future", NOW + timedelta(minutes=1))

        assert [item.id for item in repository.missed(NOW)] == [missed.id]
        assert [item.id for item in repository.due(NOW)] == [missed.id, exact.id]
        assert repository.next_due().id == missed.id  # type: ignore[union-attr]
        assert [item.id for item in repository.list(limit=2)] == [missed.id, exact.id]

        cancelled = repository.cancel(future.id)
        assert cancelled is not None
        assert cancelled.status is ReminderStatus.CANCELLED
        assert repository.cancel(future.id) == cancelled
        assert repository.due(NOW + timedelta(days=1)) == [missed, exact]
        assert repository.delete(future.id) is True
        assert repository.delete(future.id) is False
        assert repository.get(future.id) is None
    finally:
        repository.close()


def test_mark_triggered_is_idempotent_and_recurring_rules_advance(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    repository = make_repository(tmp_path, clock)
    try:
        once = repository.create("One time", NOW)
        triggered = repository.mark_triggered(once.id, NOW, expected_due_at=once.due_at)
        replay = repository.mark_triggered(once.id, NOW, expected_due_at=once.due_at)
        assert triggered.status is ReminderStatus.TRIGGERED
        assert replay == triggered

        daily = repository.create(
            "Daily",
            NOW - timedelta(days=3),
            recurrence=Recurrence.DAILY,
        )
        advanced = repository.mark_triggered(
            daily.id,
            NOW,
            expected_due_at=daily.due_at,
        )
        assert advanced.status is ReminderStatus.SCHEDULED
        assert advanced.due_at == NOW + timedelta(days=1)
        assert advanced.last_triggered_at == NOW
    finally:
        repository.close()


def test_edit_scheduled_is_atomic_consent_bound_and_preserves_rule(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    original = repository.create(
        "Original wording",
        NOW + timedelta(days=1),
        timezone="Asia/Kolkata",
        recurrence=Recurrence.WEEKLY,
        idempotency_key="edit-me",
        scheduled_action=ScheduledAction("inert_action", {"safe": True}),
    )
    new_due = original.due_at + timedelta(hours=2)
    try:
        edited = repository.edit_scheduled(
            original.id,
            message="Updated wording",
            due_at=new_due,
            expected_message=original.message,
            expected_due_at=original.due_at,
        )
        assert edited.message == "Updated wording"
        assert edited.due_at == new_due
        assert edited.timezone == "Asia/Kolkata"
        assert edited.recurrence is Recurrence.WEEKLY
        assert edited.idempotency_key == "edit-me"
        assert edited.scheduled_action == original.scheduled_action

        with pytest.raises(ReminderConflictError, match="changed after"):
            repository.edit_scheduled(
                original.id,
                message="Stale overwrite",
                due_at=new_due + timedelta(hours=1),
                expected_message=original.message,
                expected_due_at=original.due_at,
            )
        with pytest.raises(ReminderConflictError, match="changed after"):
            repository.edit_scheduled(
                original.id,
                message="Whitespace must not weaken consent",
                due_at=new_due + timedelta(hours=1),
                expected_message="Updated   wording",
                expected_due_at=edited.due_at,
            )
        assert repository.get(original.id) == edited
    finally:
        repository.close()


def test_edit_rejects_cancelled_or_delivery_started_reminders(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    cancelled = repository.create("Cancelled", NOW + timedelta(hours=1))
    delivering = repository.create("Delivering", NOW)
    try:
        repository.cancel(cancelled.id)
        with pytest.raises(ReminderConflictError, match="scheduled"):
            repository.edit_scheduled(
                cancelled.id,
                message="No",
                due_at=cancelled.due_at,
                expected_message=cancelled.message,
                expected_due_at=cancelled.due_at,
            )

        repository.claim_due(
            NOW,
            owner="edit-race",
            lease_until=NOW + timedelta(minutes=1),
        )
        assert repository.begin_delivery(
            delivering.id,
            owner="edit-race",
            expected_due_at=delivering.due_at,
            started_at=NOW,
        )
        with pytest.raises(ReminderConflictError, match="already started"):
            repository.edit_scheduled(
                delivering.id,
                message="Too late",
                due_at=NOW + timedelta(hours=1),
                expected_message=delivering.message,
                expected_due_at=delivering.due_at,
            )
    finally:
        repository.close()


def test_mark_triggered_rejects_early_or_stale_occurrences(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    due = NOW + timedelta(minutes=5)
    try:
        reminder = repository.create("Later", due)
        with pytest.raises(ReminderValidationError, match="before it is due"):
            repository.mark_triggered(reminder.id, NOW)

        recurring = repository.create(
            "Recurring",
            NOW,
            recurrence=Recurrence.DAILY,
        )
        updated = repository.mark_triggered(recurring.id, NOW, expected_due_at=NOW)
        with pytest.raises(ReminderConflictError, match="already changed"):
            repository.mark_triggered(
                recurring.id,
                NOW + timedelta(minutes=1),
                expected_due_at=NOW,
            )
        assert updated.due_at == NOW + timedelta(days=1)
    finally:
        repository.close()


def test_service_uses_injected_clock_and_wakes_scheduler_callback(tmp_path: Path) -> None:
    changes: list[str] = []
    repository = make_repository(tmp_path)
    service = ReminderService(
        repository, clock=MutableClock(NOW), on_change=lambda: changes.append("x")
    )
    try:
        relative = service.remind_in("Stretch", timedelta(minutes=15))
        weekday = service.remind_weekdays(
            "Standup",
            time(9, 0),
            timezone="Asia/Kolkata",
        )

        assert relative.due_at == NOW + timedelta(minutes=15)
        assert weekday.due_at == datetime(2026, 1, 5, 3, 30, tzinfo=UTC)
        assert service.get(relative.id) == relative
        assert len(service.list()) == 2
        service.cancel(relative.id)
        service.delete(relative.id)
        assert changes == ["x", "x", "x", "x"]
    finally:
        repository.close()


def test_service_edit_wakes_scheduler_after_atomic_update(tmp_path: Path) -> None:
    changes: list[str] = []
    repository = make_repository(tmp_path)
    service = ReminderService(
        repository,
        clock=MutableClock(NOW),
        on_change=lambda: changes.append("changed"),
    )
    original = repository.create("Before", NOW + timedelta(hours=1))
    try:
        edited = service.edit_scheduled(
            original.id,
            message="After",
            due_at=NOW + timedelta(hours=2),
            expected_message=original.message,
            expected_due_at=original.due_at,
        )
        assert edited.message == "After"
        assert changes == ["changed"]
    finally:
        repository.close()


def test_scheduler_notifies_due_reminders_and_marks_them_after_success(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    notifier = RecordingNotifier()
    reminder = repository.create("Pay invoice", NOW)
    scheduler = Scheduler(repository, notifier, clock=MutableClock(NOW))
    try:
        result = asyncio.run(scheduler.poll())

        assert [item.id for item in notifier.reminders] == [reminder.id]
        assert len(result) == 1
        assert result.failures == ()
        assert result.notified[0].status is ReminderStatus.TRIGGERED
        assert repository.due(NOW) == []
    finally:
        repository.close()


def test_scheduler_quarantines_uncertain_failed_notifications(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    repository = make_repository(tmp_path, clock)
    notifier = RecordingNotifier(fail=True)
    reminder = repository.create("Retry me", NOW)
    scheduler = Scheduler(
        repository,
        notifier,
        clock=clock,
        lease_seconds=1,
        lease_owner="failed-runner",
    )
    try:
        result = asyncio.run(scheduler.run_once())

        assert result.notified == ()
        assert result.failures[0].reminder.id == reminder.id
        assert repository.get(reminder.id).status is ReminderStatus.SCHEDULED  # type: ignore[union-attr]
        clock.value = NOW + timedelta(seconds=2)
        assert (
            repository.claim_due(
                clock.value,
                owner="replacement-runner",
                lease_until=clock.value + timedelta(minutes=1),
            )
            == []
        )
    finally:
        repository.close()


def test_cancel_after_claim_wins_before_delivery_and_clears_the_lease(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    notifier = RecordingNotifier()
    reminder = repository.create("Cancel in time", NOW)
    try:
        claimed = repository.claim_due(
            NOW,
            owner="runner-a",
            lease_until=NOW + timedelta(minutes=1),
        )
        assert [item.id for item in claimed] == [reminder.id]

        cancelled = repository.cancel(reminder.id)
        assert cancelled is not None
        assert cancelled.status is ReminderStatus.CANCELLED
        assert (
            repository.begin_delivery(
                reminder.id,
                owner="runner-a",
                expected_due_at=reminder.due_at,
                started_at=NOW,
            )
            is False
        )

        result = asyncio.run(Scheduler(repository, notifier, clock=MutableClock(NOW)).poll())
        assert notifier.reminders == []
        assert result.notified == ()
    finally:
        repository.close()


def test_cancel_cannot_report_success_after_delivery_has_started(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    reminder = repository.create("Already delivering", NOW)
    try:
        repository.claim_due(
            NOW,
            owner="runner-a",
            lease_until=NOW + timedelta(minutes=1),
        )
        assert repository.begin_delivery(
            reminder.id,
            owner="runner-a",
            expected_due_at=reminder.due_at,
            started_at=NOW,
        )
        with pytest.raises(ReminderConflictError, match="already started"):
            repository.cancel(reminder.id)

        assert not repository.release_claim(
            reminder.id,
            owner="runner-a",
            expected_due_at=reminder.due_at,
        )
        with pytest.raises(ReminderConflictError, match="already started"):
            repository.cancel(reminder.id)
        with pytest.raises(ReminderConflictError, match="already started"):
            repository.delete(reminder.id)
    finally:
        repository.close()


def test_concurrent_scheduler_instances_do_not_duplicate_delivery(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "shared.sqlite3"
        first_repository = SQLiteReminderRepository(database, clock=MutableClock(NOW))
        second_repository = SQLiteReminderRepository(database, clock=MutableClock(NOW))
        started = asyncio.Event()
        release = asyncio.Event()
        deliveries: list[int] = []

        class BlockingNotifier:
            async def notify(self, reminder: Reminder) -> None:
                deliveries.append(reminder.id)
                started.set()
                await release.wait()

        reminder = first_repository.create("Exactly one runner", NOW)
        clock = MutableClock(NOW)
        first = Scheduler(
            first_repository,
            BlockingNotifier(),
            clock=clock,
            lease_seconds=1,
            lease_owner="runner-a",
        )
        second = Scheduler(
            second_repository,
            BlockingNotifier(),
            clock=clock,
            lease_seconds=1,
            lease_owner="runner-b",
        )
        first_poll = asyncio.create_task(first.poll())
        try:
            await asyncio.wait_for(started.wait(), timeout=1)
            # The external notifier may take longer than its claim lease.  A
            # durable delivery-started marker prevents another scheduler from
            # automatically retrying the occurrence after expiry.
            clock.value = NOW + timedelta(seconds=2)
            second_result = await asyncio.wait_for(second.poll(), timeout=1)
            assert second_result.notified == ()
            release.set()
            first_result = await asyncio.wait_for(first_poll, timeout=1)
            assert [item.id for item in first_result.notified] == [reminder.id]
            assert deliveries == [reminder.id]
        finally:
            release.set()
            if not first_poll.done():
                first_poll.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first_poll
            first_repository.close()
            second_repository.close()

    asyncio.run(scenario())


def test_cancelled_to_thread_notifier_is_never_automatically_retried(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "cancelled-thread.sqlite3"
        clock = MutableClock(NOW)
        first_repository = SQLiteReminderRepository(database, clock=clock)
        second_repository = SQLiteReminderRepository(database, clock=clock)
        entered = threading.Event()
        release = threading.Event()
        deliveries: list[int] = []

        class ThreadNotifier:
            async def notify(self, reminder: Reminder) -> None:
                def emit() -> None:
                    deliveries.append(reminder.id)
                    entered.set()
                    release.wait(timeout=2)

                await asyncio.to_thread(emit)

        reminder = first_repository.create("Thread-backed notification", NOW)
        first = Scheduler(
            first_repository,
            ThreadNotifier(),
            clock=clock,
            lease_seconds=1,
            lease_owner="thread-runner",
        )
        second_notifier = RecordingNotifier()
        second = Scheduler(
            second_repository,
            second_notifier,
            clock=clock,
            lease_seconds=1,
            lease_owner="replacement-runner",
        )
        poll_task = asyncio.create_task(first.poll())
        try:
            assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 1), timeout=2)
            poll_task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await poll_task

            clock.value = NOW + timedelta(seconds=2)
            result = await second.poll()
            assert result.notified == ()
            assert second_notifier.reminders == []
            assert deliveries == [reminder.id]
        finally:
            release.set()
            first_repository.close()
            second_repository.close()

    asyncio.run(scenario())


def test_expired_claim_is_recovered_by_another_runner(tmp_path: Path) -> None:
    clock = MutableClock(NOW)
    repository = make_repository(tmp_path, clock)
    reminder = repository.create("Recover after crash", NOW)
    try:
        repository.claim_due(
            NOW,
            owner="crashed-runner",
            lease_until=NOW + timedelta(seconds=5),
        )
        clock.value = NOW + timedelta(seconds=6)
        recovered = repository.claim_due(
            clock.value,
            owner="replacement-runner",
            lease_until=clock.value + timedelta(minutes=1),
        )
        assert [item.id for item in recovered] == [reminder.id]
        assert repository.begin_delivery(
            reminder.id,
            owner="replacement-runner",
            expected_due_at=reminder.due_at,
            started_at=clock.value,
        )
        updated = repository.mark_claim_triggered(
            reminder.id,
            owner="replacement-runner",
            triggered_at=clock.value,
            expected_due_at=reminder.due_at,
        )
        assert updated.status is ReminderStatus.TRIGGERED
    finally:
        repository.close()


def test_claim_completion_preserves_recurring_schedule_semantics(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    notifier = RecordingNotifier()
    reminder = repository.create(
        "Daily through scheduler",
        NOW,
        recurrence=Recurrence.DAILY,
    )
    scheduler = Scheduler(
        repository,
        notifier,
        clock=MutableClock(NOW),
        lease_owner="recurrence-runner",
    )
    try:
        result = asyncio.run(scheduler.poll())
        assert [item.id for item in notifier.reminders] == [reminder.id]
        assert result.notified[0].status is ReminderStatus.SCHEDULED
        assert result.notified[0].due_at == NOW + timedelta(days=1)
        assert result.notified[0].last_triggered_at == NOW
    finally:
        repository.close()


def test_scheduler_wake_interrupts_timed_poll_without_real_notification(tmp_path: Path) -> None:
    async def scenario() -> None:
        repository = make_repository(tmp_path)
        notifier = RecordingNotifier()
        scheduler = Scheduler(
            repository,
            notifier,
            clock=MutableClock(NOW),
            poll_interval_seconds=60,
        )
        task = asyncio.create_task(scheduler.run())
        try:
            await asyncio.sleep(0)
            repository.create("Wake now", NOW)
            scheduler.wake()
            await asyncio.wait_for(notifier.called.wait(), timeout=1)
            assert notifier.reminders[0].message == "Wake now"
        finally:
            scheduler.stop()
            await asyncio.wait_for(task, timeout=1)
            repository.close()

    asyncio.run(scenario())


def test_scheduler_loop_logs_and_survives_one_poll_error(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def scenario() -> None:
        repository = make_repository(tmp_path)
        scheduler = Scheduler(
            repository,
            RecordingNotifier(),
            clock=MutableClock(NOW),
            poll_interval_seconds=0.01,
        )
        calls = 0

        async def flaky_poll() -> object:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("sensitive repository detail")
            scheduler.stop()
            return object()

        scheduler.poll = flaky_poll  # type: ignore[method-assign]
        try:
            await asyncio.wait_for(scheduler.run(), timeout=1)
            assert calls == 2
        finally:
            scheduler.stop()
            repository.close()

    caplog.set_level(logging.ERROR, logger="jarvis.tasks.scheduler")
    asyncio.run(scenario())
    assert "Reminder scheduler poll failed (RuntimeError)." in caplog.text
    assert "sensitive repository detail" not in caplog.text


def test_terminal_notifier_uses_injected_output_and_desktop_notifier_is_lazy() -> None:
    reminder = Reminder(1, "Hello\x00 world", NOW, created_at=NOW, updated_at=NOW)
    output: list[str] = []
    asyncio.run(TerminalNotifier(output.append).notify(reminder))
    assert output == ["Reminder: Hello world"]

    loaded: list[str] = []
    calls: list[dict[str, str]] = []

    def loader(name: str):
        loaded.append(name)
        return SimpleNamespace(
            notification=SimpleNamespace(notify=lambda **kwargs: calls.append(kwargs))
        )

    notifier = DesktopNotifier(module_loader=loader)
    assert loaded == []
    asyncio.run(notifier.notify(reminder))
    assert loaded == ["plyer"]
    assert calls == [{"title": "JARVIS Reminder", "message": "Hello world", "app_name": "JARVIS"}]
