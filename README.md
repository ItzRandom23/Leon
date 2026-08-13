# JARVIS

JARVIS is a modular, open-source Python foundation for a permission-aware
personal assistant. One composition root is shared by the terminal, optional
voice input, persistent reminders, browser and service adapters, plugins, and
the optional desktop GUI. Every built-in side effect is represented as a
validated action and passes through the same local permission engine.

> [!WARNING]
> JARVIS is **pre-alpha 0.x software**. Keep it supervised, review every
> confirmation, protect its SQLite databases and screenshots, and do not run it
> unattended or with administrator privileges.

## Capability status

| Phase | Foundation in this repository | Runtime status |
| --- | --- | --- |
| 1 — Core | Typed configuration, action schemas, permissions, events, deterministic planning, CLI | Implemented |
| 2 — AI | OpenAI Responses and OpenAI-compatible tool-calling adapters | Experimental; opt-in network service |
| 3 — Voice | Replaceable STT/TTS contracts, Google SpeechRecognition and `pyttsx3` adapters | Experimental; optional dependencies/hardware |
| 4 — Memory | Explicit categorized memory and versioned SQLite storage | Implemented |
| 5 — Computer control | Allowlisted apps, system inspection, screenshots, input, and Windows-window adapters | Experimental; Windows-first |
| 6 — Vision | Semantic OpenAI Responses and OpenAI-compatible image adapters | Experimental; no coordinate grounding |
| 7 — Browser | Bounded Playwright sessions, public-web navigation, snapshots, and verified element actions | Experimental; opt-in |
| 8 — Tasks | Persistent timezone-aware one-time and recurring reminders with notification scheduling | Implemented foundation; notification delivery needs a running process |
| 9 — Integrations | Lifecycle/credential contracts, GitHub REST adapter, email/calendar provider contracts | GitHub experimental; email/calendar built-ins are in-memory demos only |
| 10 — Plugins | Versioned entry-point API, discovery, staged registration, enable/disable state | Implemented foundation; third-party code is trusted and unsandboxed |
| 11 — GUI | Shared-runtime controller and optional PySide6/qasync desktop interface | Experimental; optional desktop runtime |

Phases 12 and later remain planned. JARVIS does **not** include unrestricted
shell execution, arbitrary executable launch, model-generated Python execution,
background surveillance, a phone companion, remote-device control, or an
unbounded autonomous agent loop. See the [roadmap](docs/ROADMAP.md).

## Requirements and installation

- Python 3.11 or newer
- `pip`
- Windows for the primary application/window-control experience
- Optional credentials, browser binaries, microphone, display, or desktop
  packages only for the corresponding experimental feature

```powershell
git clone <repository-url>
cd <repository-directory>
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

Install only the extras you intend to use:

```powershell
python -m pip install -e ".[desktop]"
python -m pip install -e ".[voice]"
python -m pip install -e ".[browser]"
python -m pip install -e ".[notifications]"
python -m pip install -e ".[gui]"
python -m pip install -e ".[dev]"
```

Playwright also needs a browser binary, for example:

```powershell
python -m playwright install chromium
```

The base install is enough for deterministic planning, configuration, memory,
reminder persistence with terminal notifications, and portable system
inspection. Read the detailed [installation guide](docs/installation.md) for
platform notes and optional-runtime verification.

## Quick start

The installed `jarvis` command and `python -m jarvis` are equivalent.

```console
jarvis doctor
jarvis config
jarvis
```

`jarvis doctor` performs local readiness checks and does not prove that a remote
endpoint, account, model, microphone, display session, or optional browser/GUI
runtime will work.

### CLI commands

| Command | Purpose |
| --- | --- |
| `jarvis` | Start the interactive text session and configured background scheduler |
| `jarvis --voice` | Start push-to-talk input with an explicitly configured STT provider |
| `jarvis --config PATH` | Load a specific TOML configuration file |
| `jarvis --debug` | Enable debug diagnostics with secret-redacting logging |
| `jarvis doctor` | Check configuration, storage, platform, and optional dependency readiness |
| `jarvis config` | Print effective configuration with credential fields redacted |
| `jarvis version` | Print the installed version |
| `jarvis gui` | Start the optional PySide6 desktop interface |
| `jarvis tasks list [--status ...]` | List persistent reminders |
| `jarvis tasks missed` | List scheduled reminders whose due time has passed |
| `jarvis tasks add --message TEXT (--at ISO_DATETIME | --in-minutes N) [--timezone ZONE]` | Add a one-time reminder |
| `jarvis tasks cancel ID --message TEXT` | Cancel the matching reminder after permission review |
| `jarvis tasks delete ID --message TEXT` | Permanently delete the matching reminder after confirmation |
| `jarvis memory list/search/delete/clear` | Inspect or delete explicit memory through permissioned actions |
| `jarvis plugins list/info/enable/disable` | Discover and manage installed `jarvis.plugins` entry points |

The exact reminder text on cancel/delete is an anti-stale-target check, not a
substitute for the permission prompt. Management commands open the database,
perform one operation, and exit; keep `jarvis` or `jarvis gui` running when you
expect reminders to be delivered.

## Configuration

JARVIS reads `~/.jarvis/config.toml` when present. `--config PATH` or
`JARVIS_CONFIG_FILE` selects another file, and supported `JARVIS_*` environment
variables override TOML. Relative paths in a loaded TOML file are resolved from
that file's directory.

The repository [`.env.example`](.env.example) is a reference list; JARVIS does
not automatically load dotenv files. Keep populated credentials outside source
control.

```toml
[ai]
enabled = false
provider = "openai-compatible"
model = ""
timeout_seconds = 30

[vision]
enabled = false
provider = "openai-compatible"
model = ""
timeout_seconds = 30

[voice]
enabled = false
tts_enabled = false
stt_provider = "none"
tts_provider = "none"
language = "en-US"

[memory]
enabled = true
auto_save = false
persist_conversations = false
allow_sensitive = false

[database]
path = "~/.jarvis/jarvis.db"

[logging]
level = "INFO"

[permissions]
read = "allow"
action = "ask"
sensitive = "ask"
destructive = "ask"

[screenshots]
directory = "~/.jarvis/screenshots"
keep_temporary = false

[browser]
enabled = false
browser_type = "chromium"
headless = true
profile = "ephemeral"
max_sessions = 2
max_tabs = 8

[scheduler]
enabled = true
database_path = "~/.jarvis/tasks.db"
timezone = "UTC"
poll_interval_seconds = 30
desktop_notifications = false

[integrations]
github_enabled = false
github_base_url = "https://api.github.com"
email_provider = "none"
calendar_provider = "none"

[plugins]
enabled = false
auto_load = false
state_path = "~/.jarvis/plugins.db"

[gui]
theme = "system"
minimize_to_tray = false
show_debug_logs = false
```

See [configuration](docs/configuration.md) for every setting and environment
variable, [providers](docs/providers.md) for AI/vision transports, and the
feature-specific guides linked below.

## Representative use

The offline planner deliberately recognizes a bounded vocabulary. A configured
model may propose only currently registered action calls; local validation and
permission checks remain authoritative.

```text
what's my CPU usage?
open notepad
open Notepad and type "Hello world"
remember that my development folder is D:\Projects
show memories
take a screenshot
what's currently on my screen?
start the browser
go to https://example.com
search the web for Python dataclasses
remind me in 20 minutes to stretch
exit
```

Browser pages, email, calendar text, GitHub content, plugin output, and model
responses are untrusted data. Never approve an action because instructions
embedded in that content tell you to do so.

## Permission and safety model

Default policies allow `READ` and ask for `ACTION`, `SENSITIVE`, and
`DESTRUCTIVE`. A blank, closed, timed-out, invalid, or negative confirmation
denies the action. `DESTRUCTIVE` actions always retain a confirmation floor;
generic keyboard input, browser element interactions, external sends/writes,
plugin inspection/enablement, and reminder cancellation also retain mandatory
confirmation for selected actions even if their category is configured as
`allow`; reminder editing/rescheduling also always confirms.

Important boundaries:

- Built-in application launch resolves exact aliases to trusted absolute paths,
  uses `shell=False`, and rejects arbitrary commands and executable paths.
- Keyboard actions are bound to an expected foreground-window handle/title in
  guarded sequences, but title matching does not prove process identity. Pointer
  actions also capture and recheck the foreground handle/title immediately
  before their backend call; the same identity limitation remains.
- Browser sessions use ephemeral contexts, block service workers and accepted
  downloads, and force traffic through a per-session authenticated loopback
  proxy that resolves/pins public numeric targets and rejects local, private,
  non-global, and DNS-rebinding destinations. Element actions require a fresh
  accessibility snapshot plus stable role/name/node recheck. This is defense in
  depth, not a complete web sandbox.
- Reminder scheduling notifies only. Persisted `ScheduledAction` metadata is
  inert and cannot execute an action in this release.
- GitHub issue creation, email sending, and calendar mutations use explicit
  action boundaries. Email/calendar live-account adapters are not bundled.
- Plugins are ordinary installed Python and are **not sandboxed**. Metadata
  declarations are review information, not an operating-system security
  boundary.
- GUI cancellation is cooperative. If external or worker-thread work has
  already started, its outcome can be unknown and must be verified at the
  target service or application.
- The Tasks GUI can create, list, atomically edit/reschedule, cancel, and delete
  reminders. Edit approval binds to expected current values and is rechecked at
  mutation time.

Read the [permission model](docs/permissions.md) and
[security guide](docs/security.md) before enabling side effects. Vulnerabilities
should be reported privately under [SECURITY.md](SECURITY.md).

## Data notes

- Memory, reminders, and plugin enablement are stored in separate versioned
  SQLite files and are not application-level encrypted.
- Active conversation history is bounded and memory-resident. It is not
  automatically persisted, but a configured remote LLM receives the history
  used for its request.
- Process lists, network/IP information, storage mounts, running applications,
  active/visible window titles, and the aggregate machine snapshot are
  `SENSITIVE` inventory reads. Lower-detail CPU/RAM, battery, uptime, and OS
  metrics remain `READ`.
- Tool and external-service results are explicitly untrusted. An optional model
  follow-up receives bounded context/results with no tools available; its raw
  exchange is discarded afterward and its prose cannot initiate another action.
- Persistent screenshots remain in their configured directory. Temporary
  vision captures are deleted locally after analysis; a remote provider may
  retain its copy under its own policy.
- Reminder text may appear in the terminal or operating-system notification
  history. The in-memory email and calendar providers lose their data when the
  process exits.
- Browser contexts are ephemeral and downloads are not accepted, but visited
  sites still receive ordinary network metadata and any data you approve for
  entry.

## Documentation

- [Installation](docs/installation.md)
- [Configuration](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [Actions and tool schemas](docs/actions.md)
- [Permission model](docs/permissions.md)
- [Memory](docs/memory.md)
- [AI and vision providers](docs/providers.md)
- [Vision and screenshots](docs/vision.md)
- [Browser automation](docs/browser.md)
- [Tasks and scheduling](docs/scheduling.md)
- [External integrations](docs/integrations.md)
- [Plugin SDK](docs/plugins.md)
- [Desktop GUI](docs/gui.md)
- [Security guide](docs/security.md)
- [Development guide](docs/development.md)
- [Roadmap](docs/ROADMAP.md)

For development:

```console
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for review and testing expectations.

## License

JARVIS is available under the [MIT License](LICENSE).
