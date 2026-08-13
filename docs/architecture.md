# Architecture

JARVIS 0.x is a modular personal-assistant foundation, not an autonomous agent.
The core design keeps language understanding, validation, authorization, and
side effects separate so that no provider response is treated as executable
input.

## Runtime flow

```text
terminal, optional microphone, or GUI input
                |
                v
       shared application composition
                |
        +-------+-------+
        |               |
        v               v
deterministic planner   configured LLM provider
        |               |
        +-------+-------+
                v
       structured action requests
                |
                v
     registry schema validation
                |
                v
        permission decision
                |
                v
       registered action handler
                |
 +-------+-------+--------+----------+-------------+
 |       |       |        |          |             |
memory computer vision  browser  reminders  integrations/plugins
 |                         |
 +----------- SQLite -----+--- scheduler/notifier
                |
                v
        structured result + events
```

The deterministic planner recognizes a deliberately bounded set of requests
and remains useful offline. When an LLM is enabled, it receives only the
provider-neutral schemas exported by the action registry. Tool calls are then
looked up and validated locally. Neither path can register a new action or
execute arbitrary code. The terminal and GUI both receive the same composed
runtime and owned service lifecycle; neither interface has a privileged direct
side-effect path.

## Main packages

| Package | Responsibility | Status |
| --- | --- | --- |
| `jarvis.core` | Configuration, action schemas, validation, permissions, planning, events, and orchestration | ✅ Implemented foundation |
| `jarvis.skills` | Small Phase 1 conversational skills and compatibility router | ✅ Implemented |
| `jarvis.ai` | Provider contract, bounded session history, OpenAI Responses adapter, and OpenAI-compatible Chat Completions adapter | 🚧 Experimental; used only when enabled and configured |
| `jarvis.memory` | Explicit memory operations and a replaceable SQLite repository | ✅ Implemented |
| `jarvis.computer` | Validated application, system, mouse, keyboard, screenshot, and Windows-window adapters | 🚧 Experimental; Windows-first and partly optional |
| `jarvis.voice` | Replaceable speech contracts, Google recognizer adapter, local `pyttsx3` output, and wake-word extension point | 🚧 Experimental and optional |
| `jarvis.vision` | Screenshot analysis contract plus OpenAI Responses and OpenAI-compatible adapters | 🚧 Experimental; semantic only |
| `jarvis.browser` | Bounded public-web Playwright controller, snapshots, verified elements, and permissioned actions | 🚧 Experimental and opt-in |
| `jarvis.tasks` | Versioned reminder persistence, timezone recurrence, notification leases, notifiers, and actions | ✅ Implemented foundation; delivery needs a running process |
| `jarvis.integrations` | Lifecycle/operation contracts, credentials, bounded HTTPS transport, GitHub, SMTP/IMAP email, CalDAV calendar, and in-memory email/calendar interfaces | 🚧 Live adapters experimental; in-memory implementations are demo-only |
| `jarvis.plugins` | API v1 metadata/context, entry-point discovery, staged registration, state, and lifecycle manager | 🚧 Implemented trusted-code foundation; unsandboxed |
| `jarvis.gui` | Framework-neutral views/controller/permission broker and lazy PySide6/qasync adapter | 🚧 Experimental and optional |
| `jarvis.bootstrap` | Composition root and deterministic startup/shutdown ownership shared by interfaces | ✅ Implemented foundation |

## Core boundaries

### Actions

An `Action` is an immutable name, description, risk level, parameter list, and
trusted Python handler. Parameters are translated into strict JSON object
schemas with `additionalProperties: false`. The registry rejects duplicate
names, unknown actions, unknown arguments, missing required values, incorrect
JSON types, and declared constraint violations before a handler runs.

See [actions.md](actions.md).

### Permissions

Every registered side effect has a `READ`, `ACTION`, `SENSITIVE`, or
`DESTRUCTIVE` category. The permission manager maps each category to `allow`,
`ask`, or `deny`. A request that needs confirmation is denied when no confirmer
is available, and destructive operations cannot become silently allowed merely
through configuration.

Closing an application is `DESTRUCTIVE` because process termination can discard
unsaved work; it always retains a confirmation floor. Generic keyboard/browser
element actions, selected external writes, and plugin inspection/enablement also
retain mandatory confirmation independent of an `allow` category setting.

See [permissions.md](permissions.md).

### Providers

LLM and vision providers implement narrow abstract interfaces. Provider
adapters translate between JARVIS models and remote API payloads; they do not
own action execution. Credentials come from configuration or environment
variables. Provider use is disabled by default.

Model requests, active context, and returned tool-result content have explicit
budgets. After a local action executes, any model follow-up receives the result
as untrusted data with no tool schemas available, so it cannot chain another
action. The raw tool exchange/follow-up is discarded rather than committed to
ordinary conversation history.

See [providers.md](providers.md).

### Memory and conversation state

`Conversation` is bounded, in-memory state for the active process. It is
distinct from explicit persistent memory. Persistent records use a repository
interface backed by SQLite, with categories and deliberate create, read, search,
forget, and clear operations. There is no hook that blindly stores every
conversation message.

See [memory.md](memory.md).

### Browser

The browser controller is optional and owns bounded ephemeral contexts. Each
session also owns an authenticated `127.0.0.1` proxy; browser traffic has no
configured direct bypass, and the proxy resolves/pins public DNS answers and
connects to numeric public destinations for HTTP and HTTPS tunnels. Alternate
browser network surfaces are disabled/constrained. Page text and
accessibility-like entries are explicitly untrusted. Element actions refer to
opaque IDs from the latest snapshot and revalidate the stable
node/page/tab/role/name fingerprint before acting. No generic selector,
JavaScript, persistent-profile, or saved-download action is exposed.

See [browser.md](browser.md).

### Reminders and scheduler

Reminder records live in their own versioned SQLite database. Service methods
create timezone-aware schedules and wake an event-driven polling scheduler.
Atomic claims and a delivery-start marker coordinate concurrent processes and
linearize edit/cancellation/deletion against notifier dispatch. Atomic edits
bind to exact current message/due values and retain timezone/recurrence. Terminal
or optional desktop notifiers deliver text; successful recurrence calculation
advances the record. Persisted scheduled-action data cannot execute.

See [scheduling.md](scheduling.md).

### Integrations and plugins

Integrations expose typed lifecycle/status/operation metadata and are registered
before actions are composed. Credentials use explicit resolvers, while the
built-in network transport fixes bounded JSON requests to one HTTPS origin.
GitHub, SMTP/IMAP email, and CalDAV calendar are live adapters; email/calendar
also ship deterministic in-memory implementations for tests and demos. Live
email uses only the standard library, while CalDAV depends on the optional
`integrations` extra.

Plugins are installed `jarvis.plugins` entry points. Discovery can occur without
import. A plugin context stages actions, integrations, and listeners for an
atomic host-registry commit; disable/shutdown unregister managed handles and
revoke the context. This is reliability isolation only: imported plugins are
fully trusted Python in the same process.

See [integrations.md](integrations.md) and [plugins.md](plugins.md).

### Interfaces and application lifecycle

`create_application` owns configuration, event bus, repositories, optional
providers, browser, scheduler, integration registry, plugin manager, and runtime.
`JarvisApplication.start()` connects integrations, discovers/optionally loads
plugins, and starts reminder polling. `aclose()` stops or closes each optional
service and persistent store while isolating cleanup failures.

The CLI confirmer blocks only its own terminal input thread; the GUI permission
broker awaits local dialogs without blocking the async loop. Both feed the same
`PermissionManager`. Cancellation is cooperative and cannot transactionally
undo already dispatched worker-thread or external effects.

See [gui.md](gui.md) and [configuration.md](configuration.md).

### Computer and platform adapters

System inspection is built on `psutil`. Desktop input is behind validated mouse
and keyboard controllers and an optional PyAutoGUI boundary. Screenshot capture
has persistent and temporary lifetimes. Window inspection uses a separate
Windows adapter. Platform-specific code is injected or mocked in tests so that
ordinary CI does not manipulate a real desktop.

Application control is allowlisted. The entire built-in Windows catalog
currently
contains Notepad, Calculator, and Visual Studio Code aliases. Resolution must
produce an existing absolute executable from trusted locations, and processes
are opened with `shell=False`, a trusted working directory, and a minimal
allowlisted child environment. Windows launch is refused while JARVIS is
elevated. Close and discovery operations match the resolved absolute executable
rather than an untrusted process name.

Keyboard and mouse controllers operate on the foreground desktop; they are not
application sandboxes. The default CLI's execution guard binds keyboard
approval to the exact active title/handle observed immediately before input,
waits briefly for an expected title after an application launch, rejects known
terminal shortcuts and targets, and cancels if focus changes. Title matching
does not prove process ownership, so input remains experimental, classified
`SENSITIVE`, always confirmation-gated, and unsuitable for unattended use.
Memory reads, machine inventory (processes, IP/network details, storage mounts,
running apps, and window titles), and provider-backed screen analysis are also
`SENSITIVE`; lower-detail metrics and local screenshot capture remain separate
`READ` actions.

The keyboard binding uses a Windows handle and exact title rather than
PID/executable identity. Pointer actions validate desktop coordinates, capture
the foreground handle/title for the request, and recheck it before their backend
call. This rejects focus changes but does not prove process ownership; that
limitation remains a visible safety boundary.

## Events and failure handling

The in-process event bus supports synchronous or asynchronous subscribers and
isolates subscriber failures by default. Core event names cover assistant,
message, AI, action, permission, memory, screenshot, browser, reminder,
integration, and plugin lifecycle points. It is an observability boundary, not
a distributed broker and not an authorization mechanism. Event payloads are
snapshotted, and plugin publications receive host-controlled source attribution.
`user.message` publishes metadata rather than raw command text. Future event
payload expansions must preserve that minimization or receive explicit privacy
review.

Action failures are returned as structured results suitable for user-facing
orchestration. Expected domain errors are translated into concise messages;
credentials, raw provider payloads, and exception objects should not be exposed
to normal users or logs.

## Deliberate non-capabilities

JARVIS does not provide an unrestricted shell, model-generated Python
execution, a terminal tool, automatic visual clicking, a bundled wake-word
engine, background surveillance, persistent browser profile, plugin sandbox,
phone companion, remote-device control, or an autonomous agent loop. These
boundaries are part of the architecture, not missing shortcuts.

The future direction is tracked in [ROADMAP.md](ROADMAP.md). Operational risks
and trust boundaries are consolidated in [security.md](security.md).
