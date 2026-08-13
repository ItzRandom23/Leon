# JARVIS roadmap

This is a direction of travel, not a delivery schedule. A status means the
foundation described here exists in the repository; it does not mean every
adapter is production-ready, available on every platform, or enabled by
default.

Status legend:

- ✅ **Implemented** — the scoped foundation exists and has automated tests;
- 🚧 **Experimental** — usable behind configuration, credentials, optional
  dependencies, or platform constraints, with known limitations; and
- 📋 **Planned** — design direction only, not a shipped capability.

## Phase 1 — Core architecture ✅ Implemented

- Interactive terminal and compatibility skill router
- Typed TOML/environment configuration and redacted diagnostics
- Structured results, errors, logging, and an async-friendly event bus
- Strict provider-neutral action registry and validation
- Bounded deterministic offline planner, including short sequential plans
- Basic system information, date/time, help, and allowlisted app launching

JARVIS never treats user or model text as an unrestricted shell command.

## Phase 2 — LLM integration ✅ Implemented foundation / 🚧 Experimental mode

- Replaceable `LLMProvider` contract
- Official OpenAI Responses API adapter
- OpenAI-compatible Chat Completions tool-calling adapter
- Bounded, active-session conversation history
- Registered action schemas supplied as tools rather than hand-maintained prompt
  capabilities
- Local validation and permission checks after every proposed tool call

Provider-backed operation is disabled unless explicitly configured. It requires
a suitable model, network endpoint, and credentials where applicable. Endpoint
compatibility and model behavior vary; the deterministic planner remains the
offline baseline.

## Phase 3 — Voice ✅ Implemented adapters / 🚧 Experimental mode

- Replaceable speech-to-text and text-to-speech contracts
- Optional SpeechRecognition adapter using its Google recognizer
- Optional local `pyttsx3` speech output
- Configuration to disable voice and TTS independently
- Wake-word extension contract

No wake-word engine is bundled, and JARVIS does not continuously listen by
default. Microphone support, the Google transcription service, and local speech
engines remain environment-dependent.

## Phase 4 — Persistent memory ✅ Implemented

- Explicit `preferences`, `facts`, `projects`, and `aliases` categories
- Replaceable repository contract and versioned SQLite implementation
- Remember, recall, list, search, forget, clear, and count operations
- Configurable database path and explicit lifecycle
- Separation between bounded session history and persistent memory

The current manager writes only after an explicit remember operation. It does
not archive conversations automatically, and SQLite values are not encrypted by
JARVIS.

## Phase 5 — Computer control ✅ Implemented foundation / 🚧 Windows-first

- Allowlisted open, close, find, and list application operations
- CPU, RAM, storage, battery, uptime, OS, process, and network inspection
- Validated mouse movement, clicking, and scrolling
- Bounded typing, key presses, and hotkeys
- Persistent and managed-temporary screenshots
- Windows active/visible window inspection and exact-title focus
- `READ`, `ACTION`, `SENSITIVE`, and `DESTRUCTIVE` permission policies with
  `allow`, `ask`, and `deny`

Desktop control requires an interactive session, OS permission, and optional
desktop dependencies. Built-in application discovery is currently Windows-only
and limited to trusted Notepad, Calculator, and Visual Studio Code definitions.
There is no general process killer, executable launcher, or shell.

Closing an allowlisted application is destructive and always confirmation-gated
because every matching approved process is terminated and unsaved work may be
lost.

## Phase 6 — Vision and screen understanding ✅ Implemented foundation / 🚧 Experimental mode

- Replaceable semantic vision contract
- Official OpenAI Responses image adapter
- OpenAI-compatible multimodal adapter
- Temporary screenshot cleanup around analysis
- Semantic descriptions and visible-text results
- Bounding-box models and validation foundation for future providers

Current providers explicitly do not support coordinate grounding. JARVIS does
not guess coordinates or automatically click a described visual target.

## Phase 7 — Browser automation ✅ Implemented foundation / 🚧 Experimental mode

- Optional Playwright controller for Chromium, Firefox, or WebKit
- Bounded ephemeral sessions and tabs
- Public HTTP(S) navigation, back/forward/reload, text reads, literal find, and
  accessibility-oriented snapshots
- Verified click, bounded type, allowlisted key, and scroll actions
- Opaque snapshot IDs tied to a stable page/tab/node fingerprint
- Per-request public-host validation, redirect checks, and session DNS pinning
- Service workers and accepted downloads disabled; only bounded download
  metadata is retained in memory

This browser is deliberately not a general remote-debugging or arbitrary-script
surface. It requires an optional browser runtime, treats every page as untrusted,
and remains supervised.

## Phase 8 — Tasks, reminders, and scheduling ✅ Implemented foundation

- Versioned SQLite reminder repository and idempotent creation
- Timezone-aware one-time, relative, daily, weekly, and weekday schedules
- Daylight-saving gap handling and UTC persistence
- Scheduled, cancelled, and triggered lifecycle states; missed-item inspection
- Atomic delivery leases for concurrent scheduler safety
- Terminal notifier and optional desktop notifier
- Permissioned create/list/edit-reschedule/cancel/delete actions and management
  interfaces

The scheduler runs only while a terminal or GUI application is running. It
delivers notifications; persisted scheduled-action metadata is intentionally
inert and cannot start an autonomous action.

## Phase 9 — External integrations ✅ Implemented contracts / 🚧 Experimental adapters

- Provider-neutral lifecycle, operation metadata, registry, and credential
  contracts
- Bounded fixed-origin HTTPS JSON transport with sanitized failures
- GitHub repositories, issues, pull requests, workflow status, and releases;
  issue creation is an explicit confirmed write
- Email list/search/read/draft/send contract with live draft verification
- Calendar list/search/create/update/delete contract with live target checks
- Deterministic in-memory email and calendar providers for tests and demos
- Opt-in live email (standard-library SMTP sending and IMAP reading) and CalDAV
  calendar adapters behind resolved credentials

GitHub, SMTP/IMAP email, and CalDAV calendar are bundled live-account adapters
and remain experimental. No Gmail, Outlook, Exchange, Slack, or
collaboration-service adapter is included yet. External content remains
untrusted and each mutation stays behind the core action and permission
boundaries.

## Phase 10 — Plugin and skill SDK ✅ Implemented foundation / 🚧 Trusted-code mode

- Plugin API version 1 and `jarvis.plugins` package entry points
- Metadata, compatibility rules, status reporting, and discovery without import
- Explicit inspection, enable, disable, persistent enablement, and optional
  startup loading
- Atomic staged registration for actions, integrations, and event listeners
- Failure isolation, rollback, shutdown, context revocation, and host-controlled
  event provenance
- Separately installable example plugin

Plugins execute ordinary local Python with JARVIS's operating-system access;
they are not sandboxed. A public marketplace, signing/reputation system, and
process-level isolation remain future design work rather than implied security
properties of this API.

## Phase 11 — Desktop GUI ✅ Implemented foundation / 🚧 Experimental runtime

- Framework-neutral controller, view models, data provider, and log store
- Async fail-closed permission broker with bounded exact action details
- Optional PySide6/qasync desktop application using the shared runtime
- Chat, Memory, Tasks, Integrations, Plugins, Settings, Logs, and About pages
- Voice capture button when configured; memory/reminder/plugin controls
- Activity/status views, cooperative cancellation, light/dark/system themes,
  and optional system-tray behavior

The terminal remains fully supported. Permission dialogs default to Deny and
must use the exact broker bound to the displayed runtime. Cancellation after an
action starts is reported as outcome unknown, not rolled back. PySide6 needs an interactive desktop and
has not been implied to work on every display server merely because the
framework-neutral controller is covered by automated tests. Cancellation is not
transactional after an external side effect has started.

## Phase 12 — Phone companion 📋 Planned

A deliberately limited companion experience designed around strong device
identity, minimal permissions, and transparent data movement.

## Phase 13 — Remote device control 📋 Planned

Authenticated, encrypted, revocable remote sessions with device-level least
privilege and safe handling of partial connectivity.

## Phase 14 — Advanced computer-use agent 📋 Planned

Grounded perception and carefully bounded interaction only after reliable target
identification, uncertainty handling, checkpoints, and mature permission UX.

## Phase 15 — Autonomous task execution 📋 Planned

Explicit goals, step and time budgets, previews, checkpoints, cancellation,
audit trails, recovery, and strict restrictions on unattended side effects.
Unbounded background autonomy is not a goal.

## Phase 16 — Multi-device JARVIS network 📋 Planned

Revocable device identity, encrypted coordination, conflict handling,
least-privilege capability delegation, and clear ownership of memory and logs.

## Phase 17+ — Community-driven development 📋 Planned

Future phases will be proposed in public based on demonstrated user value,
maintainer capacity, threat modeling, portability, and the maturity of earlier
boundaries. A public plugin marketplace, smart-home integrations, specialized
agents, or other large surfaces require separate design review.

## Cross-cutting commitments

Every phase must preserve:

- explicit action schemas, validation, permission checks, and safe failures;
- no unrestricted execution of model- or user-generated code;
- minimal secret, conversation, memory, audio, and screenshot exposure;
- platform adapters that can be tested without real side effects;
- observable, cancellable operations and bounded resource use;
- accessibility across text, optional voice, and future graphical interfaces;
  and
- documentation that distinguishes implemented, experimental, and planned
  behavior.

## Starting roadmap work

Open one focused issue describing the user problem, non-goals, data and system
access, risk category, confirmation behavior, platforms, dependencies, failure
and recovery behavior, and a practical test plan. Roadmap placement is not
approval to implement a capability or bypass security review.
