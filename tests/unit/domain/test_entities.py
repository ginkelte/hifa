"""`domain.entities` — inmutabilidad, `extra="forbid"`, defaults y properties."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError as PydanticValidationError

from hifa.domain.entities import Comment, Issue, IssueView, Label, Team, WorkflowState
from hifa.domain.enums import EstimationType, Priority, TeamVisibility, WorkflowStateType
from hifa.domain.errors import Validation as DomainValidation

NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def rebuilt(model: Issue | Team, **changes: object) -> Issue:
    """Reconstruye validando: `model_copy(update=...)` no corre validadores."""
    return type(model).model_validate({**model.model_dump(), **changes})


def test_entities_are_frozen(issue: Issue) -> None:
    with pytest.raises(PydanticValidationError):
        issue.title = "Otro"  # type: ignore[misc]
    assert issue.title == "Fix login"


def test_entities_reject_fields_outside_the_ddl(team: Team) -> None:
    with pytest.raises(PydanticValidationError, match="story_points"):
        Team(
            id=team.id,
            workspace_id=team.workspace_id,
            key=team.key,
            name=team.name,
            created_at=NOW,
            updated_at=NOW,
            story_points=3,
        )


def test_model_copy_allows_deriving_a_copy(issue: Issue) -> None:
    renamed = issue.model_copy(update={"title": "Fix logout"})
    assert renamed.title == "Fix logout"
    assert issue.title == "Fix login"
    assert renamed is not issue


def test_team_defaults_from_the_ddl(team: Team) -> None:
    assert team.timezone == "America/Santiago"
    assert team.visibility is TeamVisibility.PUBLIC
    assert team.cycles_enabled is False
    assert team.cycle_duration == 2
    assert team.cycle_start_day == 1
    assert team.issue_estimation_type is EstimationType.FIBONACCI
    assert team.is_archived is False


def test_team_key_is_normalized_and_validated(team: Team) -> None:
    normalized = rebuilt(team, key=" eng ")
    assert normalized.key == "ENG"


def test_invalid_team_key_is_domain_validation(team: Team) -> None:
    with pytest.raises(DomainValidation) as excinfo:
        rebuilt(team, key="e-1")
    assert excinfo.value.rule == "KEY_INVALID"
    assert excinfo.value.field == "team.key"


def test_naive_datetime_is_rejected(team: Team) -> None:
    with pytest.raises(PydanticValidationError):
        Team(
            id="01X",
            workspace_id=team.workspace_id,
            key="OPS",
            name="Ops",
            created_at=datetime(2026, 11, 8, 12, 0),
            updated_at=NOW,
        )


def test_bools_are_built_from_0_and_1(team: Team, issue: Issue) -> None:
    trashed = rebuilt(issue, trashed=1)
    assert trashed.trashed is True
    assert rebuilt(team, cycles_enabled=1).cycles_enabled is True


def test_iso_due_date_string_becomes_date(issue: Issue) -> None:
    dated = rebuilt(issue, due_date="2026-11-30")
    assert dated.due_date == date(2026, 11, 30)


def test_priority_accepts_int_and_normalizes_to_enum(issue: Issue) -> None:
    urgent = rebuilt(issue, priority=1)
    assert urgent.priority is Priority.URGENT
    assert issue.priority is Priority.NO_PRIORITY


def test_issue_visibility_properties(issue: Issue) -> None:
    assert issue.is_visible is True
    assert issue.is_archived is False
    assert issue.is_trashed is False
    trashed = issue.model_copy(update={"trashed": True})
    assert trashed.is_visible is False
    archived = issue.model_copy(update={"archived_at": NOW})
    assert archived.is_archived is True
    assert archived.is_visible is False


def test_is_visible_is_a_property_not_a_field(issue: Issue) -> None:
    assert "is_visible" not in Issue.model_fields
    assert "is_visible" not in issue.model_dump()


def test_issueview_adds_only_the_three_v_issue_columns(team: Team, issue: Issue) -> None:
    view = IssueView(
        **issue.model_dump(),
        priority_label="No priority",
        is_open=True,
        visible=True,
    )
    assert set(IssueView.model_fields) - set(Issue.model_fields) == {
        "priority_label",
        "is_open",
        "visible",
    }
    assert view.is_visible is True
    assert view.visible is True


def test_issueview_keeps_visible_as_a_column(
    issue: Issue,
) -> None:
    archived = issue.model_copy(update={"archived_at": NOW})
    view = IssueView(
        **archived.model_dump(),
        priority_label="High",
        is_open=True,
        visible=False,
    )
    assert view.visible is False
    assert view.model_dump()["visible"] is False
    assert view.is_visible is False


def test_label_properties(team: Team, label: Label) -> None:
    assert label.is_workspace_wide is False
    assert label.is_retired is False
    wide = label.model_copy(update={"team_id": None})
    assert wide.is_workspace_wide is True
    assert label.model_copy(update={"retired_at": NOW}).is_retired is True


def test_comment_is_resolved(team: Team, issue: Issue) -> None:
    comment = Comment(
        id="01CMT",
        issue_id=issue.id,
        body="hola",
        created_at=NOW,
        updated_at=NOW,
    )
    assert comment.is_resolved is False
    assert comment.model_copy(update={"resolved_at": NOW}).is_resolved is True


def test_workflow_state_type_is_enum_not_free_string(team: Team) -> None:
    state = WorkflowState(
        id="01S",
        team_id=team.id,
        name="Done",
        type="completed",
        position=3072.0,
    )
    assert state.type is WorkflowStateType.COMPLETED
    with pytest.raises(PydanticValidationError):
        WorkflowState(id="02S", team_id=team.id, name="X", type="done?", position=1.0)


def test_issue_without_state_id_fails(team: Team) -> None:
    with pytest.raises(PydanticValidationError):
        Issue(
            id="01ISSUE",
            workspace_id=team.workspace_id,
            team_id=team.id,
            number=1,
            identifier="ENG-1",
            title="Fix login",
            created_at=NOW,
            updated_at=NOW,
        )


def test_created_at_is_aware_in_utc(issue: Issue) -> None:
    assert issue.created_at == NOW
    assert issue.created_at.tzinfo is UTC
