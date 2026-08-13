# Actions and tool schemas

Actions are the only route from planning or provider output to a trusted JARVIS
capability. Natural-language text, provider JSON, and user-controlled strings
are data; none of them are commands to a shell.

## Action contract

An action declares:

- a unique, stable name;
- a user-relevant description;
- zero or more typed parameters;
- a risk level; and
- a trusted sync or async handler.

Parameters support JSON types and bounded constraints such as enumerations,
numeric ranges, lengths, patterns, and array item types. Exported schemas are
provider-neutral function-tool definitions. Provider adapters wrap that same
canonical schema in the shape their API expects.

Conceptually:

```python
from jarvis.core.actions import ActionParameter, action
from jarvis.skills.base import RiskLevel


@action(
    name="example_lookup",
    description="Look up one validated item",
    parameters=(
        ActionParameter(
            "name",
            str,
            description="Exact item name",
            min_length=1,
            max_length=100,
        ),
    ),
    risk_level=RiskLevel.READ,
)
async def example_lookup(name: str) -> dict[str, str]:
    return {"name": name}
```

Registering a handler does not bypass permissions. Orchestration must validate
the request, obtain the permission decision, and only then invoke it.

## Implemented action families

The Phase 1–11 foundation contains handlers for the following families. Not
every handler has deterministic natural-language wording; an enabled model or
another shared-runtime interface can propose any registered schema. Availability
depends on operating system, optional dependencies, configuration, credentials,
connected providers, and the active permission policy.

| Family | Implemented foundation | Typical category |
| --- | --- | --- |
| Applications | Open, close, find, and list allowlisted applications | `ACTION` for open; `DESTRUCTIVE` for close; `SENSITIVE` for process/running-app inspection |
| System | CPU, RAM, storage, battery, uptime, OS, processes, network, and combined snapshots | `READ` for low-detail metrics; `SENSITIVE` for processes, network/IPs, mounts, and aggregate inventory |
| Memory | Explicit remember, recall, list, search, forget, and clear operations | `SENSITIVE` for remember/recall/list/search; `DESTRUCTIVE` for forget/clear |
| Mouse | Bounded move, click, double-click, right-click, and scroll with foreground-window binding/recheck | `ACTION` |
| Keyboard | Bounded text entry, supported key presses, and validated hotkeys | `SENSITIVE` |
| Screenshots | Full-screen and active-window capture with managed lifetimes | `READ` in the current taxonomy |
| Windows | Active-window details, visible-window listing, and exact-title focus | `SENSITIVE` for title/window inventory; `ACTION` for focus |
| Vision | User-invoked semantic screen description sent to a configured provider | `SENSITIVE` |
| Browser | Start/close, public navigation/search, history, snapshot/text/find/tab reads, verified click/type/key, scroll, and download metadata | `ACTION`/`SENSITIVE`; close is `DESTRUCTIVE` |
| Reminders | One-time/relative/daily/weekly/weekday create, atomic edit/reschedule, list/missed, cancel, and delete | `SENSITIVE`; cancel is `ACTION`; delete is `DESTRUCTIVE` |
| GitHub | Repository/issue/pull-request/workflow/release reads and issue creation | `SENSITIVE`; create issue always confirms |
| Email | List/search/read, draft, and send through a configured provider | `SENSITIVE`; send always confirms |
| Calendar | List/search/create/update/delete through a configured provider | `SENSITIVE`; delete is `DESTRUCTIVE` |
| Plugins | Discover/list, inspect, enable, and disable installed entry points | `READ`/`ACTION`/`SENSITIVE`; inspect/enable always confirm |

The registry's declaration is authoritative when a specific action's category
matters. Destructive memory deletion is therefore always confirmation-gated,
even if destructive policy was configured as `allow`. See
[permissions.md](permissions.md) for policy behavior.

Closing an allowlisted application terminates every matching approved process
and can discard unsaved work. It is classified `DESTRUCTIVE`, retains mandatory
confirmation, and stops the sequence if denied or unsuccessful.

Opening uses only a resolved absolute allowlist entry with `shell=False`, its
trusted executable directory as the working directory, and a minimal allowlisted
child environment. The Windows controller refuses application launch while
JARVIS is elevated.

## Deterministic planning

`DeterministicPlanner` provides bounded offline routing for common phrases. It
can create a one-action plan or a short fixed sequence, such as opening an
allowlisted application and then typing text. It is intentionally not general
natural-language understanding.

Representative recognized requests include:

```text
open notepad
close calculator
list running applications
what is using my RAM?
remember that my development folder is D:\Projects
what is my development folder?
take a screenshot
what's currently on my screen?
move the cursor to 500, 300
type "hello world"
press Control S
list visible windows
start the browser
go to https://example.com
search the web for Python dataclasses
remind me in 20 minutes to stretch
```

Unsupported phrasing receives a safe fallback. In particular, requests such as
`run arbitrary command` are not action plans, and `click the search box` is not
converted into guessed coordinates.

## Sequential execution

Action sequences execute in order and stop on the first failure by default.
Each request has an identifier, and each outcome reports success or a safe
failure code. This supports bounded tasks such as:

```text
open Notepad and type "Hello world"
```

It is not an autonomous planning loop. JARVIS does not repeatedly invent and
execute new steps, and later steps do not run after a denied or failed action.

The default CLI adds a Windows desktop execution guard to sequential input.
After an allowlisted application launch, it waits briefly for an active window
whose title contains the expected display name, adds that exact title and handle
to the keyboard confirmation, and rechecks the handle and title immediately
before input. It cancels when the target never appears or focus changes. This is
stronger than unbound foreground typing but remains experimental because title
matching does not prove PID/process ownership.

Generic keys and text can drive a terminal, Run dialog, browser, or other
sensitive foreground application. Input length and key validation prevent
malformed calls, not harmful compositions. Keyboard actions retain an
always-confirm floor even if `SENSITIVE` is configured as `allow`. The desktop
guard also blocks known terminal-launch shortcuts, terminal-titled targets, and
type-then-Enter sequences. Review each target, chord, and text value; never use
keyboard control unattended.

The registered `type_text` action accepts at most 500 characters, matching the
maximum confirmation preview, so approved text is not hidden behind a truncated
tail.

The binding is a Windows handle plus exact title, not PID or executable
identity. Title spoofing and custom-titled terminals remain possible, and the
type-then-Enter rule sees only one submitted sequence—not separately approved
requests across turns. Pointer actions now capture the foreground window for
the request and recheck the exact handle/title immediately before their backend
call, failing closed when focus changes. This still does not prove
PID/executable identity or make application content trustworthy.

## Provider tool calls

An enabled LLM receives only the schemas currently registered by JARVIS. A
returned tool call is treated as an untrusted proposal:

1. look up the exact registered name;
2. validate every argument against the local schema;
3. apply the local permission policy and confirmation UX;
4. call the trusted handler; and
5. return a structured result.

A provider cannot add tools, weaken risk categories, pass extra properties, or
substitute arbitrary Python, PowerShell, CMD, Bash, or executable paths.

Results returned by a tool are marked untrusted before the optional model
follow-up. That follow-up receives no tool schemas and cannot propose another
action; the raw exchange is discarded afterward rather than appended to normal
conversation history. Request, context, and tool-result budgets bound what is
sent. Generated prose remains untrusted presentation, not proof that an action
succeeded.

Browser pages, integration records, and plugin-produced values remain untrusted
when returned as action data. Target-binding and lifecycle rules are detailed in
[browser.md](browser.md), [scheduling.md](scheduling.md),
[integrations.md](integrations.md), and [plugins.md](plugins.md).

## Adding an action

Before proposing an action, document its user intent, platform support, data
access, highest-impact risk category, validation rules, confirmation summary,
failure behavior, and tests. Keep handlers small and inject external boundaries
so unit tests never cause real desktop or network effects.

See [development.md](development.md), [security.md](security.md), and the
repository's [New Skill Proposal](../.github/ISSUE_TEMPLATE/new_skill_proposal.yml)
template.
