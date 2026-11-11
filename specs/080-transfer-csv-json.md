---
id: SPEC-080
title: CSV and JSON transfer
component: application
status: review
version: 1
spec_revision: 4
depends_on: [SPEC-000, SPEC-010, SPEC-040]
implements:
  - src/hifa/app/transfer.py
  - src/hifa/adapters/sqlite/transfer_sql.py
  - src/hifa/cli/commands/transfer.py
  - tests/**/transfer*
acceptance: specs/acceptance/080-transfer-csv-json.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-11
---

# SPEC-080 — CSV and JSON transfer

## 1. Responsibility and boundaries

**Does:** imports and exports issues as CSV using a documented 11-column format, exports and
imports the whole workspace as versioned JSON, resolves the entities referenced by name (states,
labels, members, projects, cycles), deduplicates against what already exists and reports, per row,
everything it rejects. All work happens in transactional batches.

**Does NOT do (explicit):**

- It does not define the schema: tables, columns and constraints belong to SPEC-010. This component
  creates no tables and no new migrations.
- It does not apply state transition rules or issue ranking: `state_id` is written exactly as it
  resolves; `sort_order`/`board_order` are left at their default (0) on create.
- It does not write `issue_history`: the import generates no audit events (that belongs to SPEC-040).
- It neither imports nor exports comments, attachments or relations in CSV (the CSV format does
  not carry them). Only the full JSON includes them.
- It does not create a `cycle` from CSV: a cycle requires `starts_at`/`ends_at` that the CSV does not
  bring. An unknown `Cycle` is a row error, never an implicit creation.
- It does not download or resolve URLs: no `http(s)://`, no `file://`, no remote attachments.
- It does not remap IDs in JSON: `id` is preserved byte for byte (see R20). A merge between two
  different workspaces is out of scope.
- It does not validate the semantic content of the `description` markdown.

## 2. Public contract

### 2.1 Types and signatures

```python
# src/hifa/app/transfer.py
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

CSV_COLUMNS: tuple[str, ...] = (
    "Title", "Description", "Priority", "Status", "Labels",
    "Assignee", "Project", "Cycle", "Estimate", "Due Date", "Identifier",
)

PRIORITY_BY_NAME: dict[str, int] = {
    "no priority": 0, "none": 0, "urgent": 1, "high": 2, "medium": 3, "low": 4,
}
PRIORITY_LABEL: dict[int, str] = {
    0: "No priority", 1: "Urgent", 2: "High", 3: "Medium", 4: "Low",
}

JOURNAL_ORDER: tuple[str, ...] = (
    # 1. root
    "workspace",
    # 2. team + key history (team_previous_key -> team)
    "team", "team_previous_key",
    # 3. team catalog
    "member", "team_membership", "workflow_state", "label",
    # 4. grouping entities
    "project", "project_milestone", "cycle",
    # 5. issues and satellites
    "issue", "issue_label", "issue_relation", "comment", "attachment", "issue_history",
    # 6. migration ledger (no FK)
    "schema_migration",
)
# 17 tables = the 17 from `CREATE TABLE` in SPEC-010 §3 (D-006/D-009). The order is topological:
# every table appears after all the ones it references (FK), except self-references
# (team.parent_id, label.parent_id, issue.parent_id, comment.parent_id), which R20 orders
# topologically inside the table itself.

@dataclass(frozen=True)
class RowError:
    line: int                 # 1-based, the header is line 1
    column: str | None        # CSV column that caused it, or None if it is about the row
    code: str                 # see §6.2
    message: str              # human-readable text, in English, with no extra personal data

@dataclass(frozen=True)
class ImportReport:
    total_rows: int
    created: int
    updated: int
    skipped: int
    failed: int
    errors: list[RowError]
    warnings: list[str]
    created_entities: dict[str, int]   # {"workflow_state": 1, "label": 2, "project": 0, "member": 0}
    dry_run: bool
    committed_batches: int
    duration_ms: int

@dataclass(frozen=True)
class ImportOptions:
    team_key: str | None = None
    delimiter: str | None = None       # None => sniff between "," and ";"
    encoding: str = "utf-8-sig"
    strict: bool = False               # implies fail_fast + no_create_* + strict_columns
    strict_columns: bool = False
    dry_run: bool = False
    create_states: bool = True
    create_labels: bool = True
    create_projects: bool = True
    create_members: bool = False
    cycle_by_date: bool = False
    on_conflict: Literal["skip", "update", "fail"] = "fail"
    fail_fast: bool = False
    batch_size: int = 500              # 1..5000
    default_state: str | None = None   # state name for rows with an empty Status

@dataclass(frozen=True)
class ExportOptions:
    team_key: str | None = None
    include_archived: bool = False
    include_trashed: bool = False
    delimiter: str = ","
    encoding: str = "utf-8"
    newline: str = "\n"

@dataclass(frozen=True)
class JsonExportOptions:
    include_history: bool = True
    pretty: bool = False               # indent=2 vs compact (separators=(",", ":"))

@dataclass(frozen=True)
class JsonImportOptions:
    mode: Literal["merge", "replace"] = "merge"
    batch_size: int = 500
    dry_run: bool = False
    force: bool = False                # mandatory if mode="replace"

@dataclass(frozen=True)
class ExportReport:
    rows: int
    path: Path
    bytes_written: int
    tables: dict[str, int]             # JSON only

def import_csv(
    uow: UnitOfWork,
    path: Path,
    opts: ImportOptions,
    *,
    actor: Actor,
    clock: Clock,
    idgen: IdGen,
    allowed_root: Path | None = None,
) -> ImportReport

def export_csv(
    db: Database,
    path: Path,
    opts: ExportOptions,
    *,
    allowed_root: Path | None = None,
) -> ExportReport

def export_json(
    db: Database,
    path: Path,
    opts: JsonExportOptions,
    *,
    clock: Clock,
    allowed_root: Path | None = None,
) -> ExportReport

def import_json(
    uow: UnitOfWork,
    path: Path,
    opts: JsonImportOptions,
    *,
    actor: Actor,
    clock: Clock,
    idgen: IdGen,
    allowed_root: Path | None = None,
) -> ImportReport

def resolve_path(path: Path, allowed_root: Path) -> Path
def parse_priority(raw: str) -> int
def parse_due_date(raw: str) -> str | None
def csv_safe(value: str) -> str
def build_match_key(title: str, due_date: str | None) -> tuple[str, str]
```

### 2.1.1 Consumed contract (SPEC-030 / SPEC-040)

Writes happen inside `with uow.write() as tx:` (SPEC-030 §2.2, `BEGIN IMMEDIATE`, `ROLLBACK` +
re-raise on exception) and reads with `db.ro_connection()` or `db.connect()`.
`Clock.now()` returns an aware UTC `datetime`; `IdGen.new()` returns a ULID `str`;
`Actor.member_id` is the member that creates the imported issues.

| Origin | Operation | Signature used |
|---|---|---|
| `tx.teams` | workspace | `ensure_workspace(*, name, url_key) -> Workspace`; `workspace() -> Workspace \| None` |
| `tx.teams` | team | `get_by_key(workspace_id, key) -> Team \| None` |
| `tx.teams` | states | `states(team_id, include_archived=False)`; `get_state_by_name(team_id, name)`; `default_state(team_id)`; `insert_state(state)` |
| `tx.labels` | labels | `get_by_name(workspace_id, team_id, name)`; `insert(label)` |
| `tx.projects` | projects | `list(team_id=…)`; `insert(project)` |
| `tx.cycles` | cycles | `list(team_id)` |
| `tx.issues` | issues | `next_number(team_id)`; `get_by_identifier(identifier)`; `get(issue_id)`; `insert(issue)`; `update(issue)`; `set_labels(issue_id, label_ids)`; `label_ids(issue_id)` |
| `tx.members` | members | `get_by_email(email)`; `find_by_display_name(display_name) -> list[Member]`; `insert(member)` |
| `transfer_sql` | JSON dump/load | `dump_table(conn, table, columns) -> list[dict]`; `load_table(conn, table, columns, rows) -> int`; `delete_all(conn, tables) -> int` |

`tx.members` covers the `member` table, which SPEC-030 §3 assigns to the adapter; its three methods
are fixed here because SPEC-030 §2.4 does not list a `MemberRepo`. The dump/load SQL for JSON lives
in `adapters/sqlite/transfer_sql.py` (SPEC-000 A3: `app/` does not write SQL); `transfer.py` only
passes it table/column names validated against an allowlist.

### 2.2 CSV mapping (normative)

| # | CSV column | Target | Type | Required | Notes |
|---|---|---|---|---|---|
| 1 | `Title` | `issue.title` | text | **yes** | trim; empty ⇒ `missing_title` error |
| 2 | `Description` | `issue.description` | markdown | no | empty ⇒ `NULL` |
| 3 | `Priority` | `issue.priority` | 0-4 | no | empty ⇒ `0`; accepts a number or a name (§R3) |
| 4 | `Status` | `workflow_state.name` → `issue.state_id` | text | no | empty ⇒ `default_state`; with no default ⇒ `Todo` or the team's `is_default=1` |
| 5 | `Labels` | `issue_label` | comma-separated list | no | trim per item; empty ones ignored |
| 6 | `Assignee` | `issue.assignee_id` | email or `display_name` | no | §R7 |
| 7 | `Project` | `issue.project_id` | name | no | §R8 |
| 8 | `Cycle` | `issue.cycle_id` | name or number | no | §R9 |
| 9 | `Estimate` | `issue.estimate` | decimal ≥ 0 | no | empty ⇒ `NULL` |
| 10 | `Due Date` | `issue.due_date` | `YYYY-MM-DD` | no | §R5 |
| 11 | `Identifier` | deduplication key | `TEAM-123` | no | never sets `number` (§R13) |

**Accepted header aliases** (`casefold` + trim comparison; column order is free):
`Description`←`description`; `Priority`←`prioridad`; `Status`←`State`; `Labels`←`Label`,`Tags`;
`Assignee`←`Assigned To`,`Responsible`; `Project`←`Proyecto`;
`Cycle`←`Sprint`; `Estimate`←`Points`,`Story Points`;
`Due Date`←`DueDate`,`due_date`,`Deadline`; `Identifier`←`Key`,`ID`.
A header outside that list is `unknown_column` (warning; error with `strict_columns`).
A missing `Title` ⇒ fatal error `missing_title_column` (exit 2, no row is processed).

`Priority` accepts, besides the number:

| Input | `priority` |
|---|---|
| `No priority`, `None`, `P0`, `0` | 0 |
| `Urgent`, `P1`, `1` | 1 |
| `High`, `P2`, `2` | 2 |
| `Medium`, `P3`, `3` | 3 |
| `Low`, `P4`, `4` | 4 |

### 2.3 CLI

| Command | Flags | Output (`--json`) | Exit codes |
|---|---|---|---|
| `hifa import FILE` | `--format {csv,json}` (default: autodetected from the `FILE` extension), `--team`, `--mode {merge,replace}` (default `merge`; `replace` only with `--format json`), `--force`, `--dry-run`, `--on-conflict {skip,update,fail}` (default `fail`), `--strict`, `--strict-columns`, `--no-create-states`, `--no-create-labels`, `--no-create-projects`, `--create-members`, `--cycle-by-date`, `--default-state`, `--batch-size`, `--fail-fast`, `--delimiter`, `--encoding` | `ImportReport` | 0, 2, 3, 4 |
| `hifa export` | `--format {csv,json}` (default `csv`), `--out PATH` (default `-` = stdout), `--team`, `--all` (includes archived and trashed), `--no-history`, `--pretty` | `ExportReport` | 0, 2, 3 |

**Single shape of the command (D-012, SPEC-050).** There is **one** command per operation:
`hifa import FILE` and `hifa export`. Neither accepts the format as a positional argument (there is
no format subcommand): it is resolved with `--format` and, if missing, by the `FILE` extension
(`.csv` → csv, `.json` → json; unknown extension ⇒ `unsupported_format`, exit 2). An explicit
`--format` wins over the extension; if the two contradict each other (`data.json --format csv`) the
flag is used and the warning `format_overrides_extension` is emitted. Canonical examples:

```
hifa import issues.csv --team ENG
hifa import backup.json --mode replace --force
hifa export --out output.csv
hifa export --format json --out backup.json
```

`hifa import` and `hifa export` are the CLI shell of this component: column mapping, entity
resolution and row-by-row validation live in `app/transfer.py`. The subset
`--format/--dry-run/--on-conflict/--team` and `--out/--all` is declared in SPEC-050 §2.5; the
remaining flags in the table are this spec's contract and SPEC-050 must incorporate them when
implemented (bump of its `spec_revision`). `--all` is equivalent to
`--include-archived --include-trashed`.

Human output of `import`: one summary line
`imported 120 rows: 98 created, 20 updated, 2 skipped, 0 failed (3 entities created) in 412 ms`
and, if there are errors, the `line | column | code | message` table on stderr (max. 50 rows; the
rest is summarized as `… and N more`). `--dry-run` prefixes `DRY RUN (rolled back): ` to the
summary.

#### 2.3.1 Execution combination matrix (normative)

Batch transactionality **is not a flag**: `import` always groups rows into batches of `batch_size`
and each batch is a transaction (`BEGIN IMMEDIATE` … `COMMIT`), with a `SAVEPOINT row_<n>` per row.
What the flags change is the **scope** of the transaction and what happens on the first error:

| `--dry-run` | `--strict` | `--fail-fast` | Effective transaction | Result | Exit |
|---|---|---|---|---|---|
| — | — | — | one per batch (default `batch_size=500`) | isolated row errors; the rest is committed | 2 if `failed>0`, otherwise 0 |
| ✔ | — | — | one per batch, **`ROLLBACK` when closing each batch** | report identical to the real run with `dry_run=true` and `committed_batches=0`; the database does not change | the same as the real run |
| — | — | ✔ | one per batch; stops at the first row error | batches **already committed stay** (`committed_batches` = complete batches) | 2 |
| — | ✔ | (implicit) | a **single** one for the whole file (`batch_size` is ignored, warning `batch_size_ignored`) | first error ⇒ full `ROLLBACK`: `created=0, updated=0, committed_batches=0` | 2 |
| ✔ | ✔ | (implicit) | a single one, `ROLLBACK` | same report as the real `--strict` + `dry_run=true`; nothing written | the same as the real `--strict` |
| ✔ | — | ✔ | one per batch, `ROLLBACK` per batch, stops at the first error | partial report with `committed_batches=0` | 2 |

**Invalid** combinations (validated **before** opening the file, exit 2,
`Validation(rule='invalid_flag_combination')`, 0 rows read):

| Combination | Why |
|---|---|
| `--strict` + `--on-conflict skip\|update` | `--strict` sets `on_conflict='fail'`; asking for another one is a contradiction |
| `--strict` + `--create-members` | `--strict` implies `create_members=False` |
| `--strict` + `--mode replace` | `replace` deletes and reinserts (it is not a strict data validation) |
| `--batch-size` outside `1..5000` | `invalid_batch_size` |

**Valid but redundant** combinations (exit 0, warning `redundant_flag`): `--strict` +
`--fail-fast`, `--strict` + `--strict-columns`, `--strict` + `--no-create-states`,
`--strict` + `--no-create-labels`, `--strict` + `--no-create-projects`.

`--dry-run` does **not** have a "validate only" path: it walks the same pipeline as the real run
(parsing, resolution, implicit creation, numbering, inserts, updates, `SAVEPOINT` per row) and the
only difference is `COMMIT` → `ROLLBACK` (§5.1 step 7). That is why the `ImportReport` of a
`--dry-run` is comparable field by field with the one from the real run.

### 2.4 JSON envelope

```json
{
  "format": "hifa.workspace",
  "schema_version": 1,
  "exported_at": "2026-11-08T15:04:05Z",
  "app_version": "0.1.0",
  "counts": {"workspace": 1, "team": 1, "issue": 120},
  "tables": {
    "workspace": [{"id": "01J...", "name": "Ginkelte", "url_key": "ginkelte", "created_at": "..."}],
    "issue": [{"id": "01J...", "workspace_id": "01J...", "team_id": "01J...", "number": 1,
               "identifier": "ENG-1", "title": "…", "description": null, "priority": 2,
               "priority_sort_order": 0.0, "estimate": null, "due_date": "2026-11-30",
               "state_id": "01J...", "assignee_id": null, "creator_id": null,
               "project_id": null, "milestone_id": null, "cycle_id": null, "parent_id": null,
               "sort_order": 0.0, "board_order": 0.0, "sub_issue_sort_order": 0.0,
               "started_at": null, "completed_at": null, "canceled_at": null,
               "snoozed_until_at": null, "archived_at": null, "auto_archived_at": null,
               "trashed": 0, "branch_name": null, "created_at": "...", "updated_at": "..."}]
  }
}
```

- `format` is the literal `"hifa.workspace"`; any other value ⇒ `unsupported_format`.
- `schema_version` is the `PRAGMA user_version` of the exported database (today `1`).
- `tables` **always** contains the 17 keys of `JOURNAL_ORDER` (all the tables of the current
  SPEC-010 DDL), with `[]` when the table is empty. `issue_history` is omitted only if
  `include_history=False`. `team_previous_key` is exported so that old identifiers keep resolving
  after a restore; `schema_migration` is exported as evidence of the ledger but is **never
  imported** (the destination ledger rules): in `import_json` it is counted as `skipped` with the
  warning `schema_migration_not_imported`.
- The keys of each row are **exactly** the SPEC-010 §3 column names in `snake_case`; `NULL` travels
  as `null`, not as `""`.

## 3. Data it touches

| Table | Operation | Invariant |
|---|---|---|
| `workspace` | SELECT | a single row; the CSV import fails if there are 0 or >1 |
| `team` | SELECT | `key` resolves the target team (by default `--team` or `[cli].default_team`) |
| `workflow_state` | SELECT/INSERT | `UNIQUE(team_id, name)`; the created state belongs to the target team |
| `label` | SELECT/INSERT | labels created with `team_id = NULL` (workspace scope, SPEC-010 R6) |
| `member` | SELECT/INSERT | `email` is `UNIQUE`; `display_name` is **not** unique (the basis of the ambiguity) |
| `project` | SELECT/INSERT | no `UNIQUE` exists: resolution by name can be ambiguous across teams |
| `cycle` | SELECT | read-only; `UNIQUE(team_id, number)` |
| `issue` | INSERT/UPDATE | `UNIQUE(team_id, number)`, `UNIQUE(identifier)`, `CHECK(priority BETWEEN 0 AND 4)`, `CHECK(due_date GLOB …)` |
| `issue_label` | INSERT/DELETE | PK `(issue_id, label_id)`; with `--on-conflict update` the whole set is replaced |
| `issue_fts` | INSERT/UPDATE (via triggers) | never written directly; the SPEC-090 triggers maintain the index |
| `issue_history` | INSERT (JSON import only) | `id INTEGER AUTOINCREMENT`; the `id` from the file is preserved |
| `comment`, `attachment`, `issue_relation`, `team_membership`, `project_milestone` | INSERT (JSON only) | SPEC-010 FKs |

Invariants this component **must** hold when any import finishes:

1. No `issue.state_id` outside the issue's `team_id` (composite FK, SPEC-010 R4).
2. No `due_date` with a format other than `YYYY-MM-DD` **and** a real calendar date.
   The SPEC-010 `CHECK` does not distinguish `2026-02-30`, so the validation is at the application
   level.
3. `identifier = team.key || '-' || number` on every row created by the import.
4. `issue.number` is the team's `MAX(number)+1`, computed inside the same transaction as the INSERT.

## 4. Business rules

- **R1 — Header (REQ-F080-01).** The first row is read as the header. Each header is normalized
  (`strip` + `casefold`) and mapped through the alias table of §2.2. An unknown header produces
  `unknown_column` (warning) and the column is ignored; with `strict_columns` it is an error and the
  row is not processed (the whole file is rejected before the first write). If `Title` is missing,
  it aborts with `missing_title_column` without writing anything.
- **R2 — Encoding and delimiter (REQ-F080-02).** The file is opened in text mode with the given
  `encoding` (`utf-8-sig` by default, which consumes the BOM if present). If the BOM appears and the
  encoding is plain `utf-8`, it is detected and removed anyway: the first header never contains
  `\ufeff`. With `delimiter=None`, `,` and `;` are tried in that order, counting separators in the
  first line outside quotes; if the count is 0 for both, `,` is used. A `UnicodeDecodeError` aborts
  with `invalid_encoding` (exit 2) before writing.
- **R3 — Priority (REQ-F080-03).** `parse_priority` normalizes (`strip` + `casefold`) and accepts an
  integer `0`..`4` or the names of §2.2. Empty string ⇒ `0`. A value out of range or unknown ⇒
  `invalid_priority`. The import **never** writes a `priority_sort_order` other than `0.0` on
  create.
- **R4 — Status (REQ-F080-04).** It is resolved by `name` against the target team's
  `workflow_state` (`casefold` + trim, only `archived_at IS NULL`). If it does not exist and
  `create_states=True`, it is created with `type` inferred from the following table,
  `color = '#6B6F76'` and `position = COALESCE(MAX(position),0)+1.0`. If it does not exist and
  `create_states=False` ⇒ `unknown_state`. An empty `Status` uses `default_state`, and if there is
  none either, the team's state with `is_default=1`, and if there is none, the first state with
  `type='unstarted'` by `position`.

  | Status (normalized) | Inferred `type` |
  |---|---|
  | `triage` | `triage` |
  | `backlog` | `backlog` |
  | `todo`, `to do`, `unstarted`, `ready` | `unstarted` |
  | `in progress`, `in review`, `started`, `doing` | `started` |
  | `done`, `completed`, `closed` | `completed` |
  | `canceled`, `cancelled`, `wontfix` | `canceled` |
  | anything else | `unstarted` |

- **R5 — Due Date (REQ-F080-05).** Strict `YYYY-MM-DD` is accepted
  (`datetime.date.fromisoformat` over a string of exactly 10 characters with `-` at positions 4 and
  7). `2026-02-30`, `2026-13-01`, `30/11/2026`, `2026-11-8` and `2026-11-30T00:00:00Z` are rejected
  with `invalid_due_date`. Empty string ⇒ `NULL`. The date is not "fixed" nor timezone-adjusted.
- **R6 — Estimate (REQ-F080-06).** Finite `float` ≥ 0 (intermediate `Decimal` to avoid `1e309`).
  Empty ⇒ `NULL`. `-1`, `abc`, `NaN`, `inf` ⇒ `invalid_estimate`. The value is not validated against
  the team's `issue_estimation_type` (an import may carry another scale); it is rounded to 4
  decimals.
- **R7 — Assignee (REQ-F080-07).** Resolution order, all `casefold` + trim:
  1. `member.email = value` → unique match (`email` is `UNIQUE`).
  2. `member.display_name = value` → if there are ≥ 2 matches ⇒ `ambiguous_assignee` (the message
     lists the candidates' emails, never their full names).
  3. No match: with `create_members=True` a `member` is created with `email = value` if it contains
     `@`, or `display_name = value` and `email = NULL` if it does not; `is_active=1`, `is_admin=0`,
     `timezone = team.timezone`. Without the flag ⇒ `unknown_assignee`.
  4. Empty ⇒ `assignee_id = NULL` (the existing assignee is not touched on an update).
- **R8 — Project (REQ-F080-08).** It is looked up by `name` (`casefold` + trim) among projects with
  `team_id = target team OR team_id IS NULL`. If there is more than one, the one with a non-null
  `team_id` wins; if they are still tied ⇒ `ambiguous_project`. No match: with
  `create_projects=True` it creates
  `project(status_type='planned', priority=0, workspace_id=workspace, team_id=team)`, otherwise
  `unknown_project`.
- **R9 — Cycle (REQ-F080-09).** The value is normalized by removing the `cycle`/`ciclo`/`sprint`
  prefix and `#`. If an integer remains, it is resolved by `cycle.number`; if not, by `cycle.name`
  (`casefold`), both within the target team. No match ⇒ `unknown_cycle` (it is never created).
  With `cycle_by_date=True` and an empty `Cycle`, `Due Date` is used to pick the cycle that contains
  it (`starts_at <= due_date < ends_at`, comparison over the first 10 characters): if 0 cycles ⇒
  `cycle_id = NULL`; if ≥ 2 cycles overlap on that date ⇒ `ambiguous_cycle` with the list of
  `number`s. SPEC-010 does not impose non-overlap, so the case is reachable and **must** fail
  instead of picking one arbitrarily.
- **R10 — Labels (REQ-F080-10).** The value is split on `,`, trimmed, empty items dropped and
  deduplicated by `casefold` keeping the first text. Maximum 32 labels per issue; more ⇒
  `too_many_labels`. Each name is resolved by `name` (`casefold`) among `(workspace-wide)
  OR (target team)`, preferring the one with a non-null `team_id`. No match: with
  `create_labels=True` it is created workspace-wide (`team_id = NULL`) with a deterministic color
  `PALETTE[sum(ord(c) for c in casefold(name)) % 12]` (a 12-hex palette fixed in the module),
  otherwise ⇒ `unknown_label`. On an update, the set replaces the previous one (delete + insert of
  `issue_label`).
- **R11 — Input identifier (REQ-F080-11).** If present, it must match
  `^[A-Z][A-Z0-9]{1,9}-[0-9]{1,9}$` **after** `strip().upper()`. The prefix must be the target
  team's `key`; otherwise ⇒ `identifier_team_mismatch`. The input `identifier` is used only as a
  deduplication key: it never defines `number` and is never written as is on a new row.
- **R12 — Deduplication (REQ-F080-12).** A row's identity key is:
  1. normalized `identifier`, if the row brings one; otherwise,
  2. `(team_id, casefold(trim(title)), due_date or "")`.

  It is compared against the database and against the rows already accepted in this same run.
  Result:
  - it exists in the database ⇒ `on_conflict` applies (`skip` | `update` | `fail` | `duplicate`).
  - repeated inside the file ⇒ `duplicate_in_file` (always a row error, `on_conflict` does not
    apply, because two equal rows in the same file are a malformed file).
  - an issue exists with the same `(title, due_date)` but a **different** identifier ⇒ warning
    `possible_duplicate` and the row is inserted anyway (it is not merged by heuristic).
- **R13 — Numbering (REQ-F080-13).** `number = COALESCE(MAX(number),0)+1` for the team, read and
  written inside the batch transaction (`BEGIN IMMEDIATE`); `identifier = team.key || '-' ||
  number`. On `UNIQUE constraint failed: issue.team_id, issue.number`, the row is retried up to 3
  times recomputing the number; on the fourth failure the row is marked `number_conflict`.
- **R14 — `on_conflict` (REQ-F080-14).** `skip` counts `skipped` and writes nothing;
  `update` updates **only** `title, description, priority, state_id, assignee_id, project_id,
  cycle_id, estimate, due_date` (+ the label set) and `updated_at`; `fail` produces
  `duplicate_issue`. `id`, `number`, `identifier`, `created_at`, `parent_id`, `sort_order` are never
  updated.
- **R15 — `--strict` (REQ-F080-15).** It is equivalent to `fail_fast=True`, `create_states=False`,
  `create_labels=False`, `create_projects=False`, `create_members=False`, `strict_columns=True` and
  `on_conflict='fail'`. It also forces a **single transaction** for the whole file (ignoring
  `--batch-size` with warning `batch_size_ignored`): with `strict`, the first row error aborts the
  run and everything written is `ROLLBACK`ed. The report returns `created=0, updated=0,
  committed_batches=0` and the created entities do not exist afterwards. Combinations that
  contradict what `strict` implies are `invalid_flag_combination` (§2.3.1).
- **R16 — `--dry-run` (REQ-F080-16).** It runs the full pipeline (parsing, resolution, implicit
  creation, numbering, inserts, updates, `SAVEPOINT` per row) through the **same** path as the real
  run and the only difference is that it closes with `ROLLBACK` instead of `COMMIT` (§5.1 step 7,
  §2.3.1); there is no "validate only" branch. The `ImportReport` must be identical to the real
  run's except for `dry_run=True` and `committed_batches=0`, and the exit code is the same one the
  real run would have. The database changes neither in `user_version` nor in counts.
- **R17 — Batches and row errors (REQ-F080-17).** Rows are grouped into batches of `batch_size`
  (1..5000; out of range ⇒ `invalid_batch_size`). Each batch is a transaction. Each row is wrapped
  in `SAVEPOINT row_<n>`; if the row fails (application error **or** `sqlite3.IntegrityError`),
  `ROLLBACK TO row_<n>` is done, the `RowError` is recorded and the next row continues. A **batch**
  failure (connection exception, unrecoverable `OperationalError`, `database is locked` after 3
  retries) reverts the whole batch and, unless `fail_fast`, continues with the next batch.
- **R18 — CSV export (REQ-F080-18).** It always writes the 11 columns of `CSV_COLUMNS` in that
  order, with a header, `QUOTE_MINIMAL` quoting, **no** `\r\n` (the `ExportOptions` `newline` is
  used, default `\n`), and internal line breaks preserved inside quoted fields. Row order:
  `team.key ASC, issue.number ASC`. `trashed = 1` and `archived_at IS NOT NULL` are excluded by
  default (`include_trashed`/`include_archived` add them). `Priority` is written as a label
  (`No priority`/`Urgent`/`High`/`Medium`/`Low`), `Labels` as names sorted alphabetically and joined
  with `, `, `Assignee` as `email` if it exists and `display_name` otherwise, `Due Date` as
  `YYYY-MM-DD` or empty, `Identifier` always present.
- **R19 — Round-trip idempotency (REQ-F080-19).** `export_csv` followed by `import_csv` with
  `on_conflict='skip'` on the same database creates and modifies no rows: `created=0,
  updated=0, failed=0` and `skipped = ` the number of exported issues. With `update`, the result is
  an observable no-op (same values, same `updated_at` untouched if nothing changed).
- **R20 — JSON: order and preservation (REQ-F080-20).** `export_json` walks `JOURNAL_ORDER`
  (parents before children). `import_json` inserts in that order and, in `mode='replace'`, deletes in
  reverse order inside the **same** transaction. The file's `id`s are preserved (never regenerated)
  and so is `issue_history.id`; `sqlite_sequence` is left at the maximum inserted. Self-references
  are sorted topologically before writing: `team.parent_id`, `label.parent_id` (groups before
  children), `issue.parent_id` (parents before sub-issues) and `comment.parent_id`. A cycle in those
  self-references ⇒ `cyclic_reference`.
- **R21 — CSV injection (REQ-S080-03).** On export, every text value (not numeric, not date) that
  starts with `=`, `+`, `-`, `@`, `\t` or `\r` is prefixed with an apostrophe (`'`) through
  `csv_safe`. This includes titles and descriptions. The rule is export-only: the import does **not**
  strip leading apostrophes (a title that really starts with `'` is preserved as is).
- **R22 — Paths (REQ-S080-01, SPEC-000 REQ-S004).** `resolve_path(path, allowed_root)` does
  `(allowed_root / path).resolve()` and requires `is_relative_to(allowed_root.resolve())`; it rejects
  any `..` component that leaves the root and any symlink whose resolved target escapes, even if the
  symlink name is inside. For writing, the final component **cannot** be a symlink (`lstat`), and
  the file is written with `tempfile.NamedTemporaryFile(dir=parent)` + `os.replace`, never opening
  the destination directly. Violation ⇒ `path_not_allowed` (exit 2). `allowed_root` defaults to the
  process `cwd`; the CLI does not accept a different root in v1.
- **R23 — Parameterized SQL (REQ-S080-02, SPEC-000 REQ-S002).** No query interpolates values: the
  bulk insert uses `executemany` with `?`, and the `IN (...)` of the export filters are built with
  one placeholder per item (`?,?,?`), never with the value embedded. JSON import table/column names
  are validated against an allowlist (`JOURNAL_ORDER` and the `PRAGMA table_info` columns) before
  entering the SQL.
- **R24 — Sizes (REQ-N080-03).** An input file larger than **64 MiB** is rejected (`file_too_large`,
  exit 2), as is a JSON with more than **200 000** total rows. The CSV is processed in streaming
  (`csv.reader` over the file object): the whole file is never materialized in memory.

### 4.1 Edge case catalog (normative)

| # | Case | Expected behavior |
|---|---|---|
| B1 | Unknown column (`Sprint Name`, `Team`) | warning `unknown_column` with the name; the column is ignored; exit 0. With `--strict-columns` or `--strict`: exit 2, 0 rows written |
| B2 | `Due Date = 2026-02-30` | `RowError(line, "Due Date", "invalid_due_date")`; the rest of the file is imported; exit 2 |
| B3 | `Labels = "bug, frontend"` with `frontend` nonexistent and `--no-create-labels` | `unknown_label` on that row; `bug` is **not** associated (the whole row is rejected, there is no partial association); exit 2 |
| B4 | `Assignee = "Ana"` with two members `Ana Pérez` and `Ana Soto` | `ambiguous_assignee`, message with both emails; the row is not written; exit 2 |
| B5 | Empty `Cycle`, `--cycle-by-date`, `Due Date` inside two overlapping cycles | `ambiguous_cycle` with `#3, #4`; the row is **not** inserted (no cycle is picked at random); exit 2 |
| B6 | CSV saved by Excel with a BOM (`\ufeffTitle`) | the BOM is consumed; the `Title` header is recognized; normal import; exit 0 |
| B7 | CSV with `;` as separator and commas inside quoted `Labels` | delimiter detected as `;`; `Labels` = `["bug","ui"]`; exit 0 |
| B8 | 10 000 rows, `--batch-size 500` | 20 transactions; peak RAM < 120 MB; < 8 s; no duplicated row; exit 0 |
| B9 | `Description` with line breaks, `"` quotes and a `;` | the field is parsed completely (one logical row); on export it is re-exported byte-identical after normalizing the EOL to `\n` |
| B10 | `Priority = "P1"` / `Urgent` / `1` in three rows | all three end up with `priority = 1` |
| B11 | `Priority = "ASAP"` | `invalid_priority` on that row; the others are imported |
| B12 | File with only the header | `total_rows=0`, `created=0`, exit 0, message `0 rows` |
| B13 | Empty file (0 bytes) | `empty_file`, exit 2, no writes |
| B14 | Same `Identifier` in two rows of the file | second row ⇒ `duplicate_in_file` (not `skipped`), exit 2 |
| B15 | `Identifier = OPS-7` in an import with `--team ENG` | `identifier_team_mismatch`; the row is not written |
| B16 | `--dry-run` with 3 new labels | report says `3 entities created` and `committed_batches=0`; after the run `SELECT count(*) FROM label` did not change |
| B17 | `export csv /tmp/../etc/out.csv` (relative path that escapes) | `path_not_allowed`, exit 2, the file is not created |
| B18 | Export destination is a symlink to `/etc/passwd` | `path_not_allowed`, exit 2, the target is not touched |
| B19 | `Estimate = "1e309"` | `invalid_estimate` (not `inf`) |
| B20 | Title `=HYPERLINK("http://x")` exported | the cell comes out as `'=HYPERLINK("http://x")`; on re-import, the title comes back with the apostrophe (documented in R21) |

### 4.2 Non-functional and security requirements

| ID | Requirement | Verification |
|---|---|---|
| **REQ-N080-01** | Import 10 000 CSV rows in **< 8 s** (p50, reference host, local SSD, `synchronous=NORMAL`). | `tests/perf/test_transfer_speed.py`, `time.perf_counter` around `import_csv`, 3 runs, median |
| **REQ-N080-02** | CSV export of 10 000 issues with **≤ 2 queries** and **< 2 s**; no N+1. | `sqlite3.Connection.set_trace_callback` counter in the test; assert `count("SELECT") <= 2` |
| **REQ-N080-03** | Peak RSS **< 120 MB** importing 10 000 rows (SPEC-000 REQ-N001); input max. 64 MiB (R24). | `resource.getrusage(RUSAGE_SELF).ru_maxrss` in a subprocess |
| **REQ-N080-04** | JSON import/export of 5 000 issues in **< 10 s** and **< 150 MB** RSS. | same perf harness, fixture of 5 000 issues with labels |
| **REQ-N080-05** | `app/transfer.py` does not import `sqlite3` (it does import stdlib `csv`) nor `fastapi`/`typer`/`jinja2` (SPEC-000 A1/A2). | `import-linter` + `grep -rn "import sqlite3" src/hifa/app/transfer.py` (checklist §8.11) |
| **REQ-S080-01** | Every path is validated against `allowed_root` with `resolve()` + `is_relative_to`; `..` and escaping symlinks are rejected; atomic write without following a symlink at the destination (R22, SPEC-000 REQ-S004). | B17, B18 + test with `tmp_path` and `os.symlink` |
| **REQ-S080-02** | Zero value interpolation in SQL: `executemany` with `?` and generated placeholders; table/column names against an allowlist (R23, SPEC-000 REQ-S002). | test that imports a JSON with a key `"; DROP TABLE issue; --` and verifies `unknown_field` without executing SQL |
| **REQ-S080-03** | Export neutralizes spreadsheet formulas with a `'` prefix in cells that start with `= + - @ TAB CR` (R21). | B20 |
| **REQ-S080-04** | Neither import nor export opens sockets: there is no URL download and no DNS resolution. | test that monkeypatches `socket.socket` to fail and runs both flows |

## 5. Flow

### 5.1 `import_csv`

```
 1. resolve_path(path, allowed_root)                 → path_not_allowed if it escapes (R22)
 2. stat: size ≤ 64 MiB                              → file_too_large (R24)
 3. open streaming with encoding + BOM handling      → invalid_encoding
 4. read header, normalize, map aliases              → missing_title_column / unknown_column
 5. resolve target team (--team → [cli].default_team → single team)  → team_not_found
 6. load team caches: states, labels, members, projects, cycles
 7. BEGIN IMMEDIATE                   # uow.write() (SPEC-030 §2.2); a single tx for the whole
                                      # file if --strict (§2.3.1)
     for each batch of batch_size rows:      # with --strict: a single batch = the whole file
       ── the batch opens here: the tx is already open from the BEGIN IMMEDIATE above ──
       per row:
         SAVEPOINT row_n
         parse cells (priority, date, estimate, labels, identifier)
         resolve entities (create if the flag allows it)
         deduplicate (identifier → title+due) and apply on_conflict
         INSERT/UPDATE issue (+ issue_label)
         RELEASE row_n
         row error → ROLLBACK TO row_n, RowError, continue with the next row
       ── end of batch: a single close, outside the row loop ──
       with --strict and failed > 0 → full ROLLBACK, committed_batches = 0, exit 2
       if dry_run               → ROLLBACK          # same path, nothing persists
       if the batch failed      → ROLLBACK          # connection error / database is locked
       if not                   → COMMIT            # one per batch, after the last row of the batch
       with --fail-fast: stop the file after the batch that failed (batches already committed stay)
 8. build ImportReport (counters + errors + created entities)
 9. exit 0 if failed = 0, otherwise exit 2
```

`dry_run` changes no step: it only changes the batch close, `COMMIT` → `ROLLBACK`, in step 7
(`committed_batches=0`). With `--strict` the close is never `COMMIT`: the first error triggers a full
`ROLLBACK` and exit 2, and the rows of all batches — including those that had already finished
without error — are reverted.

### 5.2 `export_csv`

```
1. resolve_path(path, allowed_root)          → path_not_allowed
2. single query: issue JOIN team JOIN workflow_state LEFT JOIN member
   WHERE (team) AND trashed/archived according to flags
   ORDER BY team.key, issue.number            (R18)
3. add labels in a second query (issue_id IN (?,?,…)), grouped in memory
4. write the CSV_COLUMNS header and one row per issue, applying csv_safe (R21)
5. write to a tempfile in the destination directory + os.replace (R22)
6. ExportReport(rows, path, bytes_written)
```

No N+1: at most 2 queries, regardless of the number of issues (REQ-N080-02).

### 5.3 `export_json` / `import_json`

```
export: 1. BEGIN (consistent read)
        2. per table in JOURNAL_ORDER: SELECT * (no ORDER BY; order is not contract)
        3. counts + envelope + atomic write
import: 1. read the whole JSON, validate format/schema_version/tables  → unsupported_format,
           schema_too_new, malformed_json
        2. if mode=replace: require force, DELETE in reverse JOURNAL_ORDER order
        3. per table in JOURNAL_ORDER (17): validate columns against PRAGMA table_info, sort
           self-references topologically, INSERT with executemany in batch_size batches.
           `schema_migration` is skipped (skipped + warning schema_migration_not_imported)
        4. after issue: INSERT INTO issue_fts(issue_fts) VALUES('integrity-check')
           if it fails → 'rebuild' and warning fts_rebuilt
        5. COMMIT (or ROLLBACK if dry_run)  → ImportReport(rows=Σ inserted)
```

## 6. Errors

### 6.1 Mapping by edge

| Error | When | CLI (exit) | HTTP |
|---|---|---|---|
| `Validation` — `path_not_allowed` | path escapes the root or the destination is a symlink | 2, stderr | — |
| `Validation` — `missing_title_column` | header without `Title` | 2 | — |
| `Validation` — `empty_file` | 0 bytes / no header | 2 | — |
| `Validation` — `invalid_encoding` | bytes do not decode with the requested encoding | 2 | — |
| `Validation` — `file_too_large` | > 64 MiB | 2 | — |
| `Validation` — `invalid_batch_size` | outside 1..5000 | 2 | — |
| `Validation` — `invalid_flag_combination` | contradictory flag combination (§2.3.1) | 2 | — |
| `Validation` — `malformed_json` | invalid JSON or no `tables` | 2 | — |
| `Validation` — `unsupported_format` | `format != "hifa.workspace"` | 2 | — |
| `Validation` — `schema_too_new` | `schema_version` > max supported | 2 | — |
| `Validation` — `cyclic_reference` | circular self-reference in team/label/issue/comment | 2 | — |
| `Validation` — row errors (§6.2) | at least one row rejected | 2 | — |
| `NotFound` — `file_not_found` | input path nonexistent or unreadable | 3 | — |
| `NotFound` — `team_not_found` | `--team` does not exist in the workspace | 3 | — |
| `Conflict` — `destination_exists` | `import_json --mode replace` without `--force` | 4 | — |

`import_csv` never returns exit 1, 5 or 6; the only `4` is `destination_exists` from an
`import --mode replace` without `--force` (D-014). Row errors do **not** abort the process: they
accumulate and the exit code summarizes the result of the whole batch. This component exposes no
HTTP endpoints in v1 (SPEC-060 may wrap it; in that case `Validation` maps to 422 and `NotFound` to
404 without changing the codes in this table).

### 6.2 `RowError.code` codes

| `code` | Meaning | Effect |
|---|---|---|
| `missing_title` | empty `Title` | row rejected |
| `invalid_priority` | priority not parseable or outside 0-4 | row rejected |
| `invalid_due_date` | date not `YYYY-MM-DD` or nonexistent in the calendar | row rejected |
| `invalid_estimate` | not numeric, negative, `NaN`/`inf` | row rejected |
| `unknown_state` | nonexistent `Status` and `create_states=False` | row rejected |
| `unknown_label` | nonexistent label and `create_labels=False` | row rejected, no partial association |
| `too_many_labels` | > 32 labels in one row | row rejected |
| `unknown_assignee` | nonexistent assignee and `create_members=False` | row rejected |
| `ambiguous_assignee` | ≥ 2 equal `display_name` | row rejected |
| `unknown_project` | nonexistent project and `create_projects=False` | row rejected |
| `ambiguous_project` | ≥ 2 projects with the same name in tied scopes | row rejected |
| `unknown_cycle` | nonexistent cycle (it is never created) | row rejected |
| `ambiguous_cycle` | ≥ 2 cycles cover the `Due Date` | row rejected |
| `identifier_team_mismatch` | `Identifier` prefix ≠ `team.key` | row rejected |
| `invalid_identifier` | does not match `^[A-Z][A-Z0-9]{1,9}-[0-9]{1,9}$` | row rejected |
| `duplicate_in_file` | identity key repeated in the same file | row rejected |
| `duplicate_issue` | it exists in the database and `on_conflict='fail'` | row rejected |
| `number_conflict` | 3 numbering retries exhausted | row rejected |

Each `RowError.line` is 1-based counting the header as line 1, so the reported line matches the one
a text editor shows for files without multi-line fields. In files with multi-line fields, `line` is
the **record number**, not the physical line number; the human report adds `record` to the message
(`row 42: …`).

## 7. Acceptance Criteria

```gherkin
@SPEC-080
Feature: Issue import and export in CSV and JSON

  Background:
    Given a migrated database with team "ENG" and its 6 default workflow_states
    And a workspace with member "ana@example.com" whose display_name is "Ana Pérez"

  Scenario: minimal import from a CSV
    Given a CSV with header "Title,Description,Priority,Status,Labels,Assignee,Project,Cycle,Estimate,Due Date,Identifier"
    And a row with Title "Broken login", Priority "High", Status "Todo", Due Date "2026-11-30"
    When "hifa import issues.csv --team ENG" is executed
    Then the exit code is 0
    And an issue exists with title "Broken login" and priority 2
    And the issue has due_date "2026-11-30"
    And the issue has identifier "ENG-1"

  Scenario: priority accepts a number and a label alike
    Given three rows with Priority "1", "Urgent" and "urgent"
    When "hifa import issues.csv --team ENG" is executed
    Then all three issues have priority 1
    And the report says created 3 and failed 0

  Scenario: implicit creation of states and labels
    Given a CSV with Status "In Review" and Labels "bug, frontend"
    And no workflow_state named "In Review" and no label "frontend"
    When "hifa import issues.csv --team ENG" is executed
    Then the workflow_state "In Review" is created with type "started"
    And the label "frontend" is created with team_id NULL
    And the report says 2 entities created
    And the exit code is 0

  Scenario: nonexistent label with --no-create-labels rejects the row
    Given a CSV with Labels "bug, frontend" where "frontend" does not exist
    When "hifa import issues.csv --team ENG --no-create-labels" is executed
    Then the exit code is 2
    And the report contains a RowError with code "unknown_label" on line 2
    And the issue is not created
    And the label "bug" is not associated with any issue

  Scenario: impossible date rejected per row without aborting the file
    Given a 3-row CSV where the second row has Due Date "2026-02-30"
    When "hifa import issues.csv --team ENG" is executed
    Then the exit code is 2
    And the report says created 2 and failed 1
    And the RowError has code "invalid_due_date" on line 3
    And no issue exists with due_date "2026-02-30"

  Scenario: assignee by email and by display_name
    Given a row with Assignee "ana@example.com" and another with Assignee "Ana Pérez"
    When "hifa import issues.csv --team ENG" is executed
    Then both issues have assignee_id equal to member "ana@example.com"
    And the exit code is 0

  Scenario: ambiguous assignee fails with the candidates
    Given two members with display_name "Ana" and different emails
    And a CSV with Assignee "Ana"
    When "hifa import issues.csv --team ENG" is executed
    Then the exit code is 2
    And the RowError has code "ambiguous_assignee"
    And the error message contains both emails

  Scenario: dry-run writes nothing
    Given a valid 5-row CSV that would create 2 new labels
    When "hifa import issues.csv --team ENG --dry-run" is executed
    Then the exit code is 0
    And the report says created 5 and dry_run true
    And committed_batches is 0
    And the issue count is still 0
    And the label count did not change

  Scenario: --strict reverts everything on the first error
    Given a CSV where row 2 is valid and row 3 has Priority "ASAP"
    When "hifa import issues.csv --team ENG --strict" is executed
    Then the exit code is 2
    And the report says created 0 and committed_batches 0
    And the issue count is still 0

  Scenario: deduplication by identifier
    Given an existing issue "ENG-7" in the database
    And a CSV with Identifier "ENG-7" and a different Title
    When "hifa import issues.csv --team ENG --on-conflict skip" is executed
    Then the exit code is 0
    And the report says skipped 1 and created 0
    And the title of issue "ENG-7" did not change

  Scenario: deduplication by title and date
    Given an issue "Broken deploy" with due_date "2026-11-30" in team ENG
    And a CSV without Identifier with Title "broken DEPLOY" and Due Date "2026-11-30"
    When "hifa import issues.csv --team ENG" is executed
    Then the report says skipped 1 and created 0
    And the issue count in team ENG did not change

  Scenario: update of mutable fields with --on-conflict update
    Given an issue "ENG-7" with priority 0
    And a CSV with Identifier "ENG-7", Priority "Urgent" and Title "new title"
    When "hifa import issues.csv --team ENG --on-conflict update" is executed
    Then issue "ENG-7" has priority 1 and title "new title"
    And the issue number is still 7
    And created_at did not change

  Scenario: CSV round-trip is idempotent
    Given a database with 40 issues, labels and assignees
    When "hifa export --out output.csv" is executed
    And "hifa import output.csv --team ENG" is executed
    Then the exit code is 0
    And the report says created 0, updated 0 and failed 0
    And the issue count is still 40

  Scenario: JSON export and replace import preserve IDs and FK order
    Given a database with 2 teams, 120 issues, 300 comments and 3 relations
    When "hifa export --format json --out backup.json" is executed
    And "hifa import backup.json --mode replace --force" is executed on an empty database
    Then the ids of the 120 issues are identical to the ones in the file
    And the comment count is 300
    And PRAGMA foreign_key_check returns no rows
    And issue_fts answers the search for an imported title

  Scenario: paths that escape the directory are rejected
    Given a destination "../etc/out.csv"
    When "hifa export --out ../etc/out.csv" is executed
    Then the exit code is 2
    And the message contains "path_not_allowed"
    And the file /etc/out.csv does not exist

  Scenario: file with BOM and semicolon delimiter
    Given a CSV with a UTF-8 BOM, delimiter ";" and Labels "bug, ui"
    When "hifa import issues.csv --team ENG" is executed
    Then the header is recognized with the Title column
    And the issue has exactly 2 labels
    And the exit code is 0

  Scenario: 10 000 rows are imported in batches
    Given a CSV of 10 000 valid rows
    When "hifa import issues.csv --team ENG --batch-size 500" is executed
    Then the exit code is 0
    And the total time is under 8 seconds
    And the process peak RSS is under 120 MB
    And the issue count is 10000
    And the identifiers go from ENG-1 to ENG-10000 without gaps
```

## 8. Regeneration checklist

1. Read `SPEC-000` + `SPEC-010` + this spec in full; there is no need to read any other code.
2. Create `src/hifa/app/transfer.py` with the constants, dataclasses and functions of §2.1 without
   changing names or signatures. No SQL in the module: writes through `with uow.write() as tx`,
   reads through `db.ro_connection()`, JSON dump/load through `adapters/sqlite/transfer_sql.py`.
3. Implement `parse_priority`, `parse_due_date`, `build_match_key`, `csv_safe` and `resolve_path`
   first: they are the four functions the rest of the module uses and the ones concentrating the
   most edge cases (R3, R5, R12, R21, R22).
4. Implement `import_csv` in the order of §5.1: open/BOM → header → team caches → entity
   resolution → dedup → write. Batches are implemented with `SAVEPOINT row_<n>` and `ROLLBACK TO`;
   never a per-row `try/except` without a savepoint (it leaves the batch in a dirty state).
5. Implement `export_csv` with at most two queries and atomic writing via tempfile + `os.replace`.
6. Implement `export_json`/`import_json` with the envelope of §2.4 and `JOURNAL_ORDER`, delegating
   the SQL to `transfer_sql.dump_table/load_table/delete_all`; those helpers validate table and
   columns against `PRAGMA table_info` before building the statement (R23).
7. Create `src/hifa/cli/commands/transfer.py` with the **2** commands of §2.3 (`import FILE`,
   `export`), delegating all output to `cli/output.py` (SPEC-000 A6). No `print` inside
   `app/transfer.py`. No format subcommands: `--format` + autodetection by extension.
8. Cover every `code` of §6.2 with at least one test that produces it (the case table of §4.1 as the
   source of fixtures).
9. Verify: `pytest tests -k transfer` green, including the scenarios of §7 and the 10 000-row case.
10. `python scripts/spec_contract_check.py --spec specs/080-transfer-csv-json.md`: the 11 headers
    of `CSV_COLUMNS`, the 3 priority dicts, the 2 command signatures and the 17 entries of
    `JOURNAL_ORDER` (against `CREATE TABLE` in SPEC-010) must match without drift.
11. Confirm with `grep -rn "import sqlite3" src/hifa/app/transfer.py` that there is no direct SQLite
    access outside `uow.connection` and that `import-linter` passes (SPEC-000 A1).

## 9. Provenance

- Columns `Title, Description, Assignee, Priority, Status, Labels, Project, Cycle, Estimate,
  Due Date`: decided in this spec (open decision #3, closed here) and normative in §2.
- `Priority` 0-4 with labels `No priority/Urgent/High/Medium/Low`: the enum of SPEC-020, printed
  verbatim so an export round-trips.
- `Status` → `workflow_state.name` with a 6-value `type`: a CSV carries free state names, so `type`
  is inferred from the team's existing states and never guessed from the text.
- `Due Date` as a calendar date without a time: SPEC-010 D3.
- Deduplication by `identifier` and by `(title, due_date)`: `identifier` is the stable key of an
  issue; the title+date pair covers CSVs without `Identifier`.
- `SAVEPOINT` per row + transaction per batch: our own measurement of a 10 000-row import
  (commit per row ≈ 40× slower than per batch of 500 on the reference host).
- `csv_safe` (`'` prefix): standard CSV/formula injection mitigation (OWASP: CSV Injection).
- Path validation with `resolve()` + `is_relative_to` + `lstat` + `os.replace`: requirement
  REQ-S004 of SPEC-000, applied to import/export.
- Decision not to create `cycle` from CSV: `cycle.starts_at`/`ends_at` are mandatory in SPEC-010
  and the CSV does not carry them, so the import would have to invent dates.

## 10. Out of scope

- Import of comments, attachments, relations or history from CSV (only JSON carries them).
- Merge between two different workspaces (ULIDs are preserved; they would collide).
- Remapping of `team.key` during the import (`previousIdentifiers` belongs to SPEC-020/040).
- Incremental streaming import with a resumable checkpoint (if the process dies, it is re-run).
- Duplicate detection by fuzzy title matching (exact normalized equality only).
- Formats other than CSV/JSON: no XLSX, no JSONL, no NDJSON, no ZIP.

## 11. Notes

- spec_revision 4 (2026-11-11): translated to English (repo-wide language convention).
- **r3 (`spec_revision: 3`) — batch close (D-023).** The §5.1 step 7 pseudocode did `COMMIT` inside
  the row loop, against the prose of §2.3.1/R17 ("one transaction per batch") and against R15
  (`--strict` ⇒ a single tx for the whole file). Canon applied: `BEGIN IMMEDIATE` on entering the
  batch → `SAVEPOINT row_<n>` per row → **a single batch close, after the last row**: `COMMIT` in
  the normal run, `ROLLBACK` with `--dry-run` or if the batch failed, full `ROLLBACK` + exit 2 with
  `--strict` and `failed > 0`. `--dry-run` walks the same path and ends in `ROLLBACK`. No changes in
  the §2.3.1 matrix, in the defaults (`on_conflict='fail'`) nor in the §2 contract.
- **r2 (`spec_revision: 2`) — review blockers closed.**
  1. **Flag matrix (§2.3.1, R15/R16):** `--strict` forces a single transaction for the whole file and
     `--dry-run` walks the same path closing with `ROLLBACK`; contradictory combinations
     (`--strict` + `--on-conflict skip|update`, `--strict` + `--create-members`,
     `--strict` + `--mode replace`) are `invalid_flag_combination` (exit 2) and are detected before
     opening the file.
  2. **A single shape of the command:** `hifa import FILE` (with `--format` autodetected by
     extension) and `hifa export`; the format subcommands were removed (two shapes of the same
     command, one by extension and one by `--format`) and the examples and scenarios were fixed.
     `--mode`/`--force` were added to the flag table, which the scenarios already used without being
     declared.
  3. **Complete `JOURNAL_ORDER`:** 17 tables (previously 15). `team_previous_key` and
     `schema_migration` were missing; the order is topological with respect to the FKs of the
     SPEC-010 DDL and self-references are still resolved with the topological sort of R20.
     `schema_migration` is exported but not imported (the destination ledger rules).
- **Drift fixed along the way:** R14 said `skip` was the default of `--on-conflict`, against §2.3
  and `ImportOptions` (`fail`). `fail` remains the single default.
- **Out of scope for this revision:** `--mode`/`--force` need their entry in SPEC-050 §2.5 (bump of
  that spec); here they are declared as this component's contract.
