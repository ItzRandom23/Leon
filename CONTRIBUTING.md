# Contributing to JARVIS

Thank you for helping build JARVIS. The project values small, understandable
changes, explicit safety boundaries, accurate documentation, and tests that do
not manipulate a contributor's machine.

JARVIS 0.x contains implemented foundations through Phase 11. Provider-backed
AI/vision, voice, computer control, browser automation, GitHub, third-party
plugins, desktop notifications, and the PySide GUI remain experimental or
runtime-dependent. Phases 12+ are planned only. Work from the
[roadmap](docs/ROADMAP.md), but do not treat an entry as approval to add a broad
integration, remote surface, or autonomous behavior.

## Before you begin

- Search existing issues and pull requests.
- Use the Bug Report, Feature Request, New Skill Proposal, Integration Request,
  Plugin Proposal, or Provider Request template.
- Discuss new runtime dependencies, provider integrations, public interfaces,
  persistent data, and capabilities that affect a computer or external system
  before implementing them.
- Keep one pull request focused on one problem.
- Report exploitable vulnerabilities privately according to
  [SECURITY.md](SECURITY.md).

## Local setup

JARVIS requires Python 3.11 or newer.

1. Clone the repository and enter it:

   ```console
   git clone <repository-url>
   cd <repository-directory>
   ```

2. Create and activate a virtual environment.

   Windows PowerShell:

   ```powershell
   py -3.11 -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

   macOS or Linux:

   ```bash
   python3.11 -m venv .venv
   source .venv/bin/activate
   ```

3. Install development dependencies:

   ```console
   python -m pip install --upgrade pip
   python -m pip install -e ".[dev]"
   ```

Optional desktop, voice, browser, notification, and GUI extras are not needed
for ordinary unit tests. See `pyproject.toml`, the
[installation guide](docs/installation.md), and
[development guide](docs/development.md) before working on an adapter that uses
them.

## Branch and pull request workflow

```console
git switch main
git pull --ff-only
git switch -c feature/short-description
```

Prefixes such as `fix/`, `docs/`, `test/`, and `feature/` are encouraged. Make
focused commits, push to your fork, and complete the pull request template. Link
the issue with `Closes #123` when appropriate.

Pull requests should explain the user-visible outcome, design choices, explicit
non-goals, safety and privacy impact, platform limits, dependencies, and exact
verification performed. Draft pull requests are welcome for early review.

## Development expectations

- Target Python 3.11+ and use modern type hints.
- Preserve boundaries between orchestration, actions, providers, memory, and
  platform adapters. Terminal and GUI behavior must compose the same shared
  runtime rather than creating a privileged parallel path.
- Prefer the standard library when it is a reasonable fit; justify each runtime
  dependency.
- Avoid global mutable state, circular dependencies, duplicated routing logic,
  giant modules, and hardcoded user paths.
- Use structured diagnostics without logging credentials, screenshots, typed
  secrets, or unnecessary personal data.
- Turn expected failures into useful domain errors and safe user messages.
- Keep optional features optional and make their missing-dependency errors
  actionable.
- Update README, topic docs, and CHANGELOG when user-visible behavior changes.

### No unrestricted execution

Never turn user text or provider output into an arbitrary shell, PowerShell,
CMD, Bash, Python, or other code execution request. Computer capabilities must
be explicit registered actions with strict validation, a risk level, and local
permission checks. Do not weaken application path allowlists for convenience.

### Adding or changing an action

Document:

- representative user requests and unsupported wording;
- parameters, constraints, result, and failure behavior;
- supported platforms and optional dependencies;
- files, applications, devices, accounts, provider data, or personal data it
  can access;
- the highest applicable `READ`, `ACTION`, `SENSITIVE`, or `DESTRUCTIVE`
  category;
- confirmation text and fail-closed behavior; and
- a test strategy proving invalid or denied requests cause no side effect.

Inventory reads can be sensitive even when they do not mutate state. Process
lists, IP/network data, mounted storage, running applications, and active/visible
window titles belong in `SENSITIVE`, while low-detail CPU/RAM percentages,
battery, uptime, and OS version can remain `READ`.

The action registry is the source of provider tool schemas. Do not duplicate a
capability as hand-maintained provider prompt text. See
[docs/actions.md](docs/actions.md) and
[docs/permissions.md](docs/permissions.md).

### Adding a provider

Begin with a Provider Request. State whether processing is local or hosted,
what data leaves the machine, authentication and retention behavior, API and
tool compatibility, licensing, timeouts, rate limits, failure mapping, optional
dependencies, and how tests will use an injected mock transport. Ordinary tests
and CI must not need live credentials or incur API charges.

See [docs/providers.md](docs/providers.md).

### Browser changes

- Keep browser support opt-in, ephemeral, bounded, and free of generic
  JavaScript/selector/remote-debugging actions.
- Validate every top-level and subresource target. Tests for navigation changes
  must cover private/local addresses, redirects, DNS changes, unsafe URL forms,
  and controlled recovery.
- Element actions must bind consent to a fresh stable snapshot target rather
  than re-resolving a page-authored index or label after confirmation.
- Treat page text as untrusted and keep download acceptance/file persistence
  disabled unless a separately reviewed design defines storage and permission
  boundaries.

See [docs/browser.md](docs/browser.md).

### Scheduling and persistence changes

- Database changes need forward migrations, newer-version rejection, concurrency
  tests, and recovery behavior.
- Preserve the atomic claim/start/complete boundary and test cancellation races,
  notifier failures, process cancellation, expired leases, and concurrent
  schedulers.
- Document at-least-once/uncertain delivery honestly. Do not describe a
  notification as exactly once across external failures.
- Keep `ScheduledAction` metadata inert. Future scheduled side effects need a
  separate design with a fresh permission decision and bounded execution.

See [docs/scheduling.md](docs/scheduling.md).

### Integration changes

Begin with an Integration Request. Define every operation as read/write/delete,
use the highest applicable risk category, request only minimum credential
scopes, bind confirmation to the live target/content, and specify idempotency or
uncertain-outcome behavior. Do not add generic arbitrary HTTP/GraphQL/SQL
actions. Tests must inject a transport/provider and use no live account.

See [docs/integrations.md](docs/integrations.md).

### Plugin changes

Begin with a Plugin Proposal. Keep discovery free of imports, enforce exact API
compatibility, stage registry mutations atomically, and make disable/shutdown
idempotent and cancellation-safe. Documentation must state that plugins are
trusted unsandboxed Python; metadata declarations are not security enforcement.

See [docs/plugins.md](docs/plugins.md).

### GUI changes

- Use the framework-neutral controller/models and the shared runtime. Never read
  private stores or execute effects directly from a widget.
- Permission presentation must remain non-blocking, exact, bounded, and
  fail-closed. Closing or timing out cannot imply approval.
- Treat cancellation of already dispatched thread/network/desktop work as an
  uncertain outcome, not transactional rollback.
- Preserve keyboard navigation, labels, contrast, scalable layouts, non-color
  status cues, lazy optional imports, secret redaction, and control/bidi
  sanitization.

See [docs/gui.md](docs/gui.md).

### Memory, computer, voice, and vision changes

- Memory writes remain explicit. Database changes need forward migrations and
  compatibility tests.
- Desktop tests use fakes; they must not launch, close, click, type, focus, or
  capture the contributor's real session.
- Platform-specific code stays isolated and reports unsupported platforms
  cleanly.
- Voice must remain opt-in. Do not imply that Google recognition is local or
  that a wake-word engine exists.
- Semantic vision output is not a coordinate source. Do not add automatic
  visual clicking without a reliable grounded provider and a separate
  permission design.

## Tests and quality checks

Run:

```console
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Add or update tests for every behavior change, including meaningful failure
paths. External provider calls must be mocked. Portable CI should not fail only
because it has no API key, microphone, display, Windows session, or paid
service.

When optional runtime verification is not available, distinguish fake-based
automated coverage from a manually exercised browser, GUI, microphone,
notification service, or live account. Never claim an environment-dependent
surface was verified merely because its adapter imported or unit tests passed.

If a check cannot be run locally, say exactly which check and why in the pull
request. More details live in [docs/development.md](docs/development.md).

## Community standards

All participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md).
