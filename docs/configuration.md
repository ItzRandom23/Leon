# Configuration

JARVIS configuration is immutable after startup and is validated before the
runtime is composed. Unknown TOML sections/settings, invalid types, unsupported
enum values, missing explicitly selected files, and inconsistent settings fail
startup with a configuration error.

## Sources and precedence

From lowest to highest precedence:

1. built-in conservative defaults;
2. `~/.jarvis/config.toml`, when it exists;
3. an explicit `--config PATH` or `JARVIS_CONFIG_FILE` TOML file; and
4. supported `JARVIS_*` environment variables.

`--config` takes precedence over `JARVIS_CONFIG_FILE`. Selecting a missing file
explicitly is an error; an absent default file is not. JARVIS does not
automatically read `.env` files. [`.env.example`](../.env.example) is only a
canonical variable reference.

Filesystem paths in a loaded TOML file are expanded for `~` and environment
variables. Relative TOML paths are resolved from the configuration file's
directory. Use `jarvis config` to inspect the final values; credential fields
are replaced with `***` when set.

## Complete TOML reference

### AI and vision

| Key | Default | Accepted values / behavior |
| --- | --- | --- |
| `ai.enabled` | `false` | Enables a remote language-model adapter |
| `ai.provider` | `"openai-compatible"` | `openai` or `openai-compatible` when enabled |
| `ai.model` | `""` | Required non-empty model name when enabled |
| `ai.base_url` | unset | Absolute provider URL; defaults to `https://api.openai.com/v1` at composition |
| `ai.api_key` | unset | Secret; prefer `JARVIS_AI_API_KEY` |
| `ai.timeout_seconds` | `30` | Positive number |
| `vision.enabled` | `false` | Enables remote semantic screen analysis |
| `vision.provider` | `"openai-compatible"` | `openai` or `openai-compatible` when enabled |
| `vision.model` | `""` | Required non-empty model name when enabled |
| `vision.base_url` | unset | Absolute provider URL; same default as AI |
| `vision.api_key` | unset | Secret; prefer `JARVIS_VISION_API_KEY` |
| `vision.timeout_seconds` | `30` | Positive number |

Provider credentials require HTTPS. A low-level OpenAI-compatible adapter can
use credential-free HTTP for deliberate local development, but never send an
API key over HTTP. The built-in transports bound payloads and reject unsafe
redirects; endpoint compatibility, account policy, retention, cost, and model
behavior remain the user's responsibility. See [providers](providers.md).

### Voice

| Key | Default | Accepted values / behavior |
| --- | --- | --- |
| `voice.enabled` | `false` | Enables push-to-talk input in the normal session |
| `voice.tts_enabled` | `false` | Speaks assistant responses through a configured local TTS adapter |
| `voice.stt_provider` | `"none"` | Use `google` or `speech-recognition` to select the bundled network-backed recognizer |
| `voice.tts_provider` | `"none"` | `none`, `pyttsx3`, and `system` currently select the bundled `pyttsx3` adapter when TTS is enabled |
| `voice.language` | `"en-US"` | Non-empty recognizer language tag |

`jarvis --voice` also requires an explicit supported STT provider. No local STT
or wake-word engine is bundled, and there is no continuous listening by
default.

### Memory and storage

| Key | Default | Accepted values / behavior |
| --- | --- | --- |
| `memory.enabled` | `true` | Composes the explicit categorized memory service |
| `memory.auto_save` | `false` | Reserved policy flag; 0.3 still writes only through explicit memory actions |
| `memory.persist_conversations` | `false` | Reserved policy flag; active model history remains in memory in 0.3 |
| `memory.allow_sensitive` | `false` | Reserved policy flag; it does not make storing secrets safe |
| `database.path` | `~/.jarvis/jarvis.db` | Versioned memory SQLite file |
| `screenshots.directory` | `~/.jarvis/screenshots` | Persistent screenshot directory |
| `screenshots.keep_temporary` | `false` | Reserved policy flag; temporary vision captures are still cleaned up in 0.3 |

Memory and screenshots are not application-level encrypted. Screenshot reads
are `READ`; memory reads/writes are `SENSITIVE`; forgetting and clearing are
`DESTRUCTIVE`.

### Logging and permissions

| Key | Default | Accepted values / behavior |
| --- | --- | --- |
| `logging.level` | `"INFO"` | `CRITICAL`, `ERROR`, `WARNING`, `INFO`, or `DEBUG` |
| `logging.file` | unset | Optional local log path |
| `permissions.read` | `"allow"` | `allow`, `ask`, or `deny` |
| `permissions.action` | `"ask"` | `allow`, `ask`, or `deny` |
| `permissions.sensitive` | `"ask"` | `allow`, `ask`, or `deny` |
| `permissions.destructive` | `"ask"` | `allow`, `ask`, or `deny`; `allow` is forced back to confirmation |

Selected high-impact actions retain a mandatory confirmation floor regardless
of category policy. They include generic keyboard input, browser element
click/type/key actions, GitHub issue creation, email sending, calendar
create/update, plugin inspection/enablement, and reminder edit/cancellation. See
[permissions](permissions.md).

### Browser

| Key | Default | Accepted values / behavior |
| --- | --- | --- |
| `browser.enabled` | `false` | Registers and permits creation of the Playwright browser surface |
| `browser.browser_type` | `"chromium"` | `chromium`, `firefox`, or `webkit` |
| `browser.headless` | `true` | Launch without a visible browser window |
| `browser.profile` | `"ephemeral"` | Only `ephemeral` is supported |
| `browser.max_sessions` | `2` | Positive integer, maximum 8 |
| `browser.max_tabs` | `8` | Positive integer, maximum 32 per session |

The built-in action service currently operates one active session even though
the lower-level controller is bounded for multiple sessions. Enabling the
feature requires the `[browser]` extra and a matching Playwright browser binary.
It does not expose arbitrary selectors or JavaScript. See [browser](browser.md).

### Scheduler

| Key | Default | Accepted values / behavior |
| --- | --- | --- |
| `scheduler.enabled` | `true` | Composes reminder persistence/actions and starts polling in long-running modes |
| `scheduler.database_path` | `~/.jarvis/tasks.db` | Separate versioned reminder SQLite file |
| `scheduler.timezone` | `"UTC"` | Valid IANA timezone name, for example `Asia/Kolkata` |
| `scheduler.poll_interval_seconds` | `30` | Positive fallback poll interval; state changes wake the scheduler sooner |
| `scheduler.desktop_notifications` | `false` | Use optional `plyer`; otherwise print terminal notifications |

Reminder delivery needs a running `jarvis` or `jarvis gui` process. The
scheduler never executes `ScheduledAction` metadata. See
[scheduling](scheduling.md).

### Integrations

| Key | Default | Accepted values / behavior |
| --- | --- | --- |
| `integrations.github_enabled` | `false` | Compose and connect the bundled GitHub REST adapter |
| `integrations.github_token` | unset | Required when GitHub is enabled; prefer `JARVIS_GITHUB_TOKEN` |
| `integrations.github_base_url` | `https://api.github.com` | Absolute HTTPS base URL without credentials, query, or fragment |
| `integrations.email_provider` | `"none"` | `none`, `memory`/`in-memory`, or `smtp` |
| `integrations.email_smtp_host` | `""` | SMTP host for outbound sending; `none` disables SMTP |
| `integrations.email_smtp_port` | `587` | SMTP port (`465` with `ssl` mode) |
| `integrations.email_smtp_mode` | `"starttls"` | `starttls`, `ssl`, or `none` |
| `integrations.email_imap_host` | `""` | IMAP host for reading/searching; `none` disables IMAP |
| `integrations.email_imap_port` | `993` | IMAP port (`143` without SSL) |
| `integrations.email_imap_ssl` | `true` | Whether IMAP connects over TLS |
| `integrations.email_username` | `""` | Account address used for SMTP/IMAP login |
| `integrations.email_from` | `""` | Envelope `From` address; defaults to `email_username` |
| `integrations.email_password` | unset | Required for the `smtp` provider; prefer `JARVIS_EMAIL_PASSWORD` |
| `integrations.calendar_provider` | `"none"` | `none`, `memory`/`in-memory`, or `caldav` |
| `integrations.calendar_url` | `""` | Absolute HTTPS CalDAV server or calendar collection URL |
| `integrations.calendar_username` | `""` | CalDAV account username |
| `integrations.calendar_password` | unset | Required for the `caldav` provider; prefer `JARVIS_CALENDAR_PASSWORD` |

The `memory` email/calendar providers are ephemeral test/demo implementations;
they do not connect to a real account and lose their records on exit. The
`smtp` email and `caldav` calendar providers connect to real accounts and are
opt-in; SMTP uses only the standard library, while CalDAV requires the
optional `integrations` extra (`pip install "jarvis-assistant[integrations]"`).
See [integrations](integrations.md).

### Plugins

| Key | Default | Accepted values / behavior |
| --- | --- | --- |
| `plugins.enabled` | `false` | Compose the plugin manager and registered plugin actions |
| `plugins.auto_load` | `false` | Load persistently enabled entry points at startup; requires `enabled = true` |
| `plugins.state_path` | `~/.jarvis/plugins.db` | Separate versioned enablement-state SQLite file |

Discovery lists packaging metadata without importing plugin code. Inspection,
enablement, and auto-loading import trusted installed Python; plugins are not
sandboxed. Dedicated `jarvis plugins ...` management commands can update the
separate state database even when this subsystem switch is false; the switch
controls composition/loading in normal terminal and GUI sessions. See
[plugins](plugins.md).

### GUI

| Key | Default | Accepted values / behavior |
| --- | --- | --- |
| `gui.theme` | `"system"` | `system`, `light`, or `dark` |
| `gui.minimize_to_tray` | `false` | Keep running in the system tray when the platform provides one |
| `gui.show_debug_logs` | `false` | Reserved display preference; logging verbosity is still controlled by `logging.level` in 0.3 |

The GUI needs the `[gui]` extra and an interactive display. It reuses all core
permissions and storage rather than bypassing them. See [GUI](gui.md).

## Canonical environment variables

Every canonical TOML key above has an environment form:

```text
JARVIS_CONFIG_FILE
JARVIS_AI_ENABLED
JARVIS_AI_PROVIDER
JARVIS_AI_MODEL
JARVIS_AI_BASE_URL
JARVIS_AI_API_KEY
JARVIS_AI_TIMEOUT_SECONDS
JARVIS_VISION_ENABLED
JARVIS_VISION_PROVIDER
JARVIS_VISION_MODEL
JARVIS_VISION_BASE_URL
JARVIS_VISION_API_KEY
JARVIS_VISION_TIMEOUT_SECONDS
JARVIS_VOICE_ENABLED
JARVIS_VOICE_TTS_ENABLED
JARVIS_VOICE_STT_PROVIDER
JARVIS_VOICE_TTS_PROVIDER
JARVIS_VOICE_LANGUAGE
JARVIS_MEMORY_ENABLED
JARVIS_MEMORY_AUTO_SAVE
JARVIS_MEMORY_PERSIST_CONVERSATIONS
JARVIS_MEMORY_ALLOW_SENSITIVE
JARVIS_DATABASE_PATH
JARVIS_LOGGING_LEVEL
JARVIS_LOGGING_FILE
JARVIS_PERMISSIONS_READ
JARVIS_PERMISSIONS_ACTION
JARVIS_PERMISSIONS_SENSITIVE
JARVIS_PERMISSIONS_DESTRUCTIVE
JARVIS_SCREENSHOTS_DIRECTORY
JARVIS_SCREENSHOTS_KEEP_TEMPORARY
JARVIS_BROWSER_ENABLED
JARVIS_BROWSER_TYPE
JARVIS_BROWSER_HEADLESS
JARVIS_BROWSER_PROFILE
JARVIS_BROWSER_MAX_SESSIONS
JARVIS_BROWSER_MAX_TABS
JARVIS_SCHEDULER_ENABLED
JARVIS_SCHEDULER_DATABASE_PATH
JARVIS_SCHEDULER_TIMEZONE
JARVIS_SCHEDULER_POLL_INTERVAL_SECONDS
JARVIS_SCHEDULER_DESKTOP_NOTIFICATIONS
JARVIS_GITHUB_ENABLED
JARVIS_GITHUB_TOKEN
JARVIS_GITHUB_BASE_URL
JARVIS_EMAIL_PROVIDER
JARVIS_CALENDAR_PROVIDER
JARVIS_PLUGINS_ENABLED
JARVIS_PLUGINS_AUTO_LOAD
JARVIS_PLUGINS_STATE_PATH
JARVIS_GUI_THEME
JARVIS_GUI_MINIMIZE_TO_TRAY
JARVIS_GUI_SHOW_DEBUG_LOGS
```

Boolean environment values accept `1/0`, `true/false`, `yes/no`, or `on/off`
(case-insensitive). Timeouts and intervals must be positive numbers; integer
limits must be positive integers.

Compatibility aliases still accepted include singular permission/screenshot
forms and older screenshot-path names. New configuration should use the
canonical names shown above.

## Example secure opt-in

```toml
[browser]
enabled = true
browser_type = "chromium"
headless = true

[integrations]
github_enabled = true

[plugins]
enabled = true
auto_load = false
```

```powershell
$env:JARVIS_GITHUB_TOKEN = "<least-privilege-token>"
jarvis doctor
jarvis
```

Start with `auto_load = false`, inspect installed entry points with
`jarvis plugins list`, and enable only reviewed code. Do not paste the redacted
output of `jarvis config` into a public report without also checking paths,
account names, model names, and base URLs for sensitive information.
