# Tasks, reminders, and scheduling

Phase 8 provides persistent, timezone-aware reminders and bounded notification
delivery. It is a scheduler, not an autonomous action executor: a reminder can
display text, while `ScheduledAction` records are inert metadata whose
`execution_enabled` property is always false in this release.

## Enable and keep it running

The scheduler is enabled by default:

```toml
[scheduler]
enabled = true
database_path = "~/.jarvis/tasks.db"
timezone = "UTC"
poll_interval_seconds = 30
desktop_notifications = false
```

Long-running `jarvis` and `jarvis gui` sessions start the scheduler. A command
such as `jarvis tasks add ...` performs one database operation and exits, so it
does not remain alive to deliver the reminder. Keep a terminal or GUI process
running when notifications are expected.

With `desktop_notifications = false`, due reminders print to the process's
terminal. To request OS notifications:

```powershell
python -m pip install -e ".[notifications]"
```

```toml
[scheduler]
desktop_notifications = true
```

Desktop delivery depends on `plyer` and the host notification service. A local
readiness check cannot guarantee that the OS will display or retain a
notification.

## CLI

```console
jarvis tasks list
jarvis tasks list --status scheduled
jarvis tasks missed
jarvis tasks add --message "Stretch" --in-minutes 20
jarvis tasks add --message "Call Sam" --at 2026-08-14T09:30:00 --timezone Asia/Kolkata
jarvis tasks cancel 7 --message "Stretch"
jarvis tasks delete 7 --message "Stretch"
```

`tasks add` exposes one-time absolute and relative schedules. The registered
action API also supports daily, weekly, and Monday-to-Friday recurrence, which
can be reached through compatible deterministic/model planning or another
shared-runtime interface.

The GUI exposes atomic edit/reschedule through `edit_reminder`. The request
includes the reminder ID, exact currently displayed message and UTC due instant,
plus the new message and ISO scheduled instant. The repository rejects a stale,
non-scheduled, or already-delivering record. Timezone, recurrence, idempotency
key, and inert scheduled-action metadata are retained rather than replaced.
Editing is `SENSITIVE` and always confirms.

Cancel/delete require both the numeric ID and exact current reminder text. The
service reads the live record immediately before mutation and fails closed if
the text changed. Deletion is `DESTRUCTIVE` and always requires confirmation;
cancellation is `ACTION` but also retains mandatory confirmation.

## Registered actions

| Action | Category | Main inputs |
| --- | --- | --- |
| `create_reminder` | `SENSITIVE` | Message, ISO 8601 datetime, IANA timezone |
| `create_relative_reminder` | `SENSITIVE` | Message, 1–525,600 minutes, timezone |
| `create_daily_reminder` | `SENSITIVE` | Message, local `HH:MM[:SS]`, timezone |
| `create_weekly_reminder` | `SENSITIVE` | Message, weekday name, local time, timezone |
| `create_weekday_reminder` | `SENSITIVE` | Message, local time, timezone |
| `edit_reminder` | `SENSITIVE`, always confirm | ID, exact expected message/due, new message and ISO datetime |
| `list_reminders` | `SENSITIVE` | Optional status and bounded limit |
| `list_missed_reminders` | `SENSITIVE` | Bounded limit |
| `cancel_reminder` | `ACTION`, always confirm | ID and exact expected message |
| `delete_reminder` | `DESTRUCTIVE` | ID and exact expected message |

Reminder text is sensitive because it commonly describes health, relationships,
work, travel, or other personal plans. It may be sent to a configured model when
that model proposes an action, stored in plain-text SQLite fields, printed in a
terminal, or shown in OS notification history.

## Time and recurrence semantics

- Every stored instant is timezone-aware and normalized to UTC.
- `timezone` is a validated IANA name such as `UTC`, `Asia/Kolkata`, or
  `America/New_York`.
- A naive one-time ISO datetime is interpreted in the supplied timezone. An
  offset-aware datetime represents its own instant and is normalized to UTC.
- Daily, weekly, and weekday rules retain their local wall-clock intent across
  recurrence calculations.
- A nonexistent local time during a daylight-saving spring gap advances minute
  by minute to the first valid local instant, up to the bounded gap window.
- An ambiguous fall-back time follows the supplied `datetime.time.fold` value at
  the lower-level scheduling API.
- Recurring reminders advance to the first occurrence strictly after the later
  of their previous due time and actual trigger time. They do not replay every
  elapsed interval after a long offline period.

Statuses are `scheduled`, `cancelled`, and `triggered`. A one-time reminder
becomes `triggered` after successful notification. A recurring reminder remains
`scheduled` with its next due instant and records `last_triggered_at`.

## Missed reminders and retries

`jarvis tasks missed` lists still-scheduled records whose due time is in the
past. On the next running scheduler poll, a missed occurrence is eligible for
delivery. Notification failure releases its claim so a later poll can retry.

Delivery uses a versioned SQLite schema and an atomic lease protocol:

1. one scheduler claims a bounded batch of due occurrences;
2. the repository atomically marks delivery as started;
3. cancellation/deletion are refused after that boundary;
4. the notifier runs; and
5. successful completion marks a one-time reminder triggered or advances a
   recurring reminder.

Concurrent schedulers sharing the same database should not notify the same
active lease simultaneously. This is not an exactly-once guarantee across every
external failure: if a notification is delivered and the process/database fails
before completion is durably recorded, its outcome is uncertain and a retry
after lease expiry can duplicate it. The design retains the lease after a
post-delivery persistence fault to avoid an immediate duplicate.

Edit/reschedule uses the same repository transaction boundary. It can update
only a `scheduled` reminder before notification delivery starts and compares the
exact expected current message/due values immediately before the update.

## Persistence and idempotency

The default reminder database is separate from memory and plugin state. Schema
migrations are versioned; a database created by a newer unsupported JARVIS
version fails rather than being silently interpreted.

The repository supports optional idempotency keys for programmatic creation.
Reusing a key for the identical reminder returns the existing record; reusing it
for a different schedule or message is a conflict. Independently, the store
prevents duplicate active reminders with the same normalized message, due time,
timezone, and recurrence.

Back up or remove the database only while JARVIS processes using it are stopped.
It is not application-level encrypted.

## Non-capabilities

Phase 8 does not:

- execute a remembered action at a future time;
- run while the computer is off or no JARVIS process is active;
- install an OS service, cron job, or Windows Task Scheduler entry;
- synchronize reminders to a cloud account or another device;
- guarantee exactly-once display by an operating-system notifier; or
- infer that an external task was completed because a reminder was displayed.

See [configuration](configuration.md), [permissions](permissions.md),
[security](security.md), and [architecture](architecture.md).
