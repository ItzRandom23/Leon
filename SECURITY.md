# Security policy

JARVIS can inspect local state and, when explicitly enabled and authorized,
control parts of a desktop, browser, reminder scheduler, or external account.
Security and privacy reports are welcome, especially for issues that cross
validation, permission, provider, memory, screenshot, browser, scheduler,
integration, plugin, GUI, or platform boundaries.

## Supported versions

JARVIS is pre-alpha 0.x software. Security fixes are made on the current default
branch and latest 0.x release only. Older snapshots may not receive patches.

## Report a vulnerability privately

Use the repository's **Security → Report a vulnerability** flow (GitHub private
vulnerability reporting). Do not open a public issue, discussion, or pull
request containing exploit details.

If private vulnerability reporting is unavailable, contact a repository
maintainer through a private channel shown on the repository profile and ask for
a secure reporting route without including the exploit in the first message.
Never send real API keys, passwords, private screenshots, customer data, or
other people's personal information.

Include, where possible:

- the affected version or commit;
- operating system, Python version, install extras, and relevant configuration
  with secrets redacted;
- a concise description of the security property that fails;
- minimal reproduction steps using synthetic data;
- realistic impact and required attacker access;
- whether the issue bypasses action validation or permission confirmation;
- suggested mitigations, if known; and
- whether you plan to request coordinated disclosure credit.

Maintainers will acknowledge and assess reports as capacity permits, coordinate
remediation and disclosure privately, and credit reporters who want attribution.
Please do not test against systems or accounts you do not own or have explicit
permission to assess.

## High-value report areas

Examples include:

- executing arbitrary shell, PowerShell, CMD, Python, or executable paths from
  user or provider output;
- bypassing strict action arguments, executable allowlists, or permission
  decisions;
- performing a denied or unconfirmed desktop action;
- automatic visual clicks based on invented or ambiguous coordinates;
- exposing API credentials through logs, config diagnostics, exceptions, issue
  artifacts, or provider payloads;
- escaping the configured memory or screenshot storage boundary;
- SQL injection or unauthorized memory disclosure/deletion;
- retaining temporary screenshots after normal or exceptional completion;
- sending conversations, audio, images, or tool results to a provider when the
  related feature is disabled; or
- unsafe Windows process matching that closes an unapproved executable;
- browser access to local/private/non-global network targets, DNS rebinding,
  unsafe redirects, stale element substitution, arbitrary script/selector
  execution, or unexpected file persistence;
- duplicate or post-cancellation reminder delivery caused by a repository race,
  or execution of supposedly inert scheduled-action metadata;
- an external mutation whose permission prompt is not bound to its repository,
  recipient, content, event, or other live target;
- importing/enabling a plugin without the required trust warning/confirmation,
  partial plugin registration surviving rollback, or stale plugin capabilities
  surviving disable/shutdown; or
- a GUI permission that defaults to approval, hides material action details, or
  reports a side effect as safely cancelled when its outcome is uncertain.

Missing planned features, normal provider model mistakes without a boundary
bypass, dependency availability, and bugs that have no security impact belong
in the public issue tracker.

## Current security boundaries

- There is no unrestricted shell or model-generated code execution feature.
- Providers see only registered tool schemas, and returned calls are validated
  and permission-checked locally.
- Provider adapters require HTTPS when an API key is present, and their built-in
  transport blocks cross-origin and HTTPS-downgrade redirects.
- Provider URLs reject embedded credentials, queries, and fragments; default
  transports bound response bodies and sanitize provider failures.
- Built-in application control uses exact aliases and trusted absolute paths;
  launches use `shell=False`, a trusted working directory, and a minimal
  allowlisted child environment, and are refused while JARVIS is elevated on
  Windows.
- Default policies allow `READ`, ask for `ACTION` and `SENSITIVE`, and ask for
  `DESTRUCTIVE`. Destructive `allow` is forced back to confirmation.
- Closing an application is `DESTRUCTIVE` because it terminates every matching
  approved process and can discard unsaved work.
- Confirmation-required requests fail closed without a confirmer.
- AI, vision, voice, desktop, browser, notification, plugin, integration, and GUI
  surfaces are disabled, unavailable, or limited unless deliberately configured
  and their runtime requirements are present.
- The built-in vision adapters are semantic only and do not support automatic
  grounded clicks.
- Persistent memory uses parameter-bound SQLite queries and explicit writes.
- Temporary screenshot cleanup covers local files, not copies retained by a
  remote provider, backup, or filesystem history.
- Process lists, network/IP information, storage mounts, running applications,
  active/visible window titles, and aggregate machine inventory are
  `SENSITIVE`; they are not silently grouped with low-detail `READ` metrics.
- External/model/tool content is untrusted. Any model follow-up after an action
  is budgeted, receives no tools, cannot chain another action, and is discarded
  from normal conversation history after presentation.
- Browser contexts are ephemeral, service workers and accepted downloads are
  disabled, and traffic is forced through a per-session authenticated loopback
  proxy. The proxy resolves/pins public numeric destinations for HTTP/HTTPS
  tunnels; alternate browser network surfaces and popup growth are constrained,
  and element actions revalidate a stable snapshot-derived node. Partial
  browser/proxy startup is cleaned up on failure. These controls are defense in
  depth, not a complete web or network sandbox.
- Reminder persistence uses a separate versioned SQLite schema and atomic
  notification leases. Cancel/delete are refused after delivery starts;
  exactly-once display cannot be guaranteed across a crash or OS notifier.
  Edit/reschedule rechecks exact current message/due values and also refuses
  records whose delivery began. Scheduled-action metadata cannot execute.
- GitHub requests use a fixed-origin bounded HTTPS JSON transport. Email sending
  rechecks exact draft content and calendar/reminder mutations recheck live
  target fields. GitHub is the only bundled live-account integration.
- Plugin entry points are discoverable without import, while inspection and
  enablement require confirmation and registration is staged for rollback.
  Plugins are nevertheless fully trusted unsandboxed Python and can bypass core
  boundaries by using Python/OS APIs directly.
- GUI confirmations are asynchronous and fail closed. Displayed values are
  bounded and formatting controls are escaped. Deny is the default dialog
  action, and the GUI accepts only the permission broker bound to its exact
  runtime. Cancellation reports an unknown outcome after execution has started
  because it cannot roll back an already dispatched external or desktop
  operation.

## User security guidance

- Install from a trusted source in a virtual environment and review changes
  before updating.
- Keep provider credentials in environment variables or a protected local
  secret mechanism; never commit them.
- Use least-privilege provider keys and revoke exposed credentials immediately.
- Review `jarvis config` output and `jarvis doctor` diagnostics before enabling
  experimental modes, while remembering that redaction is not a substitute for
  protecting the terminal itself.
- Set `READ` to `ask` or `deny` when screenshots or system inspection are too
  sensitive for the environment.
- Do not store secrets in JARVIS memory; SQLite data is not application-level
  encrypted.
- Close sensitive windows before screenshots and understand the remote
  provider's retention policy.
- Keep PyAutoGUI's fail-safe enabled and supervise desktop actions.
- Treat the bundled `--voice` mode as remote audio processing. Voice startup
  requires an explicit Google/SpeechRecognition provider, and captured audio can
  be uploaded to Google's recognition service.
- Keep `SENSITIVE` set to `ask` for keyboard input and `ACTION` set to `ask` for
  other desktop changes. Generic keyboard actions operate on the current
  foreground application and can indirectly drive terminals, launch dialogs,
  browsers, or privileged tools. Never approve them unattended, and verify focus
  yourself after opening an application.
- Keyboard actions retain mandatory confirmation even when sensitive policy is
  configured as `allow`. The CLI also verifies the approved active window after
  confirmation and blocks known terminal shortcuts, terminal targets, and
  type-then-Enter sequences. These are defense in depth, not a sandbox.
- Registered text input is capped at 500 characters, matching the confirmation
  preview limit.
- Keyboard binding uses a Windows handle and exact title rather than
  PID/executable identity; spoofed titles, custom terminal titles, and cross-turn
  compositions remain possible. Pointer actions capture and recheck the same
  foreground handle/title before their backend call, but cannot prove process
  ownership. Verify the foreground target before every approval.
- `user.message` events publish metadata rather than raw commands. Continue to
  audit event subscribers before adding diagnostic persistence. Terminal output
  removes ANSI escape sequences, unsafe control characters, and bidirectional
  formatting controls. Multiline, prompt-like provider prose can still be
  misleading; do not treat displayed prose as proof that an action ran.
- Do not run pre-alpha JARVIS unattended or with elevated administrator
  privileges.
- Keep the browser disabled unless needed; treat page text and labels as
  untrusted, avoid privileged/internal-network browsing, and never approve an
  action because instructions embedded in a page or provider response request
  it.
- Reminder text can appear in SQLite, terminal scrollback, and OS notification
  history. Keep secrets out of it and keep a process running only when delivery
  is desired.
- Install and enable only reviewed plugins. Installing a package already crosses
  a code-execution boundary; plugin declarations are informational and disabling
  cannot undo unmanaged threads, process mutation, or external effects.
- If GUI cancellation, a timeout, or a transport failure occurs after work was
  dispatched, verify the target application or service before retrying.

See [docs/permissions.md](docs/permissions.md),
[docs/providers.md](docs/providers.md), [docs/memory.md](docs/memory.md), and
[docs/vision.md](docs/vision.md) for the component details. The cross-cutting
[security guide](docs/security.md) covers browser, scheduling, integrations,
plugins, GUI, data retention, and a safe operating checklist.
