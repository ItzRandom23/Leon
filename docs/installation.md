# Installation

This guide installs the JARVIS 0.3 foundation from a source checkout. The base
package is deliberately small; browser, voice, desktop-input, notification, and
GUI runtimes are separate extras.

## Prerequisites

- Python 3.11 or newer
- `pip` and virtual-environment support
- A writable location for the default `~/.jarvis` data directory
- Windows for the built-in application resolver and full window controller

Core configuration, deterministic planning, SQLite memory/reminders, and
portable system inspection do not require provider credentials. Optional
features may additionally require an interactive display, microphone, browser
binary, network access, or service credential.

## Create an isolated environment

Windows PowerShell:

```powershell
git clone <repository-url>
cd <repository-directory>
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

macOS or Linux:

```bash
git clone <repository-url>
cd <repository-directory>
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
```

An editable install is convenient for contributors. A non-editable install from
the checkout may use `python -m pip install .` instead.

## Optional extras

| Extra | Installs | Additional requirements |
| --- | --- | --- |
| `desktop` | Pillow and PyAutoGUI | Interactive desktop and OS input/capture permission; controls are Windows-first |
| `voice` | SpeechRecognition audio support and `pyttsx3` | Working microphone/audio backend; Google STT uses the network |
| `browser` | Playwright Python package | A separately installed Playwright browser binary |
| `notifications` | `plyer` | Supported operating-system notification service |
| `gui` | PySide6 and qasync | Interactive graphical display/session |
| `dev` | pytest, coverage, Ruff, and mypy | Needed only for development and CI-equivalent checks |

Examples:

```powershell
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

```powershell
python -m pip install -e ".[desktop,voice,notifications,gui]"
```

If `[browser].browser_type` is `firefox` or `webkit`, install that corresponding
binary with `python -m playwright install firefox` or
`python -m playwright install webkit`. Browser installation may download a large
platform-specific artifact; review Playwright's requirements for your host.

The GUI extra does not install the desktop-control extra. Install both if the
same process should expose GUI and PyAutoGUI-backed actions.

## First-run verification

```console
jarvis version
jarvis config
jarvis doctor
```

`jarvis config` prints the effective configuration with known credential fields
redacted. `jarvis doctor` checks local paths, configuration, platform, and
optional package/browser availability. It is intentionally non-invasive: it
does not sign in to GitHub, call an AI endpoint, record audio, manipulate the
desktop, or prove that a graphical display is usable.

Start the text interface:

```console
jarvis
```

Start the optional GUI only after installing its extra:

```console
jarvis gui
```

If a selected optional package is missing, JARVIS reports a controlled startup
or action error. Optional imports are lazy so a missing GUI, browser, voice, or
desktop package should not break the base CLI.

## Data locations

Defaults are created under the current user's home directory:

| Data | Default path |
| --- | --- |
| Configuration | `~/.jarvis/config.toml` |
| Explicit memory | `~/.jarvis/jarvis.db` |
| Reminders | `~/.jarvis/tasks.db` |
| Plugin enablement state | `~/.jarvis/plugins.db` |
| Persistent screenshots | `~/.jarvis/screenshots` |

These SQLite databases and screenshots are not application-level encrypted.
Protect them with OS account and filesystem controls, exclude them from backups
that should not contain personal data, and do not store secrets in memory or
reminder text.

When a TOML file contains a relative database, log, screenshot, scheduler, or
plugin-state path, it is resolved relative to that TOML file. Environment path
overrides are resolved by the running process.

## Platform notes

- The core package is portable. Built-in allowlisted application discovery and
  the complete window-control surface target Windows.
- PyAutoGUI depends on an interactive desktop and may need accessibility or
  screen-recording permission. Its fail-safe remains enabled.
- SpeechRecognition's bundled Google recognizer uploads captured audio. No local
  STT or wake-word engine is included.
- IANA timezone names are validated with Python's `zoneinfo`. If a Python/OS
  installation lacks timezone data, install a trusted `tzdata` package or use a
  distribution that supplies it.
- Headless Playwright is still network-capable. Enabling it is a security choice,
  not merely a package-install step.

## Development checkout

```console
python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m ruff format --check .
python -m mypy
```

Unit tests use injected fakes and do not need live credentials, paid services,
real desktop input, or a live Playwright browser. A passing unit suite therefore
does not claim that optional hardware or graphical runtimes were manually
verified on the current machine.

Continue with [configuration](configuration.md), the
[security guide](security.md), or the [development guide](development.md).
