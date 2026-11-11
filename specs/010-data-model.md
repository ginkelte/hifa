---
id: SPEC-010
title: Data model and migrations
component: persistence
status: approved
version: 2
spec_revision: 3
depends_on: [SPEC-000]
implements:
  - src/hifa/adapters/sqlite/migrations/**
acceptance: specs/acceptance/010-data-model.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-11
---

# SPEC-010 — Data model and migrations

## 1. Responsibility and boundaries

**Does:** define the complete SQLite schema (tables, constraints, indexes, views, FTS), the migration
ledger, the forward-only migration policy and the rules for identifier and order generation.

**Does NOT do:**

- It does not define business rules (SPEC-020), nor how queries are made (SPEC-030), nor
  input/output formats (SPEC-050/060/080).
- It contains no connection or UoW code: `connection.py`, `uow.py` and the repos belong to
  **SPEC-030**. This spec governs **only** `src/hifa/adapters/sqlite/migrations/**`; `migrate.py`
  is the implementation of the §2 contract and its file is governed by SPEC-030 (a single owner per
  file, so the drift check has no two culprits).
- It does not decide what is seeded in `hifa init` (SPEC-100) nor the search text (SPEC-090).

## 2. Public contract

> Normative. `migrate.py` implements exactly these signatures; the rest of the system consumes them without
> redefining them.

```python
# src/hifa/adapters/sqlite/migrate.py
from __future__ import annotations
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Sequence

MIGRATIONS_DIR: Final[str] = "migrations"  # package resource (importlib.resources)
MAX_VERSION: Final[int] = 3  # 001_init, 002_schema_migration, 003_team_previous_key


@dataclass(frozen=True)
class MigrationResult:
    applied: list[int]  # versions applied in this run (empty = already up to date)
    from_version: int
    to_version: int
    backup_path: str | None  # None if there was nothing to apply


def open_db(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection: ...


def schema_version(conn: sqlite3.Connection) -> int: ...


def apply_migrations(
    conn: sqlite3.Connection,
    *,
    migrations_dir: Path | None = None,  # None => the package's MIGRATIONS_DIR
    backup: bool = True,  # VACUUM INTO before the first pending migration
) -> MigrationResult: ...
```

| Symbol | Type | Stable | Notes |
|---|---|---|---|
| `open_db` | function | yes | the only connection factory: applies the PRAGMA of §4.2 R5.3, `row_factory=sqlite3.Row`, `isolation_level=None`, `check_same_thread=False`, `busy_timeout=5000`. With `readonly=True` it opens in `ro` mode and does not write |
| `schema_version` | function | yes | `PRAGMA user_version`; `0` on a virgin database. It does not read `schema_migration` |
| `apply_migrations` | function | yes | applies, in numeric order, the versions `> schema_version`, each in its own transaction; returns `MigrationResult`. Idempotent |
| `MAX_VERSION` | constant | yes | 3; a database with a higher `user_version` ⇒ `SchemaTooNew` |
| `MigrationResult` | dataclass | yes | exact shape as above; it is the `--json` of `hifa db migrate` (SPEC-050 §2.5.5) |

Reconciliation note: SPEC-030 §2 declares `connect(path, readonly=False)` with the same
behavior; `open_db` is the canonical name in this spec and `connect` is its historical alias. The
name convergence happens in the next `spec_revision` of 030 (the edges consume `open_db`).

Errors produced by the contract (full catalog in §6): `MigrationChecksumMismatch`,
`SchemaTooNew`, `BackupFailed`, `MigrationFailed`, `CorruptDatabase`, `Fts5Unavailable`.

## 3. Data it touches (DDL v1, normative)

Three files, applied in order. `PRAGMA user_version` ends at **3** and the schema has
**17 tables** (15 domain tables + `schema_migration` + `team_previous_key`), 3 views and the FTS5 index
`issue_fts` with its 3 triggers.

| File | Version | Contents |
|---|---|---|
| `migrations/001_init.sql` | 1 | 15 domain tables, indexes, views `v_priority`/`v_issue`/`v_team_progress`, `issue_fts` and its triggers |
| `migrations/002_schema_migration.sql` | 2 | `schema_migration` table (ledger, D-006) |
| `migrations/003_team_previous_key.sql` | 3 | `team_previous_key` table (historical keys, R3) |

No file contains `PRAGMA`: `journal_mode` cannot be changed inside a transaction and
the other PRAGMAs are per connection (§4.2 R5.3).

**`001_init.sql`, part 1 — domain tables** (creation order: parents → children):

```sql
CREATE TABLE workspace (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  url_key     TEXT NOT NULL UNIQUE,
  created_at  TEXT NOT NULL
);

CREATE TABLE team (
  id          TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspace(id),
  key         TEXT NOT NULL,
  name        TEXT NOT NULL,
  icon        TEXT,
  color       TEXT,
  description TEXT,
  timezone    TEXT NOT NULL DEFAULT 'America/Santiago',
  visibility  TEXT NOT NULL DEFAULT 'public'
              CHECK (visibility IN ('public','private','restricted')),
  parent_id   TEXT REFERENCES team(id),
  cycles_enabled        INTEGER NOT NULL DEFAULT 0,
  cycle_duration        INTEGER NOT NULL DEFAULT 2,
  cycle_start_day       INTEGER NOT NULL DEFAULT 1,
  issue_estimation_type TEXT NOT NULL DEFAULT 'fibonacci'
              CHECK (issue_estimation_type IN ('notUsed','exponential','fibonacci','linear','tShirt')),
  default_issue_estimate REAL,
  triage_enabled        INTEGER NOT NULL DEFAULT 0,
  auto_close_period     INTEGER,
  archived_at TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  UNIQUE (workspace_id, key),
  UNIQUE (workspace_id, id)          -- target of the composite FKs of issue/project
);

CREATE TABLE workflow_state (
  id         TEXT PRIMARY KEY,
  team_id    TEXT NOT NULL REFERENCES team(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  type       TEXT NOT NULL
             CHECK (type IN ('triage','backlog','unstarted','started','completed','canceled')),
  color      TEXT NOT NULL DEFAULT '#6B6F76',
  position   REAL NOT NULL,
  is_default INTEGER NOT NULL DEFAULT 0,
  archived_at TEXT,
  UNIQUE (team_id, name),
  UNIQUE (team_id, id)               -- target of the composite FK of issue (R4)
);

CREATE TABLE label (
  id           TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspace(id),
  team_id      TEXT REFERENCES team(id) ON DELETE CASCADE,
  parent_id    TEXT REFERENCES label(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  color        TEXT NOT NULL,
  description  TEXT,
  is_group     INTEGER NOT NULL DEFAULT 0,
  group_type   TEXT CHECK (group_type IN ('multiSelect','singleSelect')),
  retired_at   TEXT,
  CHECK (is_group = 0 OR parent_id IS NULL)
);
-- D-010: UNIQUE(workspace_id, team_id, name) does not work with team_id NULL (NULL != NULL).
CREATE UNIQUE INDEX uq_label_team ON label(workspace_id, team_id, name) WHERE team_id IS NOT NULL;
CREATE UNIQUE INDEX uq_label_ws   ON label(workspace_id, name)           WHERE team_id IS NULL;

CREATE TABLE member (
  id           TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspace(id),
  name         TEXT NOT NULL,
  display_name TEXT NOT NULL,
  email        TEXT UNIQUE,
  initials     TEXT,
  avatar_url   TEXT,
  timezone     TEXT,
  is_admin     INTEGER NOT NULL DEFAULT 0,
  is_active    INTEGER NOT NULL DEFAULT 1,
  created_at   TEXT NOT NULL
);

CREATE TABLE team_membership (
  team_id    TEXT NOT NULL REFERENCES team(id) ON DELETE CASCADE,
  member_id  TEXT NOT NULL REFERENCES member(id) ON DELETE CASCADE,
  is_owner   INTEGER NOT NULL DEFAULT 0,
  sort_order REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (team_id, member_id)
);

CREATE TABLE project (
  id          TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspace(id),
  team_id     TEXT REFERENCES team(id),
  name        TEXT NOT NULL,
  description TEXT,
  icon        TEXT,
  color       TEXT,
  status_type TEXT NOT NULL DEFAULT 'planned'
              CHECK (status_type IN ('backlog','planned','started','paused','completed','canceled')),
  priority    INTEGER NOT NULL DEFAULT 0 CHECK (priority BETWEEN 0 AND 4),
  lead_id     TEXT REFERENCES member(id),
  start_date  TEXT,
  target_date TEXT,
  sort_order  REAL NOT NULL DEFAULT 0,
  archived_at TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  FOREIGN KEY (workspace_id, team_id) REFERENCES team(workspace_id, id)
);

CREATE TABLE project_milestone (
  id         TEXT PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'unstarted'
             CHECK (status IN ('unstarted','next','overdue','done')),
  target_date TEXT,
  sort_order REAL NOT NULL DEFAULT 0,
  UNIQUE (project_id, name)
);

CREATE TABLE cycle (
  id         TEXT PRIMARY KEY,
  team_id    TEXT NOT NULL REFERENCES team(id) ON DELETE CASCADE,
  number     INTEGER NOT NULL,
  name       TEXT,
  starts_at  TEXT NOT NULL,
  ends_at    TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE (team_id, number),
  CHECK (ends_at > starts_at)
);

CREATE TABLE issue (
  id          TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL REFERENCES workspace(id),
  team_id     TEXT NOT NULL REFERENCES team(id),
  number      INTEGER NOT NULL,
  identifier  TEXT NOT NULL UNIQUE,
  title       TEXT NOT NULL CHECK (length(trim(title)) > 0),
  description TEXT,
  priority    INTEGER NOT NULL DEFAULT 0 CHECK (priority BETWEEN 0 AND 4),
  priority_sort_order REAL NOT NULL DEFAULT 0,
  estimate    REAL,
  due_date    TEXT CHECK (due_date IS NULL OR (
                due_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'
                AND date(due_date) IS due_date)),      -- D-009: shape + real calendar
  state_id    TEXT NOT NULL,
  assignee_id TEXT REFERENCES member(id),
  creator_id  TEXT REFERENCES member(id),
  project_id  TEXT REFERENCES project(id),
  milestone_id TEXT REFERENCES project_milestone(id),
  cycle_id    TEXT REFERENCES cycle(id),
  parent_id   TEXT REFERENCES issue(id),
  sort_order  REAL NOT NULL DEFAULT 0,
  board_order REAL NOT NULL DEFAULT 0,
  sub_issue_sort_order REAL NOT NULL DEFAULT 0,
  started_at  TEXT,
  completed_at TEXT,
  canceled_at TEXT,
  snoozed_until_at TEXT,
  archived_at TEXT,
  auto_archived_at TEXT,
  trashed     INTEGER NOT NULL DEFAULT 0,
  branch_name TEXT,
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  UNIQUE (team_id, number),
  CHECK (parent_id IS NULL OR parent_id <> id),
  FOREIGN KEY (workspace_id, team_id) REFERENCES team(workspace_id, id),
  FOREIGN KEY (team_id, state_id) REFERENCES workflow_state(team_id, id)
);
```

### 3.1 Child and relation tables

```sql
CREATE TABLE issue_label (
  issue_id TEXT NOT NULL REFERENCES issue(id) ON DELETE CASCADE,
  label_id TEXT NOT NULL REFERENCES label(id) ON DELETE CASCADE,
  PRIMARY KEY (issue_id, label_id)
);

CREATE TABLE issue_relation (
  id               TEXT PRIMARY KEY,
  issue_id         TEXT NOT NULL REFERENCES issue(id) ON DELETE CASCADE,
  related_issue_id TEXT NOT NULL REFERENCES issue(id) ON DELETE CASCADE,
  type             TEXT NOT NULL CHECK (type IN ('blocks','duplicate','related','similar')),
  created_at       TEXT NOT NULL,
  UNIQUE (issue_id, related_issue_id, type),
  CHECK (issue_id <> related_issue_id)
);

CREATE TABLE comment (
  id         TEXT PRIMARY KEY,
  issue_id   TEXT NOT NULL REFERENCES issue(id) ON DELETE CASCADE,
  parent_id  TEXT REFERENCES comment(id) ON DELETE CASCADE,
  author_id  TEXT REFERENCES member(id),
  body       TEXT NOT NULL CHECK (length(trim(body)) > 0),
  resolved_at TEXT,
  edited_at  TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE attachment (
  id        TEXT PRIMARY KEY,
  issue_id  TEXT NOT NULL REFERENCES issue(id) ON DELETE CASCADE,
  title     TEXT NOT NULL,
  subtitle  TEXT,
  url       TEXT NOT NULL,
  source_type TEXT,
  metadata  TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE issue_history (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  issue_id  TEXT NOT NULL REFERENCES issue(id) ON DELETE CASCADE,
  actor_id  TEXT REFERENCES member(id),
  field     TEXT NOT NULL,
  old_value TEXT,
  new_value TEXT,
  created_at TEXT NOT NULL
);
```

### 3.2 Indexes (mandatory)

```sql
CREATE INDEX idx_issue_team_state ON issue(team_id, state_id);
CREATE INDEX idx_issue_assignee   ON issue(assignee_id);
CREATE INDEX idx_issue_due        ON issue(due_date);
CREATE INDEX idx_issue_project    ON issue(project_id);
CREATE INDEX idx_issue_cycle      ON issue(cycle_id);
CREATE INDEX idx_issue_parent     ON issue(parent_id);
CREATE INDEX idx_issue_open       ON issue(team_id, archived_at, trashed, priority);
CREATE INDEX idx_issue_sort       ON issue(team_id, sort_order);
CREATE INDEX idx_history_issue    ON issue_history(issue_id, created_at);
CREATE INDEX idx_comment_issue    ON comment(issue_id, created_at);
CREATE INDEX idx_label_team       ON label(workspace_id, team_id);
```

The two partial unique indexes of `label` (`uq_label_team`, `uq_label_ws`) are in the `label` block
above: they are constraints, not performance (D-010).

### 3.3 Derived views (never written to)

```sql
CREATE VIEW v_priority AS
SELECT 0 AS value, 'No priority' AS label UNION ALL
SELECT 1, 'Urgent' UNION ALL SELECT 2, 'High' UNION ALL
SELECT 3, 'Medium' UNION ALL SELECT 4, 'Low';

CREATE VIEW v_issue AS
SELECT i.*,
       p.label AS priority_label,
       (s.type NOT IN ('completed','canceled')) AS is_open,
       (i.archived_at IS NULL AND i.trashed = 0) AS visible
FROM issue i
JOIN workflow_state s ON s.id = i.state_id
JOIN v_priority p     ON p.value = i.priority;

CREATE VIEW v_team_progress AS
SELECT team_id,
       COUNT(*) FILTER (WHERE is_open)                       AS open_count,
       COUNT(*) FILTER (WHERE NOT is_open)                   AS closed_count,
       COUNT(*) FILTER (WHERE priority_label = 'Urgent' AND is_open) AS urgent_open
FROM v_issue WHERE visible GROUP BY team_id;
```

The column is called **`visible`**, not `is_visible` (D-019): `IssueView` of SPEC-020/030 exposes the
same name and the homonymous alias shadowed the property of the base model.

### 3.4 Search (details in SPEC-090 §3.1)

```sql
CREATE VIRTUAL TABLE issue_fts USING fts5(
  title, description, identifier UNINDEXED,
  content='issue', content_rowid='rowid', tokenize='unicode61 remove_diacritics 2'
);

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
```

`identifier` is `UNINDEXED` (D-008): the match by `ENG-12` is resolved by `parse_query` of SPEC-090
(§2.3), not the tokenizer; the identifier text is stored anyway for the `snippet`.

### 3.5 `002_schema_migration.sql` (ledger, migration 002)

```sql
CREATE TABLE schema_migration (
  version    INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  checksum   TEXT NOT NULL           -- sha256 of the .sql applied
);
```

The rows for 001 and 002 are inserted by `apply_migrations` (§5), not by the `.sql`: the checksum is computed
at apply time. After a complete `hifa db migrate` the table has **3 rows** (versions 1, 2 and 3).

### 3.6 `003_team_previous_key.sql` (migration 003)

```sql
CREATE TABLE team_previous_key (
  team_id    TEXT NOT NULL REFERENCES team(id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  PRIMARY KEY (team_id, key)
);

CREATE INDEX idx_team_previous_key_key ON team_previous_key(key);
```

It stores every `key` a team left behind in `hifa team set-key` (R3). The index by `key` resolves
`hifa issue show ENG-12` when `ENG` no longer exists as a current key.

## 4. Rules

### 4.1 Structural decisions

| # | Decision | Consequence |
|---|---|---|
| D1 | **ULID** IDs in `TEXT` | time-orderable, no coordination, safe in URLs |
| D2 | **ISO-8601 UTC** timestamps in `TEXT` (`2026-11-08T14:03:11Z`) | lexicographically comparable, no odd types |
| D3 | Calendar dates (`due_date`, `start_date`, `target_date`) in **`YYYY-MM-DD`** | a date carries no time and no TZ, which kills the ±1 day bugs |
| D4 | **Layered soft delete**: `archived_at`, `trashed`, `canceled_at` ≠ state | archiving ≠ canceling ≠ deleting |
| D5 | Order by **fractional float** (`sort_order`, `board_order`, `sub_issue_sort_order`) | inserting between two = average; no renumbering |
| D6 | `identifier` **materialized** (`ENG-123`) in addition to `team.key` + `number` | search and direct links without a join |
| D7 | No `ON DELETE CASCADE` from business tables toward `issue` except pure children | deleting a team requires an explicit decision, not silent drag |

### 4.2 Rules

- **R1** — Every write happens inside `BEGIN IMMEDIATE` (SPEC-030).
- **R2** — `issue.number` = `MAX(number)+1` of the team, computed **in the same transaction** as the
  INSERT (SPEC-030 §2.5 SQL-1); `UNIQUE(team_id, number)` is the safety net, with up to 3
  retries of the use case (D-018). There is no counter on `team`.
- **R3** — `identifier` = `team.key || '-' || number`. When a team's key changes, the
  `identifier` of its issues is recomputed with the `number` intact, one row per issue is written to
  `issue_history` (`field='identifier'`) and the old key is recorded in `team_previous_key(team_id,
  key, changed_at)`. Historical identifiers are resolved with `team_previous_key`; an old key
  is never reassigned (the `number` is not recycled).
- **R4** — `state_id` must belong to the same `team_id` as the issue: this is guaranteed by the composite FK
  `(team_id, state_id)` against `workflow_state(team_id, id)`, which requires the `UNIQUE(team_id, id)` and
  makes it impossible to insert a state from another team.
- **R5** — Migrations are **forward-only**, one file per version:
  - **R5.1** — `PRAGMA user_version` is the cursor; `schema_migration` is the auditable ledger
    (`version, name, applied_at, checksum`). The sha256 checksum detects a `.sql` edited after
    being applied ⇒ abort with `MigrationChecksumMismatch`.
  - **R5.2** — Before applying the first pending migration a backup
    `hifa.db.bak-<user_version>` is written with `VACUUM INTO`; if it fails ⇒ `BackupFailed` and no migration happens.
  - **R5.3** — The PRAGMAs (`journal_mode=WAL`, `foreign_keys=ON`, `busy_timeout=5000`,
    `synchronous=NORMAL`, `temp_store=MEMORY`) are applied on **each connection** in `open_db`; no
    `.sql` repeats them.
  - **R5.4** — Never a silent `DROP`; an applied migration is not rewritten.
- **R6** — A `label` with `team_id NULL` belongs to the workspace and can be used in any team. The
  uniqueness is provided by the partial unique indexes: `(workspace_id, name) WHERE team_id IS NULL` and
  `(workspace_id, team_id, name) WHERE team_id IS NOT NULL`, so the same name can exist
  in the workspace, in the ENG team and in the OPS team, but **not twice in the same scope**
  (D-010).
- **R7** — An invalid `due_date` is rejected at two layers, it is not "corrected":
  - **R7.1** — schema: the CHECK requires the `YYYY-MM-DD` shape (`GLOB`) **and** that the date exists on
    the calendar (`date(due_date) IS due_date`); `2026-13-45`, `2026-02-30` and `2026-3-1` fail.
  - **R7.2** — domain: `domain.rules.validate_due_date` validates the calendar before the INSERT and
    produces `Validation` (exit 2) with `field='due_date'`.
- **R8** — `issue.workspace_id` and `project.workspace_id` are denormalized with respect to
  `team.workspace_id`; the composite FK `(workspace_id, team_id) → team(workspace_id, id)` prevents
  them from diverging. In `project`, a `team_id NULL` (cross-team) is not validated by FK, by definition.
- **R9** — `v_issue`, `v_priority` and `v_team_progress` are the only way to read `is_open`, the
  priority label and the progress counts; nobody recomputes them in ad-hoc SQL. `is_open = 0`
  for `completed` and `canceled`, `1` for the other four types.

## 5. Flow

Applying a migration, step by step (`apply_migrations`):

```
1. version = schema_version(conn)                     # PRAGMA user_version
2. plan = [m for m in discover(migrations_dir) if m.version > version]  sorted by version
   If a file does not match ^\d{3}_[a-z0-9_]+\.sql$ ⇒ MigrationFailed (exit 6).
3. If plan is empty ⇒ return MigrationResult(applied=[], from=to=version, backup=None).
4. If version > MAX_VERSION ⇒ SchemaTooNew (exit 4): the database is from a newer binary.
5. If backup ⇒ VACUUM INTO "hifa.db.bak-<version>"; error ⇒ BackupFailed (exit 6), nothing is touched.
6. For each migration m of plan, in order:
   6.1 checksum = sha256(bytes of the file)
   6.2 if a row exists in schema_migration with version=m.version:
         different checksum ⇒ MigrationChecksumMismatch (exit 6), abort the whole plan;
         equal ⇒ m already applied: do not re-run, continue.
   6.3 BEGIN IMMEDIATE; executescript(m.sql); 
   6.4 INSERT INTO schema_migration(version, name, applied_at, checksum);
       UPDATE PRAGMA user_version = m.version;
   6.5 COMMIT. Any error ⇒ ROLLBACK, MigrationFailed (exit 6); the file and
       user_version stay consistent (either all or nothing).
7. When applying 002, which creates the ledger, the rows for versions 1 and 2 are inserted in the
   same transaction (the ledger is born knowing what already happened); 003 is registered normally in 6.4.
8. return MigrationResult(applied=[...], from_version=version, to_version=last).
```

`hifa db migrate` (SPEC-050 §2.5.5) is a wrapper: `open_db` → `apply_migrations` → prints
`MigrationResult` as `--json` or one line per applied version. `hifa init` (SPEC-100) calls the
same contract before seeding.

## 6. Errors

| Domain error | `error.code` | When | CLI | HTTP |
|---|---|---|---|---|
| `MigrationChecksumMismatch` | `migration_checksum_mismatch` | an already applied `.sql` changed checksum | exit 6, stderr with version and both hashes | 500 |
| `SchemaTooNew` | `schema_too_new` | `user_version` > the binary's `MAX_VERSION` | exit 4 | 409 |
| `BackupFailed` | `backup_failed` | `VACUUM INTO` could not write the `.bak` (disk full, permissions) | exit 6, with destination path | 500 |
| `MigrationFailed` | `migration_failed` | SQL error inside a migration (invalid DDL, FTS5 missing) | exit 6, with file and statement | 500 |
| `CorruptDatabase` | `corrupt_database` | `SQLITE_NOTADB` / `SQLITE_CORRUPT` when opening or migrating | exit 6, hint `hifa doctor` | 500 |
| `Fts5Unavailable` | `fts5_unavailable` | `PRAGMA compile_options` without `ENABLE_FTS5` | exit 6, Python build hint | 500 |
| `Validation` | `validation_error` | impossible `due_date` before the INSERT (R7.2) | exit 2, `field='due_date'` | 422 |

All of them inherit from `HifaError` (SPEC-000 §6). The global exit code map is the one in D-014; these
codes do not redefine it.

## 7. Acceptance Criteria

```gherkin
@SPEC-010
Feature: Schema and migrations

  Scenario: initial migration on an empty database
    Given a nonexistent database file
    When "hifa db migrate" is run
    Then user_version is 3
    And the 17 schema tables exist in "sqlite_master"
    And the views v_issue, v_priority and v_team_progress exist
    And "schema_migration" has 3 rows with versions 1, 2 and 3
    And the virtual table "issue_fts" exists with its 3 triggers

  Scenario: migration is idempotent
    Given a database already migrated to version 3
    When "hifa db migrate" is run again
    Then no migration is applied
    And user_version is still 3
    And "schema_migration" still has 3 rows

  Scenario: issue number is not repeated under concurrency
    Given an ENG team with 5 issues
    When 10 issues are created in parallel
    Then numbers 6 through 15 exist exactly once each
    And no identifier is left duplicated

  Scenario: an impossible date is rejected by the schema
    Given a connection with "foreign_keys=ON" on the v1 schema
    When an issue is inserted with due_date "2026-13-45"
    Then the INSERT fails with "CHECK constraint failed"
    And an issue is inserted with due_date "2026-02-30" and it also fails
    And an issue with due_date "2026-03-01" is inserted without error

  Scenario: a state from another team is rejected
    Given an issue of the ENG team
    When a workflow_state of the OPS team is assigned to it
    Then the operation fails with "FOREIGN KEY constraint failed"

  Scenario: two workspace labels with the same name do not coexist
    Given a workspace label named "Bug"
    When another workspace label named "Bug" is created
    Then the INSERT fails with "UNIQUE constraint failed"
    And a "Bug" label of the ENG team is inserted without error

  Scenario: the altered checksum of an applied migration aborts
    Given a database migrated to version 3
    When "001_init.sql" is edited and "hifa db migrate" is run
    Then the exit code is 6
    And the "error.code" field is "migration_checksum_mismatch"
    And user_version is still 3

  Scenario: a database from a newer binary is left untouched
    Given a database with user_version 4
    When "hifa db migrate" is run
    Then the exit code is 4
    And the "error.code" field is "schema_too_new"
    And no table was modified
```

## 8. Regeneration checklist

1. Create `migrations/001_init.sql`, `002_schema_migration.sql` and `003_team_previous_key.sql` with the
   DDL of §3 **exactly** (order: parent tables → children → indexes → views → FTS and triggers).
2. `migrate.py`: implement `open_db`, `schema_version`, `apply_migrations` with the signatures of §2 and
   the flow of §5; discover the `.sql` files with `importlib.resources` (the installed package does not depend
   on the repo).
3. `connection.py` (SPEC-030) applies the PRAGMAs of R5.3 on every connection; no `.sql` repeats them.
4. Tests: migration on an empty database (`user_version 3`, 17 tables, 3 views, 3 ledger rows),
   idempotency, each violated CHECK/FK, altered checksum, `schema_too_new`, failed backup and
   number concurrency.
5. `python scripts/spec_contract_check.py --spec specs/010-data-model.md` compares tables, columns,
   indexes and views declared against `sqlite_master`.

## 9. Provenance

- Schema: the tables and columns of §3, with the invariants of §4 and the domain rules of SPEC-020.
- Calendar dates, priority 0-4, `state.type` with 6 values and fractional ranking: defined here and
  in SPEC-020, with the reasoning behind each one recorded in `specs/DECISIONS.md`.
- `schema_migration` ledger and `team_previous_key` as migrations 002 and 003: D-006 and D-008
  (`specs/DECISIONS.md`), 2026-11-08.
- Real CHECK of `due_date` and label uniqueness through partial indexes: D-009 and D-010, verified
  against SQLite 3.46 (`GLOB` alone does not validate the calendar and `NULL != NULL`).

## 10. Out of scope

- Partitioning, replication, encryption at rest, external audit tables, binary
  attachments.
- `schema_migration` is migrator infrastructure: it is not modeled in the domain, it is not exported
  (SPEC-080) and no use case writes to it.
- The connection, UoW and repository code (SPEC-030); data seeding (SPEC-100).

## Notes

- spec_revision 3 (2026-11-11): translated to English (repo-wide language convention).
