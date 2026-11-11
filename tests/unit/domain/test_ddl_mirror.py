"""DDL ↔ models mirror: every DDL table has its model and no field is extra or missing.

Parses `specs/010-data-model.md` §3 (the DDL is normative) and compares it with
`model_fields`. It only excludes what the spec declares outside the domain: `issue_label`
(M2M, SPEC-030 read models) and `schema_migration` (the migrator ledger, §10).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hifa.domain import entities as ent

SPEC_PATH = Path(__file__).resolve().parents[3] / "specs" / "010-data-model.md"

CONSTRAINT_KEYWORDS = ("UNIQUE", "CHECK", "FOREIGN", "PRIMARY", "CONSTRAINT")

MODEL_BY_TABLE: dict[str, type[ent.Entity]] = {
    "workspace": ent.Workspace,
    "team": ent.Team,
    "team_previous_key": ent.TeamPreviousKey,
    "workflow_state": ent.WorkflowState,
    "label": ent.Label,
    "member": ent.Member,
    "team_membership": ent.TeamMembership,
    "project": ent.Project,
    "project_milestone": ent.ProjectMilestone,
    "cycle": ent.Cycle,
    "issue": ent.Issue,
    "issue_relation": ent.IssueRelation,
    "comment": ent.Comment,
    "attachment": ent.Attachment,
    "issue_history": ent.IssueHistory,
}

EXCLUDED_TABLES = {
    "issue_label": "M2M sin entidad: read models de SPEC-030 (SPEC-020 §2.2)",
    "schema_migration": "ledger del migrador, fuera de dominio (SPEC-020 §10)",
}


def _strip_sql_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _split_top_level_commas(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    current = ""
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    return parts


def _columns(body: str) -> list[str]:
    columns: list[str] = []
    for part in _split_top_level_commas(body):
        stripped = part.strip()
        if not stripped:
            continue
        if stripped.upper().startswith(CONSTRAINT_KEYWORDS):
            continue
        columns.append(stripped.split()[0])
    return columns


def parse_ddl() -> dict[str, list[str]]:
    """`{table: [columns]}` for every `CREATE TABLE` in the SPEC-010 §3 DDL."""
    text = _strip_sql_comments(SPEC_PATH.read_text(encoding="utf-8"))
    tables: dict[str, list[str]] = {}
    for match in re.finditer(r"CREATE TABLE (\w+) \(", text):
        start = match.end()
        depth = 1
        index = start
        while depth and index < len(text):
            if text[index] == "(":
                depth += 1
            elif text[index] == ")":
                depth -= 1
            index += 1
        tables[match.group(1)] = _columns(text[start : index - 1])
    return tables


DDL = parse_ddl()


def test_parser_finds_the_17_schema_tables() -> None:
    assert len(DDL) == 17, sorted(DDL)
    assert set(DDL) == set(MODEL_BY_TABLE) | set(EXCLUDED_TABLES)


@pytest.mark.parametrize("table", sorted(MODEL_BY_TABLE))
def test_model_mirrors_its_table_columns(table: str) -> None:
    model = MODEL_BY_TABLE[table]
    fields = set(model.model_fields)
    columns = set(DDL[table])
    assert fields == columns, (
        f"{model.__name__} vs {table}: "
        f"sobran={sorted(fields - columns)} faltan={sorted(columns - fields)}"
    )
    assert len(model.model_fields) == len(DDL[table])


def test_issue_has_31_fields() -> None:
    assert len(DDL["issue"]) == 31
    assert len(ent.Issue.model_fields) == 31


def test_issueview_adds_exactly_the_v_issue_columns() -> None:
    extra = set(ent.IssueView.model_fields) - set(ent.Issue.model_fields)
    assert extra == {"priority_label", "is_open", "visible"}


def test_team_membership_and_team_previous_key_have_column_names() -> None:
    assert list(ent.TeamMembership.model_fields) == [
        "team_id",
        "member_id",
        "is_owner",
        "sort_order",
    ]
    assert list(ent.TeamPreviousKey.model_fields) == ["team_id", "key", "changed_at"]


def test_base_entity_is_frozen_and_strict() -> None:
    assert ent.Entity.model_config["frozen"] is True
    assert ent.Entity.model_config["extra"] == "forbid"
    assert ent.Entity.model_config["validate_default"] is True
    for model in (*MODEL_BY_TABLE.values(), ent.IssueView):
        assert model.model_config["frozen"] is True
        assert model.model_config["extra"] == "forbid"


def test_no_model_for_schema_migration_or_issue_label() -> None:
    model_names = {model.__name__ for model in MODEL_BY_TABLE.values()}
    assert "SchemaMigration" not in model_names
    assert "IssueLabel" not in model_names
