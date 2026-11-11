-- 001_init.sql — esquema v1: 15 tablas de dominio, índices, views, FTS5 y triggers.
-- Fuente de verdad: specs/010-data-model.md §3 (D-008). Forward-only: no se reescribe.
-- Prohibido PRAGMA en este archivo (SPEC-010 R5.3): los PRAGMA son por conexión (connection.py).

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
  UNIQUE (workspace_id, id)          -- destino de las FK compuestas de issue/project
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
  UNIQUE (team_id, id)               -- destino de la FK compuesta de issue (R4)
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
-- D-010: UNIQUE(workspace_id, team_id, name) no sirve con team_id NULL (NULL != NULL).
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
                AND date(due_date) IS due_date)),      -- D-009: forma + calendario real
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
