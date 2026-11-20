"""`hifa label` and the two issue verbs, end to end.

One test per behaviour of the catalogue (five verbs) and of the two label links, including the
22 scenarios of the label feature: the two levels of a label, the `singleSelect` replacement and
its answer, the name that is renamed under the chips, the conflicts of the retire axis and the
partial application of an N-label command.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import Result

from conftest import ISSUE_HEADER, Cli, error_json, json_stdout, plain, runner

LABEL_HEADER = "NAME  COLOR  TEAM  RETIRED"
LABEL_KEYS = (
    "id",
    "name",
    "color",
    "description",
    "team_id",
    "parent_id",
    "is_group",
    "group_type",
    "retired_at",
)


@pytest.fixture(autouse=True)
def empty_catalogue(cli: Cli) -> None:
    """`hifa init` seeds four workspace labels; these tests decide their own catalogue."""
    cli.execute("DELETE FROM label")


def _add(cli: Cli, name: str, *args: str) -> Result:
    return cli.run("label", "add", "-n", name, *args)


def _maybe_label(cli: Cli, name: str, team_id: str | None = None) -> dict[str, Any] | None:
    """The stored label row as a dict, or `None` when there is no such row."""
    rows = cli.rows("SELECT * FROM label WHERE name = ? AND team_id IS ?", (name, team_id))
    assert len(rows) <= 1
    return None if not rows else dict(rows[0])


def _label(cli: Cli, name: str, team_id: str | None = None) -> dict[str, Any]:
    """The stored label row; the test fails when the row is not there."""
    row = _maybe_label(cli, name, team_id)
    assert row is not None, f"label {name!r} is not in the database"
    return row


def _team_id(cli: Cli, key: str = "ENG") -> str:
    return str(cli.rows("SELECT id FROM team WHERE key = ?", (key,))[0]["id"])


def _labels_of(cli: Cli, identifier: str) -> list[str]:
    return [
        row["name"]
        for row in cli.rows(
            "SELECT l.name FROM issue_label il JOIN label l ON l.id = il.label_id"
            " WHERE il.issue_id = (SELECT id FROM issue WHERE identifier = ?) ORDER BY l.name",
            (identifier,),
        )
    ]


def _group(cli: Cli, name: str, kind: str) -> None:
    result = _add(cli, name, "--group", kind)
    assert result.exit_code == 0, result.stdout + result.stderr


def _child(cli: Cli, name: str, parent: str, color: str = "#6B6F76") -> None:
    result = _add(cli, name, "--parent", parent, "--color", color)
    assert result.exit_code == 0, result.stdout + result.stderr


# --- label add: the two levels ---------------------------------------------------------------


def test_a_workspace_label_and_a_team_label_with_the_same_name_coexist(cli: Cli) -> None:
    """Scenario: two levels, one name, two rows — the partial unique indexes allow it."""
    workspace = _add(cli, "Bug", "--color", "#EB5757")
    team = _add(cli, "Bug", "--team", "ENG", "--color", "#EB5757")

    assert workspace.exit_code == 0, workspace.stderr
    assert team.exit_code == 0, team.stderr
    rows = cli.rows("SELECT team_id FROM label WHERE name = 'Bug' ORDER BY team_id")
    assert len(rows) == 2
    assert rows[0]["team_id"] is None
    assert rows[1]["team_id"] == _team_id(cli)


def test_duplicating_a_label_at_the_same_level_is_a_conflict(cli: Cli) -> None:
    assert _add(cli, "Bug").exit_code == 0

    result = _add(cli, "Bug", "--color", "#000000", "--json")

    assert result.exit_code == 4
    assert error_json(result)["code"] == "conflict"


def test_a_case_variant_of_a_catalogued_name_is_a_conflict(cli: Cli) -> None:
    """One name spelled two ways is one row here: the second spelling is refused, exit 4."""
    assert _add(cli, "Bug").exit_code == 0

    result = _add(cli, "bug", "--json")

    assert result.exit_code == 4
    assert error_json(result)["code"] == "conflict"
    assert _maybe_label(cli, "bug") is None


def test_label_add_prints_the_row_of_label_list(cli: Cli) -> None:
    result = _add(cli, "Bug", "--color", "#EB5757")

    assert result.exit_code == 0, result.stderr
    lines = [plain(line) for line in result.stdout.splitlines()]
    assert lines[0] == plain(LABEL_HEADER)
    assert lines[1].startswith("Bug #EB5757")


def test_label_add_json_carries_the_nine_keys_in_order(cli: Cli) -> None:
    payload = json_stdout(_add(cli, "Bug", "--json"))

    assert tuple(payload) == LABEL_KEYS
    assert payload["is_group"] is False
    assert payload["group_type"] is None
    assert payload["team_id"] is None
    assert payload["retired_at"] is None


def test_label_add_quiet_prints_the_id_because_a_label_has_no_identifier(cli: Cli) -> None:
    result = _add(cli, "Bug", "--quiet")

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == str(_label(cli, "Bug")["id"])


def test_label_add_with_team_and_no_team_is_an_invalid_option(cli: Cli) -> None:
    result = _add(cli, "Bug", "--team", "ENG", "--no-team", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert (error["code"], error["field"]) == ("invalid_option", "team")


def test_label_add_with_no_level_creates_a_workspace_label(cli: Cli) -> None:
    assert _add(cli, "infra").exit_code == 0

    assert _label(cli, "infra")["team_id"] is None


def test_a_blank_name_and_a_bad_color_are_validation_errors(cli: Cli) -> None:
    blank = _add(cli, "   ", "--json")
    color = _add(cli, "infra", "--color", "red", "--json")

    assert blank.exit_code == 2
    assert error_json(blank)["code"] == "validation_error"
    assert color.exit_code == 2
    assert error_json(color)["field"] == "color"


def test_an_unknown_team_is_not_found(cli: Cli) -> None:
    result = _add(cli, "infra", "--team", "NOPE", "--json")

    assert result.exit_code == 3
    assert error_json(result)["field"] == "team"


def test_a_group_and_a_child_are_created_at_the_same_level(cli: Cli) -> None:
    _group(cli, "Severity", "singleSelect")
    _child(cli, "High", "Severity")

    group = _label(cli, "Severity")
    assert (group["is_group"], group["group_type"]) == (1, "singleSelect")
    assert _label(cli, "High")["parent_id"] == group["id"]


def test_label_show_names_the_group_and_the_parent_of_a_child(cli: Cli) -> None:
    _group(cli, "Severity", "singleSelect")
    _child(cli, "High", "Severity")

    group_lines = cli.run("label", "show", "Severity").stdout.splitlines()
    child_lines = cli.run("label", "show", "High").stdout.splitlines()

    assert group_lines[3].split() == ["Group", "singleSelect"]
    assert child_lines[4].split() == ["Parent", "Severity"]


def test_group_together_with_parent_is_a_group_refusal(cli: Cli) -> None:
    result = _add(cli, "Severity", "--group", "multiSelect", "--parent", "Area", "--json")

    assert result.exit_code == 2
    assert (error_json(result)["field"],) == ("parent",)


def test_a_parent_the_level_spells_twice_is_refused_instead_of_picked(cli: Cli) -> None:
    """Two spellings of one group is `LABEL_AMBIGUOUS` (`field: ref`), and no child is written."""
    _group(cli, "Severity", "multiSelect")
    cli.execute(
        "INSERT INTO label (id, workspace_id, team_id, parent_id, name, color, description,"
        " is_group, group_type, retired_at)"
        " SELECT '01HZZZZZZZZZZZZZZZZZZZZZZ5', workspace_id, NULL, NULL, 'severity', '#6B6F76',"
        " NULL, 1, 'multiSelect', NULL FROM label WHERE name = 'Severity'"
    )

    result = _add(cli, "High", "--parent", "severity", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert (error["code"], error["field"], error["details"]) == ("validation_error", "ref", None)
    assert error["message"] == "label severity is ambiguous: 'Severity', 'severity'"
    assert cli.rows("SELECT * FROM label WHERE name = 'High'") == []


def test_a_parent_absent_at_the_level_is_still_the_group_refusal(cli: Cli) -> None:
    """The two verdicts stay apart: a name the level spells twice is not a name the level lacks."""
    _group(cli, "Severity", "multiSelect")

    result = _add(cli, "High", "--parent", "Area", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert (error["code"], error["field"]) == ("validation_error", "parent")
    assert cli.rows("SELECT * FROM label WHERE name = 'High'") == []


# --- label list / show -----------------------------------------------------------------------


def test_label_list_team_includes_the_workspace_labels_the_team_can_wear(cli: Cli) -> None:
    """Scenario: the team's catalogue is its own labels plus the workspace ones."""
    assert _add(cli, "Bug").exit_code == 0
    assert _add(cli, "infra", "--team", "ENG").exit_code == 0

    result = cli.run("label", "list", "--team", "ENG", "--json")

    assert result.exit_code == 0, result.stderr
    items = json_stdout(result)["items"]
    assert len(items) == 2
    assert {item["name"] for item in items} == {"Bug", "infra"}
    by_name = {item["name"]: item for item in items}
    assert by_name["Bug"]["team_id"] is None
    assert by_name["infra"]["team_id"] == _team_id(cli)


def test_label_list_hides_retired_rows_and_all_includes_them(cli: Cli) -> None:
    assert _add(cli, "infra").exit_code == 0
    assert cli.run("label", "archive", "infra").exit_code == 0

    assert json_stdout(cli.run("label", "list", "--json"))["items"] == []
    listed = json_stdout(cli.run("label", "list", "--all", "--json"))["items"]
    assert [item["name"] for item in listed] == ["infra"]
    assert listed[0]["retired_at"] is not None


def test_label_list_prints_the_stored_team_ulid_as_is(cli: Cli) -> None:
    """No use case returns a team key, and the edge reads none of its own to translate it."""
    assert _add(cli, "infra", "--team", "ENG").exit_code == 0

    table = plain(cli.run("label", "list", "--team", "ENG").stdout)

    assert _team_id(cli) in table
    assert "ENG" not in table.replace("ENGINEERING", "")


def test_label_list_quiet_prints_one_id_per_line(cli: Cli) -> None:
    assert _add(cli, "alpha").exit_code == 0
    assert _add(cli, "beta").exit_code == 0

    lines = cli.run("label", "list", "--quiet").stdout.strip().splitlines()

    assert len(lines) == 2
    assert set(lines) == {str(_label(cli, "alpha")["id"]), str(_label(cli, "beta")["id"])}


def test_label_show_prints_the_one_label_it_was_asked_for(cli: Cli) -> None:
    assert _add(cli, "Bug", "--color", "#EB5757").exit_code == 0

    result = cli.run("label", "show", "Bug")

    assert result.exit_code == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "Bug"
    assert lines[1].startswith("Color") and "#EB5757" in lines[1]
    assert lines[2].startswith("Team")
    assert [line.split()[0] for line in lines[1:]] == [
        "Color",
        "Team",
        "Group",
        "Parent",
        "Description",
        "Retired",
    ]


def test_label_show_resolves_a_name_case_insensitively(cli: Cli) -> None:
    assert _add(cli, "Bug").exit_code == 0

    result = cli.run("label", "show", "bUg")

    assert result.exit_code == 0, result.stderr
    assert result.stdout.splitlines()[0] == "Bug"


def test_label_show_json_is_the_canonical_label_and_nothing_else(cli: Cli) -> None:
    assert _add(cli, "Bug", "-d", "renders wrong").exit_code == 0

    payload = json_stdout(cli.run("label", "show", "Bug", "--json"))

    assert tuple(payload) == LABEL_KEYS
    assert payload["description"] == "renders wrong"


def test_label_show_of_an_unknown_label_is_not_found(cli: Cli) -> None:
    result = cli.run("label", "show", "nope", "--json")

    assert result.exit_code == 3
    error = error_json(result)
    assert (error["code"], error["field"]) == ("not_found", "label")


def test_a_name_two_teams_share_is_ambiguous_on_show(cli: Cli) -> None:
    other = cli.run("team", "create", "OPS", "Operations")
    assert other.exit_code == 0, other.stderr
    assert _add(cli, "shared", "--team", "ENG").exit_code == 0
    assert _add(cli, "shared", "--team", "OPS").exit_code == 0

    result = cli.run("label", "show", "shared", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert (error["code"], error["field"]) == ("validation_error", "ref")
    assert "OPS" in error["message"]


# --- label update ----------------------------------------------------------------------------


def test_renaming_a_label_keeps_the_issues_that_wear_it(cli: Cli) -> None:
    """Scenario: the link is by ULID, so a rename moves no chip and writes no history row."""
    identifier = cli.create("wears Bug")
    assert _add(cli, "Bug").exit_code == 0
    assert cli.run("issue", "label", identifier, "Bug").exit_code == 0
    history = cli.history(identifier)

    result = cli.run("label", "update", "Bug", "--name", "Defect")

    assert result.exit_code == 0, result.stderr
    assert _maybe_label(cli, "Bug") is None
    assert _label(cli, "Defect") is not None
    assert _labels_of(cli, identifier) == ["Defect"]
    assert cli.history(identifier) == history


def test_recoloring_a_label_changes_the_color_and_nothing_else(cli: Cli) -> None:
    assert _add(cli, "Bug", "--color", "#EB5757").exit_code == 0

    result = cli.run("label", "update", "Bug", "--color", "#BB87FC")

    assert result.exit_code == 0, result.stderr
    stored = _label(cli, "Bug")
    assert (stored["name"], stored["color"]) == ("Bug", "#BB87FC")


def test_label_update_with_no_flag_is_not_a_silent_success(cli: Cli) -> None:
    assert _add(cli, "Bug").exit_code == 0

    result = cli.run("label", "update", "Bug", "--json")

    assert result.exit_code == 2
    assert error_json(result)["code"] == "no_changes"


def test_label_update_refuses_to_clear_and_set_the_description_at_once(cli: Cli) -> None:
    assert _add(cli, "Bug", "-d", "renders wrong").exit_code == 0

    result = cli.run("label", "update", "Bug", "--clear-description", "-d", "a new one", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert (error["code"], error["field"]) == ("invalid_option", "description")
    assert _label(cli, "Bug")["description"] == "renders wrong"


def test_renaming_onto_a_name_of_the_same_level_is_a_conflict(cli: Cli) -> None:
    assert _add(cli, "Bug").exit_code == 0
    assert _add(cli, "Defect").exit_code == 0

    result = cli.run("label", "update", "Bug", "--name", "Defect", "--json")

    assert result.exit_code == 4
    assert error_json(result)["code"] == "conflict"
    assert len(cli.rows("SELECT id FROM label WHERE team_id IS NULL")) == 2


def test_clearing_the_description_writes_null(cli: Cli) -> None:
    assert _add(cli, "Bug", "-d", "renders wrong").exit_code == 0

    result = cli.run("label", "update", "Bug", "--clear-description")

    assert result.exit_code == 0, result.stderr
    assert _label(cli, "Bug")["description"] is None


# --- label archive ---------------------------------------------------------------------------


def test_a_label_an_issue_wears_is_not_retired(cli: Cli) -> None:
    identifier = cli.create("wears Bug")
    assert _add(cli, "Bug").exit_code == 0
    assert cli.run("issue", "label", identifier, "Bug").exit_code == 0

    result = cli.run("label", "archive", "Bug", "--json")

    assert result.exit_code == 4
    assert error_json(result)["code"] == "conflict"
    assert _label(cli, "Bug")["retired_at"] is None


def test_a_label_no_issue_wears_is_retired(cli: Cli) -> None:
    assert _add(cli, "infra").exit_code == 0

    result = cli.run("label", "archive", "infra")

    assert result.exit_code == 0, result.stderr
    assert _maybe_label(cli, "infra")["retired_at"] is not None


def test_label_archive_undo_clears_retired_at(cli: Cli) -> None:
    assert _add(cli, "infra").exit_code == 0
    assert cli.run("label", "archive", "infra").exit_code == 0

    result = cli.run("label", "archive", "infra", "--undo")

    assert result.exit_code == 0, result.stderr
    assert _label(cli, "infra")["retired_at"] is None


def test_the_undo_of_a_retire_that_never_happened_is_a_conflict(cli: Cli) -> None:
    assert _add(cli, "Bug").exit_code == 0

    result = cli.run("label", "archive", "Bug", "--undo", "--json")

    assert result.exit_code == 4
    assert error_json(result)["code"] == "conflict"


def test_retiring_a_label_that_is_already_retired_is_a_conflict(cli: Cli) -> None:
    assert _add(cli, "infra").exit_code == 0
    assert cli.run("label", "archive", "infra").exit_code == 0

    result = cli.run("label", "archive", "infra", "--json")

    assert result.exit_code == 4
    assert error_json(result)["code"] == "conflict"
    assert _label(cli, "infra")["retired_at"] is not None


def test_a_retired_label_is_renamed_by_its_name(cli: Cli) -> None:
    """The verb that un-retires or renames a retired row has to be able to see it."""
    assert _add(cli, "infra").exit_code == 0
    assert cli.run("label", "archive", "infra").exit_code == 0

    result = cli.run("label", "update", "infra", "--name", "platform")

    assert result.exit_code == 0, result.stderr
    assert _maybe_label(cli, "infra") is None
    assert _label(cli, "platform")["retired_at"] is not None


def test_the_undo_refuses_to_bring_back_a_row_beside_a_live_spelling(cli: Cli) -> None:
    """Clearing `retired_at` must not create the pair `label add` refuses: exit 4, no write."""
    assert _add(cli, "Bug").exit_code == 0
    retired_id = _label(cli, "Bug")["id"]
    assert cli.run("label", "archive", retired_id).exit_code == 0
    cli.execute(
        "INSERT INTO label (id, workspace_id, team_id, parent_id, name, color, description,"
        " is_group, group_type, retired_at)"
        " SELECT '01HZZZZZZZZZZZZZZZZZZZZZZ3', workspace_id, NULL, NULL, 'bug', '#6B6F76',"
        " NULL, 0, NULL, NULL FROM label WHERE name = 'Bug'"
    )

    result = cli.run("label", "archive", retired_id, "--undo", "--json")

    assert result.exit_code == 4
    error = error_json(result)
    assert (error["code"], error["field"], error["details"]) == ("conflict", "name", None)
    assert error["message"] == "label Bug cannot be restored: 'bug' is live at this level"
    assert _label(cli, "Bug")["retired_at"] is not None
    assert _label(cli, "bug")["retired_at"] is None


def test_the_undo_of_a_row_beside_a_retired_spelling_is_allowed(cli: Cli) -> None:
    """A retired sibling is not the pair the rule refuses: names resolve over the live set."""
    assert _add(cli, "Bug").exit_code == 0
    retired_id = _label(cli, "Bug")["id"]
    assert cli.run("label", "archive", retired_id).exit_code == 0
    cli.execute(
        "INSERT INTO label (id, workspace_id, team_id, parent_id, name, color, description,"
        " is_group, group_type, retired_at)"
        " SELECT '01HZZZZZZZZZZZZZZZZZZZZZZ4', workspace_id, NULL, NULL, 'bug', '#6B6F76',"
        " NULL, 0, NULL, '2026-11-19T00:00:00Z' FROM label WHERE name = 'Bug'"
    )

    result = cli.run("label", "archive", retired_id, "--undo")

    assert result.exit_code == 0, result.stderr
    assert _label(cli, "Bug")["retired_at"] is None
    assert _label(cli, "bug")["retired_at"] == "2026-11-19T00:00:00Z"


def test_a_lone_retired_label_still_restores(cli: Cli) -> None:
    """The new refusal is about a live sibling and about nothing else."""
    assert _add(cli, "infra").exit_code == 0
    retired_id = _label(cli, "infra")["id"]
    assert cli.run("label", "archive", retired_id).exit_code == 0

    result = cli.run("label", "archive", retired_id, "--undo", "--json")

    assert result.exit_code == 0, result.stderr
    assert json_stdout(result)["retired_at"] is None
    assert _label(cli, "infra")["retired_at"] is None


# --- issue label -----------------------------------------------------------------------------


def test_in_a_single_select_group_the_second_label_replaces_the_first(cli: Cli) -> None:
    identifier = cli.create("in the group")
    _group(cli, "Severity", "singleSelect")
    _child(cli, "High", "Severity", "#EB5757")
    _child(cli, "Low", "Severity", "#4EA7FC")
    assert cli.run("issue", "label", identifier, "High").exit_code == 0

    result = cli.run("issue", "label", identifier, "Low", "--json")

    assert result.exit_code == 0, result.stderr
    assert _labels_of(cli, identifier) == ["Low"]
    # The displaced label keeps its row: the link is what went away.
    assert _maybe_label(cli, "High") is not None


def test_the_json_answer_of_the_replacement_names_the_label_it_took_off(cli: Cli) -> None:
    identifier = cli.create("in the group")
    _group(cli, "Severity", "singleSelect")
    _child(cli, "High", "Severity", "#EB5757")
    _child(cli, "Low", "Severity", "#4EA7FC")
    assert cli.run("issue", "label", identifier, "High").exit_code == 0

    payload = json_stdout(cli.run("issue", "label", identifier, "Low", "--json"))

    assert tuple(payload) == ("issue", "replaced")
    assert payload["replaced"]["name"] == "High"
    assert tuple(payload["replaced"]) == ("id", "name", "color")
    assert [label["name"] for label in payload["issue"]["labels"]] == ["Low"]


def test_a_multi_select_group_keeps_every_child(cli: Cli) -> None:
    identifier = cli.create("in the group")
    _group(cli, "Area", "multiSelect")
    _child(cli, "backend", "Area")
    _child(cli, "frontend", "Area")

    assert cli.run("issue", "label", identifier, "backend", "frontend").exit_code == 0

    assert _labels_of(cli, identifier) == ["backend", "frontend"]


def test_issue_label_create_creates_the_label_in_the_issues_team(cli: Cli) -> None:
    identifier = cli.create("needs a label")

    result = cli.run("issue", "label", identifier, "infra", "--create", "--color", "#5E6AD2")

    assert result.exit_code == 0, result.stderr
    row = _label(cli, "infra", _team_id(cli))
    assert (row["color"], row["team_id"]) == ("#5E6AD2", _team_id(cli))
    assert _labels_of(cli, identifier) == ["infra"]


def test_issue_label_create_uses_the_default_color(cli: Cli) -> None:
    identifier = cli.create("needs a label")

    assert cli.run("issue", "label", identifier, "infra", "--create").exit_code == 0

    assert _label(cli, "infra", _team_id(cli))["color"] == "#6B6F76"


def test_a_color_without_create_is_refused_instead_of_written_nowhere(cli: Cli) -> None:
    identifier = cli.create("wears Bug")
    assert _add(cli, "Bug").exit_code == 0

    result = cli.run("issue", "label", identifier, "Bug", "--color", "#5E6AD2", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert (error["code"], error["field"]) == ("invalid_option", "color")


def test_issue_label_prints_one_row_with_the_columns_of_issue_list(cli: Cli) -> None:
    identifier = cli.create("wears Bug")
    assert _add(cli, "Bug").exit_code == 0

    result = cli.run("issue", "label", identifier, "Bug")

    assert result.exit_code == 0, result.stderr
    lines = [plain(line) for line in result.stdout.splitlines()]
    assert lines[0] == plain(ISSUE_HEADER)
    assert lines[1].startswith(identifier)


def test_issue_label_quiet_prints_the_identifier(cli: Cli) -> None:
    identifier = cli.create("wears Bug")
    assert _add(cli, "Bug").exit_code == 0

    result = cli.run("issue", "label", identifier, "Bug", "--quiet")

    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == identifier


def test_issue_label_of_an_unknown_name_is_a_validation_error(cli: Cli) -> None:
    identifier = cli.create("no label")

    result = cli.run("issue", "label", identifier, "infra", "--json")

    assert result.exit_code == 2
    assert error_json(result)["code"] == "validation_error"
    assert error_json(result)["field"] == "label"


def test_issue_label_of_an_unknown_ulid_is_not_found(cli: Cli) -> None:
    identifier = cli.create("no label")

    result = cli.run("issue", "label", identifier, "01HZZZZZZZZZZZZZZZZZZZZZZZ", "--json")

    assert result.exit_code == 3
    assert error_json(result)["field"] == "label"


def test_label_of_an_archived_issue_is_a_refused_transition(cli: Cli) -> None:
    identifier = cli.create("parked")
    assert _add(cli, "Bug").exit_code == 0
    cli.archive(identifier)

    result = cli.run("issue", "label", identifier, "Bug", "--json")

    assert result.exit_code == 5
    assert error_json(result)["code"] == "invalid_transition"


def test_label_of_an_issue_in_the_trash_is_a_validation_error(cli: Cli) -> None:
    identifier = cli.create("trashed")
    assert _add(cli, "Bug").exit_code == 0
    cli.execute(f"UPDATE issue SET trashed = 1 WHERE identifier = '{identifier}'")

    result = cli.run("issue", "label", identifier, "Bug", "--json")

    assert result.exit_code == 2
    assert error_json(result)["code"] == "validation_error"


def test_n_labels_are_n_transactions_and_the_first_failure_decides_the_exit_code(
    cli: Cli,
) -> None:
    """Scenario: no multi-label atomicity — the first label is written, the second refuses."""
    identifier = cli.create("two labels")
    assert _add(cli, "Bug").exit_code == 0

    result = cli.run("issue", "label", identifier, "Bug", "infra")

    assert result.exit_code == 2
    assert result.stdout == ""
    assert _labels_of(cli, identifier) == ["Bug"]
    assert len(cli.history(identifier)) == 1


def test_a_repeated_label_is_one_label_and_one_history_row(cli: Cli) -> None:
    identifier = cli.create("one label twice")
    assert _add(cli, "Bug").exit_code == 0

    result = cli.run("issue", "label", identifier, "Bug", "bug")

    assert result.exit_code == 0, result.stderr
    assert _labels_of(cli, identifier) == ["Bug"]
    assert len(cli.history(identifier)) == 1


def test_a_name_in_two_spellings_is_refused_instead_of_attaching_one(cli: Cli) -> None:
    """The index is case-sensitive, so the pair is reachable; the verb refuses it, exit 2."""
    identifier = cli.create("wears nothing")
    assert _add(cli, "Bug").exit_code == 0
    cli.execute(
        "INSERT INTO label (id, workspace_id, team_id, parent_id, name, color, description,"
        " is_group, group_type, retired_at)"
        " SELECT '01HZZZZZZZZZZZZZZZZZZZZZZ1', workspace_id, NULL, NULL, 'bug', '#6B6F76',"
        " NULL, 0, NULL, NULL FROM label WHERE name = 'Bug'"
    )

    result = cli.run("issue", "label", identifier, "bug", "--json")

    assert result.exit_code == 2
    error = error_json(result)
    assert (error["code"], error["field"]) == ("validation_error", "ref")
    assert "'Bug'" in str(error["message"]) and "'bug'" in str(error["message"])
    assert _labels_of(cli, identifier) == []


def test_the_ambiguous_refusal_publishes_four_keys_and_a_null_details(cli: Cli) -> None:
    """The names travel in `message`; the mapping is an accessor, never a payload."""
    identifier = cli.create("wears nothing")
    assert _add(cli, "Bug").exit_code == 0
    cli.execute(
        "INSERT INTO label (id, workspace_id, team_id, parent_id, name, color, description,"
        " is_group, group_type, retired_at)"
        " SELECT '01HZZZZZZZZZZZZZZZZZZZZZZ2', workspace_id, NULL, NULL, 'bug', '#6B6F76',"
        " NULL, 0, NULL, NULL FROM label WHERE name = 'Bug'"
    )

    result = cli.run("issue", "label", identifier, "bug", "--json")

    assert result.exit_code == 2
    assert result.stdout == ""
    error = error_json(result)
    assert tuple(error) == ("code", "message", "field", "details")
    assert error["details"] is None
    assert error["message"] == "label bug is ambiguous: 'Bug', 'bug'"


def test_issue_label_json_of_a_plain_add_has_a_null_replaced(cli: Cli) -> None:
    identifier = cli.create("plain")
    assert _add(cli, "Bug").exit_code == 0

    payload = json_stdout(cli.run("issue", "label", identifier, "Bug", "--json"))

    assert payload["replaced"] is None
    assert [label["name"] for label in payload["issue"]["labels"]] == ["Bug"]


# --- issue unlabel ---------------------------------------------------------------------------


def test_detaching_a_label_the_issue_does_not_wear_is_a_missing_link(cli: Cli) -> None:
    identifier = cli.create("wears Bug")
    assert _add(cli, "Bug").exit_code == 0
    assert _add(cli, "infra").exit_code == 0
    assert cli.run("issue", "label", identifier, "Bug").exit_code == 0

    result = cli.run("issue", "unlabel", identifier, "infra", "--json")

    assert result.exit_code == 3
    error = error_json(result)
    assert (error["code"], error["field"]) == ("not_found", "label_link")
    assert _labels_of(cli, identifier) == ["Bug"]


def test_unlinking_a_name_that_exists_nowhere_is_exit_three_not_two(cli: Cli) -> None:
    identifier = cli.create("wears Bug")
    assert _add(cli, "Bug").exit_code == 0
    assert cli.run("issue", "label", identifier, "Bug").exit_code == 0

    result = cli.run("issue", "unlabel", identifier, "infra", "--json")

    assert result.exit_code == 3
    error = error_json(result)
    assert (error["code"], error["field"]) == ("not_found", "label")
    assert _labels_of(cli, identifier) == ["Bug"]


def test_unlabel_removes_the_link_and_answers_the_issue(cli: Cli) -> None:
    identifier = cli.create("wears Bug")
    assert _add(cli, "Bug").exit_code == 0
    assert cli.run("issue", "label", identifier, "Bug").exit_code == 0

    result = cli.run("issue", "unlabel", identifier, "Bug", "--json")

    assert result.exit_code == 0, result.stderr
    payload = json_stdout(result)
    assert payload["replaced"] is None
    assert payload["issue"]["labels"] == []
    assert cli.history(identifier)[0]["field"] == "label"
    assert cli.history(identifier)[0]["new_value"] is None


def test_unlabel_of_a_retired_label_stays_possible(cli: Cli) -> None:
    """A retire must not strand the chips it left behind."""
    identifier = cli.create("wears Bug")
    assert _add(cli, "Bug").exit_code == 0
    assert cli.run("issue", "label", identifier, "Bug").exit_code == 0
    cli.execute("UPDATE label SET retired_at = '2026-11-20T00:00:00Z' WHERE name = 'Bug'")

    result = cli.run("issue", "unlabel", identifier, "Bug")

    assert result.exit_code == 0, result.stderr
    assert _labels_of(cli, identifier) == []


# --- the error envelope of the two edges -----------------------------------------------------


def test_a_missing_issue_publishes_the_resource_it_could_not_find(cli: Cli) -> None:
    """A 404 names what does not exist, and carries no `details`."""
    result = cli.run("issue", "archive", "ENG-999", "--yes", "--json")

    assert result.exit_code == 3
    error = error_json(result)
    assert (error["code"], error["field"], error["details"]) == ("not_found", "issue", None)
    assert "(field: issue)" in cli.run("issue", "archive", "ENG-999", "--yes").stderr


def test_the_two_404s_of_a_label_are_visible_in_the_field(cli: Cli) -> None:
    identifier = cli.create("wears nothing")
    assert _add(cli, "Bug").exit_code == 0

    link = cli.run("issue", "unlabel", identifier, "Bug", "--json")
    missing = cli.run("issue", "unlabel", identifier, "nope", "--json")

    assert error_json(link)["field"] == "label_link"
    assert error_json(missing)["field"] == "label"


def test_json_and_quiet_are_mutually_exclusive_on_every_label_verb(cli: Cli) -> None:
    assert _add(cli, "Bug").exit_code == 0
    for args in (
        ("label", "list"),
        ("label", "show", "Bug"),
        ("label", "update", "Bug", "--name", "Defect"),
        ("label", "archive", "Bug"),
    ):
        result = cli.run(*args, "--json", "--quiet")
        assert result.exit_code == 2, args
        assert error_json(result)["code"] == "invalid_option"


def test_an_empty_update_refuses_before_opening_the_database(tmp_path) -> None:
    """`no_changes` is decided from the flags alone, so a missing file cannot preempt it."""
    result = cli_run_with_db(tmp_path / "absent.db", "label", "update", "Bug", "--json")

    assert result.exit_code == 2
    assert error_json(result)["code"] == "no_changes"


def test_a_missing_database_is_exit_three_for_the_label_group(tmp_path, monkeypatch) -> None:
    missing = tmp_path / "absent.db"
    result = cli_run_with_db(missing, "label", "list", "--json")

    assert result.exit_code == 3
    assert error_json(result)["code"] == "database_not_found"


def cli_run_with_db(db, *args: str) -> Result:
    """Invoke the CLI against an explicit `--db` that does not exist."""
    from hifa.cli.main import app

    return runner.invoke(app, ["--db", str(db), *args])


@pytest.mark.parametrize("verb", ["add", "list", "show", "update", "archive"])
def test_the_five_label_verbs_are_in_the_tree(cli: Cli, verb: str) -> None:
    result = cli.run("label", verb, "--help")

    assert result.exit_code == 0, result.stderr


def test_label_json_is_a_single_document_on_stdout(cli: Cli) -> None:
    assert _add(cli, "Bug").exit_code == 0

    result = cli.run("label", "list", "--json")

    assert json.loads(result.stdout)["items"][0]["name"] == "Bug"


def test_an_unknown_assignee_publishes_member_not_the_flag(cli: Cli) -> None:
    """A 404 names the resource it could not find, never the flag that carried the value."""
    identifier = cli.create("nobody assigned")

    result = cli.run("issue", "edit", identifier, "-a", "nobody@example.com", "--json")

    assert result.exit_code == 3
    error = error_json(result)
    assert (error["code"], error["field"], error["details"]) == ("not_found", "member", None)
