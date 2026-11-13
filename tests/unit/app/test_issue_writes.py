"""Unit tests for the issue write use cases: edit, move and the archive axis.

In-memory fakes only. Every assertion is about an observable effect: the rows handed to
`issue_history`, the columns the repository was asked to write, how many times the clock
was read, and whether the single transaction committed.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any, cast

import pytest

from hifa.app.commands import (
    UNSET,
    Actor,
    ArchiveIssue,
    MoveIssue,
    RestoreIssue,
    TrashIssue,
    UpdateIssue,
)
from hifa.app.issues import (
    archive_issue,
    move_issue,
    restore_issue,
    trash_issue,
    update_issue,
)
from hifa.domain.entities import Issue, Member, Team, WorkflowState
from hifa.domain.enums import WorkflowStateType
from hifa.domain.errors import Conflict, HifaError, InvalidTransition, NotFound, Validation

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
NOW_TEXT = "2026-11-08T12:00:00Z"


# --- fakes --------------------------------------------------------------------------------


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


def _states() -> list[WorkflowState]:
    return [
        WorkflowState(
            id="S-BACKLOG",
            team_id="T1",
            name="Backlog",
            type=WorkflowStateType.BACKLOG,
            position=1.0,
            is_default=True,
        ),
        WorkflowState(
            id="S-TODO",
            team_id="T1",
            name="Todo",
            type=WorkflowStateType.UNSTARTED,
            position=2.0,
        ),
        WorkflowState(
            id="S-PROG",
            team_id="T1",
            name="In Progress",
            type=WorkflowStateType.STARTED,
            position=3.0,
        ),
        WorkflowState(
            id="S-DONE",
            team_id="T1",
            name="Done",
            type=WorkflowStateType.COMPLETED,
            position=4.0,
        ),
        WorkflowState(
            id="S-CANCEL",
            team_id="T1",
            name="Canceled",
            type=WorkflowStateType.CANCELED,
            position=5.0,
        ),
    ]


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


def issue(issue_id: str = "I1", identifier: str = "ENG-1", **overrides: object) -> Issue:
    data: dict[str, object] = {
        "id": issue_id,
        "workspace_id": "WS",
        "team_id": "T1",
        "number": 1,
        "identifier": identifier,
        "title": "Alpha",
        "priority": 0,
        "state_id": "S-BACKLOG",
        "board_order": 1024.0,
        "branch_name": f"{identifier.lower()}-alpha",
        "created_at": NOW,
        "updated_at": NOW,
    }
    data.update(overrides)
    return Issue(**data)  # type: ignore[arg-type]


class _States:
    def __init__(self, states: list[WorkflowState]) -> None:
        self._states = states

    def get(self, state_id: str) -> WorkflowState | None:
        return next((s for s in self._states if s.id == state_id), None)

    def get_by_name(self, team_id: str, name: str) -> WorkflowState | None:
        return next((s for s in self._states if s.team_id == team_id and s.name == name), None)


class _Teams:
    def __init__(self, team: Team, states: list[WorkflowState]) -> None:
        self._team = team
        self._states = _States(states)

    def get(self, team_id: str) -> Team | None:
        return self._team if team_id == self._team.id else None

    def get_state(self, state_id: str) -> WorkflowState | None:
        return self._states.get(state_id)

    def get_state_by_name(self, team_id: str, name: str) -> WorkflowState | None:
        return self._states.get_by_name(team_id, name)


class _Members:
    def __init__(self, members: list[Member]) -> None:
        self._members = members

    def get(self, member_id: str) -> Member | None:
        return next((m for m in self._members if m.id == member_id), None)


class _Issues:
    """Dict-backed repository: writes are visible to the same call and to the assertions."""

    def __init__(self, issues: list[Issue], children: dict[str, int] | None = None) -> None:
        self.rows: dict[str, Issue] = {row.id: row for row in issues}
        self.children = dict(children or {})
        self.updated: list[Issue] = []
        self.state_writes: list[tuple[str, str]] = []
        self.position_writes: list[tuple[str, float]] = []

    def get(self, issue_id: str) -> Issue | None:
        return self.rows.get(issue_id)

    def get_by_identifier(self, identifier: str) -> Issue | None:
        wanted = identifier.strip().upper()
        return next((r for r in self.rows.values() if r.identifier.upper() == wanted), None)

    def require(self, ref: str) -> Issue:
        found = self.get(ref) or self.get_by_identifier(ref)
        if found is None:
            raise NotFound(resource="issue", key=ref)
        return found

    def positions(
        self,
        team_id: str,
        scope: str = "sort",
        parent_id: str | None = None,
        state_id: str | None = None,
    ) -> list[tuple[str, float]]:
        attribute = {"board": "board_order", "sub": "sub_issue_sort_order"}.get(scope, "sort_order")
        rows = [r for r in self.rows.values() if r.team_id == team_id and not r.trashed]
        if state_id is not None:
            rows = [r for r in rows if r.state_id == state_id]
        rows.sort(key=lambda r: (getattr(r, attribute), r.id))
        return [(r.id, float(getattr(r, attribute))) for r in rows]

    def count_open_children(self, issue_id: str) -> int:
        return self.children.get(issue_id, 0)

    def subtree_height(self, issue_id: str) -> int:
        """Levels below this issue: a leaf is 0, its parent is 1."""
        below = sum(1 for row in self.rows.values() if row.parent_id == issue_id)
        deepest = 0
        for row in self.rows.values():
            if row.parent_id == issue_id:
                deepest = max(deepest, 1 + self.subtree_height(row.id))
        return deepest if below else 0

    def ancestor_chain(self, issue_id: str, *, max_depth: int = 32) -> dict[str, str | None]:
        chain: dict[str, str | None] = {}
        cursor: str | None = issue_id
        depth = 0
        while cursor is not None and cursor in self.rows and depth <= max_depth:
            chain[cursor] = self.rows[cursor].parent_id
            cursor = self.rows[cursor].parent_id
            depth += 1
        return chain

    def update(self, row: Issue) -> Issue:
        self.updated.append(row)
        self.rows[row.id] = row
        return row

    def set_state(self, issue_id: str, state_id: str, *, now: datetime) -> Issue:
        self.state_writes.append((issue_id, state_id))
        row = self.rows[issue_id].model_copy(update={"state_id": state_id, "updated_at": now})
        self.rows[issue_id] = row
        return row

    def set_positions(
        self,
        issue_id: str,
        *,
        sort_order: float | None = None,
        board_order: float | None = None,
        sub_issue_sort_order: float | None = None,
        now: datetime,
    ) -> None:
        patch: dict[str, Any] = {"updated_at": now}
        if board_order is not None:
            patch["board_order"] = board_order
            self.position_writes.append((issue_id, board_order))
        if sort_order is not None:
            patch["sort_order"] = sort_order
        if sub_issue_sort_order is not None:
            patch["sub_issue_sort_order"] = sub_issue_sort_order
        self.rows[issue_id] = self.rows[issue_id].model_copy(update=patch)


class _History:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record(self, issue_id: str, changes, *, actor_id: str | None, now: datetime) -> int:
        self.calls.append(
            {
                "issue_id": issue_id,
                "changes": list(changes),
                "actor_id": actor_id,
                "now": now,
            }
        )
        return len(changes)

    @property
    def fields(self) -> list[str]:
        return [field for call in self.calls for field, _, _ in call["changes"]]

    @property
    def rows(self) -> list[tuple[str, str | None, str | None]]:
        return [change for call in self.calls for change in call["changes"]]


class _Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self._now = now
        self.reads = 0

    def now(self) -> datetime:
        self.reads += 1
        return self._now


class _Repos:
    def __init__(self, teams: _Teams, issues: _Issues, history: _History, members: _Members):
        self.teams = teams
        self.issues = issues
        self.history = history
        self.members = members


class _UoW:
    def __init__(self, repos: _Repos) -> None:
        self._repos = repos
        self.clock = _Clock()
        self.writes = 0
        self.committed = False
        self.rolled_back = False

    @contextmanager
    def write(self):
        self.writes += 1
        try:
            yield self._repos
        except BaseException:
            self.rolled_back = True
            raise
        else:
            self.committed = True


def world(
    *,
    issues: list[Issue] | None = None,
    children: dict[str, int] | None = None,
    members: list[Member] | None = None,
    team: Team | None = None,
    states: list[WorkflowState] | None = None,
) -> tuple[Any, _Repos]:
    if issues is None:
        issues = [
            issue("I1", "ENG-1"),
            issue("I2", "ENG-2", number=2, board_order=2048.0),
        ]
    repos = _Repos(
        teams=_Teams(team or _team(), states if states is not None else _states()),
        issues=_Issues(issues, children),
        history=_History(),
        members=_Members(members if members is not None else [_member()]),
    )
    return cast(Any, _UoW(repos)), repos


# --- update_issue: the field diff ----------------------------------------------------------


def test_update_with_an_identical_value_writes_nothing() -> None:
    uow, repos = world()

    saved = update_issue(uow, UpdateIssue(issue="ENG-1", priority=0), Actor(member_id="M1"))

    assert saved.updated_at == NOW
    assert repos.history.calls == []
    assert repos.issues.updated == []
    assert uow.committed and not uow.rolled_back


def test_update_records_one_row_per_changed_column_in_order() -> None:
    uow, repos = world()

    update_issue(
        uow,
        UpdateIssue(issue="ENG-1", title="Beta", priority=2, estimate=3),
        Actor(member_id="M1"),
    )

    assert repos.history.fields == ["title", "priority", "estimate", "branch_name"]
    assert repos.history.rows[0] == ("title", "Alpha", "Beta")
    assert repos.history.rows[1] == ("priority", "0", "2")
    assert repos.history.rows[2] == ("estimate", None, "3.0")
    assert repos.history.rows[3] == ("branch_name", "eng-1-alpha", "eng-1-beta")
    assert len(repos.issues.updated) == 1
    assert repos.issues.updated[0].updated_at == NOW
    assert repos.history.calls[0]["now"] == NOW
    assert repos.history.calls[0]["actor_id"] == "M1"
    assert uow.writes == 1 and uow.clock.reads == 1


def test_update_distinguishes_not_touching_from_clearing() -> None:
    uow, repos = world(issues=[issue("I1", "ENG-1", description="hello", assignee_id="M1")])

    saved = update_issue(
        uow,
        UpdateIssue(issue="ENG-1", description=UNSET, assignee_id=None),
        Actor(),
    )

    assert repos.history.fields == ["assignee_id"]
    assert repos.history.rows[0] == ("assignee_id", "M1", None)
    assert saved.description == "hello"
    assert saved.assignee_id is None


def test_update_rejects_a_blank_title_without_writing() -> None:
    uow, repos = world()

    with pytest.raises(Validation) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-1", title="   "), Actor())

    assert caught.value.rule == "TITLE_EMPTY"
    assert caught.value.field == "title"
    assert uow.rolled_back and repos.history.calls == []


def test_update_rejects_a_priority_out_of_range() -> None:
    uow, _ = world()
    with pytest.raises(Validation) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-1", priority=9), Actor())
    assert caught.value.rule == "PRIORITY_OUT_OF_RANGE"


def test_update_rejects_an_unknown_issue() -> None:
    uow, _ = world()
    with pytest.raises(NotFound) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-404", title="x"), Actor())
    assert caught.value.resource == "issue"


def test_update_of_an_archived_issue_is_an_invalid_transition() -> None:
    uow, repos = world(issues=[issue("I1", "ENG-1", archived_at=NOW)])

    with pytest.raises(InvalidTransition) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-1", title="New"), Actor())

    assert caught.value.rule == "ISSUE_ARCHIVED"
    assert caught.value.field == "ref"
    assert caught.value.code == "INVALID_TRANSITION"
    assert uow.rolled_back and repos.history.calls == []


def test_update_of_a_trashed_issue_is_a_validation_error() -> None:
    uow, _ = world(issues=[issue("I1", "ENG-1", trashed=True)])

    with pytest.raises(Validation) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-1", title="New"), Actor())

    assert caught.value.rule == "ISSUE_TRASHED"


def test_update_with_state_appends_to_the_target_column() -> None:
    uow, repos = world(
        issues=[
            issue("I1", "ENG-1"),
            issue("I2", "ENG-2", number=2, state_id="S-PROG", board_order=2048.0),
        ]
    )

    saved = update_issue(uow, UpdateIssue(issue="ENG-1", state="In Progress"), Actor())

    assert saved.state_id == "S-PROG"
    assert saved.board_order == 3072.0
    assert saved.started_at == NOW and saved.completed_at is None
    assert repos.history.fields == ["state_id", "board_order", "started_at"]
    assert repos.history.rows[0] == ("state_id", "S-BACKLOG", "S-PROG")
    assert repos.history.rows[1] == ("board_order", "1024.0", "3072.0")
    assert repos.history.rows[2] == ("started_at", None, NOW_TEXT)


def test_update_with_state_to_an_empty_column_starts_at_the_first_position() -> None:
    uow, _ = world()

    saved = update_issue(uow, UpdateIssue(issue="ENG-1", state="Done"), Actor())

    assert saved.board_order == 1024.0
    assert saved.completed_at == NOW


def test_update_rejects_a_state_of_another_team() -> None:
    foreign = WorkflowState(
        id="S-OPS",
        team_id="T2",
        name="Review",
        type=WorkflowStateType.UNSTARTED,
        position=1.0,
    )
    uow, repos = world(states=[*_states(), foreign])

    with pytest.raises(Validation) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-1", state="S-OPS"), Actor())

    assert caught.value.rule == "STATE_TEAM_MISMATCH"
    assert repos.history.calls == []


def test_update_rejects_an_unknown_state() -> None:
    uow, _ = world()
    with pytest.raises(NotFound) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-1", state="Nope"), Actor())
    assert caught.value.resource == "state"


def test_update_clears_the_assignee_and_rejects_an_inactive_one() -> None:
    uow, _ = world(members=[_member(active=False)])

    with pytest.raises(Validation) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-1", assignee_id="M1"), Actor())

    assert caught.value.rule == "MEMBER_INACTIVE"


def test_update_with_an_unknown_assignee_is_not_found() -> None:
    uow, _ = world()
    with pytest.raises(NotFound) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-1", assignee_id="M9"), Actor())
    assert caught.value.resource == "member"


def test_update_does_not_regenerate_a_hand_edited_branch_name() -> None:
    uow, repos = world(issues=[issue("I1", "ENG-1", branch_name="mo/custom-work")])

    saved = update_issue(uow, UpdateIssue(issue="ENG-1", title="Beta"), Actor())

    assert repos.history.fields == ["title"]
    assert saved.branch_name == "mo/custom-work"


def test_update_regenerates_a_branch_name_that_was_derived() -> None:
    uow, repos = world()

    saved = update_issue(uow, UpdateIssue(issue="ENG-1", title="Fix: the SSO login"), Actor())

    assert saved.branch_name == "eng-1-fix-the-sso-login"
    assert repos.history.fields == ["title", "branch_name"]


def test_update_rejects_an_unsupported_project_link() -> None:
    uow, _ = world()
    with pytest.raises(HifaError) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-1", project_id="P1"), Actor())
    assert caught.value.code == "NOT_IMPLEMENTED"


def test_update_sets_and_clears_the_due_date() -> None:
    uow, repos = world()

    update_issue(uow, UpdateIssue(issue="ENG-1", due_date="2026-11-20"), Actor())
    assert repos.history.fields == ["due_date"]

    uow, repos = world(issues=[issue("I1", "ENG-1", due_date=datetime(2026, 11, 20).date())])
    update_issue(uow, UpdateIssue(issue="ENG-1", due_date=None), Actor())
    assert repos.history.fields == ["due_date"]


def test_update_reparents_and_keeps_the_sub_order() -> None:
    uow, repos = world(issues=[issue("I1", "ENG-1"), issue("I2", "ENG-2", number=2)])

    saved = update_issue(uow, UpdateIssue(issue="ENG-1", parent="ENG-2"), Actor())

    assert saved.parent_id == "I2"
    assert saved.sub_issue_sort_order == 0.0
    assert repos.history.fields == ["parent_id"]


def test_update_rejects_itself_as_parent() -> None:
    uow, _ = world()
    with pytest.raises(Validation) as caught:
        update_issue(uow, UpdateIssue(issue="ENG-1", parent="ENG-1"), Actor())
    assert caught.value.rule == "PARENT_SELF"


# --- move_issue ----------------------------------------------------------------------------


def test_move_to_the_same_state_and_position_writes_nothing() -> None:
    uow, repos = world(
        issues=[
            issue("I1", "ENG-1", board_order=1024.0),
            issue("I2", "ENG-2", number=2, board_order=2048.0),
        ]
    )

    saved = move_issue(uow, MoveIssue(issue="ENG-2", target_state="Backlog"), Actor())

    assert saved.state_id == "S-BACKLOG" and saved.board_order == 2048.0
    assert saved.updated_at == NOW
    assert repos.history.calls == []
    assert repos.issues.updated == [] and repos.issues.position_writes == []
    assert repos.issues.state_writes == []
    assert uow.committed and uow.writes == 1


def test_move_to_a_started_state_records_state_and_started_at() -> None:
    uow, repos = world(issues=[issue("I1", "ENG-1", board_order=4096.0)])

    saved = move_issue(uow, MoveIssue(issue="ENG-1", target_state="In Progress"), Actor("M1"))

    assert saved.state_id == "S-PROG"
    assert saved.started_at == NOW and saved.completed_at is None
    assert repos.history.fields == ["state_id", "board_order", "started_at"]
    assert repos.history.rows[0] == ("state_id", "S-BACKLOG", "S-PROG")
    assert repos.history.rows[2] == ("started_at", None, NOW_TEXT)
    assert repos.history.calls[0]["actor_id"] == "M1"
    assert uow.clock.reads == 1


def test_move_to_a_completed_state_clears_canceled_at() -> None:
    uow, repos = world(issues=[issue("I1", "ENG-1", canceled_at=NOW, board_order=4096.0)])

    saved = move_issue(uow, MoveIssue(issue="ENG-1", target_state="Done"), Actor())

    assert saved.completed_at == NOW and saved.canceled_at is None
    assert repos.history.fields == [
        "state_id",
        "board_order",
        "completed_at",
        "canceled_at",
    ]


def test_moving_back_to_an_open_state_keeps_started_at_and_clears_the_close() -> None:
    uow, repos = world(
        issues=[
            issue(
                "I1",
                "ENG-1",
                state_id="S-DONE",
                completed_at=NOW,
                started_at=NOW,
                board_order=4096.0,
            )
        ]
    )

    saved = move_issue(uow, MoveIssue(issue="ENG-1", target_state="Todo"), Actor())

    assert saved.started_at == NOW
    assert saved.completed_at is None
    assert repos.history.fields == ["state_id", "board_order", "completed_at"]


def test_move_twice_to_the_same_terminal_state_is_idempotent() -> None:
    uow, repos = world(issues=[issue("I1", "ENG-1", state_id="S-DONE", completed_at=NOW)])

    saved = move_issue(uow, MoveIssue(issue="ENG-1", target_state="Done"), Actor())

    assert saved.completed_at == NOW
    assert repos.history.calls == []


def test_move_to_an_empty_column_uses_the_first_position() -> None:
    uow, repos = world()

    saved = move_issue(uow, MoveIssue(issue="ENG-1", target_state="Done"), Actor())

    assert saved.board_order == 1024.0
    assert repos.issues.position_writes == [("I1", 1024.0)]


def test_move_before_an_anchor_uses_the_midpoint() -> None:
    uow, repos = world(
        issues=[
            issue("I1", "ENG-1"),
            issue("I2", "ENG-2", number=2, state_id="S-PROG", board_order=1024.0),
            issue("I3", "ENG-3", number=3, state_id="S-PROG", board_order=2048.0),
        ]
    )

    saved = move_issue(
        uow,
        MoveIssue(issue="ENG-1", target_state="In Progress", position="before", before_id="ENG-3"),
        Actor(),
    )

    assert saved.board_order == 1536.0
    assert repos.issues.position_writes == [("I1", 1536.0)]


def test_move_with_an_anchor_of_another_state_is_rejected() -> None:
    uow, repos = world()

    with pytest.raises(Validation) as caught:
        move_issue(
            uow,
            MoveIssue(
                issue="ENG-1", target_state="In Progress", position="before", before_id="ENG-2"
            ),
            Actor(),
        )

    assert caught.value.rule == "POSITION_NEIGHBOR"
    assert uow.rolled_back and repos.issues.state_writes == []


def test_move_with_an_unknown_anchor_is_not_found() -> None:
    uow, _ = world()
    with pytest.raises(NotFound) as caught:
        move_issue(
            uow,
            MoveIssue(issue="ENG-1", target_state="Done", position="after", after_id="ENG-404"),
            Actor(),
        )
    assert caught.value.resource == "neighbor"


def test_move_with_a_colliding_sort_order_is_rejected() -> None:
    uow, _ = world(
        issues=[
            issue("I1", "ENG-1"),
            issue("I2", "ENG-2", number=2, board_order=2048.0),
        ]
    )

    with pytest.raises(Validation) as caught:
        move_issue(uow, MoveIssue(issue="ENG-1", sort_order=2048.0), Actor())

    assert caught.value.rule == "POSITION_OUT_OF_ORDER"


def test_move_rejects_an_unknown_target_state() -> None:
    uow, _ = world()
    with pytest.raises(NotFound) as caught:
        move_issue(uow, MoveIssue(issue="ENG-1", target_state="Nope"), Actor())
    assert caught.value.resource == "workflow_state"


def test_move_toward_a_terminal_state_with_open_children_is_rejected() -> None:
    uow, repos = world(children={"I1": 2})

    with pytest.raises(Validation) as caught:
        move_issue(uow, MoveIssue(issue="ENG-1", target_state="Done"), Actor())

    assert caught.value.rule == "CLOSE_WITH_OPEN_CHILDREN"
    assert uow.rolled_back
    assert repos.issues.state_writes == [] and repos.history.calls == []


def test_move_of_an_archived_issue_is_an_invalid_transition() -> None:
    uow, _ = world(issues=[issue("I1", "ENG-1", archived_at=NOW)])

    with pytest.raises(InvalidTransition) as caught:
        move_issue(uow, MoveIssue(issue="ENG-1", target_state="Done"), Actor())

    assert caught.value.rule == "ISSUE_ARCHIVED"
    assert caught.value.field == "ref"


def test_move_of_a_trashed_issue_is_a_validation_error() -> None:
    uow, _ = world(issues=[issue("I1", "ENG-1", trashed=True)])

    with pytest.raises(Validation) as caught:
        move_issue(uow, MoveIssue(issue="ENG-1", target_state="Done"), Actor())

    assert caught.value.rule == "ISSUE_TRASHED"


def test_move_rebalances_a_column_without_a_gap_and_retries() -> None:
    uow, repos = world(
        issues=[
            issue("I1", "ENG-1"),
            issue("I2", "ENG-2", number=2, state_id="S-PROG", board_order=1024.0),
            issue("I3", "ENG-3", number=3, state_id="S-PROG", board_order=1024.0000005),
        ]
    )

    saved = move_issue(
        uow,
        MoveIssue(issue="ENG-1", target_state="In Progress", position="before", before_id="ENG-3"),
        Actor(),
    )

    assert saved.board_order == 1536.0
    assert repos.issues.position_writes[:2] == [("I2", 1024.0), ("I3", 2048.0)]
    assert repos.issues.position_writes[-1] == ("I1", 1536.0)


# --- archive_issue / trash_issue / restore_issue -------------------------------------------


def test_archive_stamps_archived_at_and_leaves_updated_at_alone() -> None:
    uow, repos = world()

    saved = archive_issue(uow, ArchiveIssue(issue="ENG-1"), Actor(member_id="M1"))

    assert saved.archived_at == NOW
    assert saved.updated_at == NOW
    assert repos.history.rows == [("archived_at", None, NOW_TEXT)]
    assert repos.history.calls[0]["actor_id"] == "M1"
    assert uow.clock.reads == 1 and uow.writes == 1


def test_archive_of_an_already_archived_issue_is_a_no_op() -> None:
    uow, repos = world(issues=[issue("I1", "ENG-1", archived_at=NOW)])

    saved = archive_issue(uow, ArchiveIssue(issue="ENG-1"), Actor())

    assert saved.archived_at == NOW
    assert repos.history.calls == [] and repos.issues.updated == []
    assert uow.clock.reads == 0


def test_archive_rejects_a_trashed_issue() -> None:
    uow, _ = world(issues=[issue("I1", "ENG-1", trashed=True)])

    with pytest.raises(Validation) as caught:
        archive_issue(uow, ArchiveIssue(issue="ENG-1"), Actor())

    assert caught.value.rule == "ISSUE_TRASHED"


def test_archive_rejects_a_parent_with_open_children() -> None:
    uow, repos = world(children={"I1": 1})

    with pytest.raises(Validation) as caught:
        archive_issue(uow, ArchiveIssue(issue="ENG-1"), Actor())

    assert caught.value.rule == "CLOSE_WITH_OPEN_CHILDREN"
    assert caught.value.field == "state_id"
    assert uow.rolled_back and repos.issues.updated == []


def test_archive_rejects_an_unknown_issue() -> None:
    uow, _ = world()
    with pytest.raises(NotFound) as caught:
        archive_issue(uow, ArchiveIssue(issue="ENG-404"), Actor())
    assert caught.value.resource == "issue"


def test_trash_moves_updated_at_and_records_the_flag() -> None:
    uow, repos = world(issues=[issue("I1", "ENG-1", updated_at=datetime(2026, 1, 1, tzinfo=UTC))])

    saved = trash_issue(uow, TrashIssue(issue="ENG-1"), Actor())

    assert saved.trashed is True
    assert saved.updated_at == NOW
    assert repos.history.rows == [("trashed", "0", "1")]
    assert uow.clock.reads == 1


def test_trash_of_an_archived_issue_is_allowed() -> None:
    uow, _ = world(issues=[issue("I1", "ENG-1", archived_at=NOW)])

    saved = trash_issue(uow, TrashIssue(issue="ENG-1"), Actor())

    assert saved.trashed is True and saved.archived_at == NOW


def test_trash_of_an_already_trashed_issue_is_a_no_op() -> None:
    uow, repos = world(issues=[issue("I1", "ENG-1", trashed=True)])

    trash_issue(uow, TrashIssue(issue="ENG-1"), Actor())

    assert repos.history.calls == [] and uow.clock.reads == 0


def test_restore_clears_both_layers_in_order() -> None:
    uow, repos = world(
        issues=[
            issue(
                "I1",
                "ENG-1",
                archived_at=NOW,
                trashed=True,
                updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ]
    )

    saved = restore_issue(uow, RestoreIssue(issue="ENG-1"), Actor())

    assert saved.archived_at is None and saved.trashed is False
    assert saved.updated_at == NOW
    assert repos.history.fields == ["archived_at", "trashed"]
    assert repos.history.rows == [("archived_at", NOW_TEXT, None), ("trashed", "1", "0")]
    assert uow.clock.reads == 1


def test_restore_of_an_archived_only_issue_keeps_updated_at() -> None:
    uow, repos = world(
        issues=[issue("I1", "ENG-1", archived_at=NOW, updated_at=datetime(2026, 1, 1, tzinfo=UTC))]
    )

    saved = restore_issue(uow, RestoreIssue(issue="ENG-1"), Actor())

    assert saved.archived_at is None
    assert saved.updated_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert repos.history.fields == ["archived_at"]


def test_restore_of_a_clean_issue_is_a_no_op() -> None:
    uow, repos = world()

    saved = restore_issue(uow, RestoreIssue(issue="ENG-1"), Actor())

    assert saved.archived_at is None and saved.trashed is False
    assert repos.history.calls == [] and repos.issues.updated == []
    assert uow.clock.reads == 0


def test_restore_rejects_an_unknown_issue() -> None:
    uow, _ = world()
    with pytest.raises(NotFound) as caught:
        restore_issue(uow, RestoreIssue(issue="ENG-404"), Actor())
    assert caught.value.resource == "issue"


# --- commands ------------------------------------------------------------------------------


def test_move_command_requires_the_anchor_of_its_position() -> None:
    with pytest.raises(Validation) as caught:
        MoveIssue(issue="ENG-1", position="before")
    assert caught.value.rule == "COMMAND_FIELD"
    assert caught.value.field == "before_id"


def test_move_command_rejects_anchors_with_first_and_last() -> None:
    with pytest.raises(Validation) as caught:
        MoveIssue(issue="ENG-1", position="last", before_id="ENG-2")
    assert caught.value.rule == "COMMAND_FIELD"


def test_update_command_rejects_a_primitive_that_does_not_convert() -> None:
    with pytest.raises(Validation) as caught:
        UpdateIssue(issue="ENG-1", estimate="big")
    assert caught.value.rule == "COMMAND_FIELD"
    assert caught.value.field == "estimate"


def test_move_command_accepts_an_empty_target_state() -> None:
    cmd = MoveIssue(issue="ENG-1")
    assert cmd.target_state is None
    assert cmd.position == "last"


# --- retry envelope -------------------------------------------------------------------------


def _busy() -> Conflict:
    return Conflict("database is locked", rule="DB_BUSY", code="DB_BUSY")


def _fail_the_first_transaction(monkeypatch: pytest.MonkeyPatch, repos: _Repos) -> dict[str, int]:
    """`require` raises `DB_BUSY` on its first call only; returns the attempt counter."""
    attempts = {"n": 0}
    original = repos.issues.require

    def flaky(ref: str) -> Issue:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _busy()
        return original(ref)

    monkeypatch.setattr(repos.issues, "require", flaky)
    monkeypatch.setattr("hifa.app.retry.time.sleep", lambda _: None)
    return attempts


@pytest.mark.parametrize(
    ("use_case", "command", "rows"),
    [
        (update_issue, UpdateIssue(issue="ENG-1", title="Beta"), None),
        (move_issue, MoveIssue(issue="ENG-1", target_state="Todo"), None),
        (archive_issue, ArchiveIssue(issue="ENG-1"), None),
        (trash_issue, TrashIssue(issue="ENG-1"), None),
        (restore_issue, RestoreIssue(issue="ENG-1"), [issue("I1", "ENG-1", archived_at=NOW)]),
    ],
    ids=["update", "move", "archive", "trash", "restore"],
)
def test_every_write_retries_a_busy_transaction_in_a_new_one(
    monkeypatch: pytest.MonkeyPatch,
    use_case: Any,
    command: Any,
    rows: list[Issue] | None,
) -> None:
    uow, repos = world(issues=rows)
    attempts = _fail_the_first_transaction(monkeypatch, repos)

    saved = use_case(uow, command, Actor(member_id="M1"))

    assert saved.identifier == "ENG-1"
    assert attempts["n"] == 2
    assert uow.writes == 2
    assert uow.committed
