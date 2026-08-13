# Development guide

JARVIS targets Python 3.11 or newer and uses a `src/` package layout. Keep
changes small, typed, dependency-injected where side effects are involved, and
explicit about platform and safety boundaries.

## Setup

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

On macOS or Linux, create the environment with `python3.11 -m venv .venv` and
activate it with `source .venv/bin/activate`.

Optional voice, screenshot/desktop-control, browser, notification, and GUI
dependencies should be installed only when developing those adapters. Check
`pyproject.toml` and [installation.md](installation.md) for the extras available
on the current branch; optional imports must remain lazy and fail with a clear
domain error when an extra is absent.

## Local checks

Run the same portable checks expected by CI:

```text
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Tests must not require paid credentials, a live provider/account/browser, a
microphone, a notification service, an interactive desktop/GUI, or a particular
operating system unless clearly marked as an opt-in integration test. Inject
transports, DNS resolvers, clocks, filesystem roots, process factories,
repository connections, platform APIs, Qt-independent controllers, and desktop
backends. Report fake-based and manual runtime verification separately.

## Design rules

- Keep provider parsing in provider adapters and operating-system behavior in
  platform adapters.
- Use registered actions for capabilities; do not add an unrestricted shell or
  execute model/user output.
- Validate at the boundary before a side effect, then apply permission policy.
- Use exact allowlists for executable applications and other security-sensitive
  identifiers.
- Treat provider/web/plugin/tool output, persisted data, screen contents, window
  titles, and user text as untrusted input. A post-action model summary must not
  receive tools or become a hidden action-chaining path.
- Keep active-session conversation history distinct from explicit persistent
  memory.
- Never log credentials, complete sensitive prompts, typed secret values, or
  screenshot contents.
- Preserve event-data minimization. `user.message` currently publishes metadata
  rather than raw command text; new subscribers and payload fields still require
  privacy review.
- Preserve safe failure behavior when an optional dependency or platform
  feature is unavailable.
- Compose new interfaces through `create_application`; do not bypass the shared
  action, permission, event, storage, and shutdown lifecycle.
- Keep cancellation semantics honest. Once worker-thread, desktop, browser, or
  external-service work is dispatched, its outcome may be unknown rather than
  rolled back.

## Adding an action

1. Define one cohesive handler and inject all side-effecting boundaries.
2. Declare strict `ActionParameter` entries with useful constraints.
3. Assign the highest applicable `RiskLevel`.
4. Register it in the runtime composition root; do not manually duplicate its
   schema in provider prompts.
5. Add deterministic planner wording only when it can be bounded and
   unambiguous.
6. Test schema export, valid execution, each meaningful validation failure,
   denial with no side effect, handler failure, and sequential stop behavior.
7. Update [actions.md](actions.md), [permissions.md](permissions.md), and user
   examples when behavior changes.

## Adding a provider

Implement the narrow `LLMProvider`, `VisionProvider`, `SpeechToText`, or
`TextToSpeech` contract. Keep vendor payloads out of core models. Validate base
URLs and configuration, bound timeouts and payload sizes, translate malformed
responses into domain errors, and use an injected fake transport in tests.

Live-provider tests must be opt-in and must never run in ordinary CI. Start with
the Provider Request issue template and update [providers.md](providers.md).

External account adapters use the separate integration lifecycle/operation
contracts and Integration Request template. See
[integrations.md](integrations.md).

## Memory changes

Preserve the `MemoryRepository` contract and explicit-write rule. Schema changes
need a forward migration, compatibility tests, bound SQL parameters, and clear
behavior for databases created by a newer JARVIS version. Do not overload the
SQLite file with unrelated session logs.

## Computer and vision changes

Desktop code must validate coordinates, key names, text lengths, process paths,
capture directories, and window identifiers before calling its backend. Tests
should use fakes and verify that invalid or denied requests make no backend
call.

Do not implement visual clicking from semantic prose. A future grounded vision
provider must advertise grounding, return validated non-ambiguous bounds, and
still pass a separate permission check before mouse input.

Desktop safety tests should cover handle/title changes, misleading titles,
cross-turn compositions, and mouse focus changes. The current keyboard guard is
title/handle-based rather than PID-bound, and mouse actions have no target-window
binding; do not describe either as an application sandbox.

## Browser, scheduling, plugin, and GUI changes

- Browser tests inject the Playwright boundary and DNS resolver. Cover every
  request/redirect, public-host pinning, stale/detached/substituted elements,
  snapshot invalidation, operation caps, download non-persistence, and cleanup.
- Reminder tests inject clocks/notifiers and use temporary SQLite stores. Cover
  timezone/DST recurrence, migrations, idempotency, concurrent claims,
  cancellation at the delivery boundary, retries, expired leases, and
  post-notification persistence uncertainty.
- Plugin tests use fake entry points. Cover discovery without import, exact API
  compatibility, duplicate capabilities, every lifecycle failure, reverse
  rollback, cancellation, persistent enablement, repeated disable/shutdown, and
  revoked contexts. Never imply that in-process plugins are sandboxed.
- GUI tests target the framework-neutral controller, broker, and data models.
  Keep widget imports optional; permission close/timeout/cancel must fail closed,
  display data must be bounded/sanitized/redacted, and actions must traverse the
  shared runtime.

See [browser.md](browser.md), [scheduling.md](scheduling.md),
[plugins.md](plugins.md), [gui.md](gui.md), and
[security.md](security.md).

## Pull requests

Use the repository template. Describe observable behavior, explicit non-goals,
platform scope, dependencies, data flow, risk classification, confirmation UX,
and exact verification commands. Update [CHANGELOG.md](../CHANGELOG.md) for
notable user-facing changes and follow [CONTRIBUTING.md](../CONTRIBUTING.md).
Configuration changes must update [configuration.md](configuration.md) and
[`.env.example`](../.env.example) together.
