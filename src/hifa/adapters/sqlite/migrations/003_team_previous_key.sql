-- 003_team_previous_key.sql — keys históricas de team (R3, SPEC-010 §3.6).
-- El índice por key resuelve `hifa issue show ENG-12` cuando ENG ya no es la key vigente.

CREATE TABLE team_previous_key (
  team_id    TEXT NOT NULL REFERENCES team(id) ON DELETE CASCADE,
  key        TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  PRIMARY KEY (team_id, key)
);

CREATE INDEX idx_team_previous_key_key ON team_previous_key(key);
