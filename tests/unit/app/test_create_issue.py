"""Unit tests for `app.issues.create_issue` with in-memory fakes.

Every validation that must fail is asserted by `rule`/`code`; the happy path asserts
identifier assignment, default state, labels, history and branch name without touching
SQLite (transaction behaviour is covered by the integration suite).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from typing import Any, cast

import pytest

import hifa.app.issues as issues_module
from helpers import FrozenClock, SeqIdGen
from hifa.app.commands import Actor, CreateIssue
from hifa.app.issues import create_issue
from hifa.domain.entities import Issue, Label, Member, Team, WorkflowState, Workspace
from hifa.domain.enums import WorkflowStateType
from hifa.domain.errors import Conflict, NotFound, Validation

NOW = FrozenClock().now()

#: Milestone columns of `issue`, in order.
_MILESTONES: tuple[str, ...] = ("started_at", "completed_at", "canceled_at")


def _workspace() -> Workspace:
    return Workspace(id="WS", name="Hifa", url_key="hifa", created_at=NOW)


def _team(**overrides: object) -> Team:
    data: dict[str, object] = {
        "id": "T1",
        "workspace_id": "WS",
        "key": "ENG",
        "name": "Engineering",
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return Team(**data)  # type: ignore[arg-type]


def _state(
    name: str, state_type: WorkflowStateType, position: float, default: bool = False
) -> WorkflowState:
    return WorkflowState(
        id=f"S-{name}",
        team_id="T1",
        name=name,
        type=state_type,
        position=position,
        is_default=default,
    )


def _member(active: bool = True) -> Member:
    return Member(
        id="M1",
        workspace_id="WS",
        name="ana",
        display_name="Ana",
        email="ana@example.com",
        is_active=active,
        created_at=NOW,
    )


class _Teams:
    def __init__(self, team: Team, workspace: Workspace) -> None:
        self._team = team
        self._workspace = workspace

    def workspace(self) -> Workspace:
        return self._workspace

    def get_by_key(self, workspace_id: str, key: str) -> Team | None:
        return self._team if key.upper() == self._team.key else None

    def get(self, team_id: str) -> Team | None:
        return self._team if team_id == self._team.id else None


class _States:
    def __init__(self, states: list[WorkflowState]) -> None:
        self._states = states

    def get_by_name(self, team_id: str, name: str) -> WorkflowState | None:
        return next((s for s in self._states if s.name == name), None)

    def get(self, state_id: str) -> WorkflowState | None:
        return next((s for s in self._states if s.id == state_id), None)

    def list_for_team(self, team_id: str) -> list[WorkflowState]:
        return sorted(self._states, key=lambda s: s.position)


class _Labels:
    def __init__(self, labels: list[Label]) -> None:
        self._labels = labels

    def get(self, label_id: str) -> Label | None:
        return next((lbl for lbl in self._labels if lbl.id == label_id), None)

    def get_by_name(self, workspace_id: str, team_id: str | None, name: str) -> Label | None:
        return next(
            (lbl for lbl in self._labels if lbl.name == name and lbl.team_id == team_id), None
        )


class _Members:
    def __init__(self, member: Member) -> None:
        self._member = member

    def get(self, member_id: str) -> Member | None:
        return self._member if member_id == self._member.id else None


class _Issues:
    def __init__(self, parent: Issue | None = None) -> None:
        self._parent = parent
        self.inserted: list[Issue] = []
        self.labels: list[tuple[str, list[str]]] = []
        self.number = 1

    def next_number(self, team_id: str) -> int:
        return self.number

    def get(self, issue_id: str) -> Issue | None:
        return self._parent if self._parent is not None and self._parent.id == issue_id else None

    def get_by_identifier(self, identifier: str) -> Issue | None:
        return (
            self._parent
            if self._parent is not None and self._parent.identifier == identifier
            else None
        )

    def ancestor_chain(self, issue_id: str) -> dict[str, str | None]:
        return {issue_id: None}

    def positions(self, team_id: str, scope: str = "sort", parent_id=None, state_id=None):
        return []

    def insert(self, issue: Issue) -> Issue:
        self.inserted.append(issue)
        return issue

    def set_labels(self, issue_id: str, label_ids: list[str]) -> None:
        self.labels.append((issue_id, list(label_ids)))


class _History:
    def __init__(self) -> None:
        self.rows: list[tuple[str, list[tuple[str, object, object]], str | None]] = []

    def record(self, issue_id, changes, *, actor_id, now) -> int:
        self.rows.append((issue_id, list(changes), actor_id))
        return len(changes)


class _Repos:
    def __init__(self, **parts: object) -> None:
        self.teams = parts["teams"]
        self.states = parts["states"]
        self.labels = parts["labels"]
        self.members = parts["members"]
        self.issues = parts["issues"]
        self.history = parts["history"]


class _UoW:
    def __init__(self, repos: _Repos, *, now=NOW) -> None:
        self._repos = repos
        self.clock = FrozenClock(now)
        self.idgen = SeqIdGen()
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def write(self):
        try:
            yield self._repos
        except BaseException:
            self.rolled_back = True
            raise
        else:
            self.committed = True


def build(
    *,
    team: Team | None = None,
    states: list[WorkflowState] | None = None,
    labels: list[Label] | None = None,
    member: Member | None = None,
    parent: Issue | None = None,
):
    team = team or _team()
    if states is None:
        states = [
            _state("Backlog", WorkflowStateType.BACKLOG, 1.0),
            _state("Todo", WorkflowStateType.UNSTARTED, 2.0, default=True),
            _state("Done", WorkflowStateType.COMPLETED, 3.0),
        ]
    if labels is None:
        labels = [
            Label(id="L1", workspace_id="WS", team_id="T1", name="bug", color="#EB5757"),
            Label(id="L2", workspace_id="WS", team_id=None, name="docs", color="#26B5CE"),
        ]
    repos = _Repos(
        teams=_Teams(team, _workspace()),
        states=_States(states),
        labels=_Labels(labels),
        members=_Members(member or _member()),
        issues=_Issues(parent),
        history=_History(),
    )
    return cast(Any, _UoW(repos)), repos


def issue_row(issue_id: str = "01HPARENT", identifier: str = "ENG-9") -> Issue:
    return Issue(
        id=issue_id,
        workspace_id="WS",
        team_id="T1",
        number=9,
        identifier=identifier,
        title="Parent",
        state_id="S-Backlog",
        created_at=NOW,
        updated_at=NOW,
    )


# --- happy path --------------------------------------------------------------------------


def test_create_issue_assigns_identifier_and_default_state() -> None:
    uow, repos = build()
    issue = create_issue(uow, CreateIssue(team="ENG", title="Fix login"), Actor(member_id="M1"))

    assert issue.identifier == "ENG-1"
    assert issue.state_id == "S-Todo"
    assert issue.creator_id == "M1"
    assert issue.branch_name == "eng-1-fix-login"
    assert issue.sort_order == 1024.0 and issue.board_order == 1024.0
    assert uow.committed and not uow.rolled_back
    assert repos.issues.inserted == [issue]


def test_create_issue_writes_labels_and_history() -> None:
    uow, repos = build()
    label_spec = CreateIssue(team="ENG", title="Fix", labels=("L1", "docs"))
    issue = create_issue(uow, label_spec, Actor(member_id="M1"))

    assert repos.issues.labels == [(issue.id, ["L1", "L2"])]
    assert repos.history.rows == [(issue.id, [("created", None, "ENG-1")], "M1")]


def test_create_issue_uses_explicit_state_and_milestones() -> None:
    uow, repos = build()
    issue = create_issue(uow, CreateIssue(team="ENG", title="Ship", state="Done"), Actor())
    assert issue.state_id == "S-Done"
    assert issue.completed_at is not None and issue.started_at is None


@pytest.mark.parametrize(
    ("state_type", "stamped"),
    [
        (WorkflowStateType.TRIAGE, ()),
        (WorkflowStateType.BACKLOG, ()),
        (WorkflowStateType.UNSTARTED, ()),
        (WorkflowStateType.STARTED, ("started_at",)),
        (WorkflowStateType.COMPLETED, ("completed_at",)),
        (WorkflowStateType.CANCELED, ("canceled_at",)),
    ],
)
def test_create_stamps_the_state_milestones_through_the_shared_table(
    monkeypatch: pytest.MonkeyPatch,
    state_type: WorkflowStateType,
    stamped: tuple[str, ...],
) -> None:
    """The six types are sealed by `apply_milestones`, called as a new issue (`current=None`)."""
    calls: list[dict[str, Any]] = []
    shared = issues_module.apply_milestones

    def spy(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return shared(**kwargs)

    monkeypatch.setattr(issues_module, "apply_milestones", spy)
    name = f"State-{state_type.value}"
    uow, _ = build(states=[_state(name, state_type, 1.0, default=True)])
    issue = create_issue(uow, CreateIssue(team="ENG", title="Ship", state=name), Actor())

    assert len(calls) == 1
    assert calls[0]["current"] is None
    assert calls[0]["target"] is state_type
    assert tuple(column for column in _MILESTONES if getattr(issue, column) is not None) == stamped


def test_create_issue_accepts_due_date_and_priority() -> None:
    uow, _ = build()
    issue = create_issue(
        uow,
        CreateIssue(team="ENG", title="Due", priority=1, due_date="2026-11-20"),
        Actor(),
    )
    assert issue.priority == 1
    assert issue.due_date == date(2026, 11, 20)


def test_create_issue_parent_sets_parent_and_sub_order() -> None:
    uow, _ = build(parent=issue_row())
    issue = create_issue(uow, CreateIssue(team="ENG", title="Child", parent="ENG-9"), Actor())
    assert issue.parent_id == "01HPARENT"
    assert issue.sub_issue_sort_order == 1024.0


# --- validation failures -----------------------------------------------------------------


def test_unknown_team_is_not_found() -> None:
    uow, _ = build()
    with pytest.raises(NotFound) as caught:
        create_issue(uow, CreateIssue(team="OPS", title="x"), Actor())
    assert caught.value.resource == "team"
    assert uow.rolled_back


def test_archived_team_is_rejected() -> None:
    uow, _ = build(team=_team(archived_at=NOW))
    with pytest.raises(Validation) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x"), Actor())
    assert caught.value.rule == "TEAM_ARCHIVED"


def test_empty_title_is_rejected() -> None:
    uow, _ = build()
    with pytest.raises(Validation) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="   "), Actor())
    assert caught.value.rule == "TITLE_EMPTY"


def test_priority_out_of_range_is_rejected() -> None:
    uow, _ = build()
    with pytest.raises(Validation) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x", priority=5), Actor())
    assert caught.value.rule == "PRIORITY_OUT_OF_RANGE"


def test_bad_due_date_is_rejected() -> None:
    uow, _ = build()
    with pytest.raises(Validation) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x", due_date="20-11-2026"), Actor())
    assert caught.value.rule == "DUE_DATE_FORMAT"


def test_state_of_another_team_is_rejected() -> None:
    foreign = WorkflowState(
        id="S-FOREIGN", team_id="T2", name="Todo2", type=WorkflowStateType.UNSTARTED, position=1.0
    )
    uow, _ = build(states=[_state("Todo", WorkflowStateType.UNSTARTED, 1.0, default=True), foreign])
    with pytest.raises(Validation) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x", state="S-FOREIGN"), Actor())
    assert caught.value.rule == "STATE_TEAM_MISMATCH"


def test_team_without_states_is_rejected() -> None:
    uow, _ = build(states=[])
    with pytest.raises(Validation) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x"), Actor())
    assert caught.value.rule == "TEAM_WITHOUT_STATES"


def test_missing_label_is_rejected() -> None:
    uow, _ = build()
    with pytest.raises(Validation) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x", labels=("nope",)), Actor())
    assert caught.value.rule == "LABEL_NOT_FOUND"


def test_retired_label_is_rejected() -> None:
    retired = Label(
        id="L9", workspace_id="WS", team_id="T1", name="old", color="#000000", retired_at=NOW
    )
    uow, _ = build(labels=[retired])
    with pytest.raises(Validation) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x", labels=("L9",)), Actor())
    assert caught.value.rule == "LABEL_RETIRED"


def test_inactive_assignee_is_rejected() -> None:
    uow, _ = build(member=_member(active=False))
    with pytest.raises(Validation) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x", assignee_id="M1"), Actor())
    assert caught.value.rule == "MEMBER_INACTIVE"


def test_unknown_assignee_is_not_found() -> None:
    uow, _ = build()
    with pytest.raises(NotFound) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x", assignee_id="M9"), Actor())
    assert caught.value.resource == "member"


def test_unknown_parent_is_not_found() -> None:
    uow, _ = build()
    with pytest.raises(NotFound) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x", parent="ENG-404"), Actor())
    assert caught.value.resource == "parent"


def test_unsupported_project_link_is_rejected() -> None:
    uow, _ = build()
    with pytest.raises(Exception) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x", project_id="P1"), Actor())
    assert getattr(caught.value, "code", None) == "NOT_IMPLEMENTED"


def test_duplicate_number_propagates_after_retries(monkeypatch) -> None:
    uow, repos = build()
    calls = {"n": 0}

    def boom(issue: Issue) -> Issue:
        calls["n"] += 1
        raise Conflict("issue number already used", rule="DUPLICATE_NUMBER", field="number")

    monkeypatch.setattr(repos.issues, "insert", boom)
    monkeypatch.setattr("hifa.app.retry.time.sleep", lambda _: None)
    with pytest.raises(Conflict) as caught:
        create_issue(uow, CreateIssue(team="ENG", title="x"), Actor())
    assert caught.value.rule == "DUPLICATE_NUMBER"
    assert calls["n"] == 3
