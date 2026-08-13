# Memory

JARVIS persistent memory is explicit, categorized, and separate from active
conversation history. It does not silently archive every conversation.

## What is implemented

The memory domain supports four stable categories:

| Category | Intended use | Example |
| --- | --- | --- |
| `preferences` | User choices and defaults | preferred editor |
| `facts` | User-approved facts | timezone or a named fact |
| `projects` | Project references | repository location |
| `aliases` | Human names mapped to values or paths | `my code folder` → `D:\Projects` |

The replaceable `MemoryRepository` contract supports upsert, exact recall,
listing, literal case-insensitive search, deletion, clearing, counting, and
explicit close. `SQLiteMemoryRepository` is the current implementation.

The default database path is:

```text
~/.jarvis/jarvis.db
```

Override it in TOML:

```toml
[database]
path = "D:/JarvisData/jarvis.db"
```

or with `JARVIS_DATABASE_PATH`. Relative paths in an existing TOML file are
resolved from that file's directory.

## Explicit operations

Representative deterministic requests are:

```text
remember that my development folder is D:\Projects
what is my development folder?
show memories
forget my development folder
clear memories
```

The planner assigns a category from bounded wording, and storage identifies a
record by category plus a whitespace-normalized, case-insensitive key. Saving
the same category/key updates the existing record. Different categories do not
collide.

Forget and clear are deliberate delete operations. Review the permission prompt
before approving a clear request; database backups are outside the repository's
current scope.

Remember, recall, and list can expose or persist private user context and are
classified `SENSITIVE`, so they ask by default. Forget and clear are
`DESTRUCTIVE` and cannot be silently allowed. Storage access through the Python
repository API is lower-level and must be placed behind the same policy when
used in a user-facing runtime.

## Conversation history is different

The AI conversation model keeps a bounded number of messages in memory for one
active session. Clearing that object does not delete persistent memories, and
closing the process discards session history.

The configuration contains forward-compatible memory policy fields:

```toml
[memory]
enabled = true
auto_save = false
persist_conversations = false
allow_sensitive = false
```

The safe defaults are authoritative: the current `MemoryManager` has no
conversation-ingestion hook and writes only when its explicit `remember`
operation is called. Do not interpret `auto_save` or `persist_conversations` as
a promise of an automatic persistence feature in 0.2.0.

## Storage and privacy

- Values are stored as plain text in SQLite; the database is not encrypted by
  JARVIS.
- Do not store passwords, API keys, recovery codes, private keys, or other
  secrets as memories.
- Protect the database using operating-system account permissions and disk
  encryption where appropriate.
- SQL values are parameter-bound, and literal search escapes SQL wildcard
  characters.
- Schema versions are recorded so incompatible future database versions fail
  explicitly rather than being read incorrectly.
- Removing a record from SQLite may not remove copies in backups, filesystem
  snapshots, or storage-provider history.

## Repository replacement

Alternative stores should implement the `MemoryRepository` protocol rather than
leaking database-specific behavior into assistant orchestration. They must
preserve explicit writes, category/key isolation, deterministic lifecycle,
safe parameter handling, and tests for create/update/read/search/delete/clear
behavior.
