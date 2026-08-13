"""Event-wakeable polling scheduler for due reminders."""

from __future__ import annotations

import asyncio
import inspect
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from jarvis.tasks.models import Reminder, utc_datetime
from jarvis.tasks.notifiers import ReminderNotifier
from jarvis.tasks.repository import ReminderRepository

Clock = Callable[[], datetime]
logger = logging.getLogger(__name__)


def _default_clock() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class NotificationFailure:
    """One delivery failure whose external outcome may be uncertain."""

    reminder: Reminder
    error: str


@dataclass(frozen=True, slots=True)
class SchedulerPollResult:
    """Structured result from one repository poll."""

    notified: tuple[Reminder, ...] = ()
    failures: tuple[NotificationFailure, ...] = ()

    def __len__(self) -> int:
        return len(self.notified)

    @property
    def triggered(self) -> tuple[Reminder, ...]:
        return self.notified


class Scheduler:
    """Poll due reminders and notify without executing scheduled actions.

    ``wake`` interrupts the timed fallback poll whenever reminder state changes,
    so correctness does not depend solely on sleeping until a guessed deadline.
    """

    def __init__(
        self,
        repository: ReminderRepository,
        notifier: ReminderNotifier,
        *,
        clock: Clock = _default_clock,
        poll_interval_seconds: float = 30.0,
        batch_size: int = 100,
        lease_seconds: float = 300.0,
        lease_owner: str | None = None,
    ) -> None:
        if not isinstance(repository, ReminderRepository):
            raise TypeError("repository must implement ReminderRepository")
        if not hasattr(notifier, "notify") or not callable(notifier.notify):
            raise TypeError("notifier must provide notify(reminder)")
        if not callable(clock):
            raise TypeError("clock must be callable")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ValueError("batch_size must be a positive integer")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, (int, float))
            or not math.isfinite(lease_seconds)
            or not 1 <= lease_seconds <= 86_400
        ):
            raise ValueError("lease_seconds must be between 1 and 86400")
        owner = uuid4().hex if lease_owner is None else lease_owner
        if (
            not isinstance(owner, str)
            or not owner.strip()
            or len(owner.strip()) > 128
            or any(ord(character) < 32 or 127 <= ord(character) <= 159 for character in owner)
        ):
            raise ValueError("lease_owner must contain 1 to 128 characters")
        self.repository = repository
        self.notifier = notifier
        self._clock = clock
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.batch_size = batch_size
        self.lease_seconds = float(lease_seconds)
        self.lease_owner = owner.strip()
        self._wake_event = asyncio.Event()
        self._stopping = False
        self._poll_lock = asyncio.Lock()

    def wake(self) -> None:
        """Request an immediate repository poll."""

        self._wake_event.set()

    notify_changed = wake

    def stop(self) -> None:
        """Request a running scheduler loop to stop promptly."""

        self._stopping = True
        self._wake_event.set()

    async def poll(self) -> SchedulerPollResult:
        """Poll and deliver all currently due reminders in a bounded batch."""

        async with self._poll_lock:
            now = utc_datetime(self._clock(), "clock result")
            due = await asyncio.to_thread(
                self.repository.claim_due,
                now,
                owner=self.lease_owner,
                lease_until=now + timedelta(seconds=self.lease_seconds),
                limit=self.batch_size,
            )
            notified: list[Reminder] = []
            failures: list[NotificationFailure] = []
            for reminder in due:
                started_at = utc_datetime(self._clock(), "clock result")
                try:
                    started = await asyncio.to_thread(
                        self.repository.begin_delivery,
                        reminder.id,
                        owner=self.lease_owner,
                        expected_due_at=reminder.due_at,
                        started_at=started_at,
                    )
                except asyncio.CancelledError:
                    # The worker thread may still commit the delivery marker.
                    # Never clear an outcome that is not known to be pre-delivery.
                    raise
                except Exception as exc:
                    failures.append(NotificationFailure(reminder, str(exc)))
                    continue
                if not started:
                    # Cancellation, expiry, or another owner won before any
                    # external notification began.
                    continue

                try:
                    result = self.notifier.notify(reminder)
                    if inspect.isawaitable(result):
                        await result
                except asyncio.CancelledError:
                    # A notifier backed by ``asyncio.to_thread`` keeps running
                    # after cancellation.  The durable delivery marker therefore
                    # stays set until an operator resolves the uncertain outcome.
                    raise
                except Exception as exc:
                    # A notifier may fail after it has already emitted externally.
                    # Automatic retry would risk a duplicate notification.
                    failures.append(NotificationFailure(reminder, str(exc)))
                    continue

                try:
                    updated = await asyncio.to_thread(
                        self.repository.mark_claim_triggered,
                        reminder.id,
                        owner=self.lease_owner,
                        triggered_at=started_at,
                        expected_due_at=reminder.due_at,
                    )
                    notified.append(updated)
                except asyncio.CancelledError:
                    # The external notification already completed. Retaining its
                    # durable lease is safer than releasing it for an immediate,
                    # potentially duplicate retry.
                    raise
                except Exception as exc:
                    # Completion uncertainty is recovered only after lease expiry;
                    # do not turn a database fault into an immediate duplicate.
                    failures.append(NotificationFailure(reminder, str(exc)))
            return SchedulerPollResult(tuple(notified), tuple(failures))

    run_once = poll

    async def run(self) -> None:
        """Poll until :meth:`stop`, using both change events and timed fallback."""

        self._stopping = False
        while not self._stopping:
            # Clear before polling so a wake arriving during repository or notifier
            # work remains set and causes an immediate follow-up poll.
            self._wake_event.clear()
            try:
                await self.poll()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Poll failures are isolated so one transient repository fault
                # cannot permanently stop the background scheduler.
                logger.error(
                    "Reminder scheduler poll failed (%s).",
                    type(exc).__name__,
                )
            if self._stopping:
                break
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self.poll_interval_seconds,
                )
            except TimeoutError:
                pass


ReminderScheduler = Scheduler
