# External integrations

Phase 9 defines a provider-neutral lifecycle and permission boundary for
external services. An integration declares non-secret metadata and operations,
connects through explicit credentials, exposes typed methods, and is wrapped by
registered JARVIS actions. The application starts and closes integrations with
the same lifecycle used by the terminal and GUI.

Status: GitHub is the only bundled live-account adapter and remains
experimental. Email and calendar include full contracts plus deterministic
in-memory providers for tests/demos; no Gmail, Outlook, Exchange, or CalDAV
account adapter is bundled.

## Shared lifecycle and operation model

Every `Integration` exposes:

- immutable name, display name, description, and operation metadata;
- `disconnected`, `connecting`, `connected`, `disconnecting`, `failed`, and
  `closed` states;
- serialized `connect`, `disconnect`, and permanent `close` methods; and
- operation kind (`read`, `write`, or `delete`), risk category, and whether
  explicit confirmation is required.

Registry snapshots contain safe metadata/status rather than live clients or
secrets. One integration failing to connect publishes a failure event without
silently granting it partial availability. Registered operations return
controlled results when their provider is unavailable.

Provider and remote content is untrusted. Repository names, issue bodies, email
text, calendar descriptions, and error-adjacent metadata can contain prompt
injection or misleading Unicode. Local schemas and permissions remain
authoritative.

## GitHub

Enable the adapter and provide a least-privilege token through the environment:

```toml
[integrations]
github_enabled = true
github_base_url = "https://api.github.com"
```

```powershell
$env:JARVIS_GITHUB_TOKEN = "<secret>"
jarvis doctor
jarvis
```

The token is required when GitHub is enabled. At application start the adapter
calls the authenticated-user endpoint; a network, credential, or API failure
leaves the integration failed. `jarvis doctor` does not perform this sign-in.

`github_base_url` must be an absolute HTTPS URL without embedded credentials,
query, or fragment. A fixed path is permitted for a compatible enterprise API.
Requests cannot change its HTTPS origin; redirects are allowed only to the exact
same origin.

| Registered action | Behavior | Category |
| --- | --- | --- |
| `github_list_repositories` | List repositories visible to the account | `SENSITIVE` |
| `github_inspect_repository` | Read one repository's metadata | `SENSITIVE` |
| `github_list_issues` | List open/closed/all issues | `SENSITIVE` |
| `github_read_issue` | Read one issue including body | `SENSITIVE` |
| `github_create_issue` | Create a bounded title/body in one repository | `SENSITIVE`, always confirm |
| `github_list_pull_requests` | List pull requests | `SENSITIVE` |
| `github_read_pull_request` | Read one pull request | `SENSITIVE` |
| `github_inspect_workflows` | List workflows and recent run status | `SENSITIVE` |
| `github_list_releases` | List releases | `SENSITIVE` |

The bundled adapter does not merge/close pull requests, dispatch workflows,
upload releases, modify repository settings, delete content, or run arbitrary
GraphQL. Issue bodies are capped at the same 500 characters visible in the
confirmation details. Review owner, repository, title, and complete body before
approval.

Token privileges are enforced by GitHub, not inferred by JARVIS. Use a token
restricted to only the accounts/repositories and read/write operations you need,
store it outside TOML/source control, and revoke it if exposed.

## Email contract and in-memory provider

Enable the non-network demo provider with:

```toml
[integrations]
email_provider = "memory"
```

Supported contract operations are list recent message summaries, search, read a
full message, create a draft, read a draft, and send an existing draft. All are
`SENSITIVE`; sending retains mandatory confirmation.

The send action requires a `draft_id` plus the exact single recipient, subject,
and body expected by the approval. Immediately before sending, JARVIS reads the
live immutable draft and refuses if those fields differ. Drafting and sending
are separate actions.

The bundled `memory`/`in-memory` implementation:

- performs no SMTP, IMAP, OAuth, or provider network call;
- starts with no messages unless a host application injects fixtures;
- holds messages, drafts, and sent records only for the current process; and
- treats repeated sends of the same draft idempotently within that process.

Selecting `memory` is therefore useful for development and interface demos, not
for emailing another person. A future live adapter must implement the same
contract without collapsing draft review and sending.

## Calendar contract and in-memory provider

Enable the non-network demo provider with:

```toml
[integrations]
calendar_provider = "memory"
```

The contract supports bounded list/search/upcoming reads and timezone-aware
create/update/delete operations. Registered actions currently expose list,
search, create, update, and delete.

- Reads are `SENSITIVE` because titles, attendees, locations, and times are
  personal data.
- Create/update are `SENSITIVE` and retain explicit confirmation.
- Update requires the current expected title and then replaces the bounded title,
  start, end, timezone, description, and location.
- Delete is `DESTRUCTIVE`, always confirms, and requires both the current exact
  title and start instant. JARVIS re-reads the live event before mutation and
  refuses stale targets.

The in-memory provider performs no sync and loses events at process exit. It is
not a local persistent calendar.

## Transport and credential boundaries

`SecretCredential` deliberately redacts `str` and `repr`; the secret is revealed
only at the authentication boundary. Environment and injected resolver
implementations are available. Configuration output also redacts known secret
fields, but users and plugins must still avoid logging raw credentials.

The shared standard-library HTTPS JSON transport:

- accepts only `GET`, `POST`, `PATCH`, and `DELETE`;
- fixes requests to the configured HTTPS origin;
- rejects unsafe paths, headers, embedded credentials, cross-origin redirects,
  and HTTPS downgrade;
- bounds URL/header/request/response sizes and timeouts;
- requires finite, JSON-compatible values and rejects invalid/non-JSON response
  bodies; and
- converts network/HTTP failures into sanitized domain errors without response
  bodies or tokens.

These controls do not validate a provider's business semantics or retention
policy. A configured service still receives approved query text, identifiers,
and write payloads, and may log them under its own policy.

## Adding another integration

Start with the [Integration Request](../.github/ISSUE_TEMPLATE/integration_request.yml)
template. A proposal should document:

- exact read/write/delete operations and risk categories;
- authentication, minimum scopes, token refresh/revocation, and where secrets
  live;
- every data field sent and returned, provider retention, and account regions;
- target and content binding for mutations;
- retry/idempotency/rate-limit semantics and uncertain outcomes;
- timeouts and bounded payloads;
- lifecycle and cleanup behavior; and
- mock-transport tests that use no live account or paid call.

Do not add a generic arbitrary-request action or let provider text bypass the
action registry. See [actions](actions.md), [permissions](permissions.md),
[configuration](configuration.md), and [security](security.md).
