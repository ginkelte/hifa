---
id: SPEC-030
title: SQLite adapter
component: persistence
status: review
version: 10
spec_revision: 24
depends_on: [SPEC-010, SPEC-020]
implements:
  - src/hifa/adapters/sqlite/**
  - src/hifa/adapters/clock.py
  - src/hifa/adapters/idgen.py
  - src/hifa/ports.py
  - tests/integration/db/**
  - tests/unit/adapters/**
acceptance: specs/acceptance/030-sqlite-adapter.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-21
---

# SPEC-030 — SQLite adapter

## 1. Responsibility and boundaries

**Does:** it is the **only** place in the system that talks to SQLite. It implements the connection with its
PRAGMAs, the transactional unit of work (`UnitOfWork`), the seven repositories that map rows to
domain entities and back, the migrator that applies `migrations/00X_*.sql`, and the read models
that feed the CLI, the API and the dashboard (board, list, detail, agenda).

**Does NOT do (explicit):**

- **It does not define the schema.** The DDL, the indexes, the views and the migration policy are
  normative in `SPEC-010` §3 and §4. This spec governs the adapter's *Python API* and *how* that
  DDL is executed. If there is a conflict in SQL text, `SPEC-010` wins; if there is a conflict in
  signature, this spec wins.
- **It does not apply business rules.** It does not decide whether an issue can be closed, whether a label belongs to the team or
  whether a relation is valid: that is domain (`SPEC-020`) and composition (`SPEC-040`). The adapter
  translates constraint violations into domain errors; it does not invent them.
- **It does not validate user input.** It does not parse `"High"` nor dates with alternative formats: it receives
  already normalized types (`Priority`, `date`, `bool`).
- **It does not format output.** It returns dataclasses and entities; there is no `print`, no ASCII tables, no JSON.
  `--json` belongs to `cli/output.py` (SPEC-050) and the HTTP schemas to `api/schemas.py` (SPEC-060).
- **It does not manage business transactions.** It opens and closes transactions, but **what** goes inside is
  decided by the use case (SPEC-040): the adapter exposes `write()`/`read()`, not `create_issue()`.
- **It does not cache.** Zero memoization between calls (rule A8). The only state is the connection.
- **It does no networking, nor opens files other than the database and its backups.**

## 2. Public contract

> Normative. Modules: `src/hifa/ports.py` (§2.7) and, under `src/hifa/adapters/sqlite/`:
> `connection.py`, `uow.py`, `migrate.py`, `issue_repo.py`, `team_repo.py`, `label_repo.py`,
> `project_repo.py`, `cycle_repo.py`, `comment_repo.py`, `history_repo.py`, `reads.py`. Every
> symbol not listed is private.

| Symbol | Module | Type | Stable |
|---|---|---|---|
| `Clock`, `IdGen` | `ports` (`src/hifa/ports.py`) | Protocols | yes |
| `SystemClock`, `UlidGen` | `adapters.clock` / `adapters.idgen` | classes | yes |
| `connect`, `Database`, `PRAGMAS`, `is_migrated`, `PROBE_SQL`, `Probe`, `open_probe`, `FTS5_ENABLED_SQL`, `require_fts5` | `connection` | functions / class / constants / dataclass | yes |
| `UnitOfWork`, `Repos`, `RetryPolicy`, `UNSET`, `Unset` | `uow` | classes / sentinel | yes |
| `migrate`, `current_version`, `pending_migrations`, `backup_database`, `MigrationResult` | `migrate` | functions / dataclass | yes |
| `TeamRepo`, `IssueRepo`, `LabelRepo`, `ProjectRepo`, `CycleRepo`, `CommentRepo`, `HistoryRepo` | `*_repo` | classes | yes |
| `ListFilters`, `IssuePage`, `SORT_KEYS`, `encode_cursor`, `decode_cursor`, `LabelRef`, `IssueListItem`, `Board`, `BoardGroup`, `IssueDetail`, `AgendaEntry`, `board_view`, `list_view`, `blocked_view`, `issue_detail`, `issue_id_by_ref`, `issue_id_by_number`, `team_by_previous_key`, `agenda` | `reads` | dataclasses / functions | yes |

### 2.1 `connection.py`

```python
from dataclasses import dataclass
from pathlib import Path
import sqlite3

PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("foreign_keys", "ON"),
    ("busy_timeout", "5000"),
    ("synchronous", "NORMAL"),
    ("temp_store", "MEMORY"),
    ("cache_size", "-8000"),  # 8 MiB per connection
)


def connect(db_path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    """Opens the database and applies ALL the PRAGMAs of PRAGMAS, in that order, on this connection.

    readonly=True opens with URI 'file:<path>?mode=ro' and uri=True.
    row_factory = sqlite3.Row.
    isolation_level = None  (explicit transactions; never Python's implicit autocommit).
    nonexistent path and readonly=False -> the file is created empty (the migrator decides whether it works).
    nonexistent path and readonly=True -> sqlite3.OperationalError('unable to open database file'),
    which the caller translates to DatabaseNotFound(key=<path>) (§6).
    """


def is_migrated(conn: sqlite3.Connection, *, expected: int) -> bool:
    """PRAGMA user_version == expected and the issue table exists."""


PROBE_SQL: str = "SELECT type, name FROM sqlite_master WHERE type IN ('table','view')"


@dataclass(frozen=True, slots=True)
class Probe:
    """A read-only identification of one file. `conn` is open and the caller closes it.

    The two sets are two projections of the one row PROBE_SQL returns, and they are not
    interchangeable: a **view** named `workspace` is in `objects` and not in `tables`.
    `tables` is what a schema verdict asks about (`no workspace table`); `objects` is what
    "this file carries nothing at all" asks about (the empty-file test of SPEC-100 R14a)."""

    conn: sqlite3.Connection
    tables: frozenset[str]  # names whose type = 'table', `sqlite_`'s internal tables included
    objects: frozenset[str]  # names whose type IN ('table','view')


def open_probe(path: str | Path) -> Probe:
    """Opens `path` read-only, applies NO pragma, and reads PROBE_SQL once.

    sqlite3.connect(f"file:{path}?mode=ro", uri=True); row_factory = sqlite3.Row;
    isolation_level = None. Zero entries of PRAGMAS run — not even `journal_mode`, which is a write to
    the file header, and which a `mode=ro` connection cannot honour on a file that is not already in WAL
    (this is why the symbol exists: `connect(readonly=True)` applies the whole tuple, D-068).
    The one row set PROBE_SQL returns is partitioned in Python: `tables` keeps type = 'table' and
    `objects` keeps both types; no second statement is run.
    path that does not exist -> DatabaseNotFound(key=<path>): `mode=ro` never creates a file, and
    sqlite3 answers 'unable to open database file' (§6).
    bytes SQLite refuses -> CorruptDatabase(detail=<message class>, key=<path>), raised by PROBE_SQL's
    read and not by the open, because sqlite3.connect() is lazy: 'file is not a database',
    'database disk image is malformed' (§6).
    a read that SQLite refuses because the process cannot create the WAL sidecars next to a database
    that is already in WAL -> the same read answers 'attempt to write a readonly database';
    translate_sqlite_error maps that message to Conflict(rule='DB_READONLY', code='DB_READONLY') and the
    verb's exit is 4 (§6, SPEC-100 §6).
    An empty file is not an error: `objects == frozenset()` and nothing is created either.
    The file is not modified by the probe: no pragma runs and no statement writes.
    A refusal raised by the read closes the connection it opened before it raises, so no handle
    outlives a failed probe; on the success path the caller closes `probe.conn`, in a `try/finally`,
    on the way out and on every verdict it takes after the probe returned (§6, D-069).
    """


FTS5_ENABLED_SQL: str = "SELECT 1 FROM pragma_compile_options WHERE compile_options = 'ENABLE_FTS5'"


def require_fts5(conn: sqlite3.Connection) -> None:
    """Raise Fts5Unavailable when this connection's SQLite has no FTS5 module.

    One statement, `FTS5_ENABLED_SQL`, over the `pragma_compile_options` table-valued function
    (SQLite ≥ 3.16, and the corpus requires ≥ 3.35 for RETURNING): no `PRAGMA` is executed whose
    output a caller could read twice, no virtual table is created to find out, and the check works
    on a read-only connection. `Fts5Unavailable(code='fts5_unavailable')` is the only error it
    raises; exit 6 and HTTP 500 (§6).
    """


class Database:
    """Connection factory for a path. It keeps no pool and no open connection."""

    def __init__(self, path: str | Path) -> None: ...
    @property
    def path(self) -> Path: ...
    def connect(self, *, readonly: bool = False) -> sqlite3.Connection: ...
    def ro_connection(self) -> sqlite3.Connection: ...
```

**Why each PRAGMA and not another** (all verifiable with `PRAGMA <name>;`):

| PRAGMA | Value | Reason |
|---|---|---|
| `journal_mode` | `WAL` | concurrent readers with one writer; the dashboard reads while the CLI writes. It is persistent in the file header, but it is re-issued per connection because a database created by another tool may be in `delete`. |
| `foreign_keys` | `ON` | **per connection** and off by default: without this, the composite FK `(team_id, state_id)` and the `ON DELETE CASCADE`s do not exist. It is the PRAGMA that causes the most silent bugs when missing. |
| `busy_timeout` | `5000` | the busy handler waits 5 s before returning `SQLITE_BUSY`; it turns most collisions into a short wait. |
| `synchronous` | `NORMAL` | with WAL, `NORMAL` does not corrupt on a process crash (only on a power cut during the last commit); the cost of `FULL` is not justified in a local app. |
| `temp_store` | `MEMORY` | the `ORDER BY`s of the read models do not touch disk. |
| `cache_size` | `-8000` | 8 MiB of page cache per connection; it fits in the RSS budget of REQ-N001. |
| `row_factory` | `sqlite3.Row` | column-name access, no magic indexes. |

**`require_fts5` runs in exactly two places. `connect` is not one of them, and neither is `reindex`'s body.**
The check is `FTS5_ENABLED_SQL`, one `SELECT` over `pragma_compile_options`, and it runs:

1. in `migrate()`, **before the backup and before the first `BEGIN`** of a migration set, when
   `pending_migrations()` is non-empty — `001_init.sql` creates the `issue_fts` virtual table, and a build
   without the module would otherwise fail inside the transaction with `no such module: fts5`;
2. in `hifa db reindex`, **on the probe's connection, immediately after `open_probe()` returns and before any
   writable statement exists** (SPEC-100 R21 step (b)): the verb's first act is the probe, its second is the
   verdict on `probe.tables`, its third is `require_fts5(probe.conn)`, and only then is a writable
   connection opened. That order is what makes `fts5_unavailable` an answer that leaves the file untouched.

`reindex(conn)` declares the check as a **precondition** of its own contract (SPEC-100 §2.6) and not as its
first statement: the connection it is handed is already the writable one, so a statement there would report
the missing module after the write-side open — the failure this placement exists to avoid. `require_fts5` is
the single producer of `fts5_unavailable` (D-065), and no other module raises that code. It is deliberately
**not** in `connect()`: a database whose index cannot be read is still searchable through the `like` path
(SPEC-090 R14), so a connection that refused to open would take the rows away from the reader as well. A
future `db vacuum` (SPEC-090 R19) holds its own writable connection and owes the same check on its own
probe; that verb is not shipped in v1 (SPEC-050 §2.5.5), and this sentence is where its third call site will
be declared.

### 2.2 `uow.py`

```python
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterator
from datetime import datetime

from hifa.ports import Clock, IdGen


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.05  # 50 ms, 100 ms, 200 ms; applied by app/retry.py


class _Unset:
    """Sentinel for 'do not filter by this field'. It is not `None`: `None` filters by `IS NULL`."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSET"


Unset = _Unset
UNSET: Final[Unset] = _Unset()


@dataclass(frozen=True, slots=True)
class Repos:
    teams: TeamRepo
    issues: IssueRepo
    labels: LabelRepo
    projects: ProjectRepo
    cycles: CycleRepo
    comments: CommentRepo
    history: HistoryRepo


class UnitOfWork:
    def __init__(
        self,
        db_path: str | Path | sqlite3.Connection,
        *,
        clock: Clock,
        idgen: IdGen,
        readonly: bool = False,
        retry: RetryPolicy = RetryPolicy(),
    ) -> None: ...
    @property
    def connection(self) -> sqlite3.Connection: ...
    @property
    def clock(self) -> Clock: ...
    @property
    def idgen(self) -> IdGen: ...
    @contextmanager
    def write(self) -> Iterator[Repos]:
        """BEGIN IMMEDIATE -> Repos -> COMMIT on normal exit.
        No internal retry (D-018): the body of the `with` cannot be re-executed from a
        generator. The retry of the complete transaction lives in
        `app/retry.py::with_write_retry` (SPEC-040 §2.2).
        Exception -> ROLLBACK and re-raise of the SAME exception (without wrapping).
        SQLITE_BUSY/locked -> rollback + Conflict(code='DB_BUSY') (no retry).
        readonly=True -> Conflict(code='READ_ONLY_UOW') without opening a transaction."""

    @contextmanager
    def read(self) -> Iterator[Repos]:
        """BEGIN (deferred) -> Repos(readonly=True) -> COMMIT. No writer lock; consistent
        snapshot during the whole read. A write inside this block is a bug: the read repos
        raise Conflict(code='READ_ONLY_UOW')."""

    def close(self) -> None:
        """Closes the connection if this UnitOfWork opened it. Idempotent."""
```

The first argument accepts two forms, and the difference matters:

| Form | Behavior |
|---|---|
| `db_path: str \| Path` | the `UnitOfWork` **opens** a connection with `connect(db_path, readonly=readonly)` on the first `write()`/`read()` and **closes** it in `close()` |
| `conn: sqlite3.Connection` | **borrowed** connection (the API opens one per request, SPEC-060): nothing is opened and `close()` does **not** close the connection; the caller is responsible. The connection must come from `connection.connect()`, because the PRAGMAs are per connection |

Exact semantics, in order:

1. With `db_path`, `UnitOfWork.__init__` does **not** open a connection: the first `write()`/`read()` opens it and
   reuses it until `close()`. One `UnitOfWork` = one connection = one session.
2. `write()` executes `BEGIN IMMEDIATE` (never `BEGIN` nor `BEGIN DEFERRED`).
3. The seven repos are built **with that same connection**:
   `IssueRepo(conn, clock=..., idgen=..., readonly=False)` in `write()` and `..., readonly=True` in
   `read()`. There are no repos that open their own connection; that would break atomicity and the "single
   writer".
4. Normal exit: `COMMIT`. Domain exception: `ROLLBACK` and `raise` of the same instance
   (the `raise` preserves the traceback and the `code`).
5. `read()` uses a deferred `BEGIN` and does **not** take a write lock; in WAL the reader sees a stable
   snapshot even if another process writes. `read()` does not allow writes: it builds the seven repos
   with `readonly=True` and every write method of those repos raises
   `Conflict(code='READ_ONLY_UOW')` **before** touching SQL (see §4 R6).
6. `uow.clock` and `uow.idgen` are **properties** that return the dependencies received by
   constructor: it is the only way `app/` obtains `now` and IDs without importing `adapters/`
   (D-017). `app/` never instantiates its own `Clock`/`IdGen`.
7. A `UnitOfWork` is not reentrant: `write()` inside `write()` raises `HifaError(code='UOW_NESTED')`.
8. The adapter does **not** `commit()` per operation nor `executescript` outside the migrator.
   It does not retry either: every `write()` is a single attempt (D-018).

### 2.3 `migrate.py`

```python
MIGRATIONS_DIR = Path(__file__).with_name("migrations")
MIGRATION_RE = re.compile(r"^(?P<version>\d{3})_[a-z0-9_]+\.sql$")


@dataclass(frozen=True, slots=True)
class MigrationResult:
    from_version: int
    to_version: int
    applied: tuple[str, ...]  # file names, in order
    backup_path: Path | None  # None if there was nothing to apply


def current_version(conn: sqlite3.Connection) -> int:
    """PRAGMA user_version."""


def pending_migrations(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[Path]:
    """00X_*.sql files with version > user_version, sorted by ascending version.
    Duplicate version or gap (001, 003) -> HifaError(code='MIGRATION_SEQUENCE')."""


def backup_database(db_path: Path, *, now: datetime, directory: Path | None = None) -> Path:
    """Online copy with sqlite3.Connection.backup(), NOT with shutil.copy.
    Destination: <stem>-<YYYYmmddTHHMMSSZ>.db.bak next to the database (pattern of SPEC-010 R5).
    If the destination exists, it appends -1, -2, ... It aborts with HifaError(code='BACKUP_FAILED') if the
    copy cannot be verified (size > 0 and readable PRAGMA user_version)."""


def migrate(
    db_path: Path, *, now: datetime, directory: Path = MIGRATIONS_DIR, do_backup: bool = True
) -> MigrationResult:
    """Orchestrates backup + application. See §5 flow 1."""
```

Migrator rules:

- One file = one transaction = one version. Two files are never applied in the same transaction.
- `PRAGMA user_version = N` is written **inside** the same transaction as the DDL (in SQLite,
  `user_version` is part of the header and participates in the transaction). If the DDL fails, neither
  table nor version remain.
- `do_backup=True` and a non-empty `pending_migrations()` ⇒ the backup happens **before** the first
  `BEGIN`. If the backup fails, the database is not touched (fail closed).
- `require_fts5(conn)` runs **before the backup and before the first `BEGIN`**, and only when
  `pending_migrations()` is non-empty: the v1 schema creates the `issue_fts` virtual table (SPEC-010 §3.4),
  so a build whose SQLite carries no `ENABLE_FTS5` cannot apply it. Failing there gives one named error
  (`Fts5Unavailable`, `fts5_unavailable`, exit 6) instead of `no such module: fts5` escaping from inside a
  transaction, and it leaves the database untouched.
- Database already up to date ⇒ `MigrationResult(from=n, to=n, applied=(), backup_path=None)` and zero writes.
- The migrator opens its own connection in write mode (`connect(path)`) and closes it in `finally`.
- `migrate()` is not called on its own: `hifa db migrate` (SPEC-050) or `hifa init` (SPEC-100) invoke it.

### 2.4 Repositories — signatures

All repos share the constructor
`__init__(self, conn: sqlite3.Connection, *, clock: Clock, idgen: IdGen, readonly: bool = False)
-> None` and do **not** open connections. `readonly` is the **only channel** through which a repo knows it
lives in a read unit: with `readonly=True` all write methods raise
`Conflict(code='READ_ONLY_UOW')` before emitting SQL (§4 R6); the read ones work the same.
`require_*` raises `NotFound`; `get_*` returns `None`. No method returns `sqlite3.Row` nor
tuples: always a domain entity (validated by Pydantic) or `None`.

**`TeamRepo`**

| Method | Signature | Notes |
|---|---|---|
| `get` | `(team_id: str) -> Team \| None` | includes archived |
| `get_by_key` | `(workspace_id: str, key: str) -> Team \| None` | key already normalized |
| `list` | `(*, include_archived: bool = False) -> list[Team]` | order `name` |
| `insert` | `(team: Team) -> Team` | `UNIQUE(workspace_id,key)` → `Conflict` |
| `update` | `(team: Team) -> Team` | `RETURNING *`; touches `updated_at` |
| `archive` | `(team_id: str, at: datetime) -> None` | sets `archived_at`, `updated_at` |
| `count_open_issues` | `(team_id: str) -> int` | counts `v_issue.is_open AND visible` |
| `workspace` | `() -> Workspace \| None` | first row of `workspace` |
| `ensure_workspace` | `(*, name: str, url_key: str) -> Workspace` | idempotent by `url_key` |
| `states` | `(team_id: str, *, include_archived: bool = False) -> list[WorkflowState]` | order `position, name`; SQL-7 row 16 is this statement |
| `get_state` | `(state_id: str) -> WorkflowState \| None` | |
| `get_state_by_name` | `(team_id: str, name: str) -> WorkflowState \| None` | `UNIQUE(team_id,name)` |
| `default_state` | `(team_id: str) -> WorkflowState \| None` | `is_default = 1` and not archived |
| `insert_state` | `(state: WorkflowState) -> WorkflowState` | |
| `update_state` | `(state: WorkflowState) -> WorkflowState` | |
| `members` | `(team_id: str) -> list[Member]` | `team_membership JOIN member`, order `sort_order, name` |
| `add_member` | `(team_id: str, member_id: str, *, is_owner: bool = False, sort_order: float \| None = None) -> None` | `INSERT OR IGNORE` into `team_membership` |
| `previous_keys` | `(team_id: str) -> tuple[str, ...]` | `team_previous_key` order `changed_at DESC` (SPEC-010 R3) |
| `add_previous_key` | `(team_id: str, key: str, *, at: datetime) -> None` | only if the key changes |

**`IssueRepo`**

| Method | Signature | Notes |
|---|---|---|
| `get` | `(issue_id: str) -> Issue \| None` | by PK |
| `get_by_identifier` | `(identifier: str) -> Issue \| None` | `UNIQUE(identifier)` |
| `require` | `(ref: str) -> Issue` | accepts a ULID **or** `ENG-123`; otherwise `NotFound(resource='issue', key=ref)` |
| `next_number` | `(team_id: str) -> int` | §2.5 SQL-1; **only** inside `write()` |
| `insert` | `(issue: Issue) -> Issue` | §2.5 SQL-2, `RETURNING *` |
| `update` | `(issue: Issue) -> Issue` | 31 columns, `RETURNING *` |
| `set_state` | `(issue_id: str, state_id: str, *, now: datetime) -> Issue` | sets **only** `state_id` + `updated_at`; the milestones are computed by the use case (SPEC-040 R7) |
| `set_positions` | `(issue_id: str, *, sort_order: float \| None = None, board_order: float \| None = None, sub_issue_sort_order: float \| None = None, now: datetime) -> None` | `None` = do not touch the column |
| `positions` | `(team_id: str, scope: Literal["sort","board","sub"], parent_id: str \| None = None, state_id: str \| None = None) -> list[tuple[str, float]]` | `[(issue_id, position)]` ascending; `scope='sub'` filters by `parent_id`; `state_id` filters the board by target state (D-019) |
| `list_children` | `(issue_id: str, *, include_closed: bool = True) -> list[Issue]` | order `sub_issue_sort_order, number` |
| `count_open_children` | `(issue_id: str) -> int` | §2.5 SQL-5 |
| `ancestor_chain` | `(issue_id: str, *, max_depth: int = 32) -> dict[str, str \| None]` | `{issue_id: parent_id}` from the issue upward; stops at `max_depth` |
| `subtree_height` | `(issue_id: str) -> int` | levels **below** the issue: a leaf is `0`, its parent `1`. One recursive statement; it is what the depth bound of a reparent is measured against (SPEC-020 R7) |
| `count_open` | `(team_id: str) -> int` | `v_issue` with `is_open AND visible` |
| `archive` / `unarchive` | `(issue_id: str, at: datetime) -> None` | `archived_at = at` / `NULL` |
| `trash` / `restore` | `(issue_id: str, at: datetime) -> None` | `trashed = 1` / `0` + `updated_at` |
| `label_ids` | `(issue_id: str) -> tuple[str, ...]` | order `label.name` |
| `set_labels` | `(issue_id: str, label_ids: Sequence[str]) -> None` | diff: DELETE of the leftovers + INSERT OR IGNORE of the new ones |
| `add_label` | `(issue_id: str, label_id: str) -> None` | `INSERT OR IGNORE`; duplicate PK is **not** an error |
| `remove_label` | `(issue_id: str, label_id: str) -> bool` | `True` if it deleted |
| `relations` | `(issue_id: str) -> list[IssueRelation]` | both directions (the issue as source or target) |
| `add_relation` | `(relation: IssueRelation) -> IssueRelation` | applies the canonical pair of the type before the `INSERT` (SPEC-020 D-045), so the `UNIQUE(...)` sees one key per edge; a duplicate → `Conflict(rule='RELATION_DUPLICATE')`; returns the stored row |
| `remove_relation` | `(relation_id: str) -> bool` | |

**`LabelRepo`**

| Method | Signature | Notes |
|---|---|---|
| `get` | `(label_id: str) -> Label \| None` | |
| `get_by_name` | `(workspace_id: str, team_id: str \| None, name: str) -> Label \| None` | **exact** `name` match — `name = ?`, not `casefold` — with two lookups: team and workspace (`team_id IS NULL`). No flow of this corpus resolves a name with it: every resolution of a **user-supplied** name reads the level's rows with `list(..., include_retired=True)` and compares them with `casefold` (SPEC-040 R22, whose refusal needs the **rows** and not one of them), `init`'s seed resolves its four names the same way at its own level (SPEC-100 R11bis), and `hifa import` has no implementation yet — its rule (SPEC-080 R10) reads the level's rows too, so it will not consume this method either (SPEC-080 §2.1.1). It stays on the port as the exact lookup a caller **holding a stored key** needs (a fixture, an audit, a migration script), and a name that arrives from a user must never be resolved with it: it returns one row and so cannot report that a level held two. No signature change. |
| `list` | `(workspace_id: str, *, team_id: str \| None \| Unset = UNSET, include_retired: bool = False) -> list[Label]` | `UNSET` = no team filter; `None` = workspace labels only |
| `insert` | `(label: Label) -> Label` | `uq_label_team` / `uq_label_ws` → `Conflict(rule='DUPLICATE_LABEL')` |
| `update` | `(label: Label) -> Label` | `RETURNING *` |
| `retire` | `(label_id: str, at: datetime) -> None` | `retired_at = at`; never `DELETE` |
| `count_uses` | `(label_id: str) -> int` | `issue_label`; used by `archive_label` of SPEC-040 |

**`ProjectRepo`**

| Method | Signature | Notes |
|---|---|---|
| `get` | `(project_id: str) -> Project \| None` | includes archived |
| `list` | `(*, team_id: str \| None = None, include_archived: bool = False) -> list[Project]` | `team_id=None` = all |
| `insert` | `(project: Project) -> Project` | |
| `update` | `(project: Project) -> Project` | `RETURNING *` |
| `archive` | `(project_id: str, at: datetime) -> None` | `archived_at`, `updated_at` |
| `add_milestone` | `(milestone: ProjectMilestone) -> ProjectMilestone` | `UNIQUE(project_id, name)` → `Conflict(rule='DUPLICATE_MILESTONE')` |
| `milestones` | `(project_id: str) -> list[ProjectMilestone]` | order `sort_order, name` |
| `get_milestone` | `(milestone_id: str) -> ProjectMilestone \| None` | |

**`CycleRepo`**

| Method | Signature | Notes |
|---|---|---|
| `get` | `(cycle_id: str) -> Cycle \| None` | |
| `current` | `(team_id: str, at: datetime) -> Cycle \| None` | `starts_at <= at < ends_at` and `completed_at IS NULL` |
| `list` | `(team_id: str, *, include_completed: bool = True) -> list[Cycle]` | order `number` |
| `next_number` | `(team_id: str) -> int` | `MAX(number)+1` inside `write()` |
| `insert` | `(cycle: Cycle) -> Cycle` | `UNIQUE(team_id, number)` → `Conflict(rule='DUPLICATE_CYCLE_NUMBER')` |
| `complete` | `(cycle_id: str, at: datetime) -> None` | `completed_at = at` |
| `assign` | `(issue_id: str, cycle_id: str \| None, at: datetime) -> None` | `cycle_id` + `updated_at` |

**`CommentRepo`**

| Method | Signature | Notes |
|---|---|---|
| `get` | `(comment_id: str) -> Comment \| None` | |
| `list_for_issue` | `(issue_id: str, *, include_resolved: bool = True) -> list[Comment]` | order `created_at, id` |
| `insert` | `(comment: Comment) -> Comment` | `RETURNING *` |
| `update_body` | `(comment_id: str, body: str, at: datetime) -> Comment` | `body` + `edited_at` + `updated_at` |
| `resolve` | `(comment_id: str, at: datetime) -> None` | `resolved_at = at` if it was NULL (idempotent) |
| `unresolve` | `(comment_id: str) -> None` | `resolved_at = NULL` |

**`HistoryRepo`**

| Method | Signature | Notes |
|---|---|---|
| `record` | `(issue_id: str, changes: Sequence[tuple[str, str \| None, str \| None]], *, actor_id: str \| None, now: datetime) -> int` | returns inserted rows; `executemany` (§2.5 SQL-8) |
| `list_for_issue` | `(issue_id: str, *, limit: int = 100) -> list[IssueHistory]` | order `created_at DESC, id DESC`; the window SQL-7 row 11 defines, `board_order` rows the move produced excluded (§2.4) |

`record` uses `executemany` (§2.5 SQL-8). The serialization of values to TEXT is fixed:
`Priority` → `str(int)`, `float` → `str(v)` **as stored, trailing `.0` and all**, `bool` → `"1"`/`"0"`,
`date` → `YYYY-MM-DD`, `datetime` → ISO-8601 `Z`, `None` → `NULL`, `str` → as is.

**Two `float` fields reach that list, and the trim is the grammar's, not the storage's.** `board_order`
stores a pair of floats — the table below gives its sides as `float → float`, and the measured database has
34 such rows — so `str(v)` is the rule for **both** float fields, and the stored text of a rank pair is
`1024.0` → `8192.0`: the decimal form SPEC-040 §4.1 **R3** publishes on the wire (`float` → its decimal
form, `1024.0`) and what `_text` in `src/hifa/app/issues.py` writes. A trim here would rewrite `1024.0` to
`1024` and desynchronise the stored value from the invariant R3 pins, which is why `board_order` is
untouched. The trim belongs to the **sentence grammar**, where the two kinds of float are rendered
differently: **an `estimate` renders with a trailing `.0` removed (`3.0` → `3`), and a
`board_order` pair renders as stored (`1024.0` → `8192.0`)** — the estimate is a scale label, the order is
a sort key — so no reader can carry one rule over to the other field. The trim is safe there because
**every estimation scale is whole**: `_ESTIMATE_SCALES` in `src/hifa/domain/enums.py` is exponential,
fibonacci, linear 0-100 and tShirt, all whole floats, and `validate_estimate` refuses a fraction with
`ESTIMATE_NOT_IN_SCALE`, so the rule never meets one — it removes a trailing `.0` and nothing else, and
never truncates to `int`.

**The closed `field` vocabulary is the writers', and this table is its enumeration.** `field` names a
change in the same two families SPEC-050 §2.4.2 states as the rule: **a change to a column of `issue`
carries that column's name**, and **a record event that is not a column carries the noun of the write**.
The rule is SPEC-050's; the *set* is this layer's, because the value is the first argument of the
`INSERT` SQL-8 writes, and a writer that invents a name writes a row no rendering knows. Every row below
has a producer in `src/hifa/app/` and nothing outside this table may be written; the sentence column is
**SPEC-070 §2.5.7's wording, quoted**, and that section owns it (a change of a wording is one edit there
and one here, in the same revision). The third column is the row's **shape**, and it is what separates the
sentences of one field: **`completed_at`'s two directions differ from each other by which side is `NULL`**
— `completed it` (`NULL → ISO`) against `cleared the completion` (`ISO → NULL`) — **while `started_at`
differs from both by having a single direction**, `NULL → ISO`, and renders `started work`. Field and
direction together pick the sentence; which side is `NULL` picks between the two of one field.

| `field` | writer (use case) | the row's sides | renders as (SPEC-070 §2.5.7) |
|---|---|---|---|
| `created` | `create_issue` (§5.1 step 16) | `(NULL, NULL)` — the identifier before migration `004` | `created the issue` |
| `state_id` | `update_issue`, `move_issue` (§5.4, §5.5) | `ULID → ULID` (the column is `NOT NULL`) | `moved from Backlog to Todo` |
| `title` | `update_issue` | `text → text` | `changed the title to “…”` |
| `description` | `update_issue` | `text → text` (two bodies) | `edited the description` (D-E: no diff, and the pair is never rendered) |
| `priority` | `update_issue` | `"0"…"4" → …` / `→ NULL` | `set priority to High` / `cleared the priority` / `changed the priority from High to Low` |
| `assignee_id` | `update_issue` | `ULID → ULID` / `→ NULL` | `assigned to administrator` / `unassigned` / `reassigned from administrator to Mo` |
| `parent_id` | `update_issue` | `ULID → ULID` / `→ NULL` | `moved under HIFA-8` / `removed it from HIFA-8` |
| `estimate` | `update_issue` | `float → float` / `→ NULL` | `set the estimate to 3` / `cleared the estimate` |
| `due_date` | `update_issue` | `YYYY-MM-DD → …` / `→ NULL` | `set the due date to 20 Nov 2026` / `removed the due date` |
| `branch_name` | `update_issue` (derived when the title changes) | `text → text` | `changed the branch name` |
| `board_order` | `update_issue`, `move_issue` | `float → float` | `reordered it in the column`; **no row reaches the formatter** when the preceding row of the same issue (by `id`) is a `state_id` row — the read drops it (§2.6, SQL-7 row 11) |
| `started_at` | `update_issue`, `move_issue` (`apply_milestones`) | `NULL → ISO` (**one** direction: the writer never clears it) | `started work` |
| `completed_at` | `update_issue`, `move_issue` (`apply_milestones`) | `NULL → ISO` / `ISO → NULL` | `completed it` / `cleared the completion` |
| `canceled_at` | `update_issue`, `move_issue` (`apply_milestones`) | `NULL → ISO` / `ISO → NULL` | `canceled it` / `cleared the cancellation` |
| `archived_at` | `archive_issue`, `restore_issue` | `NULL → ISO` / `ISO → NULL` | `archived the issue` / `restored the issue` |
| `trashed` | `trash_issue`, `restore_issue` | `"0" → "1"` / `"1" → "0"` | `deleted the issue` / `restored it from the trash` |
| `project_id` | `update_issue` (**clearing only**: GAP-08 refuses a value) | `NULL` in every row v1 can write | `moved to project X` / `removed it from project X` |
| `milestone_id` | `update_issue` (**clearing only**: GAP-08) | `NULL` in every row v1 can write | `set milestone Y` / `removed it from milestone Y` |
| `cycle_id` | `update_issue` (**clearing only**: GAP-08) | `NULL` in every row v1 can write | `added to cycle Z` / `removed it from cycle Z` |
| `label` | `add_label`, `remove_label` (§5.6) | `NULL → name` / `name → NULL` | `added the label bug` / `removed the label bug` |
| `relation` | `relate_issues`, `unrelate_issues` (§5.7) | `NULL → "<type> <identifier>"` / the reverse | `marked it as blocking HIFA-33` / `removed the blocking relation to HIFA-33` |

**What the table settles, and what it does not.**

- **`relation` is a noun and not a `relation:<type>`.** The value is `f"{type.name_as_seen(direction)}
  {other.identifier}"` (`_relation_value` in `src/hifa/app/issues.py`), so the type travels **inside the
  value**, in the words the asking issue reads, and beside the far end's identifier. A field of the form
  `relation:blocks` would put the same fact in the discriminator and in the value, and SPEC-050 §2.4.2's
  rule already says a non-column event carries the noun of the write.
- **`label` stores the name, not the label's ULID**, so a label row needs no resolution at all: the name
  it carries is what the chip shows. Same for `relation`, which carries an identifier. **Eight** kinds of
  stored value are not printable as they stand, and they split in two: six are ULIDs the read resolves —
  `state_id`, `assignee_id`, `parent_id` and the three reserved links — and two are keys the grammar turns
  into words, `priority` (`"2"` → `High`) and `due_date` (`"2026-11-20"` → `20 Nov 2026`). All eight have a
  producer in the read (§2.6, §2.4 below); the resolution rule below is about them and about nothing else.
- **`comment_added` is not in this table because no writer produces it.** It is the name SPEC-050 §2.4.2
  reserves for the revision that lands `comment_on_issue`'s history row (HIFA-6, D-A binds it to write
  one); until that writer exists, a `comment_added` row cannot be stored, and a sentence for it would be
  a rendering of a field nobody writes. The revision that adds the writer adds the row here, in the same
  PR, as the fourth non-column noun.
- **`started_at` publishes one direction because `apply_milestones` has one.** Entering a `started` state
  stamps `started_at` when it was empty, and no path clears it: the return to an open type nulls
  `completed_at` and `canceled_at` and keeps `started_at` (`apply_milestones` in `src/hifa/app/rules.py`:
  `started_at` is appended only as `("started_at", now)` and never as `("started_at", None)`). A row with
  `new_value` `NULL` on this field therefore has no writer, and
  `cleared the start time` was a sentence with no row — the same rule that keeps `comment_added` out of
  this table. `completed_at` and `canceled_at` keep both directions because both writers exist.
- **The three link columns cannot be written at all today, and they stay in the table.** `project_id`,
  `milestone_id` and `cycle_id` are accepted by `update_issue` for clearing and refused with
  `NOT_IMPLEMENTED` for a value (GAP-08); a clear of a column that is already `NULL` changes nothing, so
  `update_issue`'s diff writes **no** row for them (the measured database has zero). They are listed
  because the `UPDATE_FIELDS` tuple can write all three the day the repositories land, and a writer with
  no row in this table would write a name the formatter reads as unknown.
- **`board_order` is the move's consequence, and the read does not hand it over when it is.** A move that
  changes a state writes the `state_id` row and — **when the rank moves with it** — the `board_order` row,
  both from one `record` call; a move into an empty column, or any move whose slot comes out equal to the
  stored one, writes the `state_id` row **alone**. The two rows are therefore not always consecutive, and
  the rank is not always a second row of an event the `state_id` row already names. The read excludes a
  `board_order` row **when the immediately preceding history row of the same issue, by `id`, is a
  `state_id` row that carries the same `created_at`** (SQL-7 row 11's `WHERE`). Both conditions together
  are the record **batch**: one call stamps one instant, and the `id` order says the two rows left the
  writer together. Adjacency alone is not the batch — a drag written after a state-only move is the very
  next row by `id` and carries an instant of its own, so dropping it would lose an event that no count, no
  error and no floor line would ever report. The rule rests on what the writers do, never on a count: the
  measured database (`~/.local/share/hifa/hifa.db`) holds batched rank rows and, after a state-only move,
  rank rows of their own; **the counts drift as the file is used, the rule does not.** A `board_order` row
  the rule does not filter is a drag inside a single column and renders `reordered it in the column`.
- **No row is dropped by the formatter.** The exclusion is the read's, so the set `history_rows` receives is
  already the timeline's. **Three surfaces publish the window the read returned and never cut it — the
  `HISTORY` section of `issue show --with`, the REST array of SPEC-060 §2.4.2 and the page's list — while
  two cut, in two different ways: the panel cuts without being asked (8 of the window, `Show all N`), and
  `issue history` cuts to a `--limit` the caller controls — 50 by default and 100 at most, so no flag
  publishes 50 of the read's 100-row window and `--limit 100` publishes the window (SPEC-050 §2.5.3.3).**
  The panel's list holds the first 8 of those rows (D-C) and its `Show all N` names the window —
  `HistoryPage.window_size`, the rows the read returned — so a twelve-row issue has a page of 12, a REST
  array of 12, and a panel of 8 followed by `Show all 12` (SPEC-070 §2.5.7.2). The panel's 8 is a layout
  decision of that edge and not a number about the read, and the verb's 50 is a request and not a default of
  the read; a filter in a rendering would have made the payloads disagree with each other and with
  themselves after a swap, and one `WHERE` cannot.

**The `created` row carries no value from this revision's writer on, and the rows already written are
normalised by a migration: the two halves are one change.** `create_issue` writes `("created", None, None)`
— SPEC-040 §5.1 step 16, the row SPEC-040 §7 asserts — and that edit **lands in the same PR as
`migrations/004_history_created_backfill.sql`**, which is the ordering `comment_added` and HIFA-6 are
declared with for the same reason: a writer and the data it leaves behind are one fact, and a revision that
ships one without the other publishes two. Until that PR, every writer in the tree writes the identifier —
this table's row for `created` describes the **contract**, not the tree as it stands on this date.

**The window in which an older binary still writes the identifier is real and it does not close.** `004`
runs once, gated by `PRAGMA user_version` (§2.4, SPEC-010 R5.1), so a database already at 4 never re-runs
it; a binary older than this PR, pointed at the same file, keeps writing the issue's identifier into
`new_value` on every issue it creates, and no second pass will clean those rows. The consequence is bounded
and invisible: `create_issue` and the timeline read the `field` and not the value (SPEC-070 §2.5.7's
grammar has no place for a value on `created`), so a mixed file prints one shape. What this layer can state
is what it owns — on a database whose writer is this revision's, `new_value` is `NULL` on every `created`
row after `004`, and a file touched by both binaries is the one case it cannot normalise, said here rather
than papered over.

**Resolution is the read's, it is bulk, and it is not the edge's.** The values that are ULIDs are
resolved **once per entity kind per page**, in the read that carries the history rows, never per row:
`issue_detail` (§2.6) already loads the team's states (SQL-7 row 16) and the actors of the page (SQL-7
row 13, one `UNION` over `comment.author_id` and `issue_history.actor_id`), so `state_id` and
`assignee_id` cost **zero** statements; a kind the page's history names that the detail does not already
carry — `issue` for `parent_id`, and the three reserved links when their writers land — costs **one**
statement, `WHERE id IN (…)` over the distinct ULIDs of that kind, and one statement only. **The N+1 is
the thing this rule forbids**: a lookup per row would make the cost of a page a function of its history,
which is what REQ-F030-12 measures and what HIFA-18 already tracks for the relations.

**Two of the eight need no statement at all, and the read still produces them.** `priority` and
`due_date` are not ULIDs: the first is a closed 0-4 and the second is a calendar date, so a map of them is
a **constant**, not a lookup — `priorities` is the five labels `v_priority` fixes (`0` `No priority`,
`1` `Urgent`, `2` `High`, `3` `Medium`, `4` `Low`) and `due_dates` is the `20 Nov 2026` shape the grammar
of SPEC-070 §2.5.7 and the day separators of §2.5.7.2 already print, applied to the distinct values the
page's rows carry. **Neither is this layer's, and neither is declared here:** both are the application
layer's constants, declared once in SPEC-040 §2.2 and handed to `history_rows` as data — `PRIORITY_LABELS`
is that function's `priorities` **default**, and `DUE_DATE_FORMAT` is the format its `due_dates=None`
branch builds the map with, `due_dates` itself defaulting to `None` so a caller's own map wins. A map of a
closed 0-4 and a format of a date are constants, and a constant belongs to the module that reads it.
Neither costs a statement: REQ-F030-12's 12 does not move for a row of either kind, and a page with one
`priority` row and a page with two hundred pay the same nothing.
Without them the grammar's `set priority to High` would have no producer and `priority "2"` would reach
the screen as a `2`.

The maps reach the formatter of SPEC-040 §2.2 (`history_rows`) **as data**, in two families, and only one
of the two is this layer's:

- **The read's three** — `members`, `parents` and the `states` labels — are built by this layer from its
  own reads: `members` is §2.6's `members` map (SQL-7 row 13), `parents` is §2.6's `history_parents` map
  (SQL-7 row 17, the one statement a page pays for that kind) and `states` is the `{id: name}` map this
  layer projects from `IssueDetail.states` (row 16 prices as zero statements). These are `{value: label}`
  and they cross the boundary as `states`, `members` and `parents`.
- **The two word maps** — `priorities` and `due_dates` — are **not** handed over by this layer at all:
  they are SPEC-040 §2.2's constants, taken by `history_rows` as its own defaults. The read hands nothing
  for them because there is nothing to read: a closed 0-4 and a calendar date are not lookups.

Neither family is a sentence: a label is data a read model owns, a sentence is wording an application
function owns (P4), and `domain/` — which is where a state's name lives — learns nothing about how a
timeline reads.

### 2.5 SQL of the critical operations

**SQL-1 — `next_number` (R2 of SPEC-010).** It always runs inside a `write()` already opened with
`BEGIN IMMEDIATE`; the writer lock is already taken, so the `MAX+1` cannot be read by two
writers at once:

```sql
SELECT COALESCE(MAX(number), 0) + 1 AS next_number
FROM issue
WHERE team_id = ?;
```

Neither `AUTOINCREMENT` nor a counter table is used: `UNIQUE(team_id, number)` is the safety
net. **A single retry strategy (D-018):**

1. `next_number()` does **not** retry: it returns `MAX+1` and nothing else.
2. If the `INSERT` collides with `UNIQUE(team_id, number)` (an external write that did not respect the lock,
   or an issue inserted outside this use case), `translate_sqlite_error` turns it into
   `Conflict(rule='DUPLICATE_NUMBER', field='number')` (§6).
3. The retry is done by **the same helper** as for `SQLITE_BUSY`,
   `app/retry.py::with_write_retry` (SPEC-040 §2.2), with
   `retry_on={'DB_BUSY', 'DUPLICATE_NUMBER'}`: every attempt reopens `BEGIN IMMEDIATE`, re-reads
   `next_number()` and inserts; up to **3 attempts** in total. Once the 3 are exhausted, it propagates the `Conflict`.

There are no two retry strategies (D-018): `next_number()` does not retry, `write()` does not retry and
the only loop in the system is the helper's, which re-executes the complete transaction —the only
correct thing, because after a `ROLLBACK` the computed `MAX+1` is no longer valid—. This is what
`SPEC-010` R2 calls "up to 3 retries".

**SQL-2 — `insert` with `RETURNING`.** 31 columns, 31 placeholders, in DDL order:

```sql
INSERT INTO issue (
  id, workspace_id, team_id, number, identifier, title, description, priority,
  priority_sort_order, estimate, due_date, state_id, assignee_id, creator_id,
  project_id, milestone_id, cycle_id, parent_id, sort_order, board_order,
  sub_issue_sort_order, started_at, completed_at, canceled_at, snoozed_until_at,
  archived_at, auto_archived_at, trashed, branch_name, created_at, updated_at
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
RETURNING *;
```

`RETURNING` requires SQLite ≥ 3.35. The adapter verifies this in `connect()` with
`sqlite3.sqlite_version_info >= (3, 35, 0)` and, if it is not met, raises
`HifaError(code='SQLITE_TOO_OLD')` **when opening**, not on the first insert. There is no fallback: a
`lastrowid` + later `SELECT` would double the round-trip and hide DDL defaults.

**SQL-3 — `update` with `RETURNING`.** 24 updatable columns: the 31 of the table minus `id`,
`created_at`, `workspace_id`, `team_id`, `number`, `identifier` and `creator_id`.

```sql
UPDATE issue SET
  title = ?, description = ?, priority = ?, priority_sort_order = ?, estimate = ?, due_date = ?,
  state_id = ?, assignee_id = ?, project_id = ?, milestone_id = ?, cycle_id = ?, parent_id = ?,
  sort_order = ?, board_order = ?, sub_issue_sort_order = ?, started_at = ?, completed_at = ?,
  canceled_at = ?, snoozed_until_at = ?, archived_at = ?, auto_archived_at = ?, trashed = ?,
  branch_name = ?, updated_at = ?
WHERE id = ?
RETURNING *;
```

`workspace_id`, `team_id`, `number`, `identifier` and `creator_id` are **not** updatable: moving an
issue across teams would change the `identifier` and break links (out of scope §10).

**SQL-4 — `list_view` with labels in ONE query (no N+1).** The classic `LEFT JOIN` forces a `GROUP BY`
over 20 columns and multiplies rows; the correlated subselect with `json_group_array` returns one
row per issue with the labels already aggregated:

```sql
SELECT i.id, i.identifier, i.title, i.priority, i.priority_label,
       i.state_id, s.name AS state_name, s.type AS state_type,
       i.team_id, t.key AS team_key,
       i.assignee_id, m.display_name AS assignee_name,
       i.project_id, i.cycle_id, i.milestone_id, pm.name AS milestone_name,
       i.due_date, i.estimate, i.parent_id,
       i.updated_at, i.is_open, i.visible,
       COALESCE((
         SELECT json_group_array(json_object(
                  'id', l.id, 'name', l.name, 'color', l.color,
                  'is_group', l.is_group, 'parent_id', l.parent_id))
         FROM issue_label il
         JOIN label l ON l.id = il.label_id
         WHERE il.issue_id = i.id
       ), '[]') AS labels_json
FROM v_issue i
JOIN workflow_state s ON s.id = i.state_id
JOIN team t          ON t.id = i.team_id
LEFT JOIN member m   ON m.id = i.assignee_id
LEFT JOIN project_milestone pm ON pm.id = i.milestone_id
WHERE i.team_id = :team_id
  AND (:include_archived = 1 OR :archived_only = 1 OR :trashed_only = 1
       OR i.archived_at IS NULL)   -- trash is the deeper layer: trashed_only opens this axis
  AND (:archived_only    = 0 OR i.archived_at IS NOT NULL)
  AND (:include_trashed  = 1 OR :trashed_only  = 1 OR i.trashed = 0)
  AND (:trashed_only     = 0 OR i.trashed = 1)
  AND (:is_open       IS NULL OR i.is_open = :is_open)
  AND (:state_count   = 0    OR i.state_id IN (/* :state_0, :state_1, ... */))
  AND (:stype_count   = 0    OR s.type     IN (/* :stype_0, ... */))
  AND (:assignee_id   IS NULL OR i.assignee_id = :assignee_id)
  AND (:unassigned    = 0    OR i.assignee_id IS NULL)
  AND (:project_id    IS NULL OR i.project_id = :project_id)
  AND (:cycle_id      IS NULL OR i.cycle_id = :cycle_id)
  AND (:no_cycle      = 0    OR i.cycle_id IS NULL)
  AND (:parent_id     IS NULL OR i.parent_id = :parent_id)
  AND (:prio_count    = 0    OR i.priority   IN (/* :prio_0, ... */))
  AND (:due_before    IS NULL OR i.due_date <= :due_before)
  AND (:label_count   = 0 OR (
        SELECT COUNT(*) FROM issue_label il2
        WHERE il2.issue_id = i.id AND il2.label_id IN (/* :label_0, :label_1, ... */)
      ) = :label_count)
  AND (<the cursor clause: form A or form B of §2.6.1, chosen by whether the anchor has a value>)
ORDER BY (<sort_key> IS NULL) ASC, <sort_key> <dir>, i.id <dir>
LIMIT :limit_plus_one OFFSET :offset;
```

**The trash is the deeper layer, and `trashed_only` opens the archive axis.** An issue archived and
then trashed carries `archived_at IS NOT NULL` *and* `trashed = 1` (D-043, SPEC-010 D4: the layers are
independent). A listing that kept `archived_at IS NULL` in that case would hide the row that `--trashed`
promises from every view. The archive predicate of SQL-4 therefore accepts `:trashed_only = 1` as an
alternative: with `trashed_only` set the archive axis adds **no** predicate and `trashed = 1` is the
only filter on the layers. The exception lives in the predicate, not in a second field, so every caller
that sets `trashed_only` reaches the whole trash.

**Order and cursor (D-011).** `<sort_key>` is chosen from the fixed `SORT_KEYS` table by
`filters.order` and its direction by `filters.reverse`; the final tie-breaker is **always** `i.id` and
it carries the same direction (§2.6.1). There is no `ORDER BY` assembled with user input — neither the
expression nor the direction is ever concatenated from a value the caller sent.

`(<sort_key> IS NULL) ASC` is the first term and the only one `dir` does not reach: it keeps the rows
without a value **last in both directions** (D-044). The cursor clause is **not** a row comparison:
`(<sort_key>, i.id) > (:cursor_key, :cursor_id)` with a `NULL` anchor matches nothing, because SQLite
evaluates any comparison against `NULL` as `NULL` and `WHERE NULL` matches no row. `list_view`
therefore appends one of the two forms §2.6.1 declares — the valued anchor with its three branches, or
the `NULL` anchor restricted to `<sort_key> IS NULL AND i.id <dir> :cursor_id` — after binding
`:cursor_key` to the value the anchor lookup returned. The operator and the `ORDER BY` direction are
decided by the same `dir` variable, so they cannot disagree. It reads `limit + 1` rows
(`:limit_plus_one`); the adapter trims to `limit`, reports `has_more = len(rows) > limit` and sets
`next_cursor = encode_cursor(f"{order}:{'desc' if filters.reverse else 'asc'}", items[-1].id)` when
`has_more`, `None` otherwise — the token carries the **lowercase** direction of §2.6.2, not the SQL
keyword. `COUNT(*)` is **never** executed to paginate. `offset` is only used when the caller is the web
(cap 500, §2.6).

Mandatory notes:

- The list of `:state_0..:state_N`, `:stype_0..`, `:prio_0..` and `:label_0..:label_N` is expanded in
  Python (`", ".join(f":label_{i}" for i in range(n))`), **never** with value interpolation (REQ-S002).
  All parameters are named and are passed in a single `dict`: `?` and `:named` are not mixed in the
  same statement (sqlite3 forbids it).
- `COALESCE(..., '[]')` is necessary: up to SQLite 3.43 `json_group_array` over zero rows returns
  `NULL`, not `[]`. The parsing (`json.loads`) happens in the adapter, not in the template.
- `ORDER BY` includes `i.id` as a tie-breaker: without it, two issues with the same `sort_order` can
  swap position between pages and break pagination.
- **Combination semantics are the ones of §2.6**: `state_ids`/`state_types`/`priorities` are **OR**
  inside their family, `label_ids` is **AND** (all requested labels), and every family is **AND** with
  the rest. The three OR families arrive as sets, so a repeated value is one `IN` element, not two.
- `limit` is received already validated by `ListFilters` (1-200, default 50, D-011); the adapter adds 1
  for `has_more` and accepts no other value. The caller passes the user's `limit`, never `limit + 1`.
- `offset` defaults to 0 and is used **only** by SPEC-070 (web scroll, cap 500). The CLI and the API paginate with
  `cursor` and pass `offset=0`.

**SQL-5 — `count_open_children`:**

```sql
SELECT COUNT(*) FROM v_issue WHERE parent_id = ? AND is_open = 1 AND visible = 1;
```

**SQL-6 — `board_view`.** A single ordered query; grouping is done in Python, not with `GROUP BY`
(grouping in SQL would force a `json_group_array` per column and lose the per-group limit):

```sql
SELECT i.id, i.identifier, i.title, i.priority, i.priority_label,
       i.state_id, s.name AS state_name, s.type AS state_type, s.position AS state_position,
       i.assignee_id, m.display_name AS assignee_name, m.initials AS assignee_initials,
       i.board_order, i.is_open
FROM v_issue i
JOIN workflow_state s ON s.id = i.state_id
LEFT JOIN member m    ON m.id = i.assignee_id
WHERE i.team_id = ?
  AND i.archived_at IS NULL AND i.trashed = 0
ORDER BY s.position, i.board_order, i.id;
```

For `group_by='assignee'` it is ordered by `m.display_name IS NULL, m.display_name, s.position,
i.board_order`; for `group_by='priority'` by `CASE i.priority WHEN 0 THEN 5 ELSE i.priority END,
i.board_order`; for `group_by='project'` a `LEFT JOIN project p` is added and it is ordered by
`p.name IS NULL, p.name, i.board_order`. The per-column limit (`limit_per_group`) is applied in
Python **after** grouping, and each `BoardGroup` reports `total` (all) and `issues` (trimmed).

**SQL-7 — `issue_detail`.** The statements the read executes, **in order**. The `include` set of §2.6
gates the three heavy collections and nothing else, and `include_trashed` (§2.6, D-059) binds a parameter
of row 1 instead of adding a statement; no statement of this read is a `COUNT(*)`:

| # | Statement | Result | Runs when |
|---|---|---|---|
| 1 | `SELECT <the SQL-4 projection> FROM v_issue i JOIN workflow_state s ON s.id = i.state_id JOIN team t ON t.id = i.team_id LEFT JOIN member m ON m.id = i.assignee_id LEFT JOIN project_milestone pm ON pm.id = i.milestone_id WHERE (i.identifier = upper(:ref) OR i.id = :ref) AND (:include_trashed = 1 OR i.trashed = 0)` | header (`IssueListItem`), `milestone_id`/`milestone_name` included; the archive columns are not in this projection | always |
| 2 | `SELECT * FROM issue WHERE id = ?` | the entity row: `description`, `archived_at`, `trashed` | always |
| 3 | `SELECT * FROM team WHERE id = ?` | `team` | always |
| 4 | `SELECT * FROM workflow_state WHERE id = ?` | `state` | always |
| 5 | `SELECT * FROM project WHERE id = ?` | `project` | the row has a `project_id` |
| 6 | `SELECT * FROM cycle WHERE id = ?` | `cycle` | the row has a `cycle_id` |
| 7 | statement 1's projection with `WHERE i.id = :parent AND i.trashed = 0` | `parent`; `null` when the pointer names a row the trash layer holds | the row has a `parent_id` |
| 8 | statement 1's projection with `WHERE i.parent_id = :parent AND i.trashed = 0 ORDER BY i.sub_issue_sort_order, i.number` | `children` | always |
| 9 | `SELECT * FROM issue_relation WHERE issue_id = ? OR related_issue_id = ?`, then statement 1's projection `WHERE i.id = :other AND i.trashed = 0` **once per relation row** — the first in fetch order, the read returning it **unordered** | `relations` | always; one statement when the issue has no relation |
| 10 | `SELECT * FROM comment WHERE issue_id = ? ORDER BY created_at, id` | `comments` | `comments` ∈ `include` |
| 11 | `SELECT h.* FROM issue_history h WHERE h.issue_id = ? AND NOT (h.field = 'board_order' AND COALESCE((SELECT p.field = 'state_id' AND p.created_at = h.created_at FROM issue_history p WHERE p.issue_id = h.issue_id AND p.id < h.id ORDER BY p.id DESC LIMIT 1), 0) ORDER BY h.created_at DESC, h.id DESC LIMIT 100` | `history`: the window, newest first, **with the rank of a move already removed** (§2.4) | `history` ∈ `include` |
| 12 | `SELECT * FROM attachment WHERE issue_id = ? ORDER BY created_at` | `attachments` | `attachments` ∈ `include` |
| 13 | `SELECT id, display_name FROM member WHERE id IN (SELECT author_id FROM comment WHERE issue_id = ? AND author_id IS NOT NULL UNION SELECT actor_id FROM issue_history WHERE issue_id = ? AND actor_id IS NOT NULL)` | `members` | `comments` ∈ `include` or `history` ∈ `include` |
| 14 | `SELECT * FROM member WHERE id = ?` | `assignee` | the row has an `assignee_id` |
| 15 | `SELECT * FROM member WHERE id = ?` | `creator` | the row has a `creator_id` |
| 16 | `SELECT id, team_id, name, type, color, position, is_default, archived_at FROM workflow_state WHERE team_id = ? AND archived_at IS NULL ORDER BY position, name` | `states` | always |
| 17 | `SELECT id, identifier FROM issue WHERE id IN (/* the distinct ULIDs of the page's `parent_id` history rows */)` | `history_parents`: the bulk map of the one history kind this read does not already carry, `{ulid: identifier}` | `history` ∈ `include` **and** at least one row of row 11 has `field = 'parent_id'` |

**The lookups the other reference forms need are not statements of this read.** `issue_id_by_ref`,
`issue_id_by_number` and `team_by_previous_key` (§2.6) are called by the application layer's `issue_detail`
**before** this read, which then receives an issue id: the first answers a ULID or a current identifier, the
second the number inside a team, the third the team that left a key behind. They add no row above and
neither total moves, and a caller that already holds an id pays none of them.

**Row 1's identifier branch folds the parameter, never the column.** `upper(:ref)` is a deterministic
expression of a bound value, so the planner treats it as a constant and `sqlite_autoindex_issue_2` answers
one seek; `COLLATE NOCASE` on `i.identifier` reads as the same comparison and is not one — it makes the
unique index unusable, both branches of the `OR` collapse and the statement (`v_issue` flattened, `SCAN i`)
reads every row of `issue`, on `show`, on the board card and on the issue panel. The comparison is the
whole of the same rule either way: `issue.identifier` is written normalised (`normalize_key` uppercases the
team's key), so folding what the caller typed is all the case-insensitivity there is, and no Unicode claim
is made or needed. A `SCAN` here is answered by §10's rule — a new index in a migration, never a change in
the adapter — and no index is missing: the statement was asking for one it could not use.

**Row 11 is one statement, and the filter in it is §2.4's batch rule.** The two `field` names in its
`WHERE` are literal constants of the statement — §7's no-interpolation scenario looks for an f-string, `%`
or `+` around a **value**, and a constant is none of the three — and the correlated subquery asks one
question of each candidate row inside the same statement: *is the row immediately before this one, by
`id`, a `state_id` row **of the same instant**?* Two conditions because one is not the batch: the `id`
says the two rows left together and the instant says they left in one `record` call, and a drag written
after a state-only move is the next row by `id` with an instant of its own — dropping it would lose an
event and nothing in the payload would report the loss. A rank the move produced is therefore absent from every payload that carries the
window, and the statement count does not move: a filter is not a second statement.

**The two totals, and what they count.** For the issue §7's scenario declares — a creator, no
assignee, no parent and no relations, and a history with no `parent_id` row — with
`include=DETAIL_INCLUDE`: **12 statements** (11 before this revision added row 16). With
`include=frozenset()`: **8 statements** (7 before). Rows 5, 6, 7, 14 and 15 add one each only when the
row carries the id they look up, row 9 adds one per relation, and **row 17 adds one — once — when the
page's history names a parent**: those are the only statements the data can add, and R5 counts them.
Row 17 is the one addition of this revision, and it is gated by a **kind** and not by a count of rows:
a page with one `parent_id` row and a page with two hundred pay the same one statement, which is the
bulk rule of §2.4 in statement form. On a database whose history carries no `parent_id` row — measured:
the field list of `~/.local/share/hifa/hifa.db` on 2026-11-20 has ten values and `parent_id` is not one of
them, and that is a property of **that file**, not of the writers, because `update_issue` writes a
`parent_id` row on `issue edit --parent` (§2.4) — **the 12 does not move at all**, and a database that has
used `--parent` pays the 13. Labels add none — they ride in statement 1's
projection as a `json_group_array` subselect, so there is no `issue_label` statement to enumerate.
`milestone` adds none either: it is not a row but a join, `LEFT JOIN project_milestone pm ON pm.id =
i.milestone_id` with `pm.name` beside `i.milestone_id` in the same projection, so the key SPEC-050
§2.4.2 publishes on every `Issue` has a producer and the two totals above do not move (HIFA-49,
D-072 §3). The join is in **SQL-4's** projection, which is why the listing carries the same two columns:
one projection, both reads.

**Rows 7, 8 and 9 carry the trash predicate, and row 1 stays the header's only visibility test.** A row
the trash layer holds is absent from `children` (row 8), from `relations` (row 9) and from `parent`
(row 7, which answers `null`), which is **the** rule and is stated here once: it is the visibility
`issue list --parent` applies to the same children through SQL-4's `i.trashed = 0`. That predicate is
also the **whole** of what the two readers share — the listing's default view additionally binds
`is_open = 1` (`--open`, SPEC-050 §2.5.3), an axis this read does not have, so a **closed** child is
listed by one reader and not by the other and no sentence of this corpus claims the two list the same
set. The three filters are **unconditional**: they do not follow `include_trashed`, which is about the
issue the read returns and not about its neighbours — a trashed far end is absent even on the one read
that may see a trashed `ref`, and so is a trashed parent. A predicate is not a statement either, so the
totals hold.

**The keyword moves neither total, and the default answers exactly as row 1 did.** `include_trashed` is a
keyword-only argument of `issue_detail` with a default (§2.6, D-059). `False` — the default — binds
`:include_trashed = 0`, which reduces row 1's predicate to `i.trashed = 0`, so the **answer** a default read
gives is byte-identical to row 1 as it stood before `spec_revision` 12: the same rows in the same order,
from the same sixteen statements — **12** with `DETAIL_INCLUDE` and **8** with an empty `include`. `True`
binds `1`, the `OR` short-circuits and the trash predicate drops: still one statement with a wider `WHERE`,
never an extra one. **No count moves, because a predicate is not a statement**, and no other row of this
table is reached by the keyword.

**Row 1 is the read's only visibility test on the issue it returns, which is why the keyword is a
predicate and not a second read.** The header's projection is not pre-filtered: `v_issue` computes
`visible` as a *column*
(SPEC-010 §3), so this read inherits no trash filter it would have to work around — `i.trashed = 0` in row
1 is the whole exclusion, and dropping it is the whole change (D-059). Its "Runs when" stays **always**: a
trashed issue is the only row the default cannot reach, not a row of a different shape. Two surfaces do
**not** take the keyword and keep the default, so a trashed row stays unreachable through them: the row and
card fragments (the `include=frozenset()` render that answers `404` for a trashed reference, SPEC-070 §4
R29) and every listing, board and count. The one caller that passes it is the detail page and the panel of
a trashed issue (SPEC-070 §2.1.1).

Row 16 is **the statement the render needs and the one `TeamRepo.states` already runs** (§2.4): the
same SQL — non-archived, `ORDER BY position, name` — so the `<select>` this read feeds offers exactly
the columns that list offers, in exactly that order. An archived state is not one of them, and two
states at the same `position` come back by `name`, not in whatever order the page happens to hold them.

**SQL-8 — history in batch:**

```sql
INSERT INTO issue_history (issue_id, actor_id, field, old_value, new_value, created_at)
VALUES (?, ?, ?, ?, ?, ?);
-- executemany with the list of changes; a single round-trip per use case
```

**SQL-9 — `agenda`:**

```sql
SELECT i.id, i.identifier, i.title, i.due_date, i.priority, i.priority_label,
       s.type AS state_type, t.key AS team_key
FROM v_issue i
JOIN workflow_state s ON s.id = i.state_id
JOIN team t          ON t.id = i.team_id
WHERE (:team_id IS NULL OR i.team_id = :team_id)
  AND i.due_date IS NOT NULL
  AND i.archived_at IS NULL AND i.trashed = 0
  AND (:include_overdue = 0 OR (i.due_date < :start AND i.is_open = 1))
  AND (i.due_date >= :start AND i.due_date <= :end)
ORDER BY i.due_date,
         CASE i.priority WHEN 0 THEN 5 ELSE i.priority END,
         i.identifier;
```

`agenda(team, days)` with `days` in `[1, 366]`; `end = start + days - 1`. `include_overdue=True` by
default: it also shows what is overdue and open, which is the real use case (what was left behind).

**SQL-10 — `blocked_view`.** The projection is SQL-4's, one row per issue with the labels aggregated the
same way (one query, no N+1); what changes is the predicate, which is the derived verdict of SPEC-020
§4.1 R7 and not a rule of this layer:

```sql
-- SELECT list: identical to SQL-4, from the same FROM/JOIN and with the same labels_json subselect
FROM v_issue i
JOIN workflow_state s ON s.id = i.state_id
JOIN team t          ON t.id = i.team_id
LEFT JOIN member m   ON m.id = i.assignee_id
WHERE (:team_id IS NULL OR i.team_id = :team_id)
  AND i.visible = 1
  AND EXISTS (
        SELECT 1
        FROM issue_relation r
        JOIN issue blocker      ON blocker.id = r.issue_id
        JOIN workflow_state bs  ON bs.id = blocker.state_id
        WHERE r.type = 'blocks'
          AND r.related_issue_id = i.id
          AND bs.type NOT IN ('completed', 'canceled')
      )
ORDER BY i.identifier;
```

The `EXISTS` reads the edge in the direction it is stored — `issue_id` is the blocker and
`related_issue_id` the blocked issue (D-045) — so no inverse row and no `blocked_by` name is needed: the
type is a literal of the `CHECK`, and the release condition is `bs.type`, the blocker's state, never a
column on the edge (D-046). `visible = 1` is the archive axis of SPEC-010 §3.3 and the only thing that
hides a row: the blocked issue's own `state_type` is not filtered, because an issue closed while a
blocker is still open is a row the reader has to see. `:team_id IS NULL` is the workspace-wide form, and
the single index this predicate needs is the `UNIQUE(issue_id, related_issue_id, type)` of SPEC-010 read
from its second column, which is why the subquery starts from `issue_relation`.

### 2.6 `reads.py` — read models

```python
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Sequence


@dataclass(frozen=True, slots=True)
class LabelRef:
    id: str
    name: str
    color: str
    is_group: bool
    parent_id: str | None


@dataclass(frozen=True, slots=True)
class ListFilters:
    team_id: str
    state_id: str | None = None
    state_type: str | None = None
    assignee_id: str | None = None
    unassigned: bool = False
    project_id: str | None = None
    cycle_id: str | None = None
    parent_id: str | None = None
    priority: int | None = None
    label_ids: tuple[str, ...] = ()
    due_before: date | None = None
    q: str | None = None  # title filter, not search (D-025): LIKE '%term%' over i.title, ESCAPE
    include_archived: bool = False
    include_trashed: bool = False
    order: Literal["manual", "created", "updated", "priority", "due"] = "manual"
    limit: int = 50
    cursor: str | None = None  # base64url(f"{order}:{asc|desc}|{ULID}"), opaque (§2.6.2)
    offset: int = 0  # WEB ONLY (SPEC-070), cap 500
    # --- additive since spec_revision 6, all with a default (D-026) ---
    state_ids: tuple[str, ...] = ()  # OR: any of these states (union with `state_id`)
    state_types: tuple[str, ...] = ()  # OR: any of these state types (union with `state_type`)
    priorities: tuple[int, ...] = ()  # OR: any of these priorities (union with `priority`)
    is_open: bool | None = None  # True: open only; False: closed only; None: no filter
    no_cycle: bool = False  # cycle_id IS NULL
    archived_only: bool = False  # archived_at IS NOT NULL, never together with include_archived
    trashed_only: bool = False  # trashed = 1, never together with include_trashed
    reverse: bool = False  # flips `order` and the id tie-breaker, never the placement of the absent rows (D-044)

    def __post_init__(self) -> None:
        """1 <= limit <= 200 (D-011); 0 <= offset <= 500; order from the Literal; every value of
        `priorities` in 0-4. Out of range -> Validation(rule='LIMIT_RANGE' | 'INVALID_OFFSET' |
        'ORDER_INVALID' | 'PRIORITY_OUT_OF_RANGE'), the codes SPEC-040 §6 publishes. `archived_only`
        supersedes `include_archived` and `trashed_only` supersedes `include_trashed` (precedence,
        not an error); `cycle_id` together with `no_cycle` is refused by the edge that builds the
        object (`invalid_option`), because it is a contradiction between two flags and not a range."""


#: The SQL expression of each `order`, evaluated over `v_issue`. **Absence is `NULL`, never a
#: sentinel** (D-044): a sentinel is a value, so a descending direction would move it to the top,
#: while `NULL` cannot enter the key comparison and the placement of the absent rows is carried by
#: the `(<sort_key> IS NULL) ASC` term of the ORDER BY (§2.6.1).
SORT_KEYS: dict[str, str] = {
    "manual": "i.sort_order",
    "created": "i.created_at",
    "updated": "i.updated_at",
    "priority": "CASE i.priority WHEN 0 THEN NULL ELSE i.priority END",
    "due": "i.due_date",
}
```

**Every field of `ListFilters` has exactly one predicate, and the object is the whole filter.** No
edge — CLI, API or web — defines its own filter type or passes a filter as a kwarg (D-011, D-027).

| Field(s) | Predicate | Combination |
|---|---|---|
| `team_id` | `i.team_id = :team_id` | always present: the listing is per team |
| `state_id` + `state_ids` | `i.state_id IN (...)` | **OR**: the effective set is `set(state_ids) ∪ {state_id}`; empty set = no filter |
| `state_type` + `state_types` | `s.type IN (...)` | **OR**, same union |
| `priority` + `priorities` | `i.priority IN (...)` | **OR**, same union; `0` is `No priority`, a value in the column and not `NULL` in the filter — the ordering key of §2.6.1 is what maps it to `NULL` so that it stays last (D-044) |
| `assignee_id` | `i.assignee_id = :assignee_id` | **AND** with the rest; `unassigned=1` together with it selects nothing (no edge builds that pair) |
| `unassigned` | `i.assignee_id IS NULL` | |
| `label_ids` | one `issue_label` row per label, and **all** of them | **AND** (`= :label_count`), not OR — D-011 and SPEC-050 §2.5.3.1 |
| `project_id` | `i.project_id = :project_id` | |
| `cycle_id` / `no_cycle` | `=` / `IS NULL` | `no_cycle` is the only way to say "without a cycle": `cycle_id=None` means *no filter* |
| `parent_id` | `i.parent_id = :parent_id` | **direct children only**: the predicate does not recurse |
| `due_before` | `i.due_date <= :due_before` | |
| `q` | `i.title LIKE '%term%' ESCAPE '\'` | title filter, not search (D-025) |
| `include_archived` / `archived_only` | archived axis | `archived_only=1` ⇒ `archived_at IS NOT NULL`; elif `include_archived=1` **or `trashed_only=1`** ⇒ no predicate; else `archived_at IS NULL` |
| `include_trashed` / `trashed_only` | trashed axis | `trashed_only=1` ⇒ `trashed = 1`; elif `include_trashed=1` ⇒ no predicate; else `trashed = 0` |
| `is_open` | `i.is_open = :is_open` | `None` = no predicate; `True` = open only; `False` = closed only |
| `order` / `reverse` | `ORDER BY ...` | see the direction table below |
| `limit` / `cursor` / `offset` | paging | `cursor` is opaque and never combined with a non-zero `offset` |

Two rules complete the table and neither of them is optional:

1. **Across families the combination is AND.** "Families" are the rows above: state, state type,
   priority, assignee, label, project, cycle, parent, due, `q` and visibility. An issue satisfies the
   filter when it satisfies every family that is set. `ListFilters` never ORs two families.
2. **Repeated values inside one family are de-duplicated before the query.** `--label bug --label bug`
   is one label and the `= :label_count` comparison counts it once; the same for the three OR
   families. The adapter receives sets and expands them as `:state_0..:state_N` (named parameters,
   REQ-S002), never with value interpolation.

**The archive axis has one exception, and it is the trash.** `trashed_only=1` makes the archive
predicate vacuous (the row above): the trash is the deeper layer (D-043), so its listing spans both
`archived_at IS NULL` and `archived_at IS NOT NULL` and an archived-then-trashed issue is reachable from
it. No edge sets a second field to get there, and the CLI only offers the single-layer form: `--archived`
together with `--trashed` is exit 2 `invalid_option` (SPEC-050 §2.5.3.1). An object built with both
flags would AND the two predicates into the archived slice of the trash, which is the reading of its own
fields, not a third contract.

**The values are ids.** `ListFilters` carries `state_id`, not `"In Progress"`; `label_ids`, not
`"bug"`. Resolving a name, a key or a ULID is a read the caller performs first
(`resolve_filters` below, and SPEC-050 §2.5.3.1 for the CLI):

- when the resolution of a *filter* value finds nothing, the **web** drops the filter and renders the
  unfiltered view (D-027) — a stale bookmark is not an error;
- the **CLI** does not drop it: an unknown `--state`, `--label`, `--project`, `--cycle`,
  `--assignee` or `--parent` is `NotFound`, exit 3 (SPEC-050 §2.5.3.1). The difference lives in the
  edge because it is a product decision about a stale link, not a property of the query.

### 2.6.1 Order and direction

`SORT_KEYS` maps each `order` to the SQL expression of its key. **The values of all five sort ASC**
and `reverse` is the only way to invert them — the placement of the rows without a value is not a
direction (D-044). The clause is built as

```
ORDER BY (<sort_key> IS NULL) ASC, <sort_key> {dir}, i.id {dir}
     with dir = "DESC" if filters.reverse else "ASC"
```

`dir` is the SQL keyword and it is the **only** place the direction is uppercase: the cursor token of
§2.6.2 carries the same direction as `desc`/`asc`, because there it is a value and not a clause.

The order is **total** — `i.id` closes every tie — and it has two parts with different rules:

1. **`(<sort_key> IS NULL) ASC` is the absence flag**: `0` for a row with a value, `1` for a row
   without one. It always ascends, so the valued rows come first and the absent rows **last in `ASC`
   and in `DESC` alike** (D-044). Inverting a direction inverts the values; it does not change the
   treatment of the absence.
2. **`<sort_key> {dir}, i.id {dir}` is the values**: `dir` inverts the key and the tie-breaker
   together, never the flag of point 1. A tie-breaker that does not move with the key loses or repeats
   rows between pages.

The key expressions express absence as **`NULL`**, never as a sentinel (D-044): a sentinel is a value,
so `DESC` would send it to the top, while `NULL` cannot enter the key comparison at all. `sort_order`,
`created_at` and `updated_at` are `NOT NULL` columns (SPEC-010), so their flag is `0` for every row and
their clause degenerates to the plain `ORDER BY <sort_key> {dir}, i.id {dir}`; `priority` and `due` are
the two orders with an absent case.

| `order` | `<sort_key>` | ASC (default) puts first | …and last | `reverse` |
|---|---|---|---|---|
| `manual` | `i.sort_order` | the top of the backlog (lowest `sort_order`) | the bottom | bottom-up |
| `created` | `i.created_at` | the oldest issue | the newest | newest first |
| `updated` | `i.updated_at` | the least recently touched | the most recent | most recent first |
| `priority` | `CASE i.priority WHEN 0 THEN NULL ELSE i.priority END` | `Urgent` (1), then 2, 3, 4 | `No priority` (0) | 4, 3, 2, 1, then `No priority` last |
| `due` | `i.due_date` | the earliest `due_date` | the undated | the latest `due_date` first, the undated last |

Within the absent rows the order is `i.id {dir}`: the second part stays total and stable even for rows
that share the absence, so a page boundary inside that section is still decided by one unique column.

**The keyset predicate at the `NULL` edge.** The cursor names its ordering, not its position (§2.6.2),
so `list_view` resolves the anchor's key with one primary-key lookup and the result **may be `NULL`**:

```sql
SELECT <SORT_KEYS[order]> FROM v_issue WHERE id = :last_id AND team_id = :team_id;
-- no row -> Validation(rule='INVALID_CURSOR')
```

`(<sort_key> > :cursor_key)` with `:cursor_key = NULL` evaluates to `NULL`, and `WHERE NULL` matches no
row: the listing would end silently at the first row without a value. The clause of the next page is
therefore **one of two forms**, chosen by whether that lookup returned a value. Both are appended to
the filters of §2.6, both use the same `<sort_key>` expression as their `ORDER BY` (character for
character, not the cursor's `sort_key` id and not the row's value), and both are paired with the
`ORDER BY` above.

**Form A — the anchor has a value** (`cursor_key IS NOT NULL`). With `dir = ASC`:

```sql
-- form A, dir = ASC: append this clause to the filters of §2.6, then the ORDER BY below.
AND (  (<sort_key> IS NULL)                              -- 1. every absent row follows a valued one
    OR (<sort_key> > :cursor_key)                        -- 2. valued and after, by key
    OR (<sort_key> = :cursor_key AND i.id > :cursor_id)  -- 3. tied key, after by id
  )
ORDER BY (<sort_key> IS NULL) ASC, <sort_key> ASC, i.id ASC
LIMIT :limit_plus_one;
```

With `dir = DESC`, only the two comparison operators of branches 2 and 3 flip — `>` becomes `<` on the
key **and** on `id`, and branch 1 does not change:

```sql
-- form A, dir = DESC
AND (  (<sort_key> IS NULL)
    OR (<sort_key> < :cursor_key)
    OR (<sort_key> = :cursor_key AND i.id < :cursor_id)
  )
ORDER BY (<sort_key> IS NULL) ASC, <sort_key> DESC, i.id DESC
LIMIT :limit_plus_one;
```

**Form B — the anchor has no value** (`cursor_key IS NULL`). Only the rows that are **also without a
value** and follow the anchor by `id` qualify; the valued rows are before it, and `key` is `NULL` on
both sides so it is not compared at all:

```sql
-- form B, dir = ASC: append this clause to the filters of §2.6, then the ORDER BY below.
AND (<sort_key> IS NULL AND i.id > :cursor_id)
ORDER BY (<sort_key> IS NULL) ASC, <sort_key> ASC, i.id ASC
LIMIT :limit_plus_one;
```

```sql
-- form B, dir = DESC: the same clause with the other operator (never both at once).
AND (<sort_key> IS NULL AND i.id < :cursor_id)
ORDER BY (<sort_key> IS NULL) ASC, <sort_key> DESC, i.id DESC
LIMIT :limit_plus_one;
```

`:cursor_key` is bound to the value the anchor lookup returned — the `YYYY-MM-DD` text for `due`
(SPEC-010, D-009: fixed width, so its lexicographic order is the calendar order), the `1`-`4` integer
for `priority`. `:cursor_id` is the 26-character ULID of the anchor. Form B is never reached for
`manual`, `created` or `updated`, whose keys are `NOT NULL`.

**Why the predicate is the exact complement of the order.** Step by step:

1. The `ORDER BY` gives every row one tuple `(n, k, id)` with `n = (<sort_key> IS NULL) ∈ {0, 1}`,
   compared as `n ASC`, then `k {dir}`, then `id {dir}`. `i.id` is the primary key, so the tuples are
   distinct and the order is **total**: each row has exactly one position, and "before", "at" and
   "after the anchor" partition the result set with no overlap.
2. "After the anchor" is therefore exactly: `n > cursor_n`, or `n = cursor_n` and `k` after
   `cursor_k` in `dir`, or `n = cursor_n`, `k = cursor_k` and `id` after `cursor_id` in `dir`.
3. **Form A** (`cursor_n = 0`): branch 1 is `n > 0` — every absent row, since `n` has no value above
   `1`; branch 2 is `n = 0` and `k` after; branch 3 is `n = 0`, `k` equal and `id` after. The three
   are disjoint (they differ in `n`, or in the comparison of `k`, or in the comparison of `id`) and
   together they are the whole right-hand side of step 2.
4. **Form B** (`cursor_n = 1`, `cursor_k = NULL`): `n > 1` matches nothing, so only `n = 1` survives —
   the rows that are also without a value — and inside them the anchor is passed by `id {dir}` alone.
   Branch 2 of form A has no counterpart here because `k` is `NULL` on both sides, which is why `k`
   does not appear in the predicate.
5. Hence each page is the set of rows **strictly after** the anchor, and nothing else: no row of the
   previous pages can match (all of them are at or before the anchor), and no row of the later pages
   can be missing (every row after the anchor matches one branch). Rows are neither repeated nor
   skipped, including at the boundary between the valued and the absent section — the one place where
   the two parts of the order meet, and where a predicate that compared `k` against `NULL` would
   return nothing and end the listing early.

Worked check, `order=due`, `A < B < C < D` as ULIDs:

| `id` | `due_date` |
|---|---|
| `A` | `2026-11-05` |
| `B` | `2026-11-10` |
| `C` | `NULL` |
| `D` | `NULL` |

- `--sort due --limit 2`: page 1 is `A, B`; its cursor anchors on `B` (valued). Page 2 takes form A
  and branch 1 matches `C` and `D` → `C, D`.
- `--sort due --limit 1`: page 2 is `C`; its cursor anchors on an absent row, so page 3 takes form B
  → `NULL AND i.id > 'C'` → `D`.
- `--sort due --reverse --limit 2`: the order is `B, A, D, C` — the values descend and the absent rows
  stay last, ordered by `i.id DESC` among themselves. Page 1 is `B, A`, page 2 (form A, branch 1)
  is `D, C`.

### 2.6.2 Cursor codec

**The canonical token is `<order>:<asc|desc>|<ULID>`, with the direction in lowercase** — `manual:asc`,
`due:desc` — and it is written once, here: `list_view` encodes with the same expression that
`list_issues` validates against (SPEC-040 §5.3 step 4), so a token this layer emits is a token the
application layer accepts. Uppercase belongs to the SQL keyword of §2.6.1 and to nothing else.

```python
def encode_cursor(sort_key: str, issue_id: str) -> str:
    """base64url(f'{sort_key}|{issue_id}') in the URL-safe alphabet, **without** padding."""


def decode_cursor(cursor: str) -> tuple[str, str]:
    """Inverse of encode_cursor. `sort_key` is the canonical ordering id `f'{order}:{dir}'` with
    `dir` in lowercase (`manual:asc`, `priority:asc`, `created:asc`, … `due:desc`), not the SQL
    expression and not the row's value; `issue_id` is the 26-character ULID of the last row of
    the page. Undecodable base64url, a missing or extra '|', an empty component, padding, an
    `issue_id` that is not a 26-character ULID, or a `sort_key` outside `<order>:<asc|desc>`
    with `order` in SORT_KEYS -> Validation(rule='INVALID_CURSOR')."""
```

The codec is the only place that builds or reads a cursor; `app/` transports the string and the edges
never look inside it (D-011, SPEC-040 §2.2). Consequences that the adapter owes the caller:

- **The cursor names its ordering, not its position.** Because `sort_key` is `order:direction`, a
  cursor presented under another `--sort` **or another direction** is detectable and is
  `INVALID_CURSOR`; with the row's value in that slot the check would be impossible for
  `manual`↔`priority`, whose values are both numbers.
- **The anchor value is read by primary key.** The payload has no room for the sort value (D-011 fixes
  two components), so `list_view` resolves it once per page:
  `SELECT <SORT_KEYS[order]> FROM v_issue WHERE id = :last_id AND team_id = :team_id`. No row ⇒
  `Validation(rule='INVALID_CURSOR')` (the cursor does not point into this team). One indexed lookup,
  no scan and no `OFFSET`.
- **`limit` is not in the payload.** The page size may change between pages; only the anchor travels.
- **The cursor is positional, not a snapshot.** An anchor that no longer satisfies the filters is not
  an error: the keyset seeks from its value and the page continues. A row inserted before the anchor,
  or a row that left the result set, is skipped — that is the guarantee of a keyset, and the reason
  the tie-breaker is `i.id`.

```python
@dataclass(frozen=True, slots=True)
class IssueListItem:
    id: str
    identifier: str
    title: str
    priority: int
    priority_label: str
    state_id: str
    state_name: str
    state_type: str
    team_key: str
    assignee_id: str | None
    assignee_name: str | None
    project_id: str | None
    cycle_id: str | None
    due_date: date | None
    estimate: float | None
    parent_id: str | None
    updated_at: datetime
    is_open: bool
    labels: tuple[LabelRef, ...]
    # --- additive since spec_revision 4, all with a default (D-026) ---
    state_color: str = "#6B6F76"  # workflow_state.color, JOIN `s` on i.state_id
    sort_order: float = 0.0  # issue.sort_order: backlog order (order="manual")
    board_order: float = 0.0  # issue.board_order: board column order
    created_at: datetime | None = None  # issue.created_at
    # --- additive since spec_revision 18, defaulted (HIFA-49, D-072 §3) ---
    milestone_id: str | None = None  # issue.milestone_id, from the column itself
    milestone_name: str | None = None  # project_milestone.name, JOIN `pm` on i.milestone_id


@dataclass(frozen=True, slots=True)
class IssuePage:
    items: tuple[IssueListItem, ...]
    next_cursor: str | None  # None if has_more is False
    has_more: bool


@dataclass(frozen=True, slots=True)
class BoardGroup:
    key: str | None  # state_id | member_id | str(priority) | project_id | None (unassigned)
    label: str  # "In Progress" | "Unassigned" | "Urgent" | "No project"
    state_type: str | None
    position: float  # group order (state.position, priority.rank, name)
    total: int
    issues: tuple[IssueListItem, ...]
    # --- additive since spec_revision 4, all with a default (D-026) ---
    color: str | None = None  # workflow_state.color when group_by="state", else None
    truncated: bool = False  # len(issues) > limit_per_group: the query asked for limit + 1


@dataclass(frozen=True, slots=True)
class Board:
    team_id: str
    group_by: str  # "state" | "assignee" | "priority" | "project"
    groups: tuple[BoardGroup, ...]


@dataclass(frozen=True, slots=True)
class IssueDetail:
    issue: IssueListItem
    description: str | None
    team: Team
    state: WorkflowState
    assignee: Member | None
    creator: Member | None
    project: Project | None
    cycle: Cycle | None
    parent: IssueListItem | None
    children: tuple[IssueListItem, ...]
    relations: tuple[tuple[IssueRelation, IssueListItem], ...]
    comments: tuple[Comment, ...]
    history: tuple[IssueHistory, ...]
    attachments: tuple[Attachment, ...]
    #: additive since spec_revision 5, defaulted (D-026): member.id → display_name for the authors
    #: of the loaded comments and history, so the edge renders authors without an N+1 query.
    members: Mapping[str, str] = field(default_factory=dict)
    #: additive since spec_revision 21, defaulted (D-074): issue.id → identifier for the rows of
    #: `history` whose `field` is `parent_id`, the one history kind neither `members` nor `states`
    #: already resolves. Empty when the page's history names no parent, which is what makes SQL-7 row
    #: 17 conditional: the map is one statement for the kind and never one per row (§2.4's bulk rule).
    history_parents: Mapping[str, str] = field(default_factory=dict)
    #: additive since spec_revision 11, defaulted (D-026): the layer the row sits in and the columns a
    #: render offers, so that one read of one issue serves the whole page (HIFA-55). `archived_at` and
    #: `trashed` come from the `issue` row this read already loads (SQL-7 row 2) and cost no statement.
    #: `trashed` is `False` on **every default read**: SQL-7 row 1 filters `i.trashed = 0`, so a trashed
    #: issue answers `None` instead (the pinned answer of `test_a_trashed_issue_has_no_detail`). It is
    #: `True` only on a detail asked for with `include_trashed=True`, the one keyword that drops that
    #: predicate and nothing else (§2.6, D-059). What carries it is `issue_detail` and, above it, the
    #: detail page and the panel of a trashed issue (SPEC-070 §2.1.1); what does not is every listing,
    #: board and count, and the row and card fragments, which keep the default — so a trashed row is
    #: still unreachable through them (SPEC-070 §4 R29).
    #: `states` is the team's **non-archived** workflow states ordered by `position, name` — the set
    #: and the order of SQL-7 row 16, which is the statement `TeamRepo.states` runs (§2.4).
    archived_at: datetime | None = None  # issue.archived_at: the archive layer
    trashed: bool = (
        False  # issue.trashed: False on every default read, True only with include_trashed
    )
    states: tuple[WorkflowState, ...] = ()  # the team's non-archived states, by position then name
    #: additive since spec_revision 23, defaulted (D-026): when the reference the caller typed was a
    #: key the team has left behind, the identifier that key stood for, spelled as the rotation stored
    #: it (`ENG-3`); `None` when the reference named the issue directly. It is what a render says about
    #: the rename and never the issue's own name, which is `issue.identifier`. This module never sets
    #: it — the application layer's `issue_detail` fills it when it resolves a previous key — and no
    #: statement of SQL-7 carries it.
    previous_identifier: str | None = None


@dataclass(frozen=True, slots=True)
class AgendaEntry:
    id: str
    identifier: str
    title: str
    due_date: date
    priority: int
    priority_label: str
    state_type: str
    team_key: str


def board_view(
    conn: sqlite3.Connection,
    *,
    team_id: str,
    filters: ListFilters | None = None,
    group_by: Literal["state", "assignee", "priority", "project"] = "state",
    limit_per_group: int = 50,
) -> Board:
    """One query per group (SQL-5). `filters` carries the same filters as the listing
    (`state_id`, `assignee_id`, `q`, ...) minus `order`/`cursor`/`offset`, which the board
    does not use: the column order is `board_order`, not the listing's sort key (D-011, D-027)."""


def list_view(conn: sqlite3.Connection, filters: ListFilters) -> IssuePage:
    """One query (SQL-4) plus, when `filters.cursor` is set, one primary-key lookup of the anchor's
    sort value (§2.6.2). It asks for `limit + 1` rows, trims to `limit`, reports
    `has_more = len(rows) > limit` and sets `next_cursor` (the last kept row's id, D-011) or `None`.
    A cursor whose anchor does not exist in `filters.team_id` -> Validation(rule='INVALID_CURSOR')."""


def blocked_view(
    conn: sqlite3.Connection, *, team_id: str | None = None
) -> tuple[IssueListItem, ...]:
    """One query (SQL-10): the live issues that are the target of a `blocks` edge whose source is not
    finished (SPEC-020 §4.1 R7, D-046). `team_id=None` is the workspace-wide form, not "no team".
    Ordered by `identifier` ASC. The reference is resolved by the caller (`team_by_ref`), so a
    `team_id` that matches no team is not a verdict of this layer."""


def issue_detail(
    conn: sqlite3.Connection,
    ref: str,
    *,
    include: frozenset[str] = frozenset(),
    include_trashed: bool = False,
) -> IssueDetail | None:
    """ref = ULID or identifier, the identifier compared with the caller's spelling folded
    (`upper(:ref)`; the column is stored normalised, so a collation on it would only cost the
    index — SQL-7 row 1). None if it does not exist — and, on a default read, if it is trashed.
    `include` gates the heavy collections (`comments`, `history`, `attachments`): empty means
    base only, which is what the row/card fragments ask for; the panel asks for the comments too
    (D-027, D-029). Any other name is `Validation(field="include", rule="INCLUDE_INVALID")`.
    `children`, `relations` and `parent` are neither gated nor ordered here: rows the trash layer
    holds are absent from all three — `parent` answers `None` (SQL-7 rows 7, 8 and 9, D-072 §2 as
    amended) — and the order `relations` is published in is the caller's, because it is a function
    of the asking end and of the far end's identifier
    (SPEC-040 §2.2, D-072 §1) — this function returns the set, unordered.
    `states` always carries the team's **non-archived** workflow states, ordered by
    `position, name` — the set and the order `TeamRepo.states` returns (§2.4), the same statement
    as SQL-7 row 16 — so an edge renders the state `<select>` from this model instead of reading
    the database again (SPEC-040 R21). `include_trashed` is the one declared way to read an issue the
    trash layer holds (D-059): `False` keeps SQL-7 row 1's `i.trashed = 0`, so a trashed reference
    answers `None` and `trashed` is `False`; `True` drops that single predicate and nothing else —
    the same statements in the same order, so the totals of §2.5 do not move — and it is the only way
    a returned detail carries `trashed = True`. Its consumer is the detail page and the panel of a
    trashed issue (SPEC-070 §2.1.1, §4 R29 keeps the row and card fragments on the default)."""


def team_by_ref(conn: sqlite3.Connection, ref: str) -> Team | None:
    """Team by `key` (case-insensitive) or ULID; None if it does not exist."""


def issue_id_by_ref(conn: sqlite3.Connection, ref: str) -> str | None:
    """`issue.id` of the issue a ULID or a current identifier names; None when neither does.
    The identifier branch folds the caller's spelling (`upper(:ref)`) — the column is stored
    normalised — and each branch is its own index seek, so this is SQL-7 row 1's predicate
    projected to `id`: the statement a caller reads when it has to know which form it was
    handed before it asks for a detail. Visibility is the caller's, as with the other lookups."""


def team_by_previous_key(conn: sqlite3.Connection, key: str) -> tuple[Team, str] | None:
    """The team that left `key` behind, with the key as `team_previous_key` stored it; None when no
    rotation carries it. `team_previous_key` is keyed `(team_id, key)`, so two teams may have left the
    same key: the row with the greatest `changed_at` answers, an exact tie broken by the greater
    `team_id`, which makes the order total. The stored spelling travels beside the team because a
    caller renders the rotation's own key, never the one it typed. The key is folded in the
    parameter (`upper(:key)`) — the rotation stores it normalised — so `idx_team_previous_key_key`
    answers the statement and the table is not scanned."""


def issue_id_by_number(conn: sqlite3.Connection, *, team_id: str, number: int) -> str | None:
    """`issue.id` of the issue numbered `number` inside one team; None when the team has none.
    Visibility is the caller's: `issue_detail` applies its own trash predicate to the id this answers."""


def list_teams(conn: sqlite3.Connection) -> tuple[Team, ...]:
    """All teams, ordered by `key`: the team selector of the dashboard (SPEC-070 §2.3)."""


def team_counts(conn: sqlite3.Connection, team_id: str) -> dict[str, int]:
    """`{"open", "urgent", "total"}` of one team, one aggregate query. `open` = not archived,
    not trashed and `state.type` not in (completed, canceled); `urgent` = open with
    `priority` 1 (urgent) — the counters of SPEC-070 §2.1.3, not a new business rule."""


def local_member(conn: sqlite3.Connection) -> tuple[str | None, str | None]:
    """`(display_name, initials)` of the local viewer: the first active member by `created_at`,
    or `(None, None)` with no member. Read-only identity for the dashboard chrome, not auth."""


def resolve_filters(
    conn: sqlite3.Connection,
    team_id: str,
    *,
    state: str | None = None,
    assignee: str | None = None,
    label: str | None = None,
) -> dict[str, str | None]:
    """`state`/`assignee`/`label` given as name, key or ULID to `{"state_id", "assignee_id",
    "label_id"}`. A value that matches nothing is resolved to None (filter dropped), never an
    error: a stale bookmark must render an unfiltered view, not a 422 (D-027)."""


def agenda(
    conn: sqlite3.Connection,
    *,
    team_id: str | None,
    start: date,
    days: int,
    include_overdue: bool = True,
) -> list[AgendaEntry]: ...
```

The read models are **flat** `dataclass(frozen=True, slots=True)`: no logic, no computed
properties and no references to `sqlite3`. `reads.py` receives a read-only connection (the one from
`UnitOfWork.read()` or a `Database.ro_connection()`), never a write `UnitOfWork`.

**`q` is a title filter, not search (D-025).** `ListFilters.q` filters **titles only**, with
`i.title LIKE ? ESCAPE '\'` and the term wrapped as `%term%`. The `%`, `_` and `\` characters *inside*
the user's term are escaped before wrapping, so a literal `%` searches for a percent sign and does not
become a wildcard; the comparison is case-insensitive for ASCII (`LIKE` semantics). A `q` that is
empty or only whitespace is dropped (no filter), never an error. Descriptions, comments, identifiers
and the `field:value` grammar are **workspace search**, owned by SPEC-090 / `search_issues`: the
dashboard's `/search` and `/_/palette` (SPEC-070 §2.1.4) route there, the board's filter box does not.

**The fields marked "additive since `spec_revision` 4" are append-only (D-026).** They are declared
here because the edges already consume them, they always carry a default, and they are added at the
end of the dataclass: a reader that does not know them keeps working, and a regeneration that omits
them leaves the templates with a neutral colour (`#6B6F76`) instead of failing. None of them changes
the behaviour of `list_view`, `board_view` or `issue_detail`.

The eight fields added in `spec_revision` 6 (`state_ids`, `state_types`, `priorities`, `is_open`,
`no_cycle`, `archived_only`, `trashed_only`, `reverse`) follow the same rule and exist because the
**listing surface needs them**: without them a caller cannot express "any of these three states",
"open only", "without a cycle", "only the archived" or "descending", and the edge would have to invent
a second filter object — which D-011 forbids. Every one of them defaults to the behaviour the object
already had, so `ListFilters(team_id=...)` keeps meaning "visible open and closed issues, manual
order".

The **board does not paginate by cursor**: `limit_per_group` caps each column, `total` carries the
real count and `truncated=True` means "this column has more". That is why `BoardGroup` never exposes a
`next_cursor`: the keyset of D-011 is a listing contract, and per-column infinite scroll is still an
open question in SPEC-070's Notes.

### 2.7 `ports.py` — `Clock` and `IdGen` (D-017)

`src/hifa/ports.py` belongs to this spec: it is the "Ports" layer of SPEC-000 §3 and the **only**
definition of the two services that the domain cannot have inside.

```python
# src/hifa/ports.py
from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Current instant, **aware** and in **UTC** (tzinfo == timezone.utc).
        Never naive, never the process's local time."""


@runtime_checkable
class IdGen(Protocol):
    def new(self) -> str:
        """26-character ULID (Crockford base32), time-orderable and unique.
        The adapter does not reuse nor reset the generator between calls."""
```

| Symbol | Module | Signature |
|---|---|---|
| `Clock` | `hifa.ports` | `Protocol` with `now() -> datetime` |
| `IdGen` | `hifa.ports` | `Protocol` with `new() -> str` |
| `SystemClock` | `hifa.adapters.clock` | `now(self) -> datetime` (UTC aware); accepts a fixed `datetime \| None` in the constructor for tests |
| `UlidGen` | `hifa.adapters.idgen` | `new(self) -> str`; monotonic within the process |

Port rules:

- `ports.py` imports **only stdlib**: zero `hifa.*`, zero `pydantic`. That way `domain/` can name them
  in prose without importing them (SPEC-020 §1) and there is no cycle.
- The Protocols of **repos** and of `UnitOfWork` do not live here: the contracts are the concrete classes of
  §2.2/§2.4 (`uow.py`, `*_repo.py`), which is what `app/` and the edges type against (SPEC-050 §2.1 allows
  importing `hifa.adapters.sqlite.uow`). A mirror Protocol of seven repos would duplicate 60 signatures and
  would become desynchronized at the first change.
- `SystemClock` and `UlidGen` are the **only** production implementations; the tests use local
  `FrozenClock`/`SeqIdGen`. No module of `app/`, `cli/`, `api/` nor `web/` instantiates a
  `Clock` or an `IdGen`: they receive them already built and read them from `uow.clock` / `uow.idgen`.

## 3. Data it touches

All the tables of `SPEC-010` §3. This table declares **which columns each operation writes**; the
columns that do not appear are never touched from the adapter.

| Table | Operation | Columns written | Invariant |
|---|---|---|---|
| `workspace` | INSERT (bootstrap) | `id, name, url_key, created_at` | `url_key` UNIQUE |
| `team` | INSERT/UPDATE | all except `id, workspace_id, created_at` | `UNIQUE(workspace_id, key)`; `key` already normalized |
| `workflow_state` | INSERT/UPDATE | `name, type, color, position, is_default, archived_at` | `UNIQUE(team_id, name)`; `type` from the CHECK |
| `label` | INSERT/UPDATE | `name, color, description, parent_id, is_group, group_type, retired_at` | `UNIQUE(workspace_id, team_id, name)` |
| `member` | INSERT/UPDATE | `name, display_name, email, initials, avatar_url, timezone, is_admin, is_active` | `email` UNIQUE |
| `project` | INSERT/UPDATE | all except `id, workspace_id, created_at` | `priority` 0-4; `status_type` from the CHECK |
| `project_milestone` | INSERT/UPDATE | `name, status, target_date, sort_order` | `UNIQUE(project_id, name)` |
| `cycle` | INSERT/UPDATE | `number, name, starts_at, ends_at, completed_at` | `UNIQUE(team_id, number)`; `ends_at > starts_at` |
| `issue` | INSERT/UPDATE | 31 columns (§2.5 SQL-2/SQL-3) | `UNIQUE(team_id, number)`, `UNIQUE(identifier)`, composite FK `(team_id, state_id)` |
| `issue_label` | INSERT OR IGNORE / DELETE | `issue_id, label_id` | composite PK; duplicate is not an error |
| `issue_relation` | INSERT / DELETE | `id, issue_id, related_issue_id, type, created_at` | `UNIQUE(issue_id, related_issue_id, type)`; `issue_id <> related_issue_id` |
| `comment` | INSERT/UPDATE | `body, resolved_at, edited_at, updated_at` | `body` not empty (CHECK) |
| `attachment` | INSERT | `id, issue_id, title, subtitle, url, source_type, metadata, created_at` | `url` NOT NULL |
| `issue_history` | INSERT (batch) | `issue_id, actor_id, field, old_value, new_value, created_at` | `id` AUTOINCREMENT, INSERT only — the **one** exception is migration `004`, which nulls the `new_value` of a `created` row (SPEC-010 §3.7) |
| `issue_fts` | maintained by triggers | — | the adapter does **not** write FTS directly (SPEC-090) |
| `v_issue`, `v_priority`, `v_team_progress` | SELECT | — | views are never written |

No `DELETE` is ever executed on `issue`, `team`, `project`, `cycle`, `comment` nor `label`. Deletion is
logical: `trashed = 1` / `archived_at = <ts>` / `retired_at = <ts>` (rule R11).

## 4. Business rules

### 4.1 Invariants (R)

- **R1 — PRAGMAs per connection.** Every `connect()` applies the 6 PRAGMAs of §2.1. `foreign_keys` and
  `busy_timeout` are **per connection** and are not inherited: a connection without them is a bug, not an
  optimization. `open_probe()` is **not** a `connect()` and applies **none** of them: it asks for
  `mode=ro`, reads `PROBE_SQL` once and partitions that one row set into `tables`/`objects`, nothing
  else (D-068, D-069).
- **R2 — A single writer.** Every write happens inside `BEGIN IMMEDIATE`. Never `BEGIN
  DEFERRED` to write, never Python's implicit `isolation_level`, never autocommit.
- **R3 — One transaction per use case.** The adapter opens and closes; it does not commit per statement.
  One `write()` = one transaction = N atomic statements.
- **R4 — The retry lives outside the `@contextmanager` (D-018).** `write()` does **not** retry: it opens
  `BEGIN IMMEDIATE`, hands over the repos, does `COMMIT` and, on any exception, `ROLLBACK` +
  re-raise. A `SQLITE_BUSY`/`database is locked` is translated to `Conflict(code='DB_BUSY')` and
  propagated immediately, on the first attempt. The retry of the **complete transaction** —rollback,
  new `BEGIN IMMEDIATE` and re-executed body— is done by the use case with
  `app/retry.py::with_write_retry(fn, attempts=3)` (SPEC-040 §2.2): it is the only one that can re-execute
  the body of the `with`, because from a generator that is impossible. Waits of 50/100/200 ms; once the
  attempts are exhausted the `Conflict(code='DB_BUSY')` is propagated. A commit that fails is **never** retried
  without a previous rollback.
- **R5 — No N+1.** `list_view` is 1 query; `board_view` is 1 query; `blocked_view` is 1 query;
  `agenda` is 1. `issue_detail` is **12** statements with `include=DETAIL_INCLUDE` and **8** with an
  empty `include` (SQL-7, §2.5), independent of the number of children, labels, comments and history —
  its relations are the one count that follows a collection, at one statement each (SQL-7 row 9).
  Executing SQL inside a loop over rows is forbidden anywhere else.
- **R6 — Reading does not write.** The repos obtained from `read()` raise `Conflict(code='READ_ONLY_UOW')`
  in any write method. `reads.py` only receives read-only connections.
- **R7 — Bound parameters.** Zero value interpolation in SQL (REQ-S002). The only
  interpolation allowed is that of *placeholders* (repeated `?`) and table/column names taken
  from an internal allowlist.
- **R8 — Fixed type mapping.** `INTEGER 0/1` ⇄ `bool`; `TEXT ISO-8601 Z` ⇄ aware UTC `datetime`;
  `TEXT YYYY-MM-DD` ⇄ `date`; `REAL` ⇄ `float`; `NULL` ⇄ `None`. A naive `datetime` on insert is
  `Validation(rule='NAIVE_DATETIME')`: UTC is assumed only if the value declares it.
- **R9 — Forward-only migrations with backup.** They are applied in numeric order, one file per
  transaction, `user_version` inside the same transaction, backup verified before the first
  `BEGIN`.
- **R10 — Zero DDL at runtime.** Outside `migrate.py`, no module executes `CREATE`, `ALTER`,
  `DROP` nor a writing `PRAGMA`.
- **R11 — Nothing is physically deleted.** `DELETE` only on relation tables (`issue_label`,
  `issue_relation`) and on `issue_history` when an issue is deleted with engine-level
  `ON DELETE CASCADE`. Business entities are archived or sent to the trash.
- **R12 — No global state.** There is no module-global connection, no pool, no cache. Two `UnitOfWork`s
  over the same path are independent and coordinate only through SQLite.
- **R13 — Exception boundary.** No `sqlite3.Error` crosses toward `app/`: `connection.py`
  exposes `translate_sqlite_error()` and the repos use it in every operation (§6).
- **R14 — Connection per unit of work.** Each `UnitOfWork` opens a connection and closes it in
  `close()` (or in `__del__` as a safety net, with a warning). The CLI opens one per command.

### 4.2 Functional requirements (REQ-F)

| ID | Requirement |
|---|---|
| **REQ-F030-01** | `connect()` applies the 6 PRAGMAs on every connection and sets `row_factory=sqlite3.Row`, `isolation_level=None` |
| **REQ-F030-02** | `connect(readonly=True)` opens with `mode=ro`; any `INSERT`/`UPDATE` fails with `Conflict(code='DB_READONLY')` |
| **REQ-F030-03** | `write()` emits `BEGIN IMMEDIATE`; normal exit does `COMMIT`; an exception does `ROLLBACK` and re-raises the same instance |
| **REQ-F030-04** | `read()` takes no writer lock and sees a stable snapshot; a write inside `read()` raises `Conflict(code='READ_ONLY_UOW')` |
| **REQ-F030-05** | The 7 repos are built with the `UnitOfWork` connection; there is no `sqlite3.connect` outside `connection.py` and `migrate.py` |
| **REQ-F030-06** | `write()` does not retry: on `SQLITE_BUSY` it does `ROLLBACK` and raises `Conflict(code='DB_BUSY')`. The retry of the complete transaction (up to 3 attempts, 50/100/200 ms) belongs to `app/retry.py::with_write_retry` (SPEC-040 §2.2), which re-executes the body of the `with` |
| **REQ-F030-07** | `next_number` returns the team's `MAX(number)+1` and 10 concurrent creations produce 10 distinct numbers |
| **REQ-F030-08** | `insert`/`update` use `RETURNING *` and return the final row (with DDL defaults applied) |
| **REQ-F030-09** | `list_view` obtains the labels with `json_group_array` in a single query; the number of statements does not depend on the number of issues |
| **REQ-F030-10** | `ListFilters` combines its families with AND, resolves the repeated values of the `state`/`state_type`/`priority` families as **OR** and `label_ids` as **AND** (§2.6), respects `limit` (default 50, max 200, D-011) and `offset`, orders by the `SORT_KEYS` expression of `order` with `i.id` as the tie-breaker in the same direction, and returns `next_cursor`/`has_more` without a `COUNT(*)` |
| **REQ-F030-11** | `board_view` groups by `state`, `assignee`, `priority` or `project`, orders the groups by `state.position`/`priority.rank`/name, and respects `limit_per_group` reporting `total` |
| **REQ-F030-12** | `issue_detail` executes a fixed number of statements — **12** with `include=DETAIL_INCLUDE`, **13** when the page's history carries a `parent_id` row (SQL-7 row 17), **8** with an empty `include` — regardless of how many children, labels, comments or history the issue has; a project, a cycle, a parent, an assignee and a creator add one each when the row carries the id they look up, each relation adds one, and row 17 adds one for the *kind* `parent_id` and never one per row (SQL-7, §2.5) |
| **REQ-F030-13** | `agenda` returns the issues with `due_date` in `[start, start+days-1]` sorted by date and priority, and includes overdue open ones if `include_overdue=True` |
| **REQ-F030-14** | `migrate` applies the pending `00X_*.sql` in order, leaves `user_version` at the last version and is idempotent (a second run applies nothing) |
| **REQ-F030-15** | `migrate` creates a verifiable `<stem>-<ts>.db.bak` before applying and aborts without touching the database if the backup fails |
| **REQ-F030-16** | Each migration file is applied in its own transaction together with its `PRAGMA user_version`; a file that fails leaves the previous version intact |
| **REQ-F030-17** | Constraint violations are translated to domain errors according to the table of §6, without leaking SQLite's raw message to the user |
| **REQ-F030-18** | The row→entity mapping converts `0/1` to `bool`, ISO-8601 with `Z` to aware `datetime`, `YYYY-MM-DD` to `date` and `NULL` to `None` |
| **REQ-F030-19** | All adapter SQL uses placeholders; a static test finds no f-strings nor `%` nor `+` inside an SQL string |
| **REQ-F030-20** | `HistoryRepo.record` inserts all the changes of a use case with a single `executemany` |
| **REQ-F030-21** | No adapter operation executes `DELETE FROM issue`; `trash`/`archive` are UPDATEs |
| **REQ-F030-22** | The repos return validated domain entities (`Issue`, `Team`, …) and `None`, never `sqlite3.Row` nor tuples |
| **REQ-F030-23** | `require_fts5(conn)` answers from `FTS5_ENABLED_SQL` alone — one `SELECT` over `pragma_compile_options` — raises `Fts5Unavailable(code='fts5_unavailable')` when the module is absent, is called by `migrate()` before the first `BEGIN` and by `hifa db reindex` on the probe's connection immediately after `open_probe()` (SPEC-100 R21 step (b)), is **not** called by `connect()` nor by `reindex(conn)`'s body (§2.1, D-065, D-068) |
| **REQ-F030-24** | `open_probe(path)` opens with the URI `file:<path>?mode=ro` and `uri=True`, applies **no** pragma of `PRAGMAS`, runs exactly **one** statement (`PROBE_SQL`) and returns `Probe(conn, tables, objects)` — two projections of that one row set, `tables` keeping `type = 'table'` and `objects` keeping `type IN ('table','view')`, so a **view** named `workspace` is in `objects` and not in `tables`; it raises `DatabaseNotFound(key=<path>)` when the path does not exist (creating nothing), `CorruptDatabase(detail=<message class>, key=<path>)` when SQLite refuses the bytes (`file is not a database`, `database disk image is malformed`), and `Conflict(rule='DB_READONLY', code='DB_READONLY')` when the read cannot create its `-shm` beside a database already in WAL in a directory the process cannot write; an empty file answers `objects == frozenset()`; the file's bytes are the same after the probe as before it; a refusal raised by the read closes the connection before it raises, and the caller closes `conn` on every path that got a `Probe` back (`try/finally`, §6, D-068, D-069) |

### 4.3 Non-functional requirements (REQ-N)

- **REQ-N030-01** — `list_view` with 10,000 issues and 3 labels on average: **< 300 ms p95** (matches
  REQ-N008 of SPEC-000), measured with `time.perf_counter` in the integration test.
- **REQ-N030-02** — `issue_detail` of an issue with 200 comments and 100 history events:
  **< 20 ms p95**.
- **REQ-N030-03** — `migrate()` on an empty database applies `001_init.sql` in **< 200 ms**.
- **REQ-N030-04** — The overhead of `UnitOfWork.write()` (BEGIN + COMMIT without statements) is
  **< 1 ms p95**; 1,000 empty transactions in **< 1 s**.

### 4.4 Concurrency: why `BEGIN IMMEDIATE`

The classic SQLite deadlock with deferred transactions:

```
Process A: BEGIN;            -- takes SHARED (reader)
Process B: BEGIN;            -- takes SHARED (reader)
Process A: UPDATE ...        -- tries SHARED -> RESERVED
Process B: UPDATE ...        -- tries SHARED -> RESERVED
```

Both want the `RESERVED` lock, which is exclusive, and each already holds `SHARED`. SQLite detects that
the wait cannot be resolved (the other cannot release `SHARED` because it is waiting for the same thing) and
returns `SQLITE_BUSY` **immediately**, without invoking the busy handler: `busy_timeout` does not help in this
case because waiting would fix nothing. It is a lock upgrade deadlock, and it appears only when the
transaction starts by reading and then writes — exactly the pattern of `create_issue` (read the
team, compute the number, insert).

`BEGIN IMMEDIATE` asks for `RESERVED` from the start. Consequences:

1. If another process is already writing, the busy handler **does** wait (up to 5000 ms) because the lock is released
   only when the other does `COMMIT`.
2. Once taken, nobody else can start writing: the `MAX(number)+1` and the `INSERT` are
   atomic with respect to other writers.
3. Readers (`read()` with deferred `BEGIN`) keep coming in: in WAL they do not compete with `RESERVED`.

Cost: writers are serialized, which is exactly what is wanted in a single-user local app.
The lock is taken for milliseconds; the only pathological case would be a `write()` that waits for user
input, and the use cases of SPEC-040 do not do that.

`UnitOfWork.write` algorithm — **no internal retry** (D-018):

```python
@contextmanager
def write(self) -> Iterator[Repos]:
    conn = self._connection()  # opens if a path was given
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield self._repos(conn, readonly=False)
        conn.execute("COMMIT")
    except sqlite3.OperationalError as exc:
        safe_rollback(conn)  # ROLLBACK if there is an open transaction
        raise translate_sqlite_error(exc) from exc  # DB_BUSY / DB_READONLY
    except BaseException:
        safe_rollback(conn)
        raise  # the same instance, without wrapping
```

`safe_rollback` ignores the "no transaction is active" error and **never** masks the original
exception. There is no `for attempt`, no `sleep`, no `import time` in `uow.py`: the retry loop cannot
live here because the body of the `with` has already run and a generator is not re-invoked.

The retry is composed by the **use case**, in a layer above the adapter:

```python
# src/hifa/app/retry.py  (SPEC-040 §2.2)
def with_write_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.05,
    retry_on: frozenset[str] = frozenset({"DB_BUSY"}),
) -> T:
    for attempt in range(1, attempts + 1):
        try:
            return fn()  # fn opens its own uow.write()
        except Conflict as exc:
            if (exc.code or exc.rule) not in retry_on or attempt == attempts:
                raise
            time.sleep(base_delay * 2 ** (attempt - 1))  # 50/100/200 ms
    raise AssertionError("unreachable")
```

Every attempt of `with_write_retry` is a **new transaction** (rollback of the previous one included),
which is exactly what R4 requires and what a 5 s `busy_timeout` cannot guarantee in a lock upgrade
deadlock.

## 5. Flow

### 5.1 Migration (once per database, or per upgrade)

```
1. conn = connect(db_path)                       # PRAGMAs, WAL
2. v0 = current_version(conn)                    # PRAGMA user_version
3. files = pending_migrations(conn)              # 0 if up to date -> return (no backup)
3a. if files: require_fts5(conn)                 # no ENABLE_FTS5 -> Fts5Unavailable, nothing written
4. backup_database(db_path, now=clock.now())     # sqlite3 backup API + verification
5. for f in files:                               # numeric order
     BEGIN IMMEDIATE
       executescript(contents of f)              # DDL of SPEC-010
       PRAGMA user_version = <version of f>
     COMMIT                                      # if it fails: ROLLBACK and abort with the file in the message
6. conn.close(); return MigrationResult(...)
```

### 5.2 Write (what a use case does, in adapter terms)

```
1. uow = UnitOfWork(db_path, clock=clock, idgen=idgen)
2. with uow.write() as tx:                       # BEGIN IMMEDIATE
3.     team = tx.teams.require(...)              # SELECT (same snapshot)
4.     number = tx.issues.next_number(team.id)   # SQL-1
5.     issue = Issue(..., number=number, identifier=identifier.format(team.key, number))
6.     saved = tx.issues.insert(issue)           # SQL-2, RETURNING *
7.     tx.issues.set_labels(saved.id, label_ids) # 1 DELETE + 1 executemany
8.     tx.history.record(saved.id, changes, actor_id=..., now=now)   # SQL-8
9. # COMMIT on leaving the with; any exception -> ROLLBACK
10. uow.close()
```

Steps 6-8 are the atomic unit: if the history `record` fails, the issue does **not** exist. That
contract is consumed by SPEC-040 §5.

### 5.3 Read (dashboard and `hifa issue list`)

```
1. conn = Database(path).ro_connection()         # mode=ro, WAL: reads while another writes
2. items = list_view(conn, ListFilters(team_id=..., limit=50))   # 1 query (SQL-4)
3. conn.close()
```

For a view that needs consistency across several queries (`issue_detail`), `UnitOfWork.read()` is used:
a deferred `BEGIN` keeps the same snapshot across the 12 statements of the render read (8 with an empty
`include`), SQL-7 included.

## 6. Errors

`translate_sqlite_error(exc)` is the **only** function that converts `sqlite3.Error` into
`HifaError`. It maps by `sqlite3` error code + message, in this order (the first match wins):

| sqlite3 condition | Typical message | Domain error | CLI | HTTP |
|---|---|---|---|---|
| `IntegrityError`, `UNIQUE constraint failed: issue.identifier` | duplicate identifier | `Conflict(rule='UNIQUE_IDENTIFIER', field='identifier')` | 4 | 409 |
| `IntegrityError`, `UNIQUE constraint failed: issue.team_id, issue.number` | duplicate number | `Conflict(rule='DUPLICATE_NUMBER', field='number')` | 4 | 409 |
| `IntegrityError`, `UNIQUE constraint failed: issue_relation.*` | duplicate relation | `Conflict(rule='RELATION_DUPLICATE')` | 4 | 409 |
| `IntegrityError`, `UNIQUE constraint failed: label.*` | duplicate label | `Conflict(rule='DUPLICATE_LABEL', field='name')` | 4 | 409 |
| `IntegrityError`, other `UNIQUE` | — | `Conflict(rule='UNIQUE', field=<column from the message>)` | 4 | 409 |
| `IntegrityError`, `FOREIGN KEY constraint failed` | nonexistent or other-team reference | `Validation(rule='FOREIGN_KEY')` | 2 | 422 |
| `IntegrityError`, `CHECK constraint failed: <table>` | DDL CHECK | `Validation(rule='CHECK_CONSTRAINT', field=<table>)` | 2 | 422 |
| `IntegrityError`, `NOT NULL constraint failed: <table>.<col>` | missing field | `Validation(rule='NOT_NULL', field=<col>)` | 2 | 422 |
| `OperationalError`, `database is locked` / `database table is locked` | after 3 attempts | `Conflict(code='DB_BUSY')` | 4 | 409 |
| `OperationalError`, `attempt to write a readonly database` | `mode=ro` connection, or a reader that cannot create its `-shm` | `Conflict(rule='DB_READONLY', code='DB_READONLY')` | 4 | 409 |
| `OperationalError`, `no such table: <t>` | unmigrated database | `HifaError(code='DB_NOT_MIGRATED')` | 6 | 500 |
| `DatabaseError`, `file is not a database` / `database disk image is malformed` | corrupt file | `CorruptDatabase(detail=<message class>, key=<path>)` | 6 | 500 |
| `sqlite3.OperationalError` when opening in `ro` (`unable to open database file`) | nonexistent path | `DatabaseNotFound(key=<path>)` | 3 | 503 |
| `sqlite3.sqlite_version_info < (3,35,0)` | `RETURNING` unavailable | `HifaError(code='SQLITE_TOO_OLD')` | 6 | 500 |

Mapping rules:

- The domain message does **not** reproduce SQLite's text: it is assembled with `rule` and the useful data
  (`details={'constraint': ..., 'column': ...}`). The raw text goes to `logging.DEBUG`, never to the
  user nor to the error JSON.
- An unlisted `sqlite3.Error` is wrapped in `HifaError(code='DB_ERROR')` preserving
  `__cause__` for the traceback; the edge treats it as 500 / exit 6 (D-014: `1` belongs to `hifa doctor`).
- `DB_BUSY` is `Conflict` (not `Validation`) because the input is valid and retrying later may
  work; the CLI distinguishes it by `code`, not by class.
- Domain errors that already come from the repos (`NotFound`, `Conflict`, `Validation`) are **not**
  re-wrapped.
- **`open_probe()` decides three of these rows and no others**, and it decides them by raising the class
  each row names, never a `HifaError` with an ad-hoc code: `DatabaseNotFound(key=<path>)` for a path that
  `mode=ro` cannot open (nothing is created in the attempt, because `mode=ro` never creates), and, from
  the `PROBE_SQL` read — the read and not the open, since `sqlite3.connect()` is lazy —
  `CorruptDatabase(detail=…)` for the `DatabaseError` row and `Conflict(rule='DB_READONLY', code='DB_READONLY')`
  for the `attempt to write a readonly database` row. (The `rule=` is not decoration: SPEC-020 §2.6's
  `Conflict.__init__` requires it, and the adapter writes `Conflict("database is read-only",
  rule="DB_READONLY", code="DB_READONLY")` today; the `DB_BUSY` row above reads the same way.) The third
  one is the measured case the probe cannot
  dodge: a database **already in WAL** sitting in a directory the process cannot write answers that
  message on the read itself, because the reader cannot create the `-shm` it needs (measured, SQLite
  3.53.1, `hifa db reindex` on such a file: exit `4`). The probe applies no pragma, so the *other*
  path into that row — `journal_mode=WAL` failing on a file that is not in WAL — is still unreachable
  from it; the permission one is not. It translates nothing else: an empty file is
  `objects == frozenset()`, not an error (D-068, D-069).
- **`Fts5Unavailable` is raised, not translated.** It is not a `sqlite3.Error` mapping: no message of the
  driver produces it. Its one producer is `require_fts5` (§2.1), which reads `FTS5_ENABLED_SQL` and raises
  `Fts5Unavailable(code='fts5_unavailable')` when the connection's SQLite carries no `ENABLE_FTS5`; exit
  `6` and HTTP `500`, the internal class of D-014, because the binary is what is missing and nothing the
  user typed caused it. The two call sites are declared where they run: `migrate()` (§2.3, §5.1 step 3a)
  and the probe of `hifa db reindex` (SPEC-100 R21 step (b)). SPEC-050 §6 publishes the code and composes
  no sentence for it; D-065 records the producer and D-068 the placement.

## 7. Acceptance Criteria

```gherkin
@SPEC-030
Feature: SQLite adapter

  @REQ-F030-01
  Scenario: every connection applies the mandatory PRAGMAs
    Given a temporary database path
    When connect(path) is run
    Then PRAGMA journal_mode returns "wal"
    And PRAGMA foreign_keys returns 1
    And PRAGMA busy_timeout returns 5000
    And PRAGMA synchronous returns 1
    And connection.row_factory is sqlite3.Row
    And connection.isolation_level is None

  @REQ-F030-23
  Scenario: the FTS5 module check passes on a build that has the module
    Given a connection to a migrated database
    When require_fts5(conn) is run
    Then no exception is raised
    And connect() itself never runs the check

  Scenario: a build without FTS5 is refused by name, before it writes anything
    Given a connection whose FTS5_ENABLED_SQL returns no row
    When require_fts5(conn) is run
    Then Fts5Unavailable is raised with code "fts5_unavailable"

  @REQ-F030-24
  Scenario: the probe identifies a file with no pragma and without creating it
    Given a missing path, an empty file, a file SQLite refuses and a migrated database
    When open_probe(path) is called on each of them
    Then the missing path raises DatabaseNotFound with code "database_not_found" and key equal to the path
    And no file is created at the missing path
    And the empty file answers objects == frozenset() and tables == frozenset()
    And the file SQLite refuses raises CorruptDatabase with code "corrupt_database"
    And the migrated database answers tables containing "workspace" and "issue"
    And the migrated database answers objects containing "workspace", "issue", "v_issue" and "v_priority"
    And a foreign file carrying "CREATE VIEW workspace" is in objects and not in tables
    And PRAGMA foreign_keys on the probe's connection answers 0
    And the sha256 of each existing file is the same after the probe as before it

  @REQ-F030-24
  Scenario: the probe of a WAL database in a directory it cannot write answers exit 4
    Given a migrated database in WAL mode inside a directory with mode 0500
    When open_probe(path) is called on it
    Then Conflict is raised with code "DB_READONLY"
    And the bytes of the database file are the same as before the probe
    And open_probe closed the connection it had opened before raising

  @REQ-F030-02
  Scenario: a read-only connection rejects writes
    Given a migrated database with one issue
    When connect(path, readonly=True) is opened and an INSERT on issue is run
    Then Conflict with code "DB_READONLY" is raised
    And the issue count does not change

  @REQ-F030-03
  Scenario: write commits on exit and rolls back on exception
    Given a UnitOfWork over a migrated database
    When a write block that inserts a team runs and ends normally
    Then the team exists on a new connection
    When a write block that inserts a team and then raises HifaError runs
    Then the team does not exist on a new connection
    And the propagated exception is the same instance that was raised

  @REQ-F030-04
  Scenario: read does not block the writer and does not allow writes
    Given a UnitOfWork with an open read block
    When another process inserts an issue and does COMMIT
    Then the other process's INSERT is not blocked
    And an attempt to insert inside the read block raises Conflict with code "READ_ONLY_UOW"

  @REQ-F030-05
  Scenario: the repos share the UnitOfWork connection
    Given a UnitOfWork in a write block
    When the connections of issues, teams, labels, projects, cycles, comments and history are compared
    Then the seven are the same object as uow.connection
    And a static search finds no call to sqlite3.connect outside connection.py and migrate.py

  @REQ-F030-06
  Scenario: write does not retry and propagates DB_BUSY
    Given an external writer holding BEGIN IMMEDIATE
    When a write that inserts an issue is run
    Then Conflict with code "DB_BUSY" is raised on the first attempt
    And no row is left inserted
    And a static search finds no retry loop in uow.py

  @REQ-F030-06
  Scenario: with_write_retry retries the whole use case
    Given an external writer holding BEGIN IMMEDIATE for 120 ms
    When with_write_retry is run over a use case that inserts an issue
    Then the insertion succeeds on the second or third attempt
    And the failed transaction was closed with ROLLBACK before retrying

  @REQ-F030-07
  Scenario: ten concurrent creations do not repeat a number
    Given the ENG team with 5 issues
    When 10 issues are created in 10 processes in parallel
    Then numbers 6 through 15 exist exactly once each
    And the 10 identifiers are distinct

  @REQ-F030-08
  Scenario: insert and update return the final row
    Given an Issue without explicit archived_at or trashed
    When issues.insert(issue) is called
    Then the returned Issue has trashed False and archived_at None
    And its identifier matches the one that was sent
    When the title is updated and issues.update is called
    Then the returned Issue has the new title and the same created_at

  @REQ-F030-09
  Scenario: the listing with labels uses a single query
    Given 100 issues with 3 labels each in the ENG team
    When list_view is run with ListFilters(team_id="ENG", limit=100) counting statements with set_trace_callback
    Then exactly 1 SQL statement is executed
    And each IssueListItem brings its 3 labels
    And no issue triggers an additional query

  @REQ-F030-10
  Scenario: filters are combined and pagination is stable
    Given 120 issues in the ENG team, 40 of them with an assignee
    When list_view is run with assignee_id and limit 50
    Then 50 items are returned, all from the requested assignee
    When it is run twice with limit 50 and offset 50
    Then the two pages share no id
    And the ids of each page keep the same order between runs

  @REQ-F030-11
  Scenario: the board groups by state and respects the per-column limit
    Given the ENG team with 3 states and 60 issues in the state "In Progress"
    When board_view(group_by="state", limit_per_group=50) is run
    Then there is one BoardGroup per state ordered by position
    And the group "In Progress" reports total 60 and 50 issues
    And each issue appears in a single group

  @REQ-F030-12
  Scenario: the detail has a fixed number of queries
    Given an issue with a creator, 20 children, 4 labels, 30 comments and 40 history events,
      no assignee, no parent and no relations
    When issue_detail is run with include=DETAIL_INCLUDE counting SQL statements
    Then exactly 12 statements are executed
    And the detail brings the 20 children, the 4 labels, the 30 comments, the team's states
      and the archive flags of the row
    When issue_detail is run with include=frozenset() counting SQL statements
    Then exactly 8 statements are executed
    And no comment, no history entry and no attachment is loaded

  @REQ-F030-13
  Scenario: the agenda covers the requested window and includes overdue
    Given today is 2026-11-08
    And an issue with due_date 2026-11-01 open and another closed
    And an issue with due_date 2026-11-10
    And an issue with due_date 2026-12-01
    When agenda(start=2026-11-08, days=7, include_overdue=True) is run
    Then the open overdue one and the one from 2026-11-10 appear
    And the closed one and the one from 2026-12-01 do not appear
    And the order is by ascending due_date

  @REQ-F030-14
  Scenario: migrating is idempotent
    Given a nonexistent database
    When migrate(path, now=2026-11-08T12:00:00Z) is run
    Then applied contains "001_init.sql" and user_version is 1
    When migrate is run again
    Then applied is empty
    And user_version is still 1
    And no second backup was created

  @REQ-F030-15
  Scenario: the backup precedes the migration
    Given a database at user_version 1 and the pending file 002_add_previous_keys.sql
    When migrate is run with do_backup=True
    Then a file matching "*-*.db.bak" exists
    And the backup has user_version 1
    And the migrated database has user_version 2

  @REQ-F030-16
  Scenario: a failed migration leaves no advanced version
    Given a database at user_version 1
    And a file 002_broken.sql with invalid SQL
    When migrate is run
    Then HifaError is propagated with the file name in details
    And user_version is still 1
    And no table created by 002_broken.sql exists

  @REQ-F030-17
  Scenario: a uniqueness violation is translated to Conflict
    Given the identifier "ENG-1" already persisted
    When a second issue is inserted with identifier "ENG-1"
    Then sqlite3.IntegrityError does not escape
    And Conflict is raised with rule "UNIQUE_IDENTIFIER" and field "identifier"
    And the message does not contain the raw text "UNIQUE constraint failed"

  @REQ-F030-18
  Scenario: the type mapping is symmetric
    Given a row with trashed 0, due_date "2026-11-30" and created_at "2026-11-08T14:03:11Z"
    When the repo builds the Issue
    Then trashed is False, due_date is date(2026,11,30) and created_at is a datetime with UTC tzinfo
    When that Issue is inserted again into another database
    Then the resulting row has exactly the same three TEXT and INTEGER values

  @REQ-F030-19
  Scenario: there is no value interpolation in SQL
    Given the adapter modules
    When a static test looks for f-strings, % and concatenations with + inside SQL literals
    Then it finds no match
    And all values travel as bound parameters

  Scenario: the created row carries no value
    Given the ENG team with a default state "Backlog"
    When create_issue is run with title "Fix login"
    Then the issue's history has one row with field "created"
    And that row has old_value NULL and new_value NULL
    And the row's actor_id is the member that ran it

  Scenario: the field a row carries is the writer's name
    Given an issue "ENG-1" in "Backlog"
    When it is moved to "Todo", labelled "bug" and related to "ENG-2" as "blocks"
    Then the new rows carry the fields "state_id", "board_order", "label" and "relation"
    And the relation row's new_value is "blocks ENG-2"
    And no row carries "relation:blocks", "label_id", "archived" or "restored"

  @REQ-F030-12
  Scenario: resolution is one statement per kind and never one per row
    Given an issue whose history names 12 distinct states and 3 distinct parents
    When issue_detail is run with include={"history"} counting SQL statements
    Then the count is the same as for an issue whose history names one state and one parent
    And it is one more than the same read over an issue whose history names no parent

  @REQ-F030-20
  Scenario: history is written in a single batch
    Given an issue with 3 fields changed in a use case
    When history.record is called with the 3 changes counting SQL statements
    Then 1 INSERT statement with executemany is executed
    And 3 rows exist in issue_history with the same created_at

  Scenario: the rank a state change carried is not a row of its own
    Given an issue moved to another state, whose rank moved with it in the same record call
    When the issue's history is read
    Then the move is one row
    And no row of the window carries the field "board_order"

  Scenario: a drag written after a state-only move is a row of its own
    Given an issue moved into an empty column, whose rank did not move with the state
    And a drag recorded after that move
    When the issue's history is read
    Then the move is one row
    And the drag is a row of its own, reading "reordered it in the column"

  @REQ-F030-21
  Scenario: no operation deletes issues physically
    Given a persisted issue
    When trash and then archive are run
    Then the row still exists with trashed 1 and non-null archived_at
    And a static search finds no "DELETE FROM issue" in the adapter

  @REQ-F030-22
  Scenario: the repos return domain entities
    Given an issue row in the database
    When issues.get is called with its id
    Then the result is an instance of Issue
    And it is not sqlite3.Row nor a tuple
    And all its fields have the type declared in SPEC-020
```

## 8. Regeneration checklist

1. Read `SPEC-000` (§2 A3, A7, A8), `SPEC-010` (§3 complete DDL, §4 R1-R7) and `SPEC-020` (§2.2
   entities, §2.6 errors). Without `SPEC-010` you cannot know which columns exist; without `SPEC-020`
   you do not know which type to map to.
2. Create `src/hifa/adapters/sqlite/__init__.py` and the eleven modules of §2, all empty, and verify
   that the package imports without `migrations/`:
   ```bash
   mkdir -p src/hifa/adapters/sqlite tests/integration/db
   python -c "import hifa.adapters.sqlite"
   ```
3. Implement in this order, because each one depends on the previous: `connection.py` (PRAGMAs +
   SQLite version) → `migrate.py` → `uow.py` → the seven repos → `reads.py`. The migrator before the
   repos, because the repo tests need a migrated database.
4. Verify the database:
   ```bash
   python -m hifa.cli.main db migrate --db /tmp/hifa_test.db
   sqlite3 /tmp/hifa_test.db "PRAGMA user_version; PRAGMA journal_mode; PRAGMA foreign_keys;"
   sqlite3 /tmp/hifa_test.db ".tables"    # 17 tables + issue_fts
   ```
5. Run the integration tests, which use a temporary database per test (never the user's):
   ```bash
   pytest tests/integration/db -q
   ```
   Each test creates `tmp_path/"hifa.db"`, runs `migrate()` and closes the connections in a fixture with
   `yield`.
6. Verify the "single query" requirements with `sqlite3.Connection.set_trace_callback`:
   count statements in `list_view` (1), `board_view` (1), `agenda` (1) and `issue_detail` — **12** with
   `include=DETAIL_INCLUDE` (**13** when the page's history carries a `parent_id` row, SQL-7 row 17) and
   **8** with an empty `include`, on an issue with a creator and no assignee, no parent and no relations
   (SQL-7). It is the only cheap way to test
   R5/REQ-F030-09/REQ-F030-12, and a count without its `include` proves nothing.
7. Verify concurrency with real processes (`multiprocessing` with `spawn`), not with threads: the
   SQLite lock is between connections and threads of the same process share the GIL but not
   necessarily connections. The REQ-F030-07 test must launch 10 processes.
8. Verify the contract and the graph:
   ```bash
   python scripts/spec_contract_check.py --spec specs/030-sqlite-adapter.md
   lint-imports   # adapters cannot import app/ nor cli/
   ```
9. Regeneration proof: `git rm -r src/hifa/adapters/sqlite tests/integration/db`, reimplement
   from this spec and run `pytest tests/integration/db -q`. If any test needs to read the old code
   to understand the schema, the spec is incomplete: the schema is `SPEC-010` §3.

## 9. Provenance

- **PRAGMAs** (`WAL`, `foreign_keys=ON`, `busy_timeout=5000`, `synchronous=NORMAL`): `SPEC-000` §2 A3
  and `SPEC-010` §3 (DDL header). `busy_timeout=5000` is the author's decision, 2026-11-08, so that
  a writing `hifa` does not make a read-only `hifa serve` fail.
- **`BEGIN IMMEDIATE` and the lock upgrade deadlock**: official SQLite documentation,
  "Transaction" and "Resolving Hot Journal / SQLITE_BUSY"; the scenario of two readers that want to
  write is the canonical example of `SQLITE_BUSY` that the busy handler cannot resolve.
- **Retry 3 times with 50/100/200 ms backoff**: author's decision, 2026-11-08; 3 is the same
  number that `SPEC-010` R2 fixes for `number` collisions, to have a single policy. **Where
  the loop lives is fixed by D-018**: outside the `@contextmanager`, in `app/retry.py::with_write_retry`,
  because the body of the `with` cannot be re-executed from a generator.
- **`MAX(number)+1` instead of `AUTOINCREMENT`**: `SPEC-010` R2 gives every team its own counter
  (`issue.number` is per team, not global), and `AUTOINCREMENT` is a per-table sequence that cannot
  express that.
- **`RETURNING`**: available since SQLite 3.35 (2021-03); Python 3.11 on the reference host ships
  3.37+. It avoids the subsequent `SELECT` and guarantees that what the DDL applied is returned.
- **`json_group_array` for labels**: JSON1 function, included in standard SQLite builds since
  3.9 and always present in Python's. Chosen over `GROUP_CONCAT` because it returns valid JSON
  without escaping commas or quotes in label names.
- **Backup with `sqlite3.Connection.backup()`**: official online backup API; with WAL, copying the
  file with `shutil` may lose the pages that are in the `-wal`. The name
  `<stem>-<ts>.db.bak` is the one fixed by `SPEC-010` R5 (approved spec, normative).
- **Flat read models (`dataclass`, not Pydantic)**: author's decision, 2026-11-08, so that
  `list_view` of 10,000 rows does not validate 10,000 Pydantic models (REQ-N030-01) and so that the adapter does not
  depend on the API schema.
- **`limit` default 50 / max 200**: author's decision, 2026-11-08 — 50 is a screenful and 200 is
  the ceiling that keeps one page inside the REQ-N008 budget.

## 10. Out of scope

- The DDL contents, the indexes, the views and the version policy (SPEC-010).
- Business rules and semantic validations (SPEC-020 / SPEC-040).
- FTS5 **search** and its triggers (SPEC-090): the adapter only exposes the `issue_fts` table as
  existing, and the query, the ranking and the snippet are not its business. The one FTS5 question this
  layer does answer is whether the module is compiled into the binary (`require_fts5`, §2.1), and it
  answers it for the two callers that cannot work without the module — the migration that creates the
  virtual table and `hifa db reindex` (SPEC-100 R21).
- CSV/JSON import/export (SPEC-080).
- Replication, device synchronization, encryption at rest, scheduled backups.
- Connection pool, ORM, query builder, "down" migrations.
- Query plan optimization beyond the indexes of SPEC-010: if `EXPLAIN QUERY PLAN`
  shows a `SCAN`, the solution is a new index in a migration, not a change in the adapter.

## Notes

- spec_revision 24 (2026-11-21): **the identifier comparison folds the parameter, and the resolution is
  declared as the three lookups it is (HIFA-83, D-078).** `version` 9 → 10: `reads` exports one more public
  function (`issue_id_by_ref`), which is §2's ground, and no DDL moves.
  (1) **`COLLATE NOCASE` is withdrawn.** It is not a cheaper spelling of the same comparison: it makes
  `sqlite_autoindex_issue_2` unusable, both branches of row 1's `OR` collapse and the statement reads every
  row of `issue` — measured with `EXPLAIN QUERY PLAN` as `SCAN i`, on the one statement every detail read in
  the tree runs (`show`, the board card, the issue panel). Row 1 now binds `upper(:ref)`: the column is
  written normalised, so folding the parameter is the whole of the case-insensitivity and the plan is back
  to `MULTI-INDEX OR` with `SEARCH … sqlite_autoindex_issue_2 (identifier=?)` and `SEARCH …
  sqlite_autoindex_issue_1 (id=?)`. No migration and no index are involved: §10's rule asks for a new index
  when one is missing, and here nothing was.
  (2) **`team_by_previous_key` had the same defect and the same fix** — `pk.key = :key COLLATE NOCASE`
  scanned the table; it now binds `upper(:key)` and `idx_team_previous_key_key` answers. The write path
  stores the key normalised, so the two statements are the same rule with a readable plan.
  (3) **The resolution is three lookups, not two, and it runs before the read.** `issue_id_by_ref` answers a
  ULID or a current identifier in one statement — the first two forms, in the order SPEC-050 §2.5.3.2
  declares them — and the application layer resolves a reference to an id with it, with
  `team_by_previous_key` and `issue_id_by_number`, and then asks this read for that id **once**. The read's
  own statements and the two totals of §2.5 are unchanged: this is what every caller outside the read pays,
  and it replaces the probe that used to be row 1 itself.

- spec_revision 23 (2026-11-21): **the reference vocabulary of the detail read, and the two lookups it
  does not perform itself (HIFA-83, D-076 as corrected by D-077).** `version` 8 → 9: `reads` exports two
  more functions and `IssueDetail` gains one field, both public names of §2, so the version moves even
  though the data model does not.
  (1) **SQL-7 row 1 compares the identifier with its case folded.** `i.identifier = :ref` became
  `i.identifier = :ref COLLATE NOCASE`; the identifier is the team's key plus its number and the key is
  normalised on write, so no two stored identifiers differ by case and the predicate cannot answer twice.
  A ULID still reaches `i.id` and is compared as stored. *(Corrected at spec_revision 24: the collation is
  on the wrong side of the comparison — it folds the column and costs the index. The same rule is stated
  there as `upper(:ref)`.)*
  (2) **The other two forms are resolved a layer up, and this spec declares the statements that answer
  them.** `issue_id_by_number` reads `issue` by `(team_id, number)` and `team_by_previous_key` reads
  `team_previous_key`, case-insensitively, the greatest `changed_at` first with an exact tie broken by the
  greater `team_id` — the order that makes a key two teams left behind answer once. Both return an id or a
  row, never a detail: the application layer's `issue_detail` calls one of them **before** this read and
  then asks this read for the issue the answer names, which is why no row of SQL-7 grew and the two totals
  did not move.
  (3) **`IssueDetail.previous_identifier` reports what the caller's reference was.** Additive and
  defaulted (D-026), `None` on every read whose reference named the issue directly; the application layer
  fills it on the one form that resolves through a rotation. It is a fact about the call and not a column:
  no statement of this read produces it.
- spec_revision 22 (2026-11-20): **the rank rule is the batch, not a position (D-075).** The bullet above
  now states what the writers do — a state move writes the rank row **only when the rank moves**, so a move
  into an empty column writes the `state_id` row alone — and SQL-7 row 11 asks for **both** the `id`
  adjacency **and** the shared `created_at`, because either one alone is not the batch. The previous
  revision's inference from `id − 1` alone dropped a real drag written after a state-only move. §7 goes
  30 → **32** (the rank of a batched move is not a row; a drag after a state-only move is one). No
  signature, statement count, index or `.feature` file moves.
- spec_revision 21 (2026-11-20): **the `field` vocabulary is enumerated with its writers, the `created`
  row stops echoing the identifier, and the resolution is the read's, bulk and free where it can be
  (HIFA-46, D-074).** `version` 7 → 8: §2.6's `IssueDetail` gains one field, SQL-7 gains one statement,
  and §2.4's repository note becomes a closed table a writer must satisfy — a database and a read model
  that this spec produces, so the version moves.
  (1) **The vocabulary was described in prose and the prose was wrong twice.** It listed column names and
  then named `label`/`relation`/`created` as if they were other columns; the writers' set is **21**
  values, four of which are not columns of `issue` but exist only as rows, and it includes ten values the
  live database actually carried on 2026-11-20 (`archived_at`, `board_order`, `canceled_at`,
  `completed_at`, `created`, `description`, `relation`, `started_at`, `state_id`, `trashed`) and eleven
  it does not yet, because a writer can produce them. The table names the writer behind each, the shape
  of the pair (which side is `NULL`) and the sentence SPEC-070 §2.5.7 renders; the *rule* stays
  SPEC-050 §2.4.2's and the *wording* stays §2.5.7's.
  (2) **Eight kinds of stored value are not printable as they stand, and all eight now have a producer.**
  `label` rows carry the label's name and `relation` rows carry `"<type as issue reads it> <identifier>"`,
  so those two need no resolution at all. Six are ULIDs the read resolves — `state_id`, `assignee_id`,
  `parent_id` and the three reserved links — and **two are keys the grammar turns into words**, `priority`
  (`"2"` → `High`) and `due_date` (`"2026-11-20"` → `20 Nov 2026`), which had no producer at all before
  this revision: the grammar's `set priority to High` would have printed a `2`. The maps arrive in **two
  families**, and §2.6 declares only what the read builds: `members`, `history_parents` and the `states`
  tuple the third map is labelled from. The two **word** maps — `priorities` and `due_dates` — are the
  application layer's constants, declared once in SPEC-040 §2.2 as `history_rows`'s defaults
  (`PRIORITY_LABELS`, `DUE_DATE_FORMAT`), because a map of a closed 0-4 and a format of a date are
  constants and not lookups, and a constant belongs to the module that reads it and not to the database.
  (3) **Resolution is the read's and it is bulk.** `members` (row 13) and `states` (row 16) already carry
  the actors and the state labels, so `state_id` and `assignee_id` cost zero statements; **row 17** is
  the one statement a page pays for `parent_id` rows, once for the kind and never once per row; and
  `priority`/`due_date` cost none, because their maps are constants. The N+1 is named as the thing not to
  do, and REQ-F030-12's totals say 12 or 13 by whether the page's history names a parent. `domain/` learns
  nothing: the maps are strings and the sentences are `app/`'s.
  (4) **The `created` row is written with `new_value NULL` from this revision's writer on**, and the rows
  already written are normalised by `migrations/004_history_created_backfill.sql` (SPEC-010 §3.7). §3's row
  for `issue_history` says the table is INSERT-only *except* that one migration, because it is. The writer
  edit and the migration land in **one PR** — the ordering `comment_added`/HIFA-6 is declared with — and
  §2.4 names the window in which an older binary still writes the identifier into a file `004` has already
  passed; the migration is **hygiene**, because the grammar has no place for a value on `created` and the
  row printed the same sentence before it (D-074).
  (5) **The rank of a move is dropped by the read and not by a rendering.** §2.4's `board_order` rule was
  written as a property of the rendering ("when the pair shares the instant"), which left the three payloads
  free to publish different counts and made "no row is dropped" false of the surface that dropped one; the
  filter is now SQL-7 row 11's `WHERE` — a `board_order` row is excluded when the row immediately before it
  of the same issue, by `id`, is a `state_id` row — so every edge publishes the rows the read returned (the
  panel shows the first 8 of them and its `Show all N` names the window, §2.4), and
  the discriminator is the record batch rather than `created_at`'s second precision (33 of the 34 rows of
  the measured database are exactly `id − 1` from a `state_id` row). **`started_at` publishes one
  direction** (`NULL → ISO`), because `apply_milestones` has one: it stamps the column on entering
  `started` when it was empty and never clears it, so `cleared the start time` had no row and is gone.
  `completed_at` and `canceled_at` keep both directions (`NULL → ISO` / `ISO → NULL`), which are the two
  writers `apply_milestones` has for each.
  (6) **Counts.** §7 goes 27 → 30 (three scenarios: the value-less `created` row, the writer's names,
  and the bulk statement); §8's count follows. No signature of §2.4 changes, no index is added, and the
  `.feature` corpus is untouched by this revision.

- spec_revision 20 (2026-11-20): **the `field` a history row carries is stated in the two families, and
  `created` is one of them (HIFA-49, round four).** `version` stayed 7 at that revision: no signature,
  column list, statement, count or §7 scenario moved — the note beside `HistoryRepo.record` repeated the
  vocabulary SPEC-050 §2.4.2 owns instead of publishing a third list of its own. **That note is superseded
  here**: the closed table of this revision enumerates the writers' set, so "no name is added to either
  family" is true of revision 20's edit and false of the table that replaced it (D-074).
  (1) **The note said "the exact column name" and then listed two names that are not columns of
  `issue`.** `field` is a column's name for a column change and the noun of the write for the record
  events that are not columns; `label` and `relation` sat in a list introduced as column names, and
  `created` — the row `create_issue` writes and SPEC-040 §7 asserts — was missing from it. The paragraph
  now names both families, enumerates the three non-column nouns v1's tree writes (`created`,
  `relation`, `label`) with the use case behind each, and points at SPEC-050 §2.4.2 as the one owner of
  the rule. No name is added to either family and this spec declares no fourth noun.

- spec_revision 18 (2026-11-20): **the item projection learns `milestone`, and the two collections the
  detail returns learn the trash (HIFA-49, D-072 §2–§3).** `version` 5 → 6: §2.6's `IssueListItem` gains
  two fields and SQL-4/SQL-7 change — the listing's row and the detail's rows are the read model this
  layer publishes, so this is a change of §2 and not of a comment.
  (1) **`milestone` had no producer in the corpus.** SPEC-050 §2.4.2 publishes `milestone: {id, name}` on
  every `Issue` (33 keys) and nothing read `project_milestone`: `issue.milestone_id` is writable
  (SPEC-040 §5.1) and the key could only ever have been `null`. *(Corrected at `spec_revision` 19:
  `milestone_id` is **not** writable. `src/hifa/app/issues.py` raises `HifaError(NOT_IMPLEMENTED)` for
  any non-null `project_id`/`milestone_id`/`cycle_id` and accepts only `NULL`, so the read below loads a
  key no write can fill and `milestone` is `null` on every object v1 can produce — see rev 19 and
  SPEC-050 §2.3.1's GAP-08.)* SQL-4's projection — the one SQL-7 row 1
  reuses — now selects `i.milestone_id` and `pm.name AS milestone_name` from `LEFT JOIN project_milestone
  pm ON pm.id = i.milestone_id`, and `IssueListItem` carries `milestone_id`/`milestone_name`, appended
  with defaults so no positional construction changes. No statement is added (the join rides a projection
  both reads already run), so §2.5's **12/8** totals and R5 do not move.
  (2) **`children` and `relations` had no visibility rule.** `_ITEM_QUERY` binds no `trashed` predicate,
  so SQL-7 rows 8 and 9 returned a trashed child and a trashed far end while SQL-4's `i.trashed = 0` hid
  the same rows from `issue list --parent`, and a trashed `ref` answered `None`. Rows 7, 8 and 9 now carry
  `AND i.trashed = 0`, **unconditionally**: the keyword `include_trashed` is about the issue the read
  returns, not about its neighbours, so a trashed parent leaves `parent` `null` on an object whose
  `parent_id` is set. Row 1 is untouched, so `include_trashed=True` still reaches a trashed
  issue; the two counts do not move, because a predicate is not a statement.
  (3) **What this demands of the code:** `src/hifa/adapters/sqlite/reads.py` — the join and the two
  columns in `_ITEM_COLUMNS` and in the listing's `_SQL`, `milestone_id`/`milestone_name` on
  `IssueListItem`, and `AND i.trashed = 0` in the parent, the children and the far-end `WHERE` (never the
  header's). The order of `relations` is **not** this layer's: SQL-7 row 9 returns an unordered set and
  SPEC-040 §2.2 sorts it (D-072 §1), so no `ORDER BY` is added here.

- spec_revision 19 (2026-11-20): **the trash rule covers the parent too, and the key this revision gives a
  loader is one no write can fill (HIFA-49, round three).** `version` 6 → 7: §2.6's SQL-7 row 7 changes
  its `WHERE` — the statement this layer executes and the one a regeneration transcribes — and the
  paragraph that declared the visibility is the read model's contract; no signature, column list, count
  or §7 scenario moves.
  (1) **Row 7 had no `trashed` predicate, so a trashed parent was shown where the same row was hidden as
  a child.** Rows 8 and 9 were fixed and the pointer that resolves `parent_id` was not, which left the
  same row visible through one key and absent through the other two, in one object. Row 7 now carries
  `AND i.trashed = 0` and answers `null` for a parent the trash layer holds. The rule is stated **once**,
  in §2.6, and SPEC-040 §2.2 and SPEC-050 §2.4.2/§2.5.3.2 point at it instead of restating it.
  (2) **"The two readers of one level list the same set" was false and is gone.** `issue list --parent`
  applies its default `--open` (`is_open = 1`, SPEC-050 §2.5.3) on top of the trash predicate, and
  `issue_detail` has no open axis at all: a **closed** child is listed by one reader and not by the
  other. What the two share is the **trash visibility** — a trashed child is absent from `children` —
  and that is all §2.6 now claims.
  (3) **`milestone` is loaded by this layer and written by nobody.** The `LEFT JOIN` of rev 18 stands;
  what rev 18 got wrong is the other half of the sentence. `src/hifa/app/issues.py` raises
  `HifaError(NOT_IMPLEMENTED)` for **any non-null** `project_id`/`milestone_id`/`cycle_id` (clearing them
  to `NULL` is accepted), and that path is not part of this delta, so in v1 `milestone` is `null` on
  every object this read produces. The key is real the day the projects write path lands; until then
  SPEC-050 §2.3.1 carries **GAP-08** for it, in that table's one-row format, and the seventh file of this
  delta's code list is `src/hifa/app/issues.py` (the `NOT_IMPLEMENTED` raise, replaced by the repository
  write). No statement and no count moves for any of the three.

- spec_revision 17 (2026-11-20): **the two callers that justified `get_by_name` do not exist, and
  the note now names none (HIFA-14, round three, D-070's third amendment).** `version` stays 5: the
  signature and the SQL are the ones §2.4 already declared.
  Rev 16 justified keeping the method as the read "for internal keys (a seed's or an import's own
  strings)", and both halves are false in the implementation tree (`feat/labels-impl`): `hifa import`
  has **no implementation** (`src/hifa/app/transfer.py` is absent, and SPEC-080 §2.1.1 stops prescribing
  this method for the name it resolves), and the seed resolves its four names through the
  case-insensitive step of SPEC-100 R11bis — it calls `case_variant_at_level`, not this method — which
  leaves `LabelRepo.get_by_name` with **no caller** in that tree.
  The note now says that, says why a user-supplied name must never reach it (one row cannot report
  two), and says what it is still for: the exact lookup of a stored key. No row of §7 moves.

- spec_revision 16 (2026-11-20): **`LabelRepo.get_by_name` is an exact read, and the table now says so
  (HIFA-14, round two of the label review, D-070's amendment).** `version` stays 5: the signature is the
  one §2.4 already declared. The method matches `name = ?` (no `casefold`) and returns **one** row, so a
  user-supplied name resolved with it picks a spelling silently — the failure SPEC-040 R22 closes. Its
  note is now explicit: exact match for internal keys (a seed's or an import's own strings), and a
  user-supplied name goes through `list(..., include_retired=True)` plus the `casefold` compare and the
  `LABEL_AMBIGUOUS` refusal. No row of §7 moves.

- spec_revision 15 (2026-11-20): **the probe raises the two classes the corpus publishes, splits its one
  read into two sets, and admits the third verdict it was measured to have (HIFA-7, fifth review round,
  D-069).** `version` 4 → 5, because §2.1's `Probe` and `PROBE_SQL` change shape; §4.1, §4.2, §6 and §7
  carry the consequences.
  (1) **`DB_CORRUPT` was a fourth name for a condition three documents already named.** This file raised
  `HifaError(code='DB_CORRUPT')` while SPEC-010 §6 and SPEC-000 §6 published the class
  `CorruptDatabase` and SPEC-050 §6 and SPEC-100 §6 published the code `corrupt_database` — and no class
  of that name existed anywhere, so the literal implementation answered `internal_error`. The class is
  now declared in SPEC-020 §2.6 (`code = "corrupt_database"`, exit `6`, HTTP `500`) and `open_probe`
  raises it; §6's row maps the same driver message to the same class, so the adapter's internal code and
  the published code are one string and `DB_CORRUPT` stops existing.
  (2) **`NotFound(resource='database')` was the wrong `3`.** It carried `not_found` and a `404`; the
  missing database is `database_not_found`, exit `3`, HTTP `503` — the code SPEC-100 §6 and SPEC-050 §6
  published for this exact input. `open_probe` and the `ro` branch of `connect` now raise
  `DatabaseNotFound(key=<path>)` (SPEC-020 §2.6), which also satisfies SPEC-020 §2.4's keyword-only
  `key=` instead of writing a constructor call the class cannot build.
  (3) **The probe had one set where it needs two.** `PROBE_SQL` folded views into `tables`, so a foreign
  file carrying `CREATE VIEW workspace` satisfied the marker verdicts of both edges; measured, the
  migrator then died inside `init` with `view workspace already exists` — an exit nobody published.
  `PROBE_SQL` now returns `type` and `name`, still **one** statement, and `Probe` carries its two
  projections: `tables` (`type = 'table'`, what a schema verdict asks) and `objects` (both types, what
  "this file carries nothing" asks). SPEC-100 R14a's empty-file test keeps `objects`; the marker verdicts
  of `init` and R21 use `tables`.
  (4) **`DB_READONLY` is reachable from the probe, and the previous note said it was not.** Revision 14
  wrote that the probe "applies no pragma, so `DB_READONLY` is unreachable from it"; measured on SQLite
  3.53.1, a database already in WAL inside a directory the process cannot write answers
  `attempt to write a readonly database` on the `PROBE_SQL` **read**, because the reader cannot create
  its `-shm`. The row stays (§6), the sentence now says which path into it survives, and SPEC-100
  publishes exit `4` for the verb.
  (5) **The close is on every path.** A refusal raised by the read closes the connection it opened before
  raising; on the success path the caller closes `probe.conn` in a `try/finally`. §7 gains one inline
  scenario for the exit-`4` input and rewrites `@REQ-F030-24`'s steps in place: 26 → **27** inline
  scenarios, §4.2 unchanged at 24 rows. No other signature, statement or count moves.

- spec_revision 14 (2026-11-20): **the probe is a symbol of this layer, and `require_fts5` runs on it and not
  inside `reindex` (HIFA-7, fourth review round, D-068).** `version` 3 → 4, because §2 gains a public
  symbol; §2.1, §4.1, §4.2, §6 and §7 carry the consequences.
  (1) **The probe is declared, not improvised.** SPEC-100 R21 printed
  `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)` inside `cli/commands/db.py`, which `@REQ-F030-05`
  forbids — no `sqlite3.connect` outside `connection.py` and `migrate.py` — and which this port could not
  offer, because the only read-only opens it published (`connect(readonly=True)`,
  `Database.ro_connection()`) apply the whole `PRAGMAS` tuple. §2.1 now declares **`PROBE_SQL`**,
  **`Probe(conn, tables)`** (revision 15 adds the second projection, `objects`) and **`open_probe(path)`**: URI `mode=ro` with `uri=True`, no pragma at all, one
  statement, `database_not_found` for a path that does not exist (nothing created) and `corrupt_database`
  for bytes SQLite refuses. Both edges call it — SPEC-100 R21 step (b) and §5.2 step 5 — and R1 says in the
  same breath that it is not a `connect()`.
  (2) **`require_fts5`'s placement is restated where it was unsatisfiable.** Four places here said it was
  "the first statement of `reindex(conn)`", and `reindex(conn)` is handed a connection it did not open, so
  the check could never precede the probe that R21 orders before the writable open. The rule is now one
  sentence: it runs in `migrate()` before the backup and the first `BEGIN`, and in `hifa db reindex` on the
  probe's connection, **immediately after `open_probe()` and before any writable statement exists**;
  `reindex(conn)` declares it as a precondition. Its signature does not move, because `db vacuum`
  (SPEC-090 R19) rebuilds on a connection it already holds.
  (3) **The claim about `connect(readonly=True)` is now the measured one.** §2.1 and SPEC-100 R21 said the
  tuple failed "only on a file that is not already in WAL", which reads as "so it is fine for our files".
  Whether it fails is a function of the **file's journal mode** — the one thing the probe was opened to find
  out — so the probe cannot be that function: it asks for the mode and for nothing else. Measured on SQLite
  3.53.1: a foreign file, a file made by hand with `sqlite3`, a 0-byte file and any database in `delete`
  mode answer `attempt to write a readonly database` (`Conflict(DB_READONLY)`, exit `4`, outside R21's
  exit set as revision 14 published it — `0/3/6`; revision 15 publishes `0/3/4/6` for the reason (4) of
  its own note); a database already in WAL accepts the whole tuple, which is precisely the case where applying
  it tells the caller nothing.
  §7 goes 25 → **26** inline scenarios (one for `open_probe`, `@REQ-F030-24`) and §4.2 goes 23 → **24**
  rows. No other signature, statement or count moves.
- spec_revision 13 (2026-11-20): **`fts5_unavailable` gets the one producer it was missing (HIFA-7 review
  round, D-065).** SPEC-050 §6 and SPEC-100 R21 published the code and sent the reader here for the check,
  and this document declared neither the class nor the test — §10 said the adapter "only exposes the
  `issue_fts` table as existing". `version` 2 → 3, because §2 gains a function: `require_fts5(conn)`, over
  the constant `FTS5_ENABLED_SQL` — `SELECT 1 FROM pragma_compile_options WHERE compile_options =
  'ENABLE_FTS5'`, one statement, no `PRAGMA` read twice and no virtual table created to find out — which
  raises `Fts5Unavailable(code='fts5_unavailable')` (§6) when the connection's SQLite carries no FTS5.
  §2.1 declares **where it runs and where it does not**: before the first `BEGIN` of a non-empty migration
  set (the schema creates a virtual table, so a build without the module would fail inside the
  transaction) and as `reindex(conn)`'s first statement, a placement revision 14 moved to the probe of
  `hifa db reindex` (§2.1, D-068); **not** in `connect()`, because a database whose
  index cannot be read is still searchable through the `like` path of SPEC-090 R14, and a connection that
  refused to open would take the rows away too. §5.1 gains the step, §6 gains the paragraph that says the
  error is *raised* and not translated (its cause is the binary, not a driver message), §10 stops denying
  the layer knows anything about FTS5 while keeping the search itself out of it, and §7 gains two
  scenarios and REQ-F030-23. No other signature, statement or count moves; §7 goes 23 → **25** inline
  scenarios.
- spec_revision 12 (2026-11-17): **the read carries the trash as a keyword, and the spec that owns the
  read says so (D-059).** `issue_detail` gains `include_trashed: bool = False` — keyword-only, defaulted,
  additive (§2.6) — and SQL-7 row 1's `WHERE` becomes `… AND (:include_trashed = 1 OR i.trashed = 0)`
  while its "Runs when" stays **always**. `False` binds `0`, the `OR` reduces to the predicate row 1
  already had, so a default read answers exactly what it answered before: `trashed` is `False`, a trashed
  reference answers `None`, and `tests/integration/db/test_reads_detail.py::test_a_trashed_issue_has_no_detail`
  keeps passing unchanged — the implementer **adds** the case for the other side instead of editing that
  one. `True` drops that one predicate and nothing else: the same sixteen statements in the same order,
  **12** with `DETAIL_INCLUDE` and **8** with an empty `include`, because **a predicate is not a
  statement**. What keeps the default is every surface that must not see the trash — every listing, the
  board, every count, and the row and card fragments (SPEC-070 §4 R29 answers `404` for a trashed
  reference); what passes the keyword is the detail page and the panel of a trashed issue (SPEC-070
  §2.1.1). **Nothing is removed:** no parameter leaves a signature, no statement leaves SQL-7, no row
  changes its "Runs when", and no existing caller has to change. `version` stays **2** because the change
  is additive and defaulted under D-026 — the shape `spec_revision` 11 ("`version` stays 2") and 5 both
  kept; rev 4 is the revision that *established* `version` 2, not one that kept it, so it is cited here
  for the D-026 rule and not as a precedent for the number. **Why this revision exists:** SPEC-040 R21
  (rev 24) and SPEC-070 (rev 23) were rewritten to promise the keyword, but this spec — the one that
  specifies `adapters/sqlite/reads.py`, and therefore the one an implementer follows — still declared
  `trashed` unconditionally `False` (the `IssueDetail` comment and the `issue_detail` docstring) and
  SQL-7 row 1 without a keyword, so the two halves of the corpus contradicted each other and no code
  could satisfy both. The owner carries the contract now, and
  D-059's **Touches** line names this spec so that the next revision of that decision cannot omit it
  again.
- spec_revision 11 (2026-11-16): **the detail read carries the archive flags and the team's states
  (HIFA-55).** `IssueDetail` gains `archived_at`, `trashed` and `states`, appended after `members` and
  defaulted (`None`, `False`, `()`), so the shape stays additive under D-026 and `version` stays 2.
  Two of them cost nothing: `archived_at` and `trashed` are columns of the `issue` row the read loads
  with `SELECT * FROM issue WHERE id = ?` (SQL-7 row 2) and were being fetched and dropped, so the flags
  were only reachable through a second connection. The third is one statement: SQL-7's set goes
  **11 → 12** with `include=DETAIL_INCLUDE` and **7 → 8** with an empty one — the twelfth statement is
  `SELECT … FROM workflow_state WHERE team_id = ? AND archived_at IS NULL ORDER BY position, name`
  (row 16), the statement `list_states` already ran through `StateRepo.list_for_team`, so the `<select>`
  offers the same columns in the same order. SQL-7 is rewritten as the statements the code executes, so
  REQ-F030-12, R5, §5.3, the §7 scenario and the §8 checklist each name the `include` they count. The
  flags are the layers of a row, not a visibility rule: `issue_detail` answers `None` for an issue the
  trash layer hides on **every default read**, so `archived_at` is the layer a render can see without
  asking, while `trashed` is `False` on every default read and may be `True` only on a read that passes
  `include_trashed=True` — the keyword `spec_revision` 12 adds above (D-059). The stored value is what
  makes that flag readable at all, so the render's gate reads one model instead of two; and the surfaces
  that keep the default — the row and card fragments, every listing, board and count — still cannot reach
  a trashed row.
  **Why a revision and not a `D-0NN`:** the three fields are read-model internals
  of this layer — no use case signature, endpoint, command, envelope or enum moves, and the two specs
  that consume them already say an edge takes a read model as §2.6 declares it (SPEC-040 R19) and
  never reads the database itself (A1/A3, P4). The corpus has resolved this exact shape twice already,
  additively and at this level: rev 4 (D-026) and rev 5 (`members`). A decision entry settles a
  conflict between specs or a trade-off a future reader must be able to re-derive; the second read
  was a violation of rules that exist, not a missing rule, so the correction is recorded here.
- spec_revision 10 (2026-11-13): **the blocked read is one query, and its predicate is cited, not
  restated (D-048).** §2.6 gains `blocked_view` and §2.5 gains SQL-10: the live issues that are the
  target of a `blocks` edge whose source's `state.type` is neither `completed` nor `canceled`. The
  projection is SQL-4's, the predicate is SPEC-020 §4.1 R7 read in the stored direction (`issue_id` is
  the blocker, D-045), and no column, flag or name `blocked_by` is involved (D-046). R5 counts it as
  one query and §2.1 exports it. `team_id=None` is the workspace-wide form. No existing signature,
  shape or code changed, so `version` stays at 2.

- spec_revision 9 (2026-11-13): **the repository publishes what the depth bound is measured against.** `subtree_height` is declared next to `ancestor_chain`, and `add_relation` is declared to apply the canonical pair before the `INSERT`: the order is a property of the writer, and the constraint only stands behind it.

- spec_revision 8 (2026-11-13): **the trash opens the archive axis, and the cursor token carries a
  lowercase direction.** (1) `trashed_only` no longer inherits the `archived_at IS NULL` predicate of
  SQL-4: the archive term accepts `:trashed_only = 1` as an alternative, so an issue archived and then
  trashed — `archived_at IS NOT NULL` and `trashed = 1`, the two independent layers of D-043 — is part
  of the trash listing instead of being excluded by the axis it left. The exception is stated once, in
  the predicate, and the archived-axis row of §2.6 carries the same reading; no field was added.
  (2) `next_cursor` is encoded with the lowercase direction (§2.6.2), which is the form `list_issues`
  validates against (SPEC-040 §5.3 step 4): the encoder no longer emits `due:DESC` while the validator
  accepts only `due:desc`, which made every cursor this layer produced unusable at the application
  layer. §2.6.1 keeps `dir` as the uppercase SQL keyword and says that the token is the only place the
  direction travels as `desc`/`asc`. (3) The `ListFilters` post-init rule names the error codes
  SPEC-040 §6 publishes (`LIMIT_RANGE`, `ORDER_INVALID`) instead of its own `INVALID_LIMIT` /
  `INVALID_ORDER`, and the codec lists a malformed ULID among the causes of `INVALID_CURSOR`, as
  SPEC-060 §2.3 does. No signature changed, so `version` stays at 2.
- spec_revision 7 (2026-11-13): **the position of the absent rows is not a direction (D-044).** (1) The
  `ORDER BY` gains a first term, `(<sort_key> IS NULL) ASC`, that `dir` never reaches: the rows with a
  value come first and the rows without one last in `ASC` **and** in `DESC`, so `--reverse` inverts the
  sequence of values and not the treatment of the absence. (2) `SORT_KEYS` stops encoding absence as a
  sentinel — `due` is `i.due_date` and `priority` is `CASE i.priority WHEN 0 THEN NULL ELSE i.priority
  END` — because a sentinel is a value that a descending direction moves, while `NULL` cannot enter the
  key comparison; `priority` 0 (`No priority`) is the absence of a priority on this same footing. (3)
  §2.6.1 declares the keyset predicate **per case**: form A for an anchor with a value (every absent
  row, or a later value, or the same value with a later `id`) and form B for an anchor without one
  (`<sort_key> IS NULL AND i.id <dir> :cursor_id`), each next to its `ORDER BY`, with the argument for
  why the predicate is the exact complement of the total order and a worked page sequence. SQL-4 stops
  publishing a row comparison `(<sort_key>, i.id) > (:cursor_key, :cursor_id)`, which returns no rows
  against a `NULL` anchor and ended the listing silently at the first undated issue. No signature
  changed, so `version` stays at 2.
- spec_revision 6 (2026-11-13): **the listing is expressible from a single object, and its order and
  cursor are fixed.** (1) §2.6 completes `ListFilters` with the eight fields the declared
  `issue list` flags need — `state_ids`, `state_types`, `priorities`, `is_open`, `no_cycle`,
  `archived_only`, `trashed_only`, `reverse` — all additive and defaulted, so the shape of the
  dataclass changes without a `version` bump (D-026). (2) The two combination rules that until now
  were only readable in SQL-4 are stated as contract: **OR** inside the state, state-type and priority
  families (the singular field is the one-element form of the same set), **AND** for `label_ids` and
  across every family. (3) §2.6.1 fixes the direction of the five orders: **ASC**, with `reverse`
  inverting the key and the `i.id` tie-breaker together — the keyset operator follows the same
  variable, so a page boundary can neither lose nor repeat a row. (4) §2.6.2 fixes what the cursor
  carries: the canonical `order:direction` id plus the last row's ULID, which is what makes a cursor
  from another order **or another direction** detectable as `INVALID_CURSOR`; D-011's payload has no
  third slot, so the anchor's sort value is resolved with one primary-key lookup and a cursor whose
  anchor is absent from the team is rejected instead of silently restarting. (5) `list_view` owns
  `next_cursor`: it is the layer that holds the last kept row. REQ-F030-10 carries the result.
  **Open conflict, not settled here:** inverting the pair also inverts where the sentinels land, so
  under `reverse` the undated rows and `No priority` come first, while SPEC-040 R18 states that `NULL`
  goes to the end in both directions. The readings agree on every other case and no acceptance
  scenario asserts the reversed one; it needs a `DECISIONS.md` entry, not a second definition here.
  (Resolved in `spec_revision` 7 by D-044: R18 wins and the sentinel keys are gone.)
- spec_revision 5 (2026-11-11): **`IssueDetail.members` declared (§2.6, D-026, D-029).** The
  dataclass gains `members: Mapping[str, str] = field(default_factory=dict)`: `member.id` →
  `display_name` for the authors of the comments and the history entries that `include` loaded,
  resolved by one `UNION` over `comment.author_id` and `issue_history.actor_id` instead of a query
  per row. It is additive and defaulted like every other D-026 field — the shape of a §2 dataclass
  is contract, so the field could not stay only in the code. The `issue_detail` docstring is
  corrected at the same time: the panel does ask for the comments (SPEC-070 §2.1.4, D-029) and an
  unknown `include` name raises `INCLUDE_INVALID`, which SPEC-040 §2.2 and §6 already declared.
- spec_revision 4 (2026-11-11): **§2.6 completed and bumped to `version` 2 (D-025, D-026, D-027).**
  (1) `ListFilters.q: str | None = None` is declared as a **title filter** — `LIKE '%term%'` over
  `i.title` with `ESCAPE` and the `%`/`_`/`\` of the term escaped — which closes the contradiction
  with SPEC-070 §2.1.1 (D-025). (2) `IssueListItem` gains `state_color`, `sort_order`, `board_order`
  and `created_at`, and `BoardGroup` gains `color` and `truncated`, all declared, typed, appended at
  the end and defaulted: additive, no behaviour change, but the shape of a §2 dataclass is contract
  (D-026). (3) `board_view` takes `filters: ListFilters | None = None` like `list_board` of SPEC-040 so
  that a filter never travels as a loose kwarg (D-027); `issue_detail(conn, ref, *, include=...)` is
  declared with the `include` gate the dashboard already used. (4) The read functions the edges
  resolve through are declared: `team_by_ref`, `list_teams`, `team_counts`, `local_member` and
  `resolve_filters`. (5) Fixed here the board's pagination note: `BoardGroup.truncated` means "this
  column has more", not a cursor.
- spec_revision 3 (2026-11-11): translated to English (repo-wide language convention).


