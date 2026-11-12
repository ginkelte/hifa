---
id: SPEC-100
title: Configuration and bootstrap
component: support
status: review
version: 1
spec_revision: 6
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
updated: 2026-11-11
---

# SPEC-100 — Configuration and bootstrap

## 1. Responsibility and boundaries

**Does:** resolves the database path deterministically (`--db` → `HIFA_DB` → `.hifa.toml` →
XDG → home), parses and validates `.hifa.toml`, seeds a usable workspace with `hifa init`
(workspace + team + 6 workflow_states + labels + local member + settings), makes that seeding
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
  spec defines (rebuild of the FTS index), and it does not touch business data.
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
    already_initialized: bool

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
def reindex(conn: sqlite3.Connection) -> int      # returns the reindexed issue rows
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
| `hifa db reindex` | `--db` (global), `--json` | `{"reindexed": 10000}` | 0, 3 |

This table is the **only** row for `init`, `doctor` and `db`; SPEC-050 §2.5.1/§2.5.5 copies it
literally (D-013). `--name` is the workspace name (that flag, not `--workspace-name`); the
`--json` of `init` is the `BootstrapResult` of §2.5, not an ad-hoc object.

Human output of `init`:

```
initialized hifa at /home/vicho/proj/.hifa/hifa.db
  workspace  Ginkelte (ginkelte)
  team       ENG — Engineering 🚀
  states     Triage, Todo, Backlog, In Progress, Done, Canceled
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
  states     Triage, Todo, Backlog, In Progress, Done, Canceled
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
| `workflow_state` | 6 | the 6 tuples of `DEFAULT_STATES`, in that `position`; exactly one with `is_default = 1` (Todo) |
| `label` | 4 or 0 | the 4 from `DEFAULT_LABELS` with `team_id = NULL` (workspace scope, SPEC-010 R6); 0 if `--no-labels` |
| `member` | 1 or 0 | `name = member_name`, `display_name = member_name`, `email = member_email`, `initials` = first 2 letters uppercased, `timezone = opts.timezone`, `is_admin = 1`, `is_active = 1`, `created_at = clock.now()`; 0 if `--no-member` |
| `team_membership` | 1 or 0 | `(team_id, member_id, is_owner = 1, sort_order = 0)`; 0 if `--no-member` |

Invariants that `hifa init` guarantees on completion:

1. `SELECT COUNT(*) FROM workspace` = 1.
2. Exactly one `team` exists with `key = opts.team_key` in that workspace.
3. There are exactly 6 non-archived `workflow_state` rows, one for each `type` of
   `('triage','backlog','unstarted','started','completed','canceled')`, and exactly one with
   `is_default = 1`.
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
- **R10 — Exact seeding (REQ-F100-10).** The values of §3.1 are literal: 6 states with those
  names, types, colors and positions; 4 workspace-wide labels; team with `visibility='public'` and
  `issue_estimation_type='fibonacci'`. The IDs are new ULIDs (`ids.new()`), never derived from the
  name. `created_at`/`updated_at` come from `clock.now()` in ISO-8601 UTC.
- **R11 — Idempotency (REQ-F100-11).** Each seeding step is *get-or-create* by natural key:
  `workspace` by `url_key`, `team` by `(workspace_id, key)`, `workflow_state` by
  `(team_id, name)`, `label` by `(workspace_id, NULL, name)`, `member` by `email` if there is an email or
  by `display_name` if not, `team_membership` by `(team_id, member_id)`. Existing ones are added
  to `skipped` and are **not** updated. A second run with the same flags returns
  `created=[]`, `already_initialized=True` and exit 0.
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
- **R14 — Existing database detection (REQ-F100-14).** Three cases: (a) nonexistent file ⇒ it is
  created and seeded; (b) existing and migrated file (`user_version >= 1`) with a workspace ⇒
  `already_initialized` and only what is missing is completed; (c) existing file **without** a `workspace`
  table (it is not a hifa database) ⇒ `not_a_hifa_database` (exit 2) without writing. A
  `user_version` greater than the binary's maximum migration ⇒ `schema_too_new` (exit 4).
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
  | C8 | `fts_integrity` | `BEGIN; INSERT INTO issue_fts(issue_fts) VALUES('integrity-check'); ROLLBACK` | `database disk image is malformed` exception | any other `OperationalError` (table missing) → suggest `hifa db reindex` |
  | C9 | `counts` | `COUNT(*)` of `workspace, team, workflow_state, label, member, project, cycle, issue, comment` | — | — (always `info`) |

- **R19 — Exit code (REQ-F100-18).** `0` if there is no `warning` or `critical`; `1` if there is at least one
  `warning` and no `critical`; `2` if there is any `critical`; `3` if the database does not exist, cannot be
  opened or has no readable `user_version`. The `info` and `skipped` ones do not affect the exit code.
- **R20 — Read-only (REQ-F100-19).** `doctor` does not modify data: the only write statement
  is the one of C8, always inside a rolled-back transaction. `doctor` does not apply migrations, does not
  repair indexes and does not change permissions (it only reports them; hardening them belongs to `init`).
- **R21 — `hifa db reindex` (REQ-F100-20).** It runs
  `INSERT INTO issue_fts(issue_fts) VALUES('rebuild')` in a transaction and returns the number of
  indexed issues (`SELECT COUNT(*) FROM issue`). It is the action recommended by C8 and by rule
  R19 of SPEC-090 (rebuild after `VACUUM`). If the database does not exist ⇒ exit 3.
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
 5. open connection (SPEC-010 PRAGMAs) and run the migrator
       user_version > max(migrations) → schema_too_new                     (exit 4)
 6. detect state:
       workspace table missing  → not_a_hifa_database                      (exit 2)
       workspace and team exist → already_initialized = True
 7. BEGIN IMMEDIATE
       get-or-create workspace  (by url_key)
       get-or-create team       (by workspace_id, key)
       get-or-create 6 workflow_states (by team_id, name)
       get-or-create 4 labels   (by workspace_id, NULL, name)   if seed_labels
       get-or-create member     (by email | display_name)       if seed_member
       get-or-create team_membership
    COMMIT
 8. chmod(db_path, 0o600)
 9. find_project_root(cwd) is None            → write no config; report
                                                `config: none (outside a git work tree)`
    else if there is no .hifa.toml in [cwd..project root] (or --force)
                                                → write_config_file(0o600)  (+ .bak if --force)
    else                                        → report the existing file, write nothing
10. BootstrapResult(created, skipped, ids, db_path, already_initialized)
11. exit 0
```

The seeding of step 7 is a single transaction: if something fails, there is no workspace without a team and no
team without states. Step 5 commits before (migrations are not part of the seeding).

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
| `Validation` | `not_a_hifa_database` | the file exists but has no `workspace` table | exit 2 |
| `Conflict` | `schema_too_new` | `user_version` > the binary's maximum migration | exit 4 |
| `NotFound` | `db_not_found` | `doctor`/`reindex` on a nonexistent path | exit 3 |

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
| `fts_integrity` | warning / critical | `fts index out of sync — run "hifa db reindex"` |

No check invents new exit codes: `doctor` uses exclusively `0/1/2/3` from R19, and
`init` uses `0/2/4`. The `1` is **exclusive** to `doctor` (linter convention: 0 clean, 1 findings)
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
    And 6 non-archived workflow_states exist
    And exactly one workflow_state has is_default 1 and is called "Todo"
    And the types of the 6 states are triage, backlog, unstarted, started, completed, canceled
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
    And the message contains "db_not_found"

  Scenario: reindex repairs the FTS index
    Given a database where issue_fts was dropped
    When "hifa doctor" is executed
    Then the fts_integrity check is warning
    When "hifa db reindex" is executed
    Then the exit code is 0
    And the result shows the number of reindexed issues
    When "hifa doctor" is executed again
    Then the fts_integrity check is "ok"
    And the exit code is 0

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
    constants, the 6 states, the 4 labels and the signatures of `resolve_config`/`bootstrap`/`doctor`
    must match with no drift.
11. `grep -rn "import sqlite3" src/hifa/config.py` must return 0 lines.

## 9. Provenance

- Resolution order `--db` → `HIFA_DB` → `.hifa.toml` → XDG → home: pattern of CLI tools
  with local state (`git`, `direnv`, `poetry`), adapted to a single database file.
- XDG Base Directory (`$XDG_DATA_HOME`, default `~/.local/share`): freedesktop specification;
  `source="home"` explicitly distinguishes the fallback from the variable being set.
- `.hifa.toml` with `[workspace] [team] [cli] [web]`: format requested by Ginkelte (2026-11-08),
  parsed with stdlib `tomllib` to avoid adding dependencies (SPEC-000 REQ-N007).
- The 6 default states `Triage/Backlog/Todo/In Progress/Done/Canceled` and their `type`
  (`triage/backlog/unstarted/started/completed/canceled`): `workflow_state.type` of SPEC-010, with
  `is_default` on `Todo` so that a new issue always lands somewhere sane.
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
    bullet was superseded by D-034 in spec_revision 5** (see the first bullet of this section). There is no `In Review`. SPEC-050 §2.5.1
    now references this table instead of repeating its own list, and its `--json` envelope is the
    `BootstrapResult` of §2.5.
  - **Gap 6:** the `.hifa.toml` format keeps a single shape (the one in §2.3). `[defaults] team/member`
    was removed from SPEC-050: the default team is `[cli].default_team` and the member
    only `--member`/`HIFA_MEMBER` (§2.3, member bullet).
  - **Gap 8:** `hifa db path` is declared here and SPEC-050 copies it; `hifa db reindex` has a single
    envelope (`{"reindexed": N}`, exit `0,3`) and `hifa db doctor` does not exist (alias of `hifa doctor`,
    which is the only `doctor`).
  - **D-014:** `1` is exclusive to `doctor`; the global map (2 validation, 3 not found,
    4 conflict, 5 transition, 6 internal) remains in force for the rest of the commands.
- **Gap not applied (§6.1 vs D-014):** the rule "`1` is exclusive to `doctor`" depends on
  SPEC-020 §6 and SPEC-040 §6 no longer mapping `HIFA_ERROR`, `RANKING_DEGRADED` and `UOW_NESTED` to `1`.
  Those two specs are not in the scope of this Fix Agent (`020`/`040` stay with `HIFA_ERROR` ⇒
  exit 1 in their table). When D-014 is applied to them, the three codes must move to `6`
  (`internal_error`); until then the contradiction is recorded here and in SPEC-050 §11.
- spec_revision 4 (2026-11-11): translated to English (repo-wide language convention).
