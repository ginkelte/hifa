"""`domain.rules` — the 13 pure validations, happy path and rejection path per rule."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from hifa.domain import rules
from hifa.domain.entities import Issue, Label, Team, WorkflowState
from hifa.domain.enums import EstimationType, Priority, RelationType, WorkflowStateType
from hifa.domain.errors import Conflict, Validation

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


# --- constantes -----------------------------------------------------------------------


def test_size_constants() -> None:
    assert rules.MAX_TITLE == 255
    assert rules.MAX_BODY == 50_000
    assert rules.MAX_DESCRIPTION == 100_000
    assert rules.MAX_SUBISSUE_DEPTH == 5


# --- title -----------------------------------------------------------------------------


def test_validate_title_strips() -> None:
    assert rules.validate_title("  Fix login  ") == "Fix login"


def test_validate_title_rejects_empty() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_title("   ")
    assert excinfo.value.rule == "TITLE_EMPTY"
    assert excinfo.value.field == "title"
    assert excinfo.value.code == "TITLE_EMPTY"


def test_validate_title_rejects_too_long() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_title("x" * (rules.MAX_TITLE + 1))
    assert excinfo.value.rule == "TITLE_TOO_LONG"
    assert excinfo.value.details["max_length"] == rules.MAX_TITLE


def test_validate_title_accepts_the_limit() -> None:
    assert len(rules.validate_title("x" * rules.MAX_TITLE)) == rules.MAX_TITLE


# --- comment body ----------------------------------------------------------------------


def test_validate_comment_body_strips() -> None:
    assert rules.validate_comment_body("  hola  ") == "hola"


def test_validate_comment_body_rejects_empty() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_comment_body("\n\t ")
    assert excinfo.value.rule == "BODY_EMPTY"
    assert excinfo.value.field == "body"


def test_validate_comment_body_rejects_too_long() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_comment_body("x" * (rules.MAX_BODY + 1))
    assert excinfo.value.rule == "BODY_TOO_LONG"


def test_validate_comment_body_accepts_the_limit() -> None:
    assert len(rules.validate_comment_body("x" * rules.MAX_BODY)) == rules.MAX_BODY


# --- prioridad ------------------------------------------------------------------------


@pytest.mark.parametrize("value", [0, 1, 2, 3, 4])
def test_validate_priority_accepts_0_to_4(value: int) -> None:
    assert rules.validate_priority(value) is Priority(value)


@pytest.mark.parametrize("value", [-1, 5, 7, 100])
def test_validate_priority_rejects_out_of_range(value: int) -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_priority(value)
    assert excinfo.value.rule == "PRIORITY_OUT_OF_RANGE"
    assert excinfo.value.field == "priority"


@pytest.mark.parametrize("value", ["2", 2.0, True])
def test_validate_priority_rejects_non_integers(value: object) -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_priority(value)  # type: ignore[arg-type]
    assert excinfo.value.rule == "PRIORITY_OUT_OF_RANGE"


# --- due_date -------------------------------------------------------------------------


def test_validate_due_date_none() -> None:
    assert rules.validate_due_date(None) is None


@pytest.mark.parametrize("value", ["2026-11-30", "2026-01-01", "2026-02-28", "2028-02-29"])
def test_validate_due_date_accepts_yyyy_mm_dd(value: str) -> None:
    assert rules.validate_due_date(value) == date.fromisoformat(value)


def test_validate_due_date_passes_a_date_through() -> None:
    assert rules.validate_due_date(date(2026, 11, 30)) == date(2026, 11, 30)


@pytest.mark.parametrize(
    "value",
    ["2026-13-45", "2026-1-5", "2026-02-30", "30-11-2026", "2026/11/30", "2026-11-30T00:00:00", ""],
)
def test_validate_due_date_rejects_bad_formats_and_impossible_dates(value: str) -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_due_date(value)
    assert excinfo.value.rule == "DUE_DATE_FORMAT"
    assert excinfo.value.field == "due_date"


def test_validate_due_date_rejects_datetime() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_due_date(datetime(2026, 11, 30, 10, 0, tzinfo=UTC))  # type: ignore[arg-type]
    assert excinfo.value.rule == "DUE_DATE_FORMAT"


# --- date ranges -----------------------------------------------------------------------


def test_validate_date_range_ok() -> None:
    assert rules.validate_date_range(date(2026, 11, 1), date(2026, 11, 30)) is None


def test_validate_date_range_accepts_null_bounds() -> None:
    assert rules.validate_date_range(None, date(2026, 11, 30)) is None
    assert rules.validate_date_range(date(2026, 11, 1), None) is None


def test_validate_date_range_rejects_inverted() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_date_range(date(2026, 11, 30), date(2026, 11, 1))
    assert excinfo.value.rule == "DATE_RANGE_INVERTED"
    assert excinfo.value.field == "target_date"


def test_validate_date_range_for_cycle_with_own_fields() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_date_range(
            date(2026, 11, 16),
            date(2026, 11, 9),
            start_field="starts_at",
            target_field="ends_at",
        )
    assert excinfo.value.rule == "DATE_RANGE_INVERTED"
    assert excinfo.value.field == "ends_at"


# --- estimate -------------------------------------------------------------------------


def test_validate_estimate_not_used_with_none() -> None:
    assert rules.validate_estimate(None, EstimationType.NOT_USED) is None


def test_validate_estimate_not_used_rejects_value() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_estimate(3, EstimationType.NOT_USED)
    assert excinfo.value.rule == "ESTIMATE_NOT_USED"
    assert excinfo.value.field == "estimate"


def test_validate_estimate_fibonacci_rejects_4() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_estimate(4, EstimationType.FIBONACCI)
    assert excinfo.value.rule == "ESTIMATE_NOT_IN_SCALE"
    assert excinfo.value.details["estimation"] == "fibonacci"


@pytest.mark.parametrize(
    ("value", "estimation"),
    [
        (0, EstimationType.FIBONACCI),
        (21, EstimationType.FIBONACCI),
        (16, EstimationType.EXPONENTIAL),
        (100, EstimationType.LINEAR),
        (3, EstimationType.T_SHIRT),
        (None, EstimationType.LINEAR),
    ],
)
def test_validate_estimate_accepts_scale_values(
    value: float | None, estimation: EstimationType
) -> None:
    result = rules.validate_estimate(value, estimation)
    assert result == (None if value is None else float(value))


def test_validate_estimate_exponential_rejects_3() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_estimate(3, EstimationType.EXPONENTIAL)
    assert excinfo.value.rule == "ESTIMATE_NOT_IN_SCALE"


def test_validate_estimate_t_shirt_rejects_0() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_estimate(0, EstimationType.T_SHIRT)
    assert excinfo.value.rule == "ESTIMATE_NOT_IN_SCALE"


# --- team state ------------------------------------------------------------------------


def test_validate_state_team_ok(state: WorkflowState) -> None:
    assert rules.validate_state_team(state.team_id, state) is None


def test_validate_state_team_rejects_another_team(other_team: Team, state: WorkflowState) -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_state_team(other_team.id, state)
    assert excinfo.value.rule == "STATE_TEAM_MISMATCH"
    assert excinfo.value.field == "state_id"


# --- hierarchy -------------------------------------------------------------------------


def test_validate_parent_without_parent_is_ok(team: Team) -> None:
    assert rules.validate_parent("01A", team.id, None, {}) is None


def test_validate_parent_rejects_self_parent(team: Team, issue: Issue) -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_parent(issue.id, team.id, issue, {})
    assert excinfo.value.rule == "PARENT_SELF"
    assert excinfo.value.field == "parent_id"


def test_validate_parent_rejects_parent_from_another_team(
    team: Team, other_team: Team, issue: Issue
) -> None:
    foreign = issue.model_copy(update={"id": "01FOREIGN", "team_id": other_team.id})
    with pytest.raises(Validation) as excinfo:
        rules.validate_parent(issue.id, team.id, foreign, {})
    assert excinfo.value.rule == "PARENT_TEAM_MISMATCH"


def test_validate_parent_rejects_two_level_cycle(team: Team, issue: Issue) -> None:
    parent = issue.model_copy(update={"id": "01B"})
    with pytest.raises(Validation) as excinfo:
        rules.validate_parent(issue.id, team.id, parent, {"01B": issue.id})
    assert excinfo.value.rule == "PARENT_CYCLE"


def test_validate_parent_rejects_three_level_cycle(team: Team, issue: Issue) -> None:
    """A -> C, C -> B, B -> A: setting A's parent to C closes the cycle."""
    parent_c = issue.model_copy(update={"id": "01C"})
    ancestors = {"01C": "01B", "01B": issue.id, issue.id: None}
    with pytest.raises(Validation) as excinfo:
        rules.validate_parent(issue.id, team.id, parent_c, ancestors)
    assert excinfo.value.rule == "PARENT_CYCLE"


def test_validate_parent_accepts_chain_without_cycle(team: Team, issue: Issue) -> None:
    parent_c = issue.model_copy(update={"id": "01C"})
    ancestors = {"01C": "01B", "01B": None}
    assert rules.validate_parent(issue.id, team.id, parent_c, ancestors) is None


def test_validate_parent_does_not_mutate_ancestors(team: Team, issue: Issue) -> None:
    parent = issue.model_copy(update={"id": "01B"})
    ancestors = {"01B": "01A", "01A": None}
    rules.validate_parent(issue.id, team.id, parent, ancestors)
    assert ancestors == {"01B": "01A", "01A": None}


def test_validate_parent_tolerates_cycle_without_the_child(team: Team, issue: Issue) -> None:
    """A pre-existing cycle that does not include the child does not hang the check."""
    parent = issue.model_copy(update={"id": "01B"})
    assert rules.validate_parent(issue.id, team.id, parent, {"01B": "01B"}) is None


def test_validate_parent_accepts_a_subtree_that_reaches_the_last_level(
    team: Team, issue: Issue
) -> None:
    """Parent at level 3 + the child (1) + a subtree of height 1 = exactly 5 levels."""
    parent = issue.model_copy(update={"id": "01C"})
    ancestors = {"01C": "01B", "01B": "01A", "01A": None}
    assert rules.validate_parent(issue.id, team.id, parent, ancestors, subtree_height=1) is None


def test_validate_parent_rejects_a_subtree_past_the_bound(team: Team, issue: Issue) -> None:
    """The same parent with a height-2 subtree would put a descendant at level 6."""
    parent = issue.model_copy(update={"id": "01C"})
    ancestors = {"01C": "01B", "01B": "01A", "01A": None}
    with pytest.raises(Validation) as excinfo:
        rules.validate_parent(issue.id, team.id, parent, ancestors, subtree_height=2)
    assert excinfo.value.rule == "PARENT_DEPTH_EXCEEDED"
    assert excinfo.value.field == "parent_id"
    assert excinfo.value.details["max_depth"] == rules.MAX_SUBISSUE_DEPTH


def test_validate_parent_rejects_a_leaf_under_the_fifth_level(team: Team, issue: Issue) -> None:
    """Without any subtree, six levels of ancestry are already too deep."""
    parent = issue.model_copy(update={"id": "01E"})
    ancestors = {"01E": "01D", "01D": "01C", "01C": "01B", "01B": "01A", "01A": None}
    with pytest.raises(Validation) as excinfo:
        rules.validate_parent(issue.id, team.id, parent, ancestors)
    assert excinfo.value.rule == "PARENT_DEPTH_EXCEEDED"


def test_validate_parent_counts_a_missing_subtree_as_a_leaf(team: Team, issue: Issue) -> None:
    """`subtree_height` defaults to 0: an issue that does not exist yet creates a leaf."""
    parent = issue.model_copy(update={"id": "01D"})
    ancestors = {"01D": "01C", "01C": "01B", "01B": "01A", "01A": None}
    assert rules.validate_parent(issue.id, team.id, parent, ancestors) is None


# --- cierre ---------------------------------------------------------------------------


@pytest.mark.parametrize("target", [WorkflowStateType.COMPLETED, WorkflowStateType.CANCELED])
def test_validate_close_rejects_with_open_children(target: WorkflowStateType) -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_close(target, 2)
    assert excinfo.value.rule == "CLOSE_WITH_OPEN_CHILDREN"
    assert excinfo.value.details["open_children"] == 2


@pytest.mark.parametrize("target", [WorkflowStateType.COMPLETED, WorkflowStateType.CANCELED])
def test_validate_close_accepts_without_children(target: WorkflowStateType) -> None:
    assert rules.validate_close(target, 0) is None


def test_validate_close_ignores_children_in_open_states() -> None:
    assert rules.validate_close(WorkflowStateType.STARTED, 3) is None


# --- transition ------------------------------------------------------------------------


def test_validate_transition_ok(started_state: WorkflowState, done_state: WorkflowState) -> None:
    assert rules.validate_transition(started_state, done_state, 0) is None


def test_validate_transition_rejects_archived_state(
    started_state: WorkflowState, done_state: WorkflowState
) -> None:
    archived = done_state.model_copy(update={"archived_at": NOW})
    with pytest.raises(Validation) as excinfo:
        rules.validate_transition(started_state, archived, 0)
    assert excinfo.value.rule == "STATE_ARCHIVED"
    assert excinfo.value.field == "state_id"


def test_validate_transition_rejects_close_with_children(
    started_state: WorkflowState, done_state: WorkflowState
) -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_transition(started_state, done_state, 2)
    assert excinfo.value.rule == "CLOSE_WITH_OPEN_CHILDREN"


def test_validate_transition_rejects_state_from_another_team(
    other_team: Team, started_state: WorkflowState
) -> None:
    foreign = WorkflowState(
        id="01STOPS",
        team_id=other_team.id,
        name="Done",
        type=WorkflowStateType.COMPLETED,
        position=1024.0,
    )
    with pytest.raises(Validation) as excinfo:
        rules.validate_transition(started_state, foreign, 0)
    assert excinfo.value.rule == "STATE_TEAM_MISMATCH"


def test_validate_transition_allows_any_jump_between_types(
    state: WorkflowState, done_state: WorkflowState
) -> None:
    """There is no rigid state machine: unstarted -> completed with 0 children is valid."""
    assert rules.validate_transition(state, done_state, 0) is None


# --- relaciones -----------------------------------------------------------------------


def test_validate_relation_accepts_new_relation() -> None:
    assert rules.validate_relation("A", "B", RelationType.BLOCKS, []) is None


def test_validate_relation_rejects_self_relation() -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_relation("A", "A", RelationType.RELATED, [])
    assert excinfo.value.rule == "RELATION_SELF"


def test_validate_relation_rejects_exact_duplicate() -> None:
    existing = [("A", "B", RelationType.BLOCKS)]
    with pytest.raises(Conflict) as excinfo:
        rules.validate_relation("A", "B", RelationType.BLOCKS, existing)
    assert excinfo.value.rule == "RELATION_DUPLICATE"
    assert excinfo.value.code == "RELATION_DUPLICATE"


def test_validate_relation_rejects_symmetric_duplicate() -> None:
    existing = [("A", "B", RelationType.RELATED)]
    with pytest.raises(Conflict) as excinfo:
        rules.validate_relation("B", "A", RelationType.RELATED, existing)
    assert excinfo.value.rule == "RELATION_DUPLICATE"


def test_validate_relation_allows_inverse_of_blocks() -> None:
    """`blocks` is asymmetric: B blocks A coexists with A blocks B."""
    assert (
        rules.validate_relation("B", "A", RelationType.BLOCKS, [("A", "B", RelationType.BLOCKS)])
        is None
    )


def test_validate_relation_does_not_mutate_existing_list() -> None:
    existing = [("A", "B", RelationType.SIMILAR)]
    rules.validate_relation("C", "D", RelationType.SIMILAR, existing)
    assert existing == [("A", "B", RelationType.SIMILAR)]


def test_validate_relation_scope_accepts_two_teams_of_one_workspace(
    issue: Issue, other_team: Team
) -> None:
    """An edge never crosses a workspace, but it may join two teams of it."""
    far_end = issue.model_copy(update={"id": "01FAR", "team_id": other_team.id})
    assert rules.validate_relation_scope(issue, far_end) is None


def test_validate_relation_scope_rejects_two_workspaces(issue: Issue) -> None:
    far_end = issue.model_copy(update={"id": "01FAR", "workspace_id": "01OTHERWS"})
    with pytest.raises(Validation) as excinfo:
        rules.validate_relation_scope(issue, far_end)
    assert excinfo.value.rule == "RELATION_WORKSPACE_MISMATCH"
    assert excinfo.value.code == "RELATION_WORKSPACE_MISMATCH"
    assert excinfo.value.details["source_workspace_id"] == issue.workspace_id
    assert excinfo.value.details["target_workspace_id"] == "01OTHERWS"


# --- labels ---------------------------------------------------------------------------


def test_validate_label_scope_accepts_team_label(label: Label, team: Team) -> None:
    assert rules.validate_label_scope(team.id, label) is None


def test_validate_label_scope_accepts_workspace_label(label: Label, team: Team) -> None:
    assert rules.validate_label_scope(team.id, label.model_copy(update={"team_id": None})) is None


def test_validate_label_scope_rejects_label_from_other_team(label: Label, other_team: Team) -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_label_scope(other_team.id, label)
    assert excinfo.value.rule == "LABEL_TEAM_MISMATCH"


def test_validate_label_scope_rejects_retired_label(label: Label, team: Team) -> None:
    retired = label.model_copy(update={"retired_at": NOW})
    with pytest.raises(Validation) as excinfo:
        rules.validate_label_scope(team.id, retired)
    assert excinfo.value.rule == "LABEL_RETIRED"


# --- archivar team --------------------------------------------------------------------


def test_validate_archive_team_ok(team: Team) -> None:
    assert rules.validate_archive_team(team, 0) is None


def test_validate_archive_team_rejects_with_open_issues(team: Team) -> None:
    with pytest.raises(Validation) as excinfo:
        rules.validate_archive_team(team, 3)
    assert excinfo.value.rule == "TEAM_HAS_OPEN_ISSUES"
    assert excinfo.value.details["open_issues"] == 3


def test_validate_archive_team_rejects_already_archived_team(team: Team) -> None:
    archived = team.model_copy(update={"archived_at": NOW})
    with pytest.raises(Conflict) as excinfo:
        rules.validate_archive_team(archived, 0)
    assert excinfo.value.rule == "TEAM_ALREADY_ARCHIVED"


def test_validations_are_deterministic(team: Team) -> None:
    assert rules.validate_archive_team(team, 0) == rules.validate_archive_team(team, 0)
    assert rules.validate_title(" a ") == rules.validate_title(" a ")
