# Security guide

This document explains how to operate and extend JARVIS safely. To report a
vulnerability, use the private process in [SECURITY.md](../SECURITY.md); do not
put exploit details or real private data in a public issue.

JARVIS is pre-alpha software that can inspect personal data and, when enabled,
affect applications, websites, and external accounts. Its validation and
permission layers reduce risk but do not turn the host, browser, providers, or
plugins into a sandbox.

## Trust model

| Boundary | Treatment |
| --- | --- |
| Core schemas, permission engine, composition root, repositories | Trusted project boundary; still subject to bugs and local compromise |
| User confirmations | Required authority for prompted operations; meaningful only when details are reviewed |
| Model output | Untrusted proposal; may select only registered actions and is validated/permissioned locally |
| Web pages and browser accessibility text | Untrusted content, including prompt injection and deceptive labels |
| GitHub, email, calendar, and other provider content | Untrusted remote/account data |
| Third-party plugins | Fully trusted executable Python after install/enable; not isolated |
| OS, active desktop, DNS, proxy, network, remote service | External security boundary not controlled by JARVIS |
| SQLite databases, screenshots, logs, notification history | Sensitive local artifacts protected primarily by OS/filesystem policy |

Do not elevate page/provider/model text into instructions. The user request and
local policy define the goal; untrusted content is data used within that goal.

## Action and permission boundary

Every built-in capability is an `Action` with strict named parameters, bounded
types/lengths, and a `READ`, `ACTION`, `SENSITIVE`, or `DESTRUCTIVE` category.
Model-proposed and deterministic plans use the same registry, validation,
permission manager, and sequential stop-on-failure executor.

Defaults allow `READ` and ask for all other categories. Confirmation fails
closed when no confirmer exists or the prompt is rejected, closed, timed out,
invalid, or cancelled. `DESTRUCTIVE` actions cannot be made silently automatic.
Selected generic input, browser interaction, external write, and plugin-loading
actions also retain an always-confirm floor.

Confirmation is not a general capability grant. Review the exact values shown:

- target application/window or browser role/name;
- complete bounded text to type, send, or create;
- URL, service, account, repository, recipient, event, or record identifier;
- expected current values used to reject stale targets; and
- whether a remote provider will receive private content.

Built-in consent-to-target checks include stable browser element handles,
reminder ID plus current message, email draft plus exact recipient/subject/body,
calendar event plus live title/start checks, and guarded Windows keyboard target
handle/title. They reduce stale approvals; they do not prove business meaning,
process identity, or a remote service's final state.

## Computer and GUI safety

- Built-in app launch resolves exact catalog aliases to trusted absolute
  executables and uses `shell=False`; arbitrary command lines/paths are rejected.
- JARVIS refuses built-in application launch while elevated on Windows. Run as a
  standard user with only the OS permissions needed.
- Generic keyboard input can control terminals, Run dialogs, browsers, password
  managers, or privileged applications. Known terminal shortcuts/titles and
  type-then-Enter sequences are blocked, but title/handle binding does not prove
  executable or PID identity.
- Pointer coordinates are bounded and the request captures/rechecks the exact
  foreground handle/title before the backend call. That does not prove
  PID/executable identity; verify the target before every approval.
- PyAutoGUI's fail-safe stays enabled. Do not disable it in an adapter.
- GUI permission dialogs show exact details; private text may therefore be
  visible on screen. GUI cancellation is cooperative and cannot undo an
  already-dispatched thread, browser call, desktop input, or remote mutation.
  After an action starts the UI reports its outcome as unknown, rather than
  claiming cancellation; verify it at the target.
- The GUI accepts only the permission broker bound to its exact runtime, and
  Deny is the default dialog action. Status explicitly identifies enabled
  external vision, STT, and GitHub components.
- Minimize-to-tray can keep reminders/providers/plugins running after the main
  window disappears. Use the tray Quit action when you intend to stop JARVIS.

No built-in semantic vision provider supplies trusted coordinates. A screen
description must not be converted into an automatic click target.

## Browser safety

The Playwright adapter uses ephemeral contexts and a per-session authenticated
`127.0.0.1` proxy with no configured direct bypass. That proxy resolves/pins
only public addresses and connects to numeric destinations for HTTP and HTTPS
`CONNECT` traffic. Service workers, accepted downloads, QUIC, non-proxied WebRTC
UDP, background/speculative networking, resolver bypass, and unsafe init-script
surfaces are disabled or constrained. Popup/tab growth and proxy resources are
bounded. Element actions require a fresh bounded snapshot and revalidate the
exact node fingerprint. Partially created browser/proxy/runtime state is cleaned
up when startup fails.

Limitations:

- a public site can proxy requests to private systems;
- browser/OS or loopback-host compromise sits below the application policy;
- the proxy enforces tunnel destinations but does not decrypt HTTPS page
  content;
- a label can truthfully match the snapshot while the site's eventual effect is
  malicious or surprising;
- cookies and form data exist for the session even though the profile is not
  persisted by JARVIS; and
- typed data is sent to the selected site.

Do not use the browser as a privileged internal network client. Do not enter
credentials or payment/recovery data unless the exact destination and purpose
are deliberate. See [browser automation](browser.md).

## Provider and integration safety

AI/vision providers can receive prompts, bounded active history, action schemas
or results, and screenshots. GitHub receives authenticated API requests and
approved issue content. Future email/calendar adapters may handle highly
sensitive account data. Review each provider's retention, training, region,
subprocessor, deletion, cost, and incident policies before enabling it.

External service data and tool results are explicitly untrusted. Model request,
context, and action-result budgets bound what can be forwarded. A model
follow-up after an executed action receives no tools, cannot initiate another
action, and its raw exchange is discarded afterward rather than persisted into
ordinary conversation history.

Built-in transports:

- require HTTPS whenever a credential is present;
- reject embedded URL credentials and unsafe/cross-origin redirects;
- bound URLs, headers, JSON payloads, response bodies, and timeouts; and
- sanitize remote failures instead of displaying response bodies or secrets.

Use least-privilege, account/repository-scoped credentials and rotate/revoke on
suspected exposure. Prefer environment variables or a protected secret manager.
Known credential fields are redacted in configuration/log presentation, but
paths, account names, repository names, model names, and base URLs can still be
sensitive.

Remote writes can reach a service even when local cancellation follows. Network
timeouts are ambiguous: inspect the service before retrying a create/send/update
that may be non-idempotent.

## Plugin safety

Installing or enabling a plugin crosses the strongest trust boundary in this
repository. A plugin can ignore JARVIS actions and call Python, the filesystem,
network, subprocess, microphone, or desktop APIs directly. It can read process
environment credentials and in-process data. API compatibility, staged
registration, rollback, source attribution, and metadata improve reliability
and reviewability; they do not confine malicious code.

Before installation/enablement:

- verify source and artifact provenance, maintainer identity, license, build
  backend, dependency tree, release history, and update channel;
- review initialization, registration, event handlers, shutdown, threads,
  subprocesses, network access, storage, telemetry, and secret handling;
- run uncertain code in an OS/container/VM boundary without valuable secrets;
- leave auto-load off until the exact installed version is reviewed; and
- re-review after every upgrade.

Disabling removes managed registrations and calls shutdown but cannot undo
unmanaged process mutations or external effects. See [plugins](plugins.md).

## Reminder safety and reliability

Reminder and memory SQLite fields are plain text. Reminder content can also
appear in terminal scrollback or OS notification history. Do not put passwords,
tokens, recovery phrases, or unnecessary medical/legal/financial details in
them.

Atomic leases reduce duplicate notification by concurrent schedulers and
linearize cancel/delete against notification start. Notification delivery is
not transactional with the external terminal/OS surface. A crash after display
but before durable completion can eventually duplicate a notification.
Edit/reschedule is a mandatory-confirm `SENSITIVE` operation that rechecks exact
current message/due fields and refuses a non-scheduled or already-delivering
target. Scheduled-action metadata is intentionally non-executable.

## Data retention and backups

- Protect `jarvis.db`, `tasks.db`, `plugins.db`, screenshot directories, and log
  files with least-privilege filesystem permissions.
- Persistent screenshots remain until the user removes them. Temporary vision
  captures are removed locally by normal/failure cleanup in 0.3; remote copies
  follow provider policy.
- Active conversation history and GUI transcript/log views are memory-resident,
  but OS swap, crash dumps, terminal capture, plugins, and remote providers can
  still expose them.
- SQLite deletion does not guarantee forensic erasure from WAL pages, free
  pages, filesystem snapshots, backups, or storage media.
- Stop all JARVIS processes before copying/restoring databases and test recovery
  with synthetic data.

## Safe operating checklist

1. Install in a dedicated virtual environment from a reviewed revision.
2. Run as a standard OS user, never as administrator/root.
3. Leave optional browser, providers, voice, plugins, and tray behavior disabled
   until explicitly needed.
4. Run `jarvis config` and `jarvis doctor`; review their output before sharing.
5. Keep `ACTION`, `SENSITIVE`, and `DESTRUCTIVE` set to `ask`; set `READ` to
   `ask` or `deny` in sensitive environments.
6. Use least-privilege, short-lived/revocable provider credentials.
7. Close unrelated sensitive windows and verify focus before desktop actions or
   screenshots.
8. Treat all browser/provider/model output as untrusted and read confirmation
   details yourself.
9. Verify uncertain/cancelled remote or desktop outcomes before retrying.
10. Stop the process (including tray mode) when reminder polling, integrations,
    and plugins should no longer run.

## Deliberate non-capabilities

JARVIS 0.3 does not provide an unrestricted shell, arbitrary code execution from
model/user text, persistent browser profile, credential vault, plugin sandbox,
phone companion, remote device controller, grounded visual clicking, or
unbounded autonomous execution. A future roadmap item is not an existing
security property or authorization to bypass the current boundaries.

For contributor design rules, see [architecture](architecture.md),
[actions](actions.md), [permissions](permissions.md),
[development](development.md), and [CONTRIBUTING.md](../CONTRIBUTING.md).
