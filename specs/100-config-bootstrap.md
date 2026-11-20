---
id: SPEC-100
title: Configuration and bootstrap
component: support
status: review
version: 7
spec_revision: 14
depends_on: [SPEC-000, SPEC-010, SPEC-040]
implements:
  - src/hifa/config.py
  - src/hifa/app/teams.py
  - src/hifa/cli/commands/db.py
  - tests/**/config*
  - tests/**/doctor*
acceptance: specs/acceptance/100-config-bootstrap.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-20
---

# SPEC-100 — Configuration and bootstrap

## 1. Responsibility and boundaries

**Does:** resolves the database path deterministically (`--db` → `HIFA_DB` → `.hifa.toml` →
XDG → home), parses and validates `.hifa.toml`, seeds a usable workspace with `hifa init`
(workspace + team + 7 workflow_states + labels + local member + settings), makes that seeding
idempotent and diagnoses the database with `hifa doctor` (integrity, FKs, orphans, identifiers,
`user_version`, size, permissions, FTS).

**Does NOT do (explicit):**

- It does not define the schema: tables, columns and `PRAGMA user_version` belong to SPEC-010. `hifa init` does
  not create tables: the migrator (SPEC-030) creates them before seeding.
- **There is no `settings` table.** The "settings" seeded by `hifa init` are (a) the
  `.hifa.toml` file (`[cli]`, `[web]`) and (b) `team` columns (`cycles_enabled`,
  `issue_estimation_type`, `triage_enabled`, `cycle_duration`, `cycle_start_day`, `visibility`).
  Inventing a new table would break SPEC-010 and is forbidden in this spec.
- It does not implement migrations: `hifa doctor` **reads** `user_version` and the migrations
  folder; it does not apply them. Applying them belongs to `hifa db migrate` (SPEC-030).
- It repairs nothing: `hifa doctor` reports; `hifa db reindex` is the only corrective action this
  spec defines (it recreates the index if it is gone and then rebuilds it, R21), and it touches no
  business data — no `issue` row and no `issue_history` row moves.
- It does not manage multiple workspaces: one database file = one workspace (1 row in `workspace`).
- It does not resolve credentials, profiles or API/server environment variables (SPEC-060).
- It does no recursive `chown` or `chmod`: only the database file (0600) and its directory (0700).

## 2. Public contract

### 2.1 Types and signatures — `src/hifa/config.py`

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping

DEFAULT_DB_FILENAME: str = "hifa.db"
CONFIG_FILENAME: str = ".hifa.toml"
APP_DIRNAME: str = "hifa"
MAX_WALK_UP: int = 32
DEFAULT_TEAM_KEY: str = "ENG"
DEFAULT_TEAM_NAME: str = "Engineering"
DEFAULT_ICON: str = "🚀"
DEFAULT_COLOR: str = "#5E6AD2"
DEFAULT_TIMEZONE: str = "America/Santiago"
DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 7777

@dataclass(frozen=True)
class TeamConfig:
    key: str | None = None
    name: str | None = None
    icon: str | None = None
    color: str | None = None
    timezone: str | None = None

@dataclass(frozen=True)
class CliConfig:
    default_team: str | None = None

@dataclass(frozen=True)
class WebConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

@dataclass(frozen=True)
class Config:
    db_path: Path
    source: Literal["flag", "env", "file", "xdg", "home"]
    config_file: Path | None
    workspace_name: str | None
    url_key: str | None
    team: TeamConfig
    cli: CliConfig
    web: WebConfig
    warnings: tuple[str, ...] = ()

@dataclass(frozen=True)
class ConfigFile:
    path: Path
    workspace_name: str | None
    db: str | None
    url_key: str | None
    team: TeamConfig
    cli: CliConfig
    web: WebConfig
    warnings: tuple[str, ...] = ()

class ConfigError(Exception):
    code: str          # config_invalid_toml | config_invalid_value | config_unreadable
    field: str | None  # "team.key", "web.port", …

def find_config_file(start: Path) -> Path | None
def find_project_root(start: Path) -> Path | None
def load_config_file(path: Path) -> ConfigFile
def resolve_config(
    *,
    db_flag: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Config
def resolve_db_path(
    *,
    db_flag: str | None,
    env: Mapping[str, str],
    cwd: Path,
) -> tuple[Path, Literal["flag", "env", "file", "xdg", "home"], Path | None]
def write_config_file(path: Path, config: Config) -> None
def slugify(text: str) -> str
def validate_timezone(name: str) -> str
```

`resolve_config` is **pure with respect to the DB**: it opens no connections, creates no directories and writes
nothing. The creation of the directory and the file happens in `hifa init` (§5.1).

`find_project_root(start)` returns the nearest ancestor of `start` — `start` included — that contains
`.git`, or `None` when there is none. `.git` is a **directory** in a normal clone and a **file**
(`gitdir: …`) in a linked work tree or a submodule, so the test is existence, never `is_dir()`.
It is the single notion of project root in this module: `find_config_file` bounds its walk-up with
it (R3) and the write guard of `hifa init` consumes it (R13, D-041). It reads no file and resolves
no symlink beyond `Path.resolve()` on `start`.

### 2.2 Path resolution order

It is applied in this order, and the first one that produces a path wins. `source` says which one won.

| Order | Source (`source`) | Rule |
|---|---|---|
| 1 | `flag` | `--db <path>`. Empty or absent is ignored. `~` is expanded. If relative, it is resolved against the **cwd**. |
| 2 | `env` | `HIFA_DB`. `strip()` is applied; empty is ignored. Same expansion rules as the flag, relative to the cwd. |
| 3 | `file` | `.hifa.toml` found walking up from the cwd (§2.4). If it declares `[workspace] db`, it is resolved **relative to the directory of the `.hifa.toml`**. If it does not declare it, the path is `<dir of .hifa.toml>/.hifa/hifa.db`. |
| 4 | `xdg` | `${XDG_DATA_HOME}/hifa/hifa.db`, only if `XDG_DATA_HOME` is defined and not empty. |
| 5 | `home` | `~/.local/share/hifa/hifa.db`. It is the XDG standard default when the variable does not exist; it is reported as `home` to distinguish "variable set" from "fallback". |

Normative details:

- The final path is always **absolute and resolved** (`Path.resolve()`), with no `..` or symlinks in the
  result. It is not required to exist (`strict=False`).
- `--db` and `HIFA_DB` with the value `":memory:"` produce `Path(":memory:")` unresolved and the
  corresponding `source`; `hifa init` rejects it with `in_memory_not_supported` (an ephemeral
  database cannot be seeded usefully).
- A `--db` pointing to an existing directory ⇒ `ConfigError(config_invalid_value, "db")`.
- The environment variable has **lower** priority than the flag and **higher** than the file: so
  `HIFA_DB=/tmp/x.db hifa issue list` works from any directory.
- None of the 5 sources does `mkdir`. If the parent directory does not exist, whoever writes (init,
  import, migrator) creates it.

### 2.3 `.hifa.toml` format

```toml
[workspace]
name = "Ginkelte"
db = ".hifa/hifa.db"      # relative to the directory of this file
url_key = "ginkelte"

[team]
key = "ENG"
name = "Engineering"
icon = "🚀"
color = "#5E6AD2"
timezone = "America/Santiago"

[cli]
default_team = "ENG"

[web]
host = "127.0.0.1"
port = 7777
```

| Section | Key | Type | Default | Validation (on failure ⇒ `config_invalid_value`) |
|---|---|---|---|---|
| `[workspace]` | `name` | string | `basename(cwd)` | 1..80 characters, no `\n` |
| `[workspace]` | `db` | string | `".hifa/hifa.db"` | not empty; resolved relative to the `.hifa.toml` |
| `[workspace]` | `url_key` | string | `slugify(name)` | `^[a-z0-9][a-z0-9-]{0,62}$` |
| `[team]` | `key` | string | `"ENG"` | `^[A-Z][A-Z0-9]{1,9}$` (2-10 characters, uppercase) |
| `[team]` | `name` | string | `"Engineering"` | 1..80 characters |
| `[team]` | `icon` | string | `"🚀"` | 1..8 characters, no spaces or line breaks |
| `[team]` | `color` | string | `"#5E6AD2"` | `^#[0-9A-Fa-f]{6}$` |
| `[team]` | `timezone` | string | `"America/Santiago"` | present in `zoneinfo.available_timezones()` |
| `[cli]` | `default_team` | string | `[team].key` | same pattern as `team.key` |
| `[web]` | `host` | string | `"127.0.0.1"` | IPv4, IPv6 or hostname; not empty |
| `[web]` | `port` | integer | `7777` | `1..65535` |

- **There is no member key in the file.** The default member is resolved only through `--member`
  or `HIFA_MEMBER` (SPEC-050 §2.2.3); there is no `[cli].default_member` and no `[defaults] member`. A
  `.hifa.toml` with that key enters through the unknown-key rule (`unknown_key`, warning).
- This is the **only** `.hifa.toml` format: SPEC-050 §2.2.2/§2.2.3/§2.2.4 references this table
  and declares no sections of its own (`[defaults]` does not exist).

- Parsing with `tomllib` (Python 3.11 stdlib, no new dependency: SPEC-000 REQ-N007). A malformed
  file ⇒ `config_invalid_toml` with the line in the message.
- An **unknown** section or key does not break anything: it is added to `Config.warnings` as
  `unknown_key: <section>.<key>` and ignored. This allows a `.hifa.toml` from a future version
  not to freeze an old binary.
- All keys are optional: an empty `.hifa.toml` is valid and equivalent to the defaults.
- `[web].port` as a string (`port = "7777"`) is `config_invalid_value` (no implicit coercion of
  TOML types is done).

### 2.4 File search (walk-up)

`find_config_file(start)` walks up from `start` (resolved) to the root `/`, checking at each level
`<dir>/.hifa.toml`, with a maximum of 32 levels. It returns the first one that exists and is readable; if
none exists, `None`. An existing but unreadable file (`PermissionError`) ⇒
`ConfigError(config_unreadable)` instead of silently continuing to walk up: an unreadable
`.hifa.toml` is a configuration error, not a missing file.

### 2.5 `src/hifa/app/teams.py` — bootstrap

```python
# Default states: (name, type, color, position, is_default)
DEFAULT_STATES: tuple[tuple[str, str, str, float, int], ...] = (
    ("Triage",      "triage",    "#6B6F76", 1.0, 0),
    ("Todo",        "unstarted", "#E2E2E2", 2.0, 1),
    ("Backlog",     "backlog",   "#6B6F76", 3.0, 0),
    ("In Progress", "started",   "#F2C94C", 4.0, 0),
    ("In Review",   "started",   "#BB87FC", 4.5, 0),
    ("Done",        "completed", "#5E6AD2", 5.0, 0),
    ("Canceled",    "canceled",  "#95A2B3", 6.0, 0),
)

# Default labels: (name, color), workspace scope (team_id NULL)
DEFAULT_LABELS: tuple[tuple[str, str], ...] = (
    ("Bug",         "#EB5757"),
    ("Feature",     "#BB87FC"),
    ("Improvement", "#4EA7FC"),
    ("Docs",        "#95A2B3"),
)

@dataclass(frozen=True)
class BootstrapOptions:
    team_key: str = DEFAULT_TEAM_KEY
    team_name: str = DEFAULT_TEAM_NAME
    icon: str = DEFAULT_ICON
    color: str = DEFAULT_COLOR
    timezone: str = DEFAULT_TIMEZONE
    workspace_name: str | None = None      # None => basename(cwd) with the first letter uppercased
    seed_labels: bool = True
    seed_member: bool = True
    force: bool = False
    member_name: str | None = None         # None => getpass.getuser()
    member_email: str | None = None        # None => os.environ["HIFA_ME_EMAIL"] or NULL

@dataclass(frozen=True)
class BootstrapResult:
    created: list[str]                     # ["workspace", "team", "workflow_state:Todo", "label:Bug", "member", "team_membership"]
    skipped: list[str]
    workspace_id: str
    team_id: str
    member_id: str | None
    db_path: Path
    already_initialized: bool              # True iff a `workspace` row existed before the seeding (R14, D-068)

def bootstrap(
    uow: UnitOfWork,
    opts: BootstrapOptions,
    *,
    clock: Clock,
    ids: IdGen,
) -> BootstrapResult
```

### 2.6 `src/hifa/cli/commands/db.py` — doctor

```python
from typing import Literal

SEVERITY_RANK: dict[str, int] = {"ok": 0, "info": 0, "warning": 1, "critical": 2, "skipped": 0}

@dataclass(frozen=True)
class CheckResult:
    name: str                 # "integrity_check", "foreign_keys", "orphans", "identifiers",
                              # "schema_version", "file_size", "permissions", "fts_integrity", "counts"
    severity: Literal["ok", "info", "warning", "critical", "skipped"]
    count: int                # affected rows; 0 when it is a boolean check
    detail: str               # human-facing text, one line

@dataclass(frozen=True)
class DoctorReport:
    db_path: Path
    file_size_bytes: int
    user_version: int
    migrations_max: int
    checks: list[CheckResult]

def doctor(conn: sqlite3.Connection, *, db_path: Path, migrations_dir: Path) -> DoctorReport
def migrations_max_version(migrations_dir: Path) -> int
# reindex(conn): R21 steps (a) and (b) are its preconditions, and they run in this module before it is
# called — open_probe(db_path) (SPEC-030 §2.1: URI mode=ro, uri=True, NO pragma of PRAGMAS, one statement
# partitioned into probe.tables and probe.objects), the verdict on probe.tables (no `workspace` TABLE ->
# NotAHifaDatabase, exit 3, nothing written; a path that does not exist -> DatabaseNotFound, exit 3,
# nothing created; bytes SQLite refuses -> CorruptDatabase, exit 6, from the PROBE_SQL read; a WAL file
# in a directory it cannot write -> Conflict(rule='DB_READONLY', code='DB_READONLY'), exit 4, same read)
# and require_fts5(probe.conn) -> fts5_unavailable, exit 6; the whole of (b) runs in a try/finally whose
# last act is probe.conn.close(), on the refusal paths too.
# Only then is `conn` opened, and reindex runs ONE writable transaction with the exact sequence of R21 —
# CREATE VIRTUAL TABLE IF NOT EXISTS, DROP TRIGGER IF EXISTS x3, CREATE TRIGGER x3, VALUES('rebuild') —
# returning the count of `issue` rows. reindex opens nothing, probes nothing and checks nothing: it receives
# a connection the caller has already identified and cleared (D-068).
def reindex(conn: sqlite3.Connection) -> int
```

`doctor` is read-only except for the FTS5 `integrity-check` command, which runs inside a
transaction that **always** rolls back (`BEGIN; INSERT INTO issue_fts(issue_fts)
VALUES('integrity-check'); ROLLBACK`). With the database opened in `ro` mode, that check is marked
`skipped` with `detail = "read-only database"` and is not a finding.

### 2.7 CLI surface

| Command | Flags | Output (`--json`) | Exit codes |
|---|---|---|---|
| `hifa init` | `--name TEXT` (workspace; default `basename(cwd)`), `--team-key TEXT` (default `ENG`), `--team-name TEXT` (default `Engineering`), `--icon TEXT` (default `🚀`), `--color TEXT` (default `#5E6AD2`), `--timezone TEXT` (default `America/Santiago`), `--no-labels`, `--no-member`, `--force`, `--db PATH` (global), `--json` | `BootstrapResult` | 0, 2, 4 |
| `hifa doctor` | `--db` (global), `--json`, `--verbose` | `DoctorReport` | 0, 1, 2, 3 |
| `hifa db path` | `--db` (global), `--json` | `{"db_path": "...", "source": "file", "config_file": "..."}` | 0, 2 |
| `hifa db reindex` | `--db` (global), `--json`, `--quiet` | `{"reindexed": 10000}` | 0, 3, 4, 6 |

`hifa db reindex` is the row with four failure exits: `database_not_found` and
`not_a_hifa_database` leave at `3`, `DB_READONLY` at `4` (a database already in WAL inside a directory
the process cannot write: the read-only probe cannot create its `-shm`), `fts5_unavailable` and
`corrupt_database` at `6` (§6, R21). This table is the **only** row
for `init`, `doctor` and `db`; SPEC-050 §2.5.1/§2.5.5 copies it literally (D-013). `--name` is the workspace name (that flag, not `--workspace-name`); the
`--json` of `init` is the `BootstrapResult` of §2.5, not an ad-hoc object.

Human output of `init`:

```
initialized hifa at /home/vicho/proj/.hifa/hifa.db
  workspace  Ginkelte (ginkelte)
  team       ENG — Engineering 🚀
  states     Triage, Todo, Backlog, In Progress, In Review, Done, Canceled
  labels     Bug, Feature, Improvement, Docs
  member     vicho <vicho@local>
  config     /home/vicho/proj/.hifa.toml (created)
next: hifa issue create "My first issue" --team ENG
```

Second run:

```
hifa is already initialized at /home/vicho/proj/.hifa/hifa.db
  workspace Ginkelte · team ENG · 0 changes
```

The `config` field of the `init` report takes exactly four shapes, and the fourth is not a path:

| Value | When |
|---|---|
| `<path> (created)` | the file did not exist in the work tree and `init` wrote it (R13) |
| `<path> (existing)` | a `.hifa.toml` exists in the cwd or in an ancestor below the project root and was left untouched (R13) |
| `<path> (rewritten)` | `--force` rewrote an existing file, leaving `<path>.bak` (R12) |
| `none (outside a git work tree)` | the cwd has no `.git` in itself or in any ancestor, so nothing was written and the database is the one resolution produced (R13) |

Outside a work tree the human block is therefore:

```
initialized hifa at /home/vicho/.local/share/hifa/hifa.db
  workspace  Tmp (tmp)
  team       ENG — Engineering 🚀
  states     Triage, Todo, Backlog, In Progress, In Review, Done, Canceled
  labels     Bug, Feature, Improvement, Docs
  member     vicho <vicho@local>
  config     none (outside a git work tree)
next: hifa issue create "My first issue" --team ENG
```

`--json` is unaffected: the envelope is the `BootstrapResult` of §2.5, which carries `db_path` and
not the config file. `--quiet` prints the three IDs and no report.

Human output of `doctor`: a `CHECK | SEVERITY | COUNT | DETAIL` table and a footer
`4 ok, 1 warning, 0 critical` (it never prints `ok` in the footer if there is a `critical`).

## 3. Data it touches

### 3.1 What `hifa init` seeds (exact)

| Entity | Rows | Values |
|---|---|---|
| `workspace` | 1 | `id = ULID`, `name = opts.workspace_name or title(basename(cwd))`, `url_key = slugify(name)`, `created_at = clock.now()` |
| `team` | 1 | `key = opts.team_key`, `name = opts.team_name`, `icon = opts.icon`, `color = opts.color`, `timezone = opts.timezone`, `visibility = 'public'`, `cycles_enabled = 0`, `cycle_duration = 2`, `cycle_start_day = 1`, `issue_estimation_type = 'fibonacci'`, `triage_enabled = 0`, `parent_id = NULL`, `archived_at = NULL` |
| `workflow_state` | 7 | the 7 tuples of `DEFAULT_STATES`, in that `position`; exactly one with `is_default = 1` (Todo) |
| `label` | 4 or 0 | the 4 from `DEFAULT_LABELS` with `team_id = NULL` (workspace scope, SPEC-010 R6); 0 if `--no-labels`. A name the workspace already carries **in another case** is not written a second time: the step resolves case-insensitively, keeps the row it found and reports `label:<seed name>` as skipped (R11bis, SPEC-040 R22) |
| `member` | 1 or 0 | `name = member_name`, `display_name = member_name`, `email = member_email`, `initials` = first 2 letters uppercased, `timezone = opts.timezone`, `is_admin = 1`, `is_active = 1`, `created_at = clock.now()`; 0 if `--no-member` |
| `team_membership` | 1 or 0 | `(team_id, member_id, is_owner = 1, sort_order = 0)`; 0 if `--no-member` |

Invariants that `hifa init` guarantees on completion:

1. `SELECT COUNT(*) FROM workspace` = 1.
2. Exactly one `team` exists with `key = opts.team_key` in that workspace.
3. There are exactly 7 non-archived `workflow_state` rows covering the six `type`s of
   `('triage','backlog','unstarted','started','completed','canceled')`, with `started` carried twice
   (`In Progress`, `In Review`), and exactly one with `is_default = 1`.
4. `SELECT COUNT(*) FROM workflow_state WHERE team_id <> <team>` = 0 right after initialization.
5. The local member is the **first** by `ORDER BY created_at, id` (SPEC-090 depends on that for
   `assignee:me`).
6. `PRAGMA user_version` = 1 (left by the migrator, not by `init`).
7. The database file has mode `0600` and its directory `0700`.

### 3.2 Tables read by the checks

| Table | Check | Invariant verified |
|---|---|---|
| `workspace`, `team` | C5 | 1 workspace; `UNIQUE(workspace_id, key)` |
| `issue` | C3, C4 | `UNIQUE(identifier)`, `UNIQUE(team_id, number)`, FK of team/state/member/project |
| `workflow_state` | C3 | `issue.state_id` belongs to the issue's `team_id` (SPEC-010 R4) |
| `member`, `project`, `cycle`, `issue_label` | C3 | FKs declared in SPEC-010 |
| `issue_fts` | C8 | `integrity-check` of the external content |
| `sqlite_master` + `PRAGMA user_version` | C1, C5 | applied schema vs. available migrations |

## 4. Business rules

### 4.1 Configuration resolution

- **R1 — Precedence (REQ-F100-01).** The 5 sources of §2.2 are evaluated in strict order and the
  first one that produces a path wins. There is no merge between sources: if `--db` is present, `HIFA_DB` and the
  `.hifa.toml` are **not** consulted for the path (they are read for `team`/`cli`/`web`, which are
  independent of the path).
- **R2 — Value normalization (REQ-F100-02).** `--db` and `HIFA_DB` are processed the same way:
  `strip()` → if empty, the source is ignored → `expanduser()` → if not absolute, it is joined to the
  `cwd` → `resolve()`. A value pointing to an existing directory ⇒
  `ConfigError(config_invalid_value, field="db")`. `":memory:"` is preserved literally (without
  `resolve()`) and `source` is reported normally.
- **R3 — Walk-up (REQ-F100-03).** `find_config_file` starts from the resolved `cwd` and walks up
  looking for `.hifa.toml`, bounded by `find_project_root` (§2.1): it stops at the first ancestor
  that contains `.git` and never climbs above it, so a config that sits above the project root is
  not consulted. Without a `.git` ancestor the historical cap of 32 levels and the filesystem root
  apply. It does not follow symlinks outside the tree (it uses the
  resolved directory). An unreadable `.hifa.toml` ⇒ `config_unreadable`, without continuing the search.
- **R4 — Path relative to the file (REQ-F100-04).** If the `.hifa.toml` declares `[workspace] db`, the
  path is resolved against the **directory of the file**, not against the cwd. A `db = ".hifa/hifa.db"`
  in `/repo/.hifa.toml` points to `/repo/.hifa/hifa.db` even if the command is run from
  `/repo/src/deep/`. If the file does **not** declare `db`, the path is `<dir>/.hifa/hifa.db` (project-local
  default), not the XDG one: a project with a `.hifa.toml` never writes to `~/.local/share`.
- **R5 — XDG and home (REQ-F100-05).** Only if there is no file in the tree: `XDG_DATA_HOME` not empty ⇒
  `$XDG_DATA_HOME/hifa/hifa.db` (`source="xdg"`); otherwise ⇒ `~/.local/share/hifa/hifa.db`
  (`source="home"`). A relative `XDG_DATA_HOME` is treated as invalid and ignored (the XDG spec requires
  an absolute path) with a warning in `Config.warnings`.
- **R6 — TOML validation (REQ-F100-06).** Each key is validated with the table of §2.3. An invalid
  value ⇒ `ConfigError(config_invalid_value)` with `field` = `"<section>.<key>"` and a message that
  includes the received and the expected value. A `TOMLDecodeError` ⇒ `config_invalid_toml` with the line
  number. Unknown sections/keys ⇒ warning `unknown_key: <section>.<key>`, never an error.
- **R7 — Purity (REQ-F100-07).** `resolve_config`, `load_config_file`, `find_config_file` and
  `resolve_db_path` do not create directories, do not open the database, do not write files and do not read
  `os.environ` when they are given an explicit `env=`. The test runs them with `env={}` and a `cwd` of
  `tmp_path` to verify that there are no effects.
- **R8 — `slugify` (REQ-F100-08).** NFKD → removes diacritics → lowercase → everything that is not
  `[a-z0-9]` becomes `-` → collapses repeated `-` → trims `-` from the ends → truncates to 63
  characters. If the result is empty, it returns `"workspace"`. `"Hifa Dev"` ⇒ `"hifa-dev"`;
  `"Ñandú"` ⇒ `"nandu"`.

### 4.2 `hifa init`

- **R9 — Mandatory order (REQ-F100-09).** `init` does, in this order: (1) resolve config,
  (2) validate flags, (3) create the database directory with `0700`, (4) open the connection and apply
  pending migrations (SPEC-030; `user_version` stays at 1), (5) detect existing state,
  (6) seed in **one** transaction, (7) set the file permissions to `0600`, (8) write
  `.hifa.toml` if applicable. It never seeds before migrating.
- **R10 — Exact seeding (REQ-F100-10).** The values of §3.1 are literal: 7 states with those
  names, types, colors and positions; 4 workspace-wide labels; team with `visibility='public'` and
  `issue_estimation_type='fibonacci'`. The IDs are new ULIDs (`ids.new()`), never derived from the
  name. `created_at`/`updated_at` come from `clock.now()` in ISO-8601 UTC.
- **R11 — Idempotency (REQ-F100-11).** Each seeding step is *get-or-create* by natural key:
  `workspace` by `url_key`, `team` by `(workspace_id, key)`, `workflow_state` by
  `(team_id, name)`, `label` by `(workspace_id, NULL, name)`, `member` by `email` if there is an email or
  by `display_name` if not, `team_membership` by `(team_id, member_id)`. Existing ones are added
  to `skipped` and are **not** updated. A second run with the same flags returns
  `created=[]`, `already_initialized=True` and exit 0.
- **R11bis — The seed writes no second spelling of a label name (D-070).** The `label` step of R11 is the
  one natural key of the seeding that is **not** an exact match: the four names of `DEFAULT_LABELS` are
  resolved **case-insensitively** over the rows with `team_id IS NULL`, because SPEC-040 R22 refuses a
  second spelling of a name at one level and this seed is a writer of that table. A row that matches a
  seed name in another case **is** that entry: it keeps its own spelling, is not updated, and the entry
  joins `skipped` under the seed's own string (`label:Bug`). The seed therefore **skips and never
  refuses** — a database whose labels are legal must not make `init` fail, and a refusal here would
  answer exit `4` on a file `init` had seeded itself after a legal rename, an outcome R9's order and the
  exit set `0,2,4` do not describe. The third behaviour is the one forbidden: writing `Bug` beside
  `bug`.
- **R12 — `--force` (REQ-F100-12).** It runs the seeding again to create whatever is missing (for
  example, a state deleted by hand, or the labels if the first run used `--no-labels`), **without**
  touching existing rows and **without** deleting issues. Inside a work tree it also rewrites
  `.hifa.toml` even if it exists, leaving a copy of the previous one in `.hifa.toml.bak` (overwriting
  the earlier copy). Outside a work tree `--force` lifts nothing: R13 writes no file there, so there
  is nothing to rewrite and no `.bak` appears. `--force` is not equivalent to reset: there is no
  reset flag in v1.
- **R13 — `.hifa.toml` (REQ-F100-13).** `init` writes the file **only when the cwd is inside a git
  work tree** — some ancestor of the cwd, the cwd included, contains `.git` (`find_project_root` of
  §2.1, which is also the bound of the walk-up of R3). Outside a work tree it writes **nothing**:
  it does not fail, it keeps exit code `0`, and it reports `config: none (outside a git work tree)`
  (§2.7). Inside a work tree it writes the file only if no `.hifa.toml` exists in the cwd or in an
  ancestor below the project root; if one exists, nothing is written and the report says so
  (`config: /repo/.hifa.toml (existing)`). The guard concerns the **write** and never the
  resolution: `--db`, `HIFA_DB` and an ancestor `.hifa.toml` keep deciding the database exactly as
  in R1, so outside a work tree the effective base is the global one of R5 unless one of those
  sources says otherwise. The generated content is the one in §2.3 with the effective values, with
  `db` as a path **relative** to the file's directory when the database is inside that tree, and
  absolute when it is not.
- **R14 — Existing database detection (REQ-F100-14).** Three cases, decided by §5.2 step 5, **before**
  the migrator of step 6 runs: (a) a nonexistent file — or an existing file whose `sqlite_master` holds
  no table and no view at all, i.e. a file that has never carried a schema — ⇒ it is created and seeded,
  and `already_initialized` is **`False`**; (b) existing with a `workspace` **row** ⇒ `already_initialized`
  is **`True`** and only what is missing is completed; (c) existing, carrying schema objects and **no**
  `workspace` table (it is not a hifa database) ⇒ `not_a_hifa_database` (exit 2), refused without writing a
  byte and without a migration having run. A fourth file — the hifa schema with an **empty** `workspace`,
  which is what a migration that ran without a seed leaves behind — is none of the three: it is (a)'s
  outcome by another route (step 6 has nothing to apply, step 8 seeds) and answers `already_initialized`
  `False`.
  `--force` skips the probe of (c) — it is the override §6's row names, and the only way to seed a file
  that already holds another tool's tables. A `user_version` greater than the binary's maximum migration
  ⇒ `schema_too_new` (exit 4), answered by the migrator at step 6, so a hifa database from a newer binary
  passes step 5 (it carries `workspace`) and is refused there.

  **`already_initialized` is about a row, never about a table (D-068).** It is `True` iff a `workspace`
  **row** existed when step 7 read the database, and `False` otherwise — including every file `init`
  created or migrated itself. The table-only reading was underdetermined and wrong in both directions: it
  made a **first** `init` report `true` (the migrator of step 6 creates the table, so "the table exists"
  is true of every database that reaches step 7, and R14(a)'s "created and seeded" would contradict it),
  and it would have made the flag mean "a file was already here", which is the fact `init` already reports
  through `config` and `created`. The four inputs and the one answer:

  | The file at `db_path`, before `init` runs | step 5 (probe) | step 7 (`SELECT 1 FROM workspace LIMIT 1`) | `already_initialized` |
  |---|---|---|---|
  | does not exist | nothing to probe; step 6 creates it | no row (0 rows) | `False` |
  | exists, 0 bytes, no objects | `tables == frozenset()` ⇒ proceed (R14a) | no row (0 rows) | `False` |
  | hifa schema, `workspace` **empty** (migrated, never seeded) | `workspace` ∈ `tables` ⇒ proceed | no row (0 rows) | `False` |
  | a `workspace` row exists (seeded) | `workspace` ∈ `tables` ⇒ proceed | one row | `True` |
- **R15 — Flag validation (REQ-F100-15).** `--team-key` must match
  `^[A-Z][A-Z0-9]{1,9}$` **after** `strip().upper()`; if the user passes `--team-key eng`, it is
  normalized to `ENG` (not an error). `--timezone` must exist in `zoneinfo.available_timezones()`
  (it accepts `UTC`). `--icon` 1..8 characters without spaces. `--color` with the hex pattern. `--team-name`
  1..80. Any violation ⇒ `ConfigError(config_invalid_value)` and exit 2 **before** touching the
  database.
- **R16 — Permissions (REQ-S100-01).** The database directory is created with `0o700` (or respected if
  it already exists and is more restrictive). The file is created and, at the end of `init`, forced to `0o600`
  with `os.chmod` whether `init` created it or it already existed (hardening an existing file is
  intentional and is reported). The `.hifa.toml` is written with `0o600` as well, because
  it may contain the user's absolute paths.
- **R17 — Non-writable directory (REQ-F100-16).** If the parent directory cannot be created or is not
  writable, `init` fails with `db_dir_not_writable` (exit 2) and a message with the path and the
  cause (`EACCES`/`EROFS`). It leaves no partial files: the database is created first in
  `<dir>/.hifa.db.tmp` and renamed with `os.replace` only if the seeding finished well.

### 4.3 `hifa doctor`

- **R18 — Check catalog (REQ-F100-17).** All 9 always run, in this order, and each one
  produces exactly one `CheckResult`:

  | # | `name` | Query / operation | `critical` when | `warning` when |
  |---|---|---|---|---|
  | C1 | `integrity_check` | `PRAGMA integrity_check` | the result ≠ `ok` | — |
  | C2 | `foreign_keys` | `PRAGMA foreign_key_check` | returns ≥ 1 row | — |
  | C3 | `orphans` | 5 `LEFT JOIN … WHERE … IS NULL`: issue without team, issue with a `state_id` from another team, issue with a nonexistent `assignee_id`, issue with a nonexistent `project_id`, `issue_label` with a nonexistent `issue_id` | count > 0 | — |
  | C4 | `identifiers` | `GROUP BY identifier HAVING COUNT(*) > 1`; `GROUP BY team_id, number HAVING COUNT(*) > 1`; `identifier <> team.key \|\| '-' \|\| number` | count > 0 | — |
  | C5 | `schema_version` | `PRAGMA user_version` vs. `migrations_max_version(migrations_dir)` | `user_version > max` (database newer than the binary) | `user_version < max` (pending migrations) |
  | C6 | `file_size` | `os.stat(db_path).st_size` + size of the `-wal` | — | > 512 MB (suggest `VACUUM`) |
  | C7 | `permissions` | `stat.st_mode & 0o777` of the file and of the directory | — | file ≠ `0600` or directory ≠ `0700` (or more permissive) |
  | C8 | `fts_integrity` | `BEGIN; INSERT INTO issue_fts(issue_fts) VALUES('integrity-check'); ROLLBACK` | `database disk image is malformed` exception, or `issue_fts` missing (`critical`, detail `fts index is missing — run "hifa db reindex"`: `reindex` recreates the virtual table, R21) | the index and the table disagree (`warning`, detail `fts index out of sync — run "hifa db reindex"`) |
  | C9 | `counts` | `COUNT(*)` of `workspace, team, workflow_state, label, member, project, cycle, issue, comment` | — | — (always `info`) |

- **R19 — Exit code (REQ-F100-18).** `0` if there is no `warning` or `critical`; `1` if there is at least one
  `warning` and no `critical`; `2` if there is any `critical`; `3` if the database does not exist, cannot be
  opened or has no readable `user_version`. The `info` and `skipped` ones do not affect the exit code.
- **R20 — Read-only (REQ-F100-19).** `doctor` does not modify data: the only write statement
  is the one of C8, always inside a rolled-back transaction. `doctor` does not apply migrations, does not
  repair indexes and does not change permissions (it only reports them; hardening them belongs to `init`).
- **R21 — `hifa db reindex` (REQ-F100-20), and it is the whole contract of the verb.** The command
  resolves the database path with §2.2 and then runs **three ordered steps, and the file is only asked
  what it is on a connection that cannot write**:

  ```
  (a) db_path exists?  no  →  database_not_found, exit 3, nothing opened and nothing created
  (b) probe ← open_probe(db_path)      # SPEC-030 §2.1: URI mode=ro, uri=True, and NO pragma of PRAGMAS
      try:
          tables  ← probe.tables       # type = 'table'          } the ONE statement the symbol ran,
          objects ← probe.objects      # type IN ('table','view') } PROBE_SQL, partitioned in Python:
                                       #   {(type, name) for (type, name) in probe.conn.execute(PROBE_SQL)}
          "workspace" ∉ tables, or "issue" ∉ tables  →  not_a_hifa_database, exit 3, nothing written
          require_fts5(probe.conn)  →  fts5_unavailable, exit 6, nothing written   (SPEC-030 §2.1)
      finally:
          probe.conn.close()           # the caller closes it on every path, the refusals included
  (c) conn ← writable connection; ONE transaction with the sequence below; then SELECT COUNT(*) FROM issue
  ```

  **The verdict asks about `tables`, and the empty-file question asks about `objects`.** They are not
  the same set: `PROBE_SQL` returns tables and views, and a foreign file carrying `CREATE VIEW
  workspace` has the *name* without the *table*. Asking `objects` would pass that file at (b) and then
  fail inside `init`'s migrator with `view workspace already exists` — an exit nobody published — so the
  marker verdict reads `tables` (`workspace` **table**, R14c) and only the "this file carries nothing at
  all" question reads `objects` (R14a). Both sets are printed here and in `init`'s step 5 because the
  difference is the whole point.

  **The order is the contract, and (a) and (b) leave the file byte-identical.** Both facts are measured:
  with the writable open first — which is what revision 10 prescribed — `connect(readonly=False)` on a
  **missing** path creates it (4096 bytes, empty schema, SPEC-030 §2.1), so the `database_not_found`
  answer was unreachable and the command created the very file it promised not to touch; and the
  `sqlite_master` read on a **read-write** connection is a write-side open of somebody else's database
  before it has been identified. Now (a) is a `stat` and nothing else, (b) cannot write because the URI is
  `mode=ro` and `open_probe` applies no pragma, and the first writable open is (c) — after the verdict.

  **The probe is a symbol of the adapter, and this file does not reach for the driver.** Step (b) calls
  `open_probe(path)` of SPEC-030 §2.1, which opens by URI `mode=ro` with `uri=True`, applies **no** pragma,
  runs `PROBE_SQL` once and hands back `Probe(conn, tables, objects)` — the one row set, partitioned into
  the two sets; the caller closes `conn` in the `finally`. `@REQ-F030-05`
  forbids any `sqlite3.connect` outside `connection.py` and `migrate.py`, and `connection.py` is the one
  place that knows how a file is opened, so the probe had to become a symbol of the port before this step
  could be written at all (D-068). **Why not `connect(readonly=True)`:** that function applies the whole
  `PRAGMAS` tuple — R1 says every connection applies the six — and its first entry, `journal_mode=WAL`, is
  a write to the file header. On every file that is not already in WAL (another tool's database, a file
  made by hand with `sqlite3`, a 0-byte file) the read-only open therefore answers `attempt to write a
  readonly database` (measured, SQLite 3.53.1), which SPEC-030 §6 maps to
  `Conflict(rule='DB_READONLY', code='DB_READONLY')`; that is exit `4`, and it is why the verb's exit set
  includes it. On a file that **is** in WAL the same tuple
  applies without error — so whether the open succeeds is a function of the file's journal mode, which is
  the one thing this step exists to find out. An instrument whose success depends on the answer cannot be
  the instrument that gives it: `open_probe` asks for the mode and for nothing else.

  **The `4` of this verb is reached by the probe on a file that *is* in WAL, and only there.** Measured on
  SQLite 3.53.1: a database in WAL mode sitting in a directory the process cannot write answers
  `attempt to write a readonly database` on `PROBE_SQL`'s **read** — the reader cannot create the `-shm`
  it needs next to the database — and SPEC-030 §6 maps that message to
  `Conflict(rule='DB_READONLY', code='DB_READONLY')`.
  The probe applies no pragma, so revision 14's sentence ("`DB_READONLY` is unreachable from it") was
  true of one path into the row and false of the other: the pragma path is gone, the permission path
  remains, and a verb whose file can be inside a directory the process cannot write cannot pretend
  otherwise. It answers exit `4` with the message of §6 and leaves the database byte-identical, exactly
  like the two `3`s and the two `6`s.

  **A file SQLite refuses is not a third verdict of (b).** `open_probe`'s `PROBE_SQL` read raises
  `CorruptDatabase(detail=…)` — SPEC-020 §2.6, whose `code` is `corrupt_database` — for `file is not a
  database` and `database disk image is malformed` (SPEC-030 §2.1, §6) before any `tables` exist, because
  `sqlite3.connect()` is lazy; this verb answers `corrupt_database`, exit `6` (§6), and the class it
  raises is the class this table names, not an adapter code with a second spelling.

  Those are the two names a hifa database cannot lack: `workspace` is the marker `hifa init` already
  reads (R14c) and `issue` is the table the rebuild indexes. **The marker is a table and not an object:**
  a foreign file that carries `CREATE VIEW workspace` has the name and not the table, and a verdict taken
  over `objects` would let it through to a migrator that dies with `view workspace already exists`. A file
  without them is a valid SQLite
  database of some other tool and not this one, and a rebuild attempted on it fails *inside* the
  transaction with `no such table: main.issue` — an error about the index that is really an answer about
  the file. A **missing** `issue_fts` is not a refusal: it is the state the sequence repairs, and it
  arrives at (c) like any other. Then it executes **exactly this sequence**, in one transaction:

  ```sql
  BEGIN IMMEDIATE;

  -- The canonical DDL is SPEC-090 §3.1 and this block matches it column-for-column.
  CREATE VIRTUAL TABLE IF NOT EXISTS issue_fts USING fts5(
    title, description, identifier UNINDEXED,
    content='issue', content_rowid='rowid',
    tokenize='unicode61 remove_diacritics 2'
  );

  -- The three triggers are attached to `issue`, not to the index: `DROP TABLE issue_fts` takes the
  -- virtual table and its shadow tables and leaves the triggers in `sqlite_master`, so a plain
  -- `CREATE TRIGGER issue_fts_ai` on a database that lost only the table fails with
  -- "trigger issue_fts_ai already exists". Dropping them first is what makes this sequence run from
  -- every starting state: index and triggers present, index gone, triggers gone, both gone.
  DROP TRIGGER IF EXISTS issue_fts_ai;
  DROP TRIGGER IF EXISTS issue_fts_ad;
  DROP TRIGGER IF EXISTS issue_fts_au;

  CREATE TRIGGER issue_fts_ai AFTER INSERT ON issue BEGIN
    INSERT INTO issue_fts(rowid, title, description, identifier)
    VALUES (new.rowid, new.title, new.description, new.identifier);
  END;

  CREATE TRIGGER issue_fts_ad AFTER DELETE ON issue BEGIN
    INSERT INTO issue_fts(issue_fts, rowid, title, description, identifier)
    VALUES ('delete', old.rowid, old.title, old.description, old.identifier);
  END;

  CREATE TRIGGER issue_fts_au AFTER UPDATE OF title, description, identifier ON issue BEGIN
    INSERT INTO issue_fts(issue_fts, rowid, title, description, identifier)
    VALUES ('delete', old.rowid, old.title, old.description, old.identifier);
    INSERT INTO issue_fts(rowid, title, description, identifier)
    VALUES (new.rowid, new.title, new.description, new.identifier);
  END;

  INSERT INTO issue_fts(issue_fts) VALUES('rebuild');

  COMMIT;
  ```

  One transaction, so a failure leaves the previous index (or its absence) in place rather than half of a
  new one — and DDL is transactional in SQLite, so a rebuild that fails after the `DROP TRIGGER`s rolls
  the triggers back with it. It then returns `SELECT COUNT(*) FROM issue`, the number of indexed issues,
  which is what `reindex`'s `int` and the `reindexed` key both carry. Five consequences, all of them
  testable:
  - **The count is every row of `issue`, trashed and archived included**, because the index holds every
    row — visibility is a predicate of the query (SPEC-090 R10), never of the index. After the rebuild,
    `COUNT(*)` of `issue_fts` equals `COUNT(*)` of `issue`.
  - **A missing index is recreated, not refused.** `db migrate` cannot repair it — SPEC-030 §2.3 applies
    only the migrations above `user_version`, and a database whose `user_version` is current has none to
    apply — so the sequence above recreates the table (`CREATE VIRTUAL TABLE IF NOT EXISTS`, columns as
    in SPEC-090 §3.1) and the three triggers (`DROP TRIGGER IF EXISTS` for each name, then `CREATE
    TRIGGER`), and the same run repairs the state `DROP TABLE issue_fts` actually leaves behind: **the
    table gone and the triggers still there**. That is why C8's advice for the missing table is this
    command and not `db migrate`: the corpus has **one** repair path and it works.
  - **It writes nothing else.** No row of `issue` changes, no `issue_history` row is written, no
    `VACUUM` runs (that is `db vacuum`, which owes the rebuild of SPEC-090 R19), and the command is
    idempotent in what it promises: **reindexing twice leaves the same row count in `issue_fts` and the
    same search results**. The bytes of the file are *not* the invariant — the `change counter` of the
    header, the WAL and the FTS5 shadow tables all move on a second commit — so no test compares files.
  - **It is the action C8 recommends**, and the only corrective action the search contract allows
    (SPEC-090 R18, D-003): no search ever rebuilds the index and `hifa search --reindex` does not exist.
  - **The failures are loud, and each input has one exit and one code.** A path that does not exist, or
    one that cannot be opened ⇒ exit `3`, `database_not_found` (SPEC-050 §6's code for a database that is
    not there, carried by `DatabaseNotFound` of SPEC-020 §2.6). A file that **opens** and is **valid
    SQLite** but carries no `workspace` **table** and no `issue` table — another tool's database is the
    case that named this rule — ⇒ exit `3`, `not_a_hifa_database`: the same code and the same name
    `hifa init` publishes (R14c, SPEC-050 §6), carried here by `NotAHifaDatabase` and
    answered at the `3` of "the database this command was given is not a hifa database" rather than at
    `init`'s `2` of "the `--db` value is not one I will overwrite" (D-066). A database that **is** a hifa
    database but sits in a directory the process cannot write, in WAL mode ⇒ the probe's read cannot
    create its `-shm` and answers `attempt to write a readonly database` (SPEC-030 §6) ⇒
    `Conflict(rule='DB_READONLY', code='DB_READONLY')`, exit `4`: the file is fine and the permissions are
    not, and no rebuild can change that. A file that **SQLite itself refuses** — `file is not a
    database`, `database disk image is malformed` (SPEC-030 §6) ⇒
    `corrupt_database`, exit `6`: the image is unreadable, so no `sqlite_master` verdict about its schema
    can even be asked. A build whose SQLite has no `ENABLE_FTS5` ⇒ `fts5_unavailable`, exit `6`
    (SPEC-030 §2.1). **There is no exit `2` in this row**: nothing the user types here is a typo — `--db`
    is a path, and the file behind it is the subject of the command, not an argument to correct. None of
    the five is answered with `{"reindexed": 0}`: an index that could not be rebuilt and an index with
    nothing to rebuild are different answers, and the empty database is the second one — exit 0,
    `{"reindexed": 0}`.
  Human output is the one line `reindexed N issues` (SPEC-050 §2.4.1); `--json` is
  `{"reindexed": N}`; `--quiet` prints nothing and the exit code is the answer.
- **R22 — Parameterized SQL (REQ-S100-02, SPEC-000 REQ-S002).** The checks use constant
  queries; the few values (paths, versions) never enter the SQL. `migrations_max_version`
  reads file names with `re.fullmatch(r"(\d{3})_[A-Za-z0-9_]+\.sql", name)` and discards whatever
  does not match (a file `999_bad.sql.bak` does not count).
- **R23 — No network (REQ-S100-03).** `init` and `doctor` open no sockets: the timezone is validated against
  the local `zoneinfo`, not against a service.

### 4.4 Non-functional and security requirements

| ID | Requirement | Verification |
|---|---|---|
| **REQ-N100-01** | `resolve_config` < **5 ms** p50 (includes reading and parsing a 15-line `.hifa.toml`). | `tests/perf/test_config_speed.py`, 100 iterations |
| **REQ-N100-02** | full `hifa init` < **2 s** cold (includes the SPEC-010 migration). | `time.perf_counter` over the command in a subprocess |
| **REQ-N100-03** | `hifa doctor` < **300 ms** with 10 000 issues (C1 included). | same harness, DB with 10 000 issues |
| **REQ-N100-04** | `config.py` imports neither `sqlite3`, `fastapi`, `typer`, `jinja2`; it uses stdlib `tomllib` (SPEC-000 REQ-N007). | `import-linter` + `grep` |
| **REQ-S100-01** | Database file `0600` and directory `0700` after `init`; `.hifa.toml` `0600` (R16). | `stat` in the test |
| **REQ-S100-02** | SQL always parameterized; migration names validated with a regex (R22). | test with a file `999_x.sql; DROP TABLE issue` in the migrations directory |
| **REQ-S100-03** | `init` and `doctor` with no network (R23). | `socket.socket` monkeypatched to raise |
| **REQ-S100-04** | `HIFA_DB`/`--db` escaping the tree are not validated against a root (they are user paths), but they are **never** written outside the directory the user indicated: `init` writes only to `db_path`, `db_path.parent` and `.hifa.toml`. | test with `--db /tmp/x/y.db` and an assert that only those paths are created |

## 5. Flow

### 5.1 `resolve_config`

```
 1. cwd ← (cwd or Path.cwd()).resolve();  env ← (env or os.environ)
 2. db_flag not empty → (resolve(expanduser(db_flag), cwd), "flag", None)          [end]
 3. env["HIFA_DB"].strip() not empty → (resolved, "env", None)                     [end]
 4. cfg_path ← find_config_file(cwd)                     (walk-up, max. 32 levels)
 5. if cfg_path: cfg ← load_config_file(cfg_path)
       db ← cfg.db or ".hifa/hifa.db"  resolved against cfg_path.parent
       → (db, "file", cfg_path)                                                      [end of path]
 6. XDG_DATA_HOME not empty and absolute → ($XDG_DATA_HOME/hifa/hifa.db, "xdg", None)  [end]
 7. ~/.local/share/hifa/hifa.db                                                    (source "home")
 8. complete team/cli/web: from the TOML if it exists, defaults if not; accumulated warnings
```

Step 5 and steps 6-7 are mutually exclusive **only** for the path: if there is a `.hifa.toml`,
the `team`/`cli`/`web` values come from it; if not, from the defaults.

### 5.2 `hifa init`

```
 1. cfg ← resolve_config(db_flag=…)                  → ConfigError (exit 2)
 2. validate flags (R15); normalize --team-key to uppercase                → exit 2
 3. db_path = ":memory:" → in_memory_not_supported                          → exit 2
 4. mkdir(db_path.parent, mode=0o700, exist_ok=True)   → db_dir_not_writable (exit 2)
 5. db_path exists?  yes and not --force → probe ← open_probe(db_path)     (SPEC-030 §2.1: mode=ro, NO pragma)
       try:
           objects ← probe.objects      # PROBE_SQL, run once inside the symbol: type IN ('table','view')
           tables  ← probe.tables       # the same row set: type = 'table'
           objects == frozenset()                            → an empty file: proceed (R14a)
           "workspace" ∉ tables                              → not_a_hifa_database          (exit 2)
           "workspace" ∈ tables                              → proceed
       finally:
           probe.conn.close()            # on the refusals too, not only on the happy path
    no  → nothing to probe; step 6 creates the file (R14a)
 6. open connection (SPEC-010 PRAGMAs) and run the migrator
       user_version > max(migrations) → schema_too_new                     (exit 4)
 7. detect state:  (R14, D-068)
       SELECT 1 FROM workspace LIMIT 1 → a row?  → already_initialized = True
       no row (the table is empty: a migrated file never seeded, or a file this run created)
                                                 → already_initialized = False
 8. BEGIN IMMEDIATE
       get-or-create workspace  (by url_key)
       get-or-create team       (by workspace_id, key)
       get-or-create 7 workflow_states (by team_id, name)
       get-or-create 4 labels   (by workspace_id, NULL, name — case-insensitively, R11bis)
                                                                    if seed_labels
       get-or-create member     (by email | display_name)       if seed_member
       get-or-create team_membership
    COMMIT
 9. chmod(db_path, 0o600)
10. find_project_root(cwd) is None            → write no config; report
                                                `config: none (outside a git work tree)`
    else if there is no .hifa.toml in [cwd..project root] (or --force)
                                                → write_config_file(0o600)  (+ .bak if --force)
    else                                        → report the existing file, write nothing
11. BootstrapResult(created, skipped, ids, db_path, already_initialized)
12. exit 0
```

**Step 5 is the one that has to come before step 6, and the reason is measured.** `001_init.sql` creates
`workspace` on any `user_version`-0 file, so a probe that ran after the migrator could never fire: applied
to a foreign file, the migration leaves `workspace` and `issue` in `sqlite_master` — and, under the row
reading of step 7, an **empty** `workspace` — so the exit `2` refusal would not merely be late, it would be
unreachable, and the file would have been given the whole hifa schema and a seed on top of another tool's
tables. At step 5 the file is refused with no table created and no migration run.
Step 5 makes the exit `2` reachable and leaves the file byte-identical after the refusal, because the probe
is `open_probe()` of SPEC-030 §2.1 — the same symbol R21 uses: `mode=ro`, no `PRAGMA`, one `PROBE_SQL`
read — and it costs one `stat` on the happy path (a path that does not exist is not probed at all). It is
also what keeps the two carriers of `not_a_hifa_database` of D-066 honest: `init` refuses the **argument**
at `2` before a single migration runs, `db reindex` refuses the **database** at `3` before a single write,
and the two of them are the only two edges that publish that code.

The seeding of step 8 is a single transaction: if something fails, there is no workspace without a team and no
team without states. Step 6 commits before (migrations are not part of the seeding).

### 5.3 `hifa doctor`

```
1. cfg ← resolve_config(db_flag=…)
2. db_path does not exist → CheckResult(db_not_found, critical) and exit 3
3. open in ro mode if possible; if not, rw (for C8)
4. C1 PRAGMA integrity_check            → ok | critical
5. C2 PRAGMA foreign_key_check          → ok | critical (count = rows)
6. C3 orphans (5 LEFT JOIN)             → ok | critical (count = sum)
7. C4 identifiers (3 GROUP BY)          → ok | critical (count = sum)
8. C5 user_version vs migrations_max    → ok | warning | critical
9. C6 file_size (+ -wal)                → info | warning (> 512 MB)
10. C7 permissions (file and dir)       → ok | warning
11. C8 FTS integrity-check (BEGIN/ROLLBACK) → ok | warning | critical | skipped
12. C9 counts per table                 → info
13. exit according to R19
```

Fixed order: C1..C9 always run, even if C1 fails (a failed `integrity_check` must not
prevent seeing the size and permissions). An unrecoverable `sqlite3.DatabaseError` in C1-C4 stops the
run and returns exit 2 with the checks already computed.

## 6. Errors

| Error | `code` | When | CLI |
|---|---|---|---|
| `ConfigError` | `config_invalid_toml` | `.hifa.toml` does not parse | exit 2, message with line |
| `ConfigError` | `config_invalid_value` | value outside the table of §2.3 or invalid flag | exit 2, message with `field` |
| `ConfigError` | `config_unreadable` | `.hifa.toml` exists but cannot be read | exit 2 |
| `ConfigError` | `db_dir_not_writable` | the database directory cannot be created/written | exit 2 |
| `ConfigError` | `in_memory_not_supported` | `hifa init` with `--db :memory:` | exit 2 |
| `Validation` | `not_a_hifa_database` | **`hifa init`** (R14c): the file exists but has no `workspace` table — the `--db` value is refused **before the migrator runs** (§5.2 step 5) and `--force` is the override, which is why this edge answers `2` | exit 2 |
| `NotAHifaDatabase` | `not_a_hifa_database` | **`db reindex`** (R21): the file opens and is valid SQLite but carries no `workspace` **table** and no `issue` table (a *view* of either name does not count — the verdict reads `probe.tables`, not `probe.objects`), so there is nothing this command can index. One code, two edges, two exits: `init`'s `2` refuses the argument, `reindex`'s `3` says the database is not a hifa database (D-066) | exit 3 |
| `Conflict` | `schema_too_new` | `user_version` > the binary's maximum migration | exit 4 |
| `Conflict` | `DB_READONLY` | `reindex` on a database that **is** a hifa database but cannot be read: a WAL database inside a directory the process cannot write answers `attempt to write a readonly database` on the probe's `PROBE_SQL` read, because the reader cannot create its `-shm` (SPEC-030 §6: `Conflict(rule='DB_READONLY', code='DB_READONLY')`). The file is intact and the permissions are not, which is a conflict with the environment and not a corruption — no rebuild changes it | exit 4 |
| `DatabaseNotFound` | `database_not_found` | `open_probe` on a path that is not there — R21 step (a) (`doctor`'s `3` is its own check result, not this class), the code of SPEC-050 §6 for a missing file and one of the two `3`s of this table, with `not_a_hifa_database` below it | exit 3 |
| `Fts5Unavailable` | `fts5_unavailable` | `reindex` on a build whose SQLite has no `ENABLE_FTS5` (R21) | exit 6 |
| `CorruptDatabase` | `corrupt_database` | `reindex` on a file **SQLite itself refuses** — `file is not a database`, `database disk image is malformed` — raised by `open_probe()`'s `PROBE_SQL` read at R21 step (b) (SPEC-030 §2.1, class declared in SPEC-020 §2.6) and, through `translate_sqlite_error`, on the same two messages any other command meets (SPEC-030 §6). A file that opens but is not a hifa database is `not_a_hifa_database` (above), and a **missing** `issue_fts` is not this code either: it is recreated (R21) | exit 6 |

`DatabaseNotFound`, `NotAHifaDatabase` and `CorruptDatabase` are the three classes of this table whose
`code` is the code the envelope publishes, with no translation between them (SPEC-020 §2.6 for the first
and third, D-066 for the second); `Conflict(rule='DB_READONLY', code='DB_READONLY')` is the fourth exit of
`db reindex` and
the only one whose class is the generic `Conflict` — it carries `DB_READONLY` in `code`, exactly as
SPEC-030 §6's row says, because SPEC-040's retry and the CLI's exit-4 arms already key on that string.

`NotAHifaDatabase` is the one class of this spec that carries a code and no `field`: `code =
"not_a_hifa_database"`, `message = "not a hifa database: <path>; run 'hifa init'"`, raised by `reindex`
at R21's step (b) — the read-only probe, before the writable connection exists — and mapped to exit `3`
by SPEC-050 §6, which publishes it as a row of its own.
`hifa init` keeps raising `Validation` with the same lowercase code (exit `2`, SPEC-050 §6's
pass-through), because what it refuses is the `--db` argument, and it raises it at §5.2 step 5, before
its own migrator; D-066 is the decision that argues one code with two exits, and this revision is what
makes both of them reachable.

Catalog of `CheckResult`s that produce exit ≠ 0:

| Check | `severity` | `detail` (exact shape) |
|---|---|---|
| `integrity_check` | critical | `integrity_check returned: <first error>` |
| `foreign_keys` | critical | `<n> foreign key violations` |
| `orphans` | critical | `<n> orphan rows (issues: <a>, states: <b>, assignees: <c>, projects: <d>, labels: <e>)` |
| `identifiers` | critical | `<n> identifier problems (<d> duplicated, <m> inconsistent with team.key+number)` |
| `schema_version` | warning / critical | `user_version V, available migrations M` (warning if V < M, critical if V > M) |
| `file_size` | warning | `<bytes> bytes (<MB> MB) — consider VACUUM` |
| `permissions` | warning | `db <mode> (expected 0600), dir <mode> (expected 0700)` |
| `fts_integrity` | warning / critical | `fts index out of sync — run "hifa db reindex"` (warning); `fts index is missing — run "hifa db reindex"` (critical — the same command, because `reindex` recreates the table before it rebuilds it, R21) |

No check invents new exit codes: `doctor` uses exclusively `0/1/2/3` from R19, `init` uses `0/2/4` and
`db reindex` uses `0/3/4/6` (R21). The `1` is **exclusive** to `doctor` (linter convention: 0 clean, 1 findings)
and is documented here because the global table of SPEC-000 A7 reserves it for that command and no
other spec may use it (D-014). An internal bug is `6`, never `1`.

## 7. Acceptance Criteria

```gherkin
@SPEC-100
Feature: Configuration, bootstrap and database diagnostics

  Scenario: init in an empty directory seeds a usable workspace
    Given an empty directory inside a git work tree without .hifa.toml
    When "hifa init --team-key ENG --team-name Engineering" is executed
    Then the exit code is 0
    And the file ".hifa/hifa.db" exists
    And the file ".hifa.toml" exists
    And the workspace has a name equal to the basename of the directory
    And team "ENG" exists with timezone "America/Santiago"
    And 7 non-archived workflow_states exist
    And exactly one workflow_state has is_default 1 and is called "Todo"
    And the seven states cover the six types, with "In Progress" and "In Review" both started
    And 4 labels exist with team_id NULL
    And 1 member exists with is_admin 1
    And 1 team_membership exists with is_owner 1

  Scenario: init outside a git work tree writes no config and uses the global database
    Given an empty directory with no .git in itself or in any ancestor
    And XDG_DATA_HOME pointing to "/tmp/xdg"
    When "hifa init" is executed
    Then the exit code is 0
    And the file ".hifa.toml" does not exist in the directory
    And the resolved database is "/tmp/xdg/hifa/hifa.db"
    And the report shows "config" equal to "none (outside a git work tree)"

  Scenario: --force outside a git work tree still writes no config
    Given an empty directory with no .git in itself or in any ancestor
    When "hifa init --force" is executed
    Then the exit code is 0
    And the file ".hifa.toml" does not exist in the directory
    And the file ".hifa.toml.bak" does not exist in the directory

  Scenario: init is idempotent
    Given a directory already initialized with "hifa init"
    When "hifa init" is executed again
    Then the exit code is 0
    And the output contains "already initialized"
    And the report has an empty created
    And the workspace count is still 1
    And the team count is still 1
    And the workflow_state count is still 6
    And the label count is still 4
    And the member count is still 1

  Scenario: init reports already_initialized from the workspace row, not from the table
    Given a missing path, an empty file and a migrated database with no workspace row
    When "hifa init --json" is executed against each of them in turn
    Then each run reports already_initialized false
    And each run leaves exactly one workspace row behind
    When "hifa init --json" is executed again against the database it just seeded
    Then the exit code is 0
    And the report has an empty created
    And already_initialized is true

  Scenario: the team key is normalized to uppercase
    When "hifa init --team-key eng" is executed
    Then the created team has key "ENG"
    And the exit code is 0

  Scenario: --no-labels does not seed labels and --force adds them later
    Given a directory initialized with "hifa init --no-labels"
    Then the label count is 0
    When "hifa init --force" is executed
    Then the exit code is 0
    And the label count is 4
    And the issue count did not change

  Scenario: the seed does not write a second spelling of a workspace label
    Given a directory initialized with "hifa init" whose workspace label "Bug" was renamed to "bug"
    When "hifa init --force --json" is executed
    Then the exit code is 0
    And the report has an empty created
    And the label count is still 4
    And the workspace label is still named "bug"

  Scenario: --db wins over HIFA_DB and over the file
    Given a .hifa.toml with db ".hifa/hifa.db"
    And HIFA_DB pointing to "/tmp/env.db"
    When "hifa db path --db /tmp/flag.db" is executed
    Then the output shows "/tmp/flag.db"
    And the output shows source "flag"

  Scenario: HIFA_DB wins over the file
    Given a .hifa.toml with db ".hifa/hifa.db"
    And HIFA_DB pointing to "/tmp/env.db"
    When "hifa db path" is executed
    Then the output shows "/tmp/env.db"
    And the output shows source "env"

  Scenario: the file path is relative to the .hifa.toml and not to the cwd
    Given "/repo/.hifa.toml" with db ".hifa/hifa.db"
    When "hifa db path" is executed from "/repo/src/deep"
    Then the output shows "/repo/.hifa/hifa.db"
    And the output shows source "file"
    And the output shows config_file "/repo/.hifa.toml"

  Scenario: a .hifa.toml without db uses the project-local database
    Given "/repo/.hifa.toml" with only the [team] section
    When "hifa db path" is executed from "/repo"
    Then the output shows "/repo/.hifa/hifa.db"
    And the output shows source "file"

  Scenario: XDG_DATA_HOME is used when there is no file
    Given no .hifa.toml in the tree
    And XDG_DATA_HOME pointing to "/tmp/xdg"
    When "hifa db path" is executed
    Then the output shows "/tmp/xdg/hifa/hifa.db"
    And the output shows source "xdg"

  Scenario: with no file and no XDG the home is used
    Given no .hifa.toml in the tree
    And XDG_DATA_HOME undefined
    When "hifa db path" is executed
    Then the output shows "$HOME/.local/share/hifa/hifa.db"
    And the output shows source "home"

  Scenario: an invalid timezone is rejected before touching the database
    When "hifa init --timezone Mars/Olympus" is executed
    Then the exit code is 2
    And the message contains "config_invalid_value" and "team.timezone"
    And no database file exists

  Scenario: a malformed .hifa.toml is rejected with the line
    Given a .hifa.toml with the line "port = = 7777"
    When "hifa init" is executed
    Then the exit code is 2
    And the message contains "config_invalid_toml" and the line number

  Scenario: the file and directory permissions are restrictive
    When "hifa init" is executed in an empty directory
    Then the mode of ".hifa/hifa.db" is 0600
    And the mode of the directory ".hifa" is 0700
    And the mode of ".hifa.toml" is 0600

  Scenario: init rejects a file that is not a hifa database
    Given a valid SQLite file without a workspace table at the database path
    When "hifa init" is executed
    Then the exit code is 2
    And the message contains "not_a_hifa_database"
    And the file was not modified
    When the same run is repeated on a file carrying "CREATE VIEW workspace" instead of the table
    Then the exit code is 2
    And the message contains "not_a_hifa_database"
    And the file was not modified
    And no migration ran and no other table was created

  Scenario: doctor on a healthy database
    Given a directory initialized with "hifa init"
    When "hifa doctor" is executed
    Then the exit code is 0
    And the 9 checks are present
    And integrity_check is "ok"
    And foreign_keys is "ok"
    And schema_version is "ok"

  Scenario: doctor detects an issue with the state of another team
    Given an issue whose state_id belongs to another team
    When "hifa doctor" is executed
    Then the exit code is 2
    And the orphans check is critical
    And the detail mentions at least 1 orphan row

  Scenario: doctor detects inconsistent identifiers
    Given team ENG with its key changed to "ENGX" without updating the identifiers
    When "hifa doctor" is executed
    Then the exit code is 2
    And the identifiers check is critical
    And the count is greater than 0

  Scenario: doctor detects a database newer than the binary
    Given a database with user_version 99
    When "hifa doctor" is executed
    Then the exit code is 2
    And the schema_version check is critical
    And the detail contains 99

  Scenario: doctor reports lax permissions as a warning
    Given a database with mode 0644
    When "hifa doctor" is executed
    Then the exit code is 1
    And the permissions check is warning
    And no check is critical

  Scenario: doctor on a nonexistent path
    When "hifa doctor --db /tmp/no-existe/hifa.db" is executed
    Then the exit code is 3
    And error.code is "database_not_found"

  Scenario: reindex repairs an out-of-sync FTS index
    Given a database whose "issue_fts" disagrees with "issue"
    When "hifa doctor" is executed
    Then the fts_integrity check is warning
    And the exit code is 1
    When "hifa db reindex --json" is executed
    Then the exit code is 0
    And the "reindexed" field is the number of issues
    When "hifa doctor" is executed again
    Then the fts_integrity check is "ok"
    And the exit code is 0

  Scenario: reindex recreates an index that is gone with its triggers
    Given a database where "issue_fts" was dropped and its three triggers were removed
    When "hifa db reindex --json" is executed
    Then the exit code is 0
    And the "reindexed" field is the number of issues
    And "issue_fts" is created again with the columns of SPEC-090 §3.1
    And the triggers "issue_fts_ai", "issue_fts_ad" and "issue_fts_au" exist
    And "hifa search deploy" answers backend "fts"
    And "hifa doctor" reports the fts_integrity check as "ok"

  Scenario: reindex is idempotent in its rows and its results, not in its bytes
    Given a migrated database with 12 issues
    When "hifa db reindex --json" is executed twice
    Then COUNT(*) of "issue_fts" is 12 both times
    And the two "reindexed" values are 12 and 12
    And the hits of the same search are identical both times

  Scenario: reindex refuses a valid SQLite file that is not a hifa database
    Given a valid SQLite file with a table "notes" and no "workspace" table at the database path
    When "hifa db reindex --json" is executed
    Then the exit code is 3
    And error.code is "not_a_hifa_database"
    And the "reindexed" field is absent
    And the file still has no "issue_fts" table
    And the bytes of the file are the same as before the command

  Scenario: reindex answers a missing path without creating it
    Given no database file at the path "/tmp/hifa-no-db/hifa.db"
    When "hifa db reindex --json" is executed
    Then the exit code is 3
    And error.code is "database_not_found"
    And no file exists at "/tmp/hifa-no-db/hifa.db"

  Scenario: reindex refuses a file that SQLite itself cannot read
    Given a file with no SQLite header at the database path
    When "hifa db reindex --json" is executed
    Then the exit code is 6
    And error.code is "corrupt_database"
    And the "reindexed" field is absent

  Scenario: reindex answers exit 4 when the database is intact and its directory is not writable
    Given a migrated database in WAL mode inside a directory with mode 0500
    When "hifa db reindex --json" is executed
    Then the exit code is 4
    And error.code is "DB_READONLY"
    And the "reindexed" field is absent
    And the bytes of the database file are the same as before the command
    And the probe's connection was closed before the command exited

  Scenario: config resolution has no side effects
    Given an empty directory
    When "resolve_config(env={}, cwd=tmp_path)" is called
    Then no file or directory is created under tmp_path
    And no connection to the database is opened
```

## 8. Regeneration checklist

1. Read `SPEC-000` + `SPEC-010` + this spec in full. There is no need to read any other module.
2. Write `src/hifa/config.py` in this order: constants → dataclasses → `slugify` →
   `validate_timezone` → `find_config_file` → `load_config_file` → `resolve_db_path` →
   `resolve_config` → `write_config_file`. Every function without side effects except
   `write_config_file`.
3. `load_config_file` with `tomllib.loads(path.read_text("utf-8"))`, mapping to `ConfigFile` and
   validating with the table of §2.3 **before** returning. `ConfigError` with `field` populated on every
   validation.
4. `src/hifa/app/teams.py`: literal `DEFAULT_STATES`/`DEFAULT_LABELS` constants, and `bootstrap`
   as 6 get-or-create calls inside a single transaction. Each get-or-create returns
   `(id, created: bool)` so that `created`/`skipped` can be assembled.
5. `src/hifa/cli/commands/db.py`: `migrations_max_version` (regex of R22), `doctor` (C1..C9 in
   order), `reindex`. `doctor` must not execute `COMMIT` on any path.
6. Typer commands of §2.7; the human output is assembled in `cli/output.py` (SPEC-000 A6). The
   `init` command prints the block of §2.7 and `doctor` the `CHECK | SEVERITY | COUNT | DETAIL` table.
7. Tests: (a) `tests/unit/config/` with a precedence matrix (5 sources × empty/relative/
   invalid cases), (b) `tests/integration/init/` with idempotency and permissions, (c)
   `tests/integration/doctor/` with a database corrupted per check (orphan, inconsistent identifier,
   high `user_version`, lax permissions, missing FTS).
8. The precedence tests must pass explicit `env={}` and `cwd=tmp_path`: never depend on the
   real environment of the machine.
9. `pytest tests -k "config or init or doctor"` green.
10. `python scripts/spec_contract_check.py --spec specs/100-config-bootstrap.md`: the `DEFAULT_*`
    constants, the 7 states, the 4 labels and the signatures of `resolve_config`/`bootstrap`/`doctor`
    must match with no drift.
11. `grep -rn "import sqlite3" src/hifa/config.py` must return 0 lines.

## 9. Provenance

- Resolution order `--db` → `HIFA_DB` → `.hifa.toml` → XDG → home: pattern of CLI tools
  with local state (`git`, `direnv`, `poetry`), adapted to a single database file.
- XDG Base Directory (`$XDG_DATA_HOME`, default `~/.local/share`): freedesktop specification;
  `source="home"` explicitly distinguishes the fallback from the variable being set.
- `.hifa.toml` with `[workspace] [team] [cli] [web]`: format requested by Ginkelte (2026-11-08),
  parsed with stdlib `tomllib` to avoid adding dependencies (SPEC-000 REQ-N007).
- The 7 default states `Triage/Todo/Backlog/In Progress/In Review/Done/Canceled` and their `type`
  (`triage/unstarted/backlog/started/started/completed/canceled`): `workflow_state.type` of SPEC-010, with
  `is_default` on `Todo` so that a new issue always lands somewhere sane, and `In Review` shared by both
  review phases (D-058).
- `issue_estimation_type='fibonacci'`, `cycle_duration=2`, `cycle_start_day=1`: the defaults of
  `team` in SPEC-010.
- Default labels `Bug/Feature/Improvement/Docs`: the usual minimal set in trackers; the colours are
  hifa's own palette.
- `0600`/`0700`: requirement REQ-S100-01 derived from REQ-S004 of SPEC-000 (the user's local data).
- `doctor` with exit `0/1/2`: linter convention (`shellcheck`, `ruff`) — 0 clean, 1 findings,
  2 operation error; the `3` reuses `NotFound` from SPEC-000 A7.
- Check C8 with the FTS5 `'integrity-check'`: documented SQLite command to detect
  desynchronization in external content tables (necessary because of R19 of SPEC-090: `VACUUM` can
  renumber rowids).
- `not_a_hifa_database` instead of migrating a foreign file: SPEC-010 R5 (never a silent `DROP`).

## 10. Out of scope

- Multiple profiles, `--profile`, multiple workspaces in one database or multi-tenancy.
- `hifa init --reset`, deletion or recreation of the database (destructive, outside v1).
- Automatic data repair from `doctor` (`--fix`): it only reports; `db reindex` is the only
  action.
- Database rotation, scheduled backups or automatic `VACUUM`.
- API/web configuration through environment variables (`HIFA_API_*`): that belongs to SPEC-060.
- Secret management, encryption at rest or per-ACL permissions.
- Installing shell completions, `systemd` units or scheduled tasks.

## 11. Notes

- **spec_revision 14 (2026-11-20): the seed is a writer of the `label` table, and it now resolves the
  four names case-insensitively (HIFA-14, review of the label implementation, D-070).** `version` stays
  7: no signature, no constant, no flag and no exit code of §2 moves — `bootstrap`'s declaration and
  `DEFAULT_LABELS` are the ones this revision found, and what changes is the rule §4.2 and §5.2 state
  about them.
  SPEC-040 R22 refuses a second spelling of a label name at one level (`Bug` beside `bug`), and this
  seeding step looked its names up **exactly**, so `init` on a workspace whose seeded `Bug` had been
  legally renamed to `bug` wrote `Bug` back beside it — the one writer of the table the label rule had
  not counted. R11bis now says how the step resolves (case-insensitively, over `team_id IS NULL`), what
  a case variant does (**skipped**, under the seed's own string, the row keeping its spelling) and why it
  is not a refusal: `init` answering exit `4` because a label of its own database is spelled `bug` is an
  outcome R9's order and the exit set `0,2,4` do not describe, and skipping is also what keeps the flag
  idempotent. §3.1's `label` row and §5.2's step 8 say the same thing. §7 goes 31 → **32** inline
  scenarios (the renamed-then-reseeded workspace); the `.feature` corpus does not move — 17 files and
  240 scenarios.

- **spec_revision 13 (2026-11-20): one class and one code per verdict, an exit set that admits its own `4`,
  and a probe that closes on every path (HIFA-7, fifth review round, D-069).** `version` 6 → 7: §2.7's
  `db reindex` row publishes a different exit set and §2.6's `reindex` precondition names the classes
  §6 publishes.
  (1) **Three names for the corrupt verdict, and the printed handler had no arm for any of them.** R21
  said the probe raises `HifaError(code='DB_CORRUPT')`, §6 of this file said `CorruptDatabase` /
  `corrupt_database`, and SPEC-050 §5.3's printed handler — four domain classes, `KeyboardInterrupt`,
  `Exception` — had no `HifaError` arm at all, so the literal implementation of the flow answered
  `internal_error` and SPEC-100 §7's `error.code is "corrupt_database"` could not be produced. The class
  is now declared (SPEC-020 §2.6), `open_probe` raises **it**, and SPEC-050 §5.3 gains the arm that reads
  a code the exception carries. The same repair covers `database_not_found`, which four specifications
  published and none defined.
  (2) **`database_not_found` was pinned to `NotFound`, the other `3`.** `NotFound` publishes `not_found`
  and a `404`; a missing database file is `database_not_found` with a `503`, and this file's §6 and §7
  said so while SPEC-030 raised the other class. R21 step (a) now names `DatabaseNotFound`
  (SPEC-020 §2.6) and `open_probe` raises it, so both edges carry one class and one code.
  (3) **The exit set grew to `0/3/4/6`, and the sentence that denied it was measured false.** R21 said
  the probe "applies no pragma, so `DB_READONLY` is unreachable from it". On SQLite 3.53.1 a **WAL**
  database inside a directory the process cannot write answers `attempt to write a readonly database`
  from `PROBE_SQL`'s read — the reader cannot create its `-shm` — which SPEC-030 §6 maps to
  `Conflict(rule='DB_READONLY', code='DB_READONLY')`, exit `4`. §2.7's row, §6's catalog (a row of its
  own, with its message)
  and §7 (one scenario) now publish it. The set grew because the measurement said so, not because the
  verb gained a feature: the input is a hifa database whose directory is not writable, and the previous
  answer — exit `6` `internal_error` by falling through the handler — named the wrong cause.
  (4) **The marker verdict reads `tables`, the empty-file test reads `objects`.** `PROBE_SQL` returned
  tables **and views**, so a foreign file carrying `CREATE VIEW workspace` passed step 5 of `init`;
  measured, the migrator then died with `view workspace already exists` — an exit this file does not
  publish. `Probe` now carries both projections (§2.6's precondition, R21's block, `init`'s step 5) and
  R14c's "no `workspace` table" is what the verdict asks.
  (5) **`probe.conn.close()` was the last line of the block, so every refusal skipped it.** R21's step (b)
  and `init`'s step 5 are now `try/finally`, with the close in the `finally`; the resource contract this
  revision declares is therefore the one the printed text meets. §7 goes 30 → **31** inline scenarios
  (one for exit `4`) and one existing scenario gains the view case in place; the `.feature` corpus does
  not move — 17 files and 240 scenarios.
- **spec_revision 12 (2026-11-20): the probe is a symbol, `require_fts5` runs on it, and
  `already_initialized` means a row (HIFA-7, fourth review round, D-068).** `version` 5 → 6: §2.6's
  `reindex` declaration is what a regeneration reads, and §2.5's `BootstrapResult` now says what the flag
  means. Five corrections, the first two of them a prescription that could not be executed as written:
  (1) **R21 printed a call the adapter did not offer and the layer forbids.** Step (b) prescribed
  `sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)` inside `cli/commands/db.py`: `@REQ-F030-05` bans
  any `sqlite3.connect` outside `connection.py` and `migrate.py`, and the port had no read-only open
  without pragmas — `connect(readonly=True)` and `Database.ro_connection()` apply the whole `PRAGMAS`
  tuple. SPEC-030 §2.1 now declares `open_probe(path) -> Probe` with `PROBE_SQL` and `Probe(conn, tables)`,
  and step (b) calls it: `tables ← probe.tables`, the verdict, `require_fts5(probe.conn)`,
  `probe.conn.close()`. §5.2 step 5 of `init` calls the same symbol, so the two edges probe with one
  implementation instead of two comprehensions.
  (2) **`tables` was tested and never produced.** Revision 11's step tested `"workspace" ∉ tables` while no
  printed statement produced the set (revision 10 printed the `SELECT`; revision 11 dropped it). Step (b)
  now prints `tables ← probe.tables` **and** the statement behind it, `PROBE_SQL` of SPEC-030 §2.1.
  (3) **`require_fts5`'s placement is the probe's, not `reindex`'s.** SPEC-030 said four times that it is
  "the first statement of `reindex(conn)`", and `reindex(conn)` receives a connection it did not open, so
  that statement could never precede the probe this rule requires first. It runs on the probe's connection,
  immediately after `open_probe()` and before any writable statement — the order step (b) prints — and
  `reindex(conn)` declares it as a precondition (§2.6). The signature does not move: `db vacuum`
  (SPEC-090 R19) rebuilds on a connection it already holds.
  (4) **`already_initialized` has one meaning: the `workspace` row.** R14 said "existing with a workspace",
  which is table-or-row; the table reading made a **first** `init` report `true` — the migrator of step 6
  creates the table — and contradicted R14(a)'s "created and seeded" and SPEC-050 §2.5.1. It is now `True`
  iff a `workspace` row existed when step 7 read the database, with the four inputs tabulated in R14
  (missing / empty file / migrated-but-unseeded / seeded ⇒ `false, false, false, true`), §5.2 step 7
  carrying the `SELECT 1 FROM workspace LIMIT 1`, and the paragraph that justifies step 5's position
  rewritten — under the row reading the danger is not a false `true` but an exit `2` that can never fire.
  (5) **The sentence about `connect(readonly=True)` was false in its scope.** R21 said the tuple fails
  "only on a file that is not already in WAL", which reads as "fine for our files". Whether it fails is a
  function of the **file's journal mode** — the very thing the probe exists to find out — and a file already
  in WAL accepts the tuple from a read-only connection (measured, SQLite 3.53.1), so a probe built on it
  would answer exit `4` on some inputs and nothing at all on others. The paragraph now says that and says
  what `open_probe` asks for instead: the mode, and no pragma.
  §7 goes 29 → **30** inline scenarios (one for the flag's four inputs); the `.feature` corpus does not
  move — 17 files, **240** scenarios (228 `Scenario` + 12 `Scenario Outline`).
- **spec_revision 11 (2026-11-20): the probe runs before the write, and `init`'s refusal can be reached
  (HIFA-7, third review round).** `version` 4 → 5: §2.6's `reindex` declaration carries the new order and
  R21's contract changes shape. Three corrections, the first two measured on SQLite 3.53.1:
  (1) **R21 created the file it promised not to touch.** Revision 10 opened the database read-write
  before the `sqlite_master` verdict, and `connect(readonly=False)` on a **missing** path creates it
  (4096 bytes, empty schema, SPEC-030 §2.1): `database_not_found` was unreachable — the command answered
  `not_a_hifa_database` for a file it had just created — and "before it writes anything" was false.
  R21 now prints three ordered steps: (a) the path exists? no ⇒ exit `3` `database_not_found`, nothing
  opened; (b) a **read-only** open (`mode=ro`) and the verdict plus `require_fts5` ⇒ exit `3` or `6`,
  nothing written; (c) only then the writable connection and the transaction. Both of the first two
  leave the file byte-identical (measured: a foreign file keeps its sha and its 8192 bytes, and no
  sidecar appears). The probe opens by URI mode and **not** with `connect(readonly=True)` — that
  function applies `PRAGMAS`, whose first entry is `journal_mode=WAL`, a header write that answers
  `attempt to write a readonly database` on a file that is not already in WAL, i.e. exit `4` on exactly
  the input the step exists to name.
  (2) **`init`'s exit `2` was unreachable.** §5.2 ran the "no `workspace` table" probe **after** the
  migrator, and `001_init.sql` creates `workspace` on any `user_version`-0 file: applied to a foreign
  file, the migration leaves `workspace` and `issue` behind and the state detection reads
  `already_initialized`. The probe is now §5.2 step 5, on the same read-only open R21 uses, **before**
  step 6's migrator: a foreign file is refused at `2` with no table created and no migration run, an
  empty file (no objects at all) is seeded as R14a says, `--force` is the override §6 already named, and
  D-066's two carriers are both reachable. (3) **A count that did not match its own list**: the
  revision-10 note said "the six statements" and then enumerated eight; it now says eight between
  `BEGIN IMMEDIATE` and `COMMIT`. §7 gains one inline scenario for the missing path (exit `3`,
  `database_not_found`, and no file created) and one assertion to the foreign-file scenario (the bytes are
  the same as before the command): **28 → 29** inline scenarios; the `.feature` corpus does not move —
  17 files, **240** scenarios (228 `Scenario` + 12 `Scenario Outline`).
- **spec_revision 10 (2026-11-20): the reindex sequence runs, and the foreign file has a code of its own
  (HIFA-7, second review round, D-066).** `version` 3 → 4, because §2.6's `reindex` declaration is what a
  regeneration reads and it now carries the verdict and the statement sequence §2.7's row serves, and
  §2.7 names the exit of each failure; the exit set of the row does not move in that revision
  (`0, 3, 6`; revision 13 adds the `4`). Two
  corrections, both of them a prescription that could not be executed as written:
  (1) **The sequence of R21 was not runnable, and the reason is where the triggers live.** Revision 9
  sent the `CREATE VIRTUAL TABLE` and "the three triggers of SPEC-090 §3.1, verbatim" into the
  transaction. The three are `AFTER INSERT/DELETE/UPDATE ON issue`, so they belong to `issue` and not to
  the index: `DROP TABLE issue_fts` takes the virtual table and its shadow tables and **leaves the three
  triggers in `sqlite_master`** (verified on SQLite 3.53.1), and the verbatim `CREATE TRIGGER issue_fts_ai`
  of a database that lost only the table raises `trigger issue_fts_ai already exists` — inside the
  transaction, so the run §7 promised (exit `0`, the index intact) could not be produced. R21 now prints
  the eight statements it means, between `BEGIN IMMEDIATE` and `COMMIT` (ten counting those two):
  `CREATE VIRTUAL TABLE IF NOT EXISTS`, `DROP TRIGGER IF EXISTS` for each of
  the three names, the three `CREATE TRIGGER`s, then `VALUES('rebuild')` — one transaction, SPEC-090 §3.1
  still the canonical DDL the block must match column-for-column, and every starting state (both present,
  table gone, triggers gone, both gone) ending at exit `0` with the index rebuilt and the triggers back.
  §7's recreate scenario says which state it pins and asserts the triggers, and §7's refusal scenario
  splits in two.
  (2) **`not_a_hifa_database` returns to this table at exit `3`, and `corrupt_database` keeps the
  images.** Revision 9's item (2) — the one below, kept as written — took the code out of R21 and sent
  "another tool's SQLite file" to `corrupt_database`; that input does not answer it. A valid foreign file
  opens, passes `require_fts5`, has its virtual table created and fails the rebuild with `no such table:
  main.issue`, which is SPEC-030 §6's `DB_NOT_MIGRATED` — a code about a schema that was never hifa's. R21
  now reads `sqlite_master` **before any write** and answers exit `3` `not_a_hifa_database` for a file
  without `workspace`/`issue`; `corrupt_database` (exit `6`) is the file SQLite itself refuses
  (`file is not a database`, `database disk image is malformed`). One code, two edges, two exits —
  `init`'s `2` for the `--db` value it will not overwrite, `reindex`'s `3` for the database it cannot
  index — and §6 carries both rows, `NotAHifaDatabase` among them. §7 goes 27 → **28** inline scenarios;
  the `.feature` corpus does not move — 17 files, **240** scenarios (228 `Scenario` + 12 `Scenario
  Outline`).
- **spec_revision 9 (2026-11-20): `db reindex` gains one repair path, one exit set and one idempotence
  invariant (HIFA-7 review round).** `version` 2 → 3, because R21's contract changes. Three corrections,
  each of them a contradiction the previous revision left standing:
  (1) **A missing `issue_fts` is recreated, not refused.** Revision 8 sent it to `critical` in C8 with
  the advice `run "hifa db migrate"`, and `db migrate` cannot do it: SPEC-030 §2.3 applies only the
  migrations above `user_version`, and the database that lost its virtual table has none to apply. R21
  now declares the `CREATE VIRTUAL TABLE IF NOT EXISTS` (+ the three triggers of SPEC-090 §3.1, verbatim)
  that precedes the rebuild inside the same transaction (revision 10 corrects the "verbatim" — the
  triggers do not leave with a dropped table, so the sequence is now printed in full), C8 and the `CheckResult` catalog send both
  halves of the check to `hifa db reindex`, and there is **one** repair path instead of two that worked
  or one that did not.
  (2) **The exit set is `0, 3, 6`, and `not_a_hifa_database` leaves it.** Revision 8 gave R21 a fifth code
  for a fourth situation — exit `2` for a file with no `workspace` table — while §2.7's row, SPEC-050
  §2.6, SPEC-090 §11 and this spec's own §6 table all published `0,3,6`. A foreign file is
  `corrupt_database`, exit `6`: that is what the corpus already maps "not a readable hifa database" to
  (SPEC-030 §6, `file is not a database` / `database disk image is malformed`), and `db reindex` has no
  input a user types, so nothing of it is a `2`. `not_a_hifa_database` survives where it was born — the
  `hifa init` refusal of R14c — and §6 says so in its row.
  (3) **"the same database bytes" was unachievable and is gone.** A second commit moves the header's
  change counter, the WAL and the FTS5 shadow tables, so no test could assert it. What R21 promises now
  is the invariant that is testable: reindexing twice leaves **the same row count in `issue_fts` and the
  same search results**. §7's two reindex scenarios were rewritten for the two halves (the index that is
  gone is rebuilt; the file that is not a hifa database is refused) and a third pins the row-count
  idempotence: 25 → **27** inline scenarios.
- **spec_revision 8 (2026-11-20): `hifa db reindex` has a contract down to its transaction (HIFA-7).**
  `version` 1 → 2: §2.7's row gains `--quiet` and the exit code `6`, R21 is rewritten, and §6 gains the
  two codes that row can now produce. In terms of the system: (1) **the verb the search cannot live
  without is the only one of the `db` group this revision contracts** — `db migrate`, `db vacuum`,
  `db path` and `hifa doctor` stay declared and unimplemented, one ticket each, and no row moved;
  (2) **`reindexed` counts every row of `issue`**, trashed and archived included, because the index
  holds them (visibility is a query predicate, SPEC-090 R10): after the rebuild `COUNT(*)` of
  `issue_fts` equals `COUNT(*)` of `issue`, which is what `10-search.feature` asserts;
  (3) **the two failures are loud and named** — `fts5_unavailable` when the interpreter's SQLite has no
  `ENABLE_FTS5`, `corrupt_database` when `issue_fts` is missing or the image is malformed — and neither
  is answered with `{"reindexed": 0}`, which is the *empty database*, an exit-0 answer;
  (4) **C8 stops sending a missing table to `reindex`**, which could never repair it: a missing
  `issue_fts` is `critical` and names `db migrate`; the warning is the out-of-sync index, whose only
  fix is the rebuild; (5) **the code of a missing file is `database_not_found`**, not `db_not_found`:
  §6's row, its scenario and SPEC-050 §6 now agree on the one name.
- **spec_revision 7 (2026-11-16): the seed gains one shared `In Review` at `4.5` (D-058).** §2.5's
  `DEFAULT_STATES` is now `Triage(1.0) · Todo(2.0, is_default=1) · Backlog(3.0) · In Progress(4.0) ·
  In Review(4.5) · Done(5.0) · Canceled(6.0)`: one column for spec review and code review alike, of type
  `started` and colour `#BB87FC` — the values SPEC-050 §2.7 already printed for a hand-created
  `In Review`, and the position `between(4.0, 5.0)` yields. No state that exists today changes its
  number, so a fresh team and a six-state team that runs `hifa state create 'In Review' --type started
  --color '#BB87FC' --before Done` (SPEC-050 §2.5.6) hold the same seven. `started` is the type the
  template carries twice — the pre-D-013 bespoke list had a state named `In Review` too, until D-013
  removed it (SPEC-050 §11, spec_revision 3) — which is why §3.1 invariant 3 counts rows **per type**
  instead of one per type.
  No data migration is written and §3.1's `schema_migration` count is untouched: the old team is one
  command away (D-058).
- **spec_revision 6 (2026-11-11): `init` writes `.hifa.toml` only inside a git work tree (D-041,
  closes P-12).** The write guard of R13 is stated in terms of `find_project_root` (§2.1), the same
  notion of project root that bounds the walk-up of R3 — so the file is written where a project
  exists and nowhere else. Outside a work tree `init` writes nothing, keeps exit `0`, resolves the
  database through R1 as always (the global one of R5 when no other source speaks) and reports
  `config: none (outside a git work tree)` (§2.7); `--force` lifts nothing, because there is no
  file inside the tree to rewrite. The guard is about the **write**, never about the resolution:
  `--db`, `HIFA_DB` and an ancestor `.hifa.toml` keep winning the path, and nothing about R1-R6
  changes. R3 is restated with the `.git` bound that had been implemented since P-11 without a
  sentence in this spec.
- **spec_revision 5 (2026-11-11): the canonical order puts `Todo` before `Backlog` (D-034).**
  `DEFAULT_STATES` is now `Triage(1.0) · Todo(2.0, is_default=1) · Backlog(3.0) · In Progress(4.0) ·
  Done(5.0) · Canceled(6.0)`: same six names, same types, same colors, same `is_default`, only the
  `position` of `Todo` and `Backlog` swapped. For the user, `Backlog` is "some day" and `Todo` is
  "the next thing", so the board reads left to right in the order work is actually pulled. The
  change reaches **new** teams through this template and **existing** teams through
  `hifa state update --before` (SPEC-050 §2.5.6), never through a data migration: `workflow_state`
  rows are not rewritten by any migration, and a team that was already initialized keeps whatever
  order its owner gives it (D-034).
- **spec_revision 3 (2026-11-08, Fix Agent, spec-review-03):** application of D-011…D-020.
  - **D-013 (gap 1/2):** the canonical seed is the one in §2.5 (`DEFAULT_STATES`: Triage, Backlog, Todo,
    In Progress, Done, Canceled; `Todo` with `is_default=1`) — **the `Todo`/`Backlog` order of this
    bullet was superseded by D-034 in spec_revision 5** (see the first bullet of this section), and
    `In Review` joined the seed in spec_revision 7 (D-058). SPEC-050 §2.5.1
    now references this table instead of repeating its own list, and its `--json` envelope is the
    `BootstrapResult` of §2.5.
  - **Gap 6:** the `.hifa.toml` format keeps a single shape (the one in §2.3). `[defaults] team/member`
    was removed from SPEC-050: the default team is `[cli].default_team` and the member
    only `--member`/`HIFA_MEMBER` (§2.3, member bullet).
  - **Gap 8:** `hifa db path` is declared here and SPEC-050 copies it; `hifa db reindex` has a single
    envelope (`{"reindexed": N}`, exit `0,3` — `0,3,6` since rev 8, R21: the two `6`s are the index's
    unavailability, `fts5_unavailable` and `corrupt_database`) and `hifa db doctor` does not exist (alias
    of `hifa doctor`, which is the only `doctor`).
  - **D-014:** `1` is exclusive to `doctor`; the global map (2 validation, 3 not found,
    4 conflict, 5 transition, 6 internal) remains in force for the rest of the commands.
- **Gap not applied (§6.1 vs D-014):** the rule "`1` is exclusive to `doctor`" depends on
  SPEC-020 §6 and SPEC-040 §6 no longer mapping `HIFA_ERROR`, `RANKING_DEGRADED` and `UOW_NESTED` to `1`.
  Those two specs are not in the scope of this Fix Agent (`020`/`040` stay with `HIFA_ERROR` ⇒
  exit 1 in their table). When D-014 is applied to them, the three codes must move to `6`
  (`internal_error`); until then the contradiction is recorded here and in SPEC-050 §11.
- spec_revision 4 (2026-11-11): translated to English (repo-wide language convention).
