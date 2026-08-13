# Desktop GUI

Phase 11 provides a framework-neutral controller and an optional PySide6/qasync
desktop adapter. The GUI is another view of the same `JarvisApplication`: it
uses the same runtime, registered actions, permission manager, memory/reminder
stores, integrations, plugin manager, events, and cleanup path as the terminal.

Status: controller, data-model, permission-broker, and adapter behavior have
automated coverage using fakes. Starting a real Qt window is experimental and
depends on PySide6, qasync, an interactive display, OS integration, and any
feature-specific optional packages. Automated controller tests do not claim a
manually exercised GUI session on every platform.

## Install and start

```powershell
python -m pip install -e ".[gui]"
jarvis gui
```

```toml
[gui]
theme = "system"
minimize_to_tray = false
show_debug_logs = false
```

Themes are `system`, `light`, and `dark`. Tray behavior is enabled only when the
host reports a system tray. When minimize-to-tray is active, closing the window
hides it and keeps the process—including reminder polling—running until Quit is
selected from the tray menu.

`show_debug_logs` is a reserved presentation preference in 0.3; actual logging
verbosity remains controlled by `[logging].level` or `--debug`.

The GUI extra does not include PyAutoGUI, voice, Playwright, notifications, or
provider credentials. Install/configure those independently when needed.

## Pages and controls

| Page | Current behavior |
| --- | --- |
| Chat | Send text through `JarvisRuntime`, view the local-session transcript and action activity, optionally capture one voice utterance |
| Memory | Permissioned list/search of explicit memories and deletion of a selected record |
| Tasks | Permissioned reminder list plus one-time create, edit/reschedule, cancel, and delete controls |
| Integrations | Read registered provider names, lifecycle status, and bounded non-secret detail |
| Plugins | Discover/inspect status and enable or disable a selected trusted entry point |
| Settings | Display effective configuration with known secrets redacted |
| Logs | Display a bounded in-memory sanitized log snapshot |
| About | Display package, version, Python, and project information |

The Memory and Tasks pages do not read SQLite files directly. Their operations
are issued as shared action requests, so `SENSITIVE` and `DESTRUCTIVE` policy
still applies. Reminder and plugin mutations use the same target checks and
plugin warning as the terminal.

The status view identifies provider-backed/external components including remote
vision, network speech-to-text, and GitHub when enabled, so a local-looking
window does not obscure that data may leave the process.

Reminder editing/rescheduling uses shared `edit_reminder`, a `SENSITIVE` action
with a mandatory confirmation floor. It submits the ID, exact displayed message
and UTC due instant, then the new message and ISO scheduled instant. The
repository rejects a stale, non-scheduled, or already-delivering record;
timezone, recurrence, idempotency key, and inert scheduled-action metadata are
retained. The control does not bypass the repository or permission engine.

Settings are a read-only snapshot in this foundation; the GUI is not yet a
configuration-file editor. Email/calendar live-account management, provider
OAuth, browser visual embedding, and a remote/web/mobile interface are not
included.

## Permission dialogs

`GuiPermissionBroker` adapts the core confirmer to a non-blocking local dialog.
Each prompt shows:

- risk category and action name;
- the action's bounded summary; and
- exact sanitized details such as target IDs, URLs, titles, expected current
  values, recipients, or text to be sent/typed.

Permission details are intentionally not automatically redacted because hiding
the target or content would make meaningful consent impossible. Do not capture
or share permission-dialog screenshots containing private data.

The broker never defaults to approval; Deny is the dialog's default action.
Rejecting or closing a dialog, closing
the controller, reaching the default timeout, cancelling the waiting request,
receiving an invalid decision, or encountering a UI observer failure denies the
pending action. A decision is applied at most once.

The PySide adapter must receive the same broker instance bound as the confirmer
for that exact application runtime; a mismatched broker is refused rather than
showing a dialog whose decision cannot authorize the runtime being displayed.

## Activity and cancellation

The controller exposes idle/planning/awaiting-permission/executing/error status
and requested/running/completed/failed/cancelled/unknown-outcome activity
records. The Cancel button requests cancellation of the active orchestration
task and denies pending permission dialogs.

Cancellation is cooperative, not transactional. It can prevent work that has
not started, but Python worker-thread operations, browser calls, desktop input,
or external service requests may not be interruptible after dispatch. Once an
action has started, the GUI labels its outcome unknown until verified at the
target instead of claiming it was cancelled. Cancellation does not roll back a
GitHub issue, sent message, calendar change, typed text, or other side effect
that already completed.

Close/shutdown also requests cancellation and releases shared application
resources. Plugins remain responsible for their own unmanaged threads and
subprocesses.

## Display and log safety

Framework-neutral view models bound string lengths, collection sizes, and
nesting depth. Unsafe control characters are removed and Unicode format/bidi
characters are rendered as visible `\uXXXX` escapes. HTML rendering escapes
page-authored values.

Settings redact dataclass fields marked secret. The GUI log store bounds its
record count and sanitizes messages. Redaction is defense in depth, not a reason
to log credentials, full emails, reminder text, screenshots, or provider
payloads. A plugin runs in-process and can bypass the provided logging helpers.

Chat and log views are local in-memory presentation state for the process. They
are not a durable audit log. Conversely, remote model providers may receive the
active bounded conversation history under their own retention policy.

## Accessibility and platform limits

The adapter uses ordinary Qt widgets, keyboard-focusable controls, table views,
and modal permission dialogs. Contributors should preserve labels, keyboard
navigation, readable contrast, screen-reader semantics, scalable layout, and
non-color status cues. Accessibility has not been certified across every Qt
platform/theme/screen reader.

Core GUI models are platform-neutral. Computer-control actions remain
Windows-first, and any PyAutoGUI-backed action still needs an interactive local
desktop with OS permission. Headless servers generally cannot provide the GUI
even when the Python package imports successfully.

See [installation](installation.md), [configuration](configuration.md),
[permissions](permissions.md), [security](security.md), and
[architecture](architecture.md).
