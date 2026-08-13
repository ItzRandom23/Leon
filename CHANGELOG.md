# Changelog

Notable changes to JARVIS are documented here. The project follows semantic
versioning as closely as practical during 0.x development; public interfaces can
still change between minor releases.

## [0.3.0] - 2026-08-13

### Added

- Phase 7's opt-in Playwright browser foundation: isolated ephemeral contexts,
  bounded tabs/sessions, public-web navigation, visible text, literal find,
  accessibility-oriented snapshots, verified click/type/key actions, history,
  scrolling, and non-persistent download metadata.
- Phase 8's versioned SQLite reminder store, one-time/relative/daily/weekly/
  weekday recurrence, IANA timezone and daylight-saving handling, missed-item
  inspection, consent-bound atomic edit/reschedule, cancellation/deletion,
  idempotency, terminal notifications, and optional desktop notifications.
- Phase 9's provider-neutral integration lifecycle, credential resolution,
  bounded HTTPS JSON transport, typed GitHub REST operations, email/calendar
  contracts, and deterministic in-memory email/calendar providers.
- Phase 10's plugin API version 1, `jarvis.plugins` entry-point discovery,
  compatibility/status metadata, staged capability registration, persistent
  enablement state, lifecycle rollback, and a separately installable example.
- Phase 11's framework-neutral GUI controller/data models, asynchronous
  fail-closed permission broker, and optional PySide6/qasync desktop interface
  with Chat, Memory, Tasks, Integrations, Plugins, Settings, Logs, and About
  pages.
- CLI management commands for reminders, memory, plugins, and the GUI; expanded
  local diagnostics for the new optional dependencies and storage locations.
- Browser, scheduling, integrations, plugins, GUI, installation, configuration,
  and cross-cutting security documentation.

### Changed

- Moved all terminal and GUI modes onto the same application composition root,
  action registry, permission manager, event bus, repositories, providers, and
  shutdown lifecycle.
- Expanded typed TOML/environment configuration for browser, scheduler,
  integrations, plugins, and GUI preferences.
- Added static type checking to the development and continuous-integration
  quality gates.
- Updated the public roadmap: Phases 1–11 now have implemented foundations;
  runtime-dependent surfaces remain explicitly experimental, and Phases 12+
  remain planned.

### Security

- Browser requests are restricted to structurally valid public HTTP(S) targets.
  A per-session authenticated loopback proxy resolves/pins public DNS and
  connects to numeric destinations for HTTP/HTTPS tunnels; direct and alternate
  browser network surfaces are constrained. HTTPS downgrades are blocked,
  service workers/accepted downloads are disabled, popup/resources are bounded,
  and partial startup is cleaned up.
- Browser interactions use opaque IDs from a fresh bounded snapshot and bind
  approval to the exact stable node, page/tab, role, accessible name, and
  enabled state. Page-authored content remains explicitly untrusted.
- Reminder delivery uses atomic leases to avoid duplicate notification across
  concurrent schedulers. Cancel/delete requests bind to the current reminder
  text, and persisted scheduled-action metadata cannot execute.
- Integration credentials have redacted string forms. Network requests use a
  fixed HTTPS origin, bounded JSON bodies, sanitized failures, and same-origin
  redirects. External writes retain explicit confirmation boundaries.
- Email send verifies the live draft recipient, subject, and body; calendar
  update/delete verify live target fields before mutation.
- Reminder edit/reschedule verifies the live message and UTC due instant,
  refuses non-scheduled/already-delivering records, and retains recurrence and
  timezone.
- Reminder edit/reschedule and cancellation retain mandatory confirmation even
  if the `SENSITIVE` or `ACTION` category is configured as allowed.
- Plugin discovery can list entry points without import. Inspection and
  enablement require confirmation; registration is staged and rolled back on
  failure; plugin event provenance is host-controlled. Plugins nevertheless run
  as unsandboxed trusted Python.
- GUI permission prompts are asynchronous and fail closed on close, timeout,
  invalid response, or cancellation. Display values are bounded, secrets are
  redacted where appropriate, and Unicode formatting controls are escaped.
- Machine inventory reads that expose processes, IPs, storage mounts, running
  apps, or window titles are `SENSITIVE`; pointer actions bind/recheck the
  foreground handle/title.
- External/tool content is untrusted. Model follow-up is budgeted, has no tools,
  cannot chain an action, and is discarded from ordinary conversation history.
- The GUI requires the runtime-bound broker, defaults permission dialogs to
  Deny, identifies external vision/STT/GitHub status, and reports cancellation
  after action start as an unknown outcome.

### Known limitations

- Browser automation requires the optional Playwright package and a separately
  installed browser binary. No live-browser session was required by the normal
  unit test suite, and browser defenses are not a complete web sandbox.
- Reminder delivery occurs only while an interactive terminal or GUI process is
  running. Desktop notification behavior depends on `plyer` and the operating
  system; scheduled actions remain inert metadata.
- GitHub is the only bundled live-account integration. The built-in email and
  calendar implementations are process-local demos, not Gmail, Outlook, or
  CalDAV adapters.
- Third-party plugins are not isolated from the process, filesystem, network, or
  operating system. Install and enable only code you trust and have reviewed.
- The PySide6 interface requires an interactive display and optional packages.
  Controller behavior is covered independently; successful automated tests do
  not claim a manually exercised desktop session on every platform.
- Cancellation cannot roll back a request that has already reached an external
  service, browser, or worker thread. Verify any outcome reported as unknown.

## [0.2.0] - 2026-08-13

### Added

- Typed TOML and `JARVIS_*` environment configuration with secret redaction.
- Provider-neutral action definitions, strict JSON schemas, registry validation,
  structured outcomes, and sequential stop-on-failure execution.
- Configurable `READ`, `ACTION`, `SENSITIVE`, and `DESTRUCTIVE` permission
  policies with fail-closed confirmation behavior.
- Lightweight in-process events and a bounded deterministic offline planner.
- Replaceable LLM interface, official OpenAI Responses adapter, and
  OpenAI-compatible Chat Completions adapter.
- Bounded active-session conversation models without automatic persistence.
- Optional SpeechRecognition Google speech-to-text and local `pyttsx3`
  text-to-speech adapters, plus a wake-word extension contract.
- Explicit categorized memory backed by a versioned SQLite repository.
- Windows-first application/process, system, mouse, keyboard, screenshot, and
  window-control foundations.
- Replaceable semantic vision interface with official OpenAI Responses and
  OpenAI-compatible image adapters.
- Architecture, actions, permissions, memory, provider, vision, development,
  security, and expanded roadmap documentation.

### Changed

- Expanded the repository from the Phase 1 compatibility skill foundation to a
  Phase 1–6 modular 0.x foundation.
- Kept provider, voice, desktop, and vision behavior opt-in and explicit about
  dependencies, credentials, and platform support.
- Clarified community templates for provider proposals and later roadmap phases.

### Security

- Provider output cannot introduce arbitrary tools or bypass local argument
  validation and permission policy.
- Built-in application operations resolve exact aliases to trusted absolute
  executables and never invoke a shell.
- Desktop inputs, screenshot paths, window titles, provider URLs, and memory
  queries are validated at their boundaries.
- Provider credentials require HTTPS, and the built-in transport blocks
  cross-origin and HTTPS-downgrade redirects.
- Provider URLs reject embedded credentials, queries, and fragments; response
  bodies are bounded and transport errors are sanitized.
- Terminal output strips ANSI escapes, unsafe controls, and bidirectional
  formatting characters.
- Built-in vision providers do not claim coordinate grounding or automatically
  click semantic targets.
- Keyboard input, persistent-memory reads, and provider-backed screen analysis
  are classified `SENSITIVE` and confirmation-gated by default.
- Registered text input is capped at the complete 500-character confirmation
  preview.
- Closing an allowlisted application is `DESTRUCTIVE` and retains mandatory
  confirmation because it can discard unsaved work.

### Known limitations

- JARVIS remains pre-alpha and should not run unattended or with elevated
  privileges.
- Provider-backed modes require explicit configuration, appropriate credentials,
  and compatible models/endpoints.
- Voice and desktop support depend on optional packages, hardware, an interactive
  session, and operating-system permissions.
- Voice activation requires an explicit Google/SpeechRecognition provider; the
  network-backed adapter can upload captured audio to Google.
- Computer control and application discovery are Windows-first.
- Guarded open-and-type plans wait for an expected active window title, bind
  confirmation to its handle and title, and cancel if focus changes; title
  matching still does not prove process identity.
- Mouse actions are coordinate-validated but not bound to a target window;
  user-message events publish metadata rather than raw command text.
- No wake-word engine, browser automation, unrestricted shell, autonomous agent
  loop, or automatic visual clicking is included.

## [0.1.0] - 2026-08-13

### Added

- Interactive Phase 1 terminal foundation with deterministic skills for
  greeting, exit, help, date, time, system information, and allowlisted Windows
  application launch.
- Python packaging, tests, linting, CI, and initial community documentation.
