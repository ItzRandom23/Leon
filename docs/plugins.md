# Plugin and skill SDK

Phase 10 provides a versioned Python extension contract built on installed
package entry points. It supports discovery, metadata inspection, staged
registration, persistent enablement, unload, and shutdown.

> [!CAUTION]
> Plugins are ordinary **trusted local Python code**. They run in the JARVIS
> process with the same user, filesystem, network, credentials, and OS access.
> They are not sandboxed. Metadata declarations and core action permissions do
> not prevent a malicious plugin from performing side effects directly.

## User workflow

Plugin-contributed capabilities are disabled in the running assistant by
default:

```toml
[plugins]
enabled = true
auto_load = false
state_path = "~/.jarvis/plugins.db"
```

Discover installed entry points without importing them:

```console
jarvis plugins list
```

Inspect one plugin's Python metadata after an explicit confirmation:

```console
jarvis plugins info example
```

Enable/import/initialize/register reviewed code after another explicit
confirmation:

```console
jarvis plugins enable example
jarvis plugins disable example
```

`plugin_inspect` and `plugin_enable` retain a mandatory confirmation floor even
if `SENSITIVE` is configured as `allow`. Disablement is an `ACTION` and follows
that category's policy. Enablement state persists separately from memory and
reminders.

The dedicated `jarvis plugins ...` management commands can discover and update
state even when `plugins.enabled` is false; they create a short-lived manager
and still apply their confirmations. The `enabled` configuration switch controls
whether the normal terminal/GUI application composes plugin actions and honors
that persisted state. An enabled record has no effect in ordinary sessions while
the subsystem remains disabled.

Set `auto_load = true` only after review. It requires `plugins.enabled = true`
and imports every persistently enabled entry point during application startup
without a new per-start prompt. This convenience expands the startup trust
boundary; leave it false when you want each load to be deliberate.

Installing a Python package can itself execute packaging/build code. Review the
source, build configuration, dependencies, maintainer identity, and artifact
provenance before installation—not only before `jarvis plugins enable`.

## Bundled example

The example is a separate package and is not installed with JARVIS:

```powershell
python -m pip install -e .\examples\jarvis_plugin_example
jarvis plugins list
jarvis plugins info example
jarvis plugins enable example
```

It declares the `example` entry point and contributes one local read-only
`example_greeting` action. It is a minimal API demonstration, not evidence that
unknown third-party plugins are safe.

## Packaging contract

Current constants:

```python
JARVIS_PLUGIN_API = 1
PLUGIN_ENTRY_POINT_GROUP = "jarvis.plugins"
```

A package declares an entry point in `pyproject.toml`:

```toml
[project.entry-points."jarvis.plugins"]
example = "jarvis_example.plugin:ExamplePlugin"
```

JARVIS discovers installed distribution metadata; it does not recursively scan
plugin directories or execute loose `.py` files. Entry-point names are stable
plugin IDs containing only letters, numbers, dots, underscores, and hyphens.
Duplicates are reported and not arbitrarily selected.

The loaded object implements `PluginProtocol` or subclasses `Plugin`:

```python
from jarvis.plugins import JARVIS_PLUGIN_API, Plugin, PluginContext, PluginMetadata


class ExamplePlugin(Plugin):
    @property
    def metadata(self) -> PluginMetadata:
        return PluginMetadata(
            name="Example Plugin",
            version="0.1.0",
            author="Example Author",
            description="One focused extension.",
            permissions=("read",),
            capabilities=("actions",),
            dependencies=(),
            api_version=JARVIS_PLUGIN_API,
        )

    def register(self, context: PluginContext) -> None:
        context.register_action(build_example_action())
```

`initialize(context)`, `register(context)`, and `shutdown()` may be synchronous
or async and must return `None`. `initialize` is for private resource setup;
`register` stages capabilities through the supplied context. Plugins do not
receive the mutable host registries directly.

## Metadata

`PluginMetadata` requires bounded name, version, author, description, and exact
integer API version. Optional declarations are:

- `permissions`: lower-case identifiers describing required access;
- `capabilities`: lower-case identifiers such as `actions` or `integrations`;
- `dependencies`: bounded human-readable dependency declarations.

Declarations are surfaced for review and compatibility decisions. They are not
automatically translated into OS permissions, dependency installation, or a
security sandbox. A plugin targeting an API version other than 1 is marked
incompatible and is not registered.

Observable states include `discovered`, `loaded`, `enabled`, `disabled`,
`incompatible`, `failed`, and `duplicate`.

## Staged registration

During registration a `PluginContext` can stage:

- an `Action` through `register_action`, `register_tool`, or `register_skill`;
- an `Integration` whose registered name matches its metadata; and
- an event listener through `subscribe_event`/`register_event_listener`.

The manager validates names and duplicates, initializes the plugin, stages all
capabilities, and commits them to host registries only when the lifecycle and
registration phase complete. If commit fails, already committed handles are
unregistered in reverse order, the context is revoked, and plugin shutdown is
attempted. One plugin's failure is represented in its status rather than
silently registering a partial capability set.

Atomic registry rollback does not undo arbitrary work a plugin performed inside
its own Python code before failing. Plugin authors must design `initialize` and
`shutdown` as a reliable pair, bound resource usage, avoid global mutation, and
make cleanup idempotent.

On disable/shutdown, the manager unregisters committed actions, integrations,
and listeners, closes/removes integrations, revokes the context, and invokes the
plugin's shutdown hook. Code already imported remains part of the Python process
and any unmanaged threads, subprocesses, monkey patches, or external side
effects remain the plugin's responsibility.

Events published through a context are republished by the host with
`plugin:<plugin_id>` source attribution. A plugin can still call unrelated
Python APIs directly; provenance control applies only to the provided event
boundary.

## Author requirements

- Register every JARVIS-visible operation as a strict `Action` with the highest
  applicable risk category.
- Never interpret user/model text as shell, Python, SQL, selectors, or generic
  service requests.
- Do not access JARVIS internal SQLite files directly.
- Keep provider credentials outside metadata, logs, exceptions, and fixtures.
- Treat browser/service/model/plugin content as untrusted input.
- Use injected transports and fakes; standard tests must not need credentials or
  cause a real external side effect.
- Test duplicate names, incompatible versions, denial, initialization failure,
  partial commit rollback, cancellation, disable, repeated shutdown, and
  missing optional dependencies.
- Document installation, data flow, platform support, persistent state,
  permissions, cleanup, and explicit non-goals.

Start a proposal with the
[Plugin Proposal](../.github/ISSUE_TEMPLATE/plugin_proposal.yml) template. See
[actions](actions.md), [integrations](integrations.md),
[development](development.md), and [security](security.md).
