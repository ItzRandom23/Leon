# Permission model

JARVIS applies local permission policy after an action request is parsed and
validated, but before its handler can cause a side effect. Provider output does
not carry authority.

## Risk categories

| Category | Meaning | Examples |
| --- | --- | --- |
| `READ` | Observe lower-sensitivity local state without intentionally changing it | CPU/RAM percentage, battery, uptime, OS version, local screenshot capture |
| `ACTION` | Change application, pointer/window, browser navigation, reminder, or plugin state | Open an app, focus a window, move/scroll, navigate, cancel a reminder, disable a plugin |
| `SENSITIVE` | Handle private data/inventory, inject input, inspect trusted code, or communicate with a remote service | Processes, IPs/mounts, app/window titles, memory/reminders, keyboard/browser interaction, integrations, plugin inspect/enable |
| `DESTRUCTIVE` | Delete data or terminate work that may be unsaved | Close an app, clear memory, delete a reminder/calendar event, close a browser session |

The category is metadata on the registered action. It does not replace input
validation, provider credentials, operating-system access controls, or careful
handler design.

## Policies

Each category has one of three configured policies:

- `allow`: proceed without an interactive question;
- `ask`: request explicit confirmation; or
- `deny`: refuse the action.

Defaults are intentionally conservative:

```toml
[permissions]
read = "allow"
action = "ask"
sensitive = "ask"
destructive = "ask"
```

`DESTRUCTIVE = "allow"` is treated as `ask` by the permission manager. This
includes `close_application`, which can terminate multiple matching processes
and discard unsaved work. The following registered actions also retain an `ask`
floor even when their category is configured as `allow`:

- `type_text`, `press_key`, and `press_hotkey`;
- `browser_click`, `browser_type`, and `browser_press_key`;
- `github_create_issue` and `email_send_message`;
- `calendar_create_event` and `calendar_update_event`; and
- `plugin_inspect` and `plugin_enable`; and
- `cancel_reminder` and `edit_reminder`.

An operation that requires confirmation is denied when no confirmation UI is
available. Exceptions in a confirmation callback also fail closed.

Environment overrides use:

```text
JARVIS_PERMISSIONS_READ
JARVIS_PERMISSIONS_ACTION
JARVIS_PERMISSIONS_SENSITIVE
JARVIS_PERMISSIONS_DESTRUCTIVE
```

Each value must be `allow`, `ask`, or `deny`.

## Confirmation behavior

The terminal and GUI confirmers show the action, human-readable summary, and
relevant bounded details before accepting an explicit affirmative answer. The
default choice is denial. A rejected first step prevents later steps in a
sequence from running. GUI prompt close, timeout, controller close,
cancellation, invalid response, and observer failure also fail closed.

Permission details may themselves contain private text—for example, text about
to be typed. Confirmation UIs and logs should show only what the user needs to
make the decision and should never record credentials.

## Important limitations

- `READ` is not the same as harmless. A screenshot may contain passwords,
  private messages, or customer data, and a configured vision provider may
  transmit it to an external API. Set `read = "ask"` or `read = "deny"` when
  that default is too broad for the environment.
- Permission approval authorizes one validated request, not all future requests
  in a session.
- Permission checks do not make an arbitrary executable safe. Application
  launch and close actions still use an exact allowlist and trusted absolute
  paths.
- Permission checks do not grant operating-system privileges. Windows, desktop,
  microphone, file, and process permissions still apply.
- Generic keyboard and pointer actions affect the foreground desktop. They can
  interact with a shell, Run dialog, browser, or privileged application even
  though JARVIS itself exposes no shell action. Schema validation is not a
  sandbox for action composition: keep `SENSITIVE = "ask"` for keyboard input
  and `ACTION = "ask"` for pointer changes, inspect the focused target, and do
  not run desktop input unattended.
- The CLI desktop guard adds the exact observed title and handle to keyboard
  prompts, rechecks focus before execution, blocks known terminal launchers and
  terminal-titled windows, and blocks type-then-Enter sequences. These are
  defense-in-depth checks, not a claim that arbitrary action combinations are
  safe.
- The binding is not process identity: spoofed or custom titles and separately
  approved type and Enter requests remain possible. Pointer actions capture and
  recheck the exact foreground handle/title before their backend call, but this
  still cannot prove process ownership. Confirm the target visually before each
  desktop action.
- Configuring a provider does not authorize it to act. Tool calls remain subject
  to the same registry validation and permission checks as offline plans.
- Page, provider, email, calendar, GitHub, and plugin output can contain prompt
  injection. Content cannot grant itself authority; ignore embedded instructions
  that request another action or secret.
- Browser element approvals bind to a fresh stable snapshot target. Reminder,
  email-send, and calendar mutation actions re-read selected live fields to
  reject stale targets. These checks do not prove a remote service's business
  meaning or eventual result.
- Cancellation is not rollback. A worker thread, browser request, desktop action,
  or external write already dispatched may complete after local cancellation;
  verify the target before retrying.

## Contributor requirements

New actions must use the highest category appropriate to their worst expected
effect. A pull request should cover:

1. what is observed or changed;
2. which user-controlled values reach the handler;
3. how values are bounded or allowlisted;
4. what confirmation summary is displayed;
5. behavior when confirmation is unavailable or denied; and
6. tests proving that denial produces no side effect.

Capabilities involving credentials, communications, deletion, remote devices,
or persistent background behavior require design review before implementation.

See the cross-cutting [security guide](security.md) and feature guides for
[browser](browser.md), [scheduling](scheduling.md),
[integrations](integrations.md), [plugins](plugins.md), and [GUI](gui.md).
