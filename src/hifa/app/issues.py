"""Issue use cases.

`create_issue` is the only write implemented in T-05. It runs entirely inside **one**
`uow.write()` and is invoked through `with_write_retry` with `CREATE_RETRY_CODES`
(`DB_BUSY`, `DUPLICATE_NUMBER`); the retry loop lives outside the context manager, so
every attempt is a fresh transaction.

`identifier` atomicity: `number` comes from `IssueRepo.next_number` (MAX+1) under the
UoW's `BEGIN IMMEDIATE`, and `UNIQUE(team_id, number)` closes the race; a collision
arrives translated as `Conflict(rule='DUPLICATE_NUMBER')` and the retry gets the next
transaction. No new locks: SQLite's write lock is the only one.

`project_id` / `milestone_id` / `cycle_id` are accepted by the command but have no
repository yet in T-04a/T-04b; passing them raises `HifaError(NOT_IMPLEMENTED)` instead
of ignoring them silently. The CLI does not expose them.
"""

from __future__ import annotations

from datetime import datetime

from hifa.adapters.sqlite.uow import Repos, UnitOfWork
from hifa.app.commands import Actor, CreateIssue
from hifa.app.retry import CREATE_RETRY_CODES, with_write_retry
from hifa.app.rules import branch_name
from hifa.domain import identifier as identifier_module
from hifa.domain import ranking, rules
from hifa.domain.entities import Issue, Label, Member, Team, WorkflowState
from hifa.domain.enums import WorkflowStateType
from hifa.domain.errors import HifaError, NotFound, Validation


def create_issue(uow: UnitOfWork, cmd: CreateIssue, actor: Actor) -> Issue:
    """Create an issue, its labels and its `created` history row; return the saved entity.

    raises: `NotFound(team|parent|label|member|state)`, `Validation`, `Conflict(UNIQUE)`
    """
    return with_write_retry(
        lambda: _create_once(uow, cmd, actor),
        retry_on=CREATE_RETRY_CODES,
    )


def _create_once(uow: UnitOfWork, cmd: CreateIssue, actor: Actor) -> Issue:
    with uow.write() as tx:
        workspace = tx.teams.workspace()
        if workspace is None:
            raise HifaError("workspace is not initialized", code="NO_WORKSPACE")

        team = tx.teams.get_by_key(workspace.id, cmd.team) or tx.teams.get(cmd.team)
        if team is None:
            raise NotFound(resource="team", key=cmd.team)
        if team.archived_at is not None:
            raise Validation(
                f"team {team.key} is archived",
                rule="TEAM_ARCHIVED",
                field="team",
                value=team.key,
                code="TEAM_ARCHIVED",
            )

        title = rules.validate_title(cmd.title)
        priority = rules.validate_priority(cmd.priority)
        due = rules.validate_due_date(cmd.due_date)
        state = _resolve_state(tx, team, cmd.state)
        rules.validate_state_team(team.id, state)
        estimate = rules.validate_estimate(cmd.estimate, team.issue_estimation_type)

        new_id = uow.idgen.new()
        parent = _resolve_parent(tx, cmd.parent, team, new_id)
        labels = _resolve_labels(tx, workspace.id, team, cmd.labels)
        assignee_id = _resolve_assignee(tx, cmd.assignee_id)
        _reject_unlinked_relations(cmd)

        number = tx.issues.next_number(team.id)
        now = uow.clock.now()
        identifier = identifier_module.format(team.key, number)
        backlog = [position for _, position in tx.issues.positions(team.id, scope="backlog")]
        board = [
            position
            for _, position in tx.issues.positions(team.id, scope="board", state_id=state.id)
        ]
        sub_positions = (
            [
                position
                for _, position in tx.issues.positions(team.id, scope="sub", parent_id=parent.id)
            ]
            if parent is not None
            else []
        )
        started_at, completed_at, canceled_at = _milestones(state.type, now)

        issue = Issue(
            id=new_id,
            workspace_id=team.workspace_id,
            team_id=team.id,
            number=number,
            identifier=identifier,
            title=title,
            description=cmd.description,
            priority=priority,
            priority_sort_order=0.0,
            estimate=estimate,
            due_date=due,
            state_id=state.id,
            assignee_id=assignee_id,
            creator_id=cmd.creator_id or actor.member_id,
            parent_id=parent.id if parent is not None else None,
            sort_order=cmd.sort_order if cmd.sort_order is not None else ranking.last(backlog),
            board_order=cmd.board_order if cmd.board_order is not None else ranking.last(board),
            sub_issue_sort_order=(ranking.last(sub_positions) if parent is not None else 0.0),
            started_at=started_at,
            completed_at=completed_at,
            canceled_at=canceled_at,
            branch_name=branch_name(identifier, title),
            created_at=now,
            updated_at=now,
        )
        saved = tx.issues.insert(issue)
        tx.issues.set_labels(saved.id, [label.id for label in labels])
        tx.history.record(
            saved.id,
            [("created", None, saved.identifier)],
            actor_id=actor.member_id,
            now=now,
        )
        return saved


# --- reference resolution ----------------------------------------------------------------


def _resolve_state(tx: Repos, team: Team, ref: str | None) -> WorkflowState:
    """Requested name/ULID; otherwise triage (when enabled), `is_default`, first `unstarted`."""
    if ref is not None:
        state = tx.states.get_by_name(team.id, ref) or tx.states.get(ref)
        if state is None:
            raise NotFound(resource="state", key=ref)
        return state

    states = tx.states.list_for_team(team.id)
    if not states:
        raise Validation(
            "team has no workflow states",
            rule="TEAM_WITHOUT_STATES",
            field="state",
            value=team.key,
            code="TEAM_WITHOUT_STATES",
        )
    if team.triage_enabled:
        triage = next((state for state in states if state.type is WorkflowStateType.TRIAGE), None)
        if triage is not None:
            return triage
    default = next((state for state in states if state.is_default), None)
    if default is not None:
        return default
    unstarted = next((state for state in states if state.type is WorkflowStateType.UNSTARTED), None)
    if unstarted is not None:
        return unstarted
    raise Validation(
        "team has no usable default state",
        rule="TEAM_WITHOUT_STATES",
        field="state",
        value=team.key,
        code="TEAM_WITHOUT_STATES",
    )


def _resolve_parent(tx: Repos, ref: str | None, team: Team, child_id: str) -> Issue | None:
    if ref is None:
        return None
    parent = tx.issues.get_by_identifier(ref) or tx.issues.get(ref)
    if parent is None:
        raise NotFound(resource="parent", key=ref)
    rules.validate_parent(
        child_id=child_id,
        child_team_id=team.id,
        parent=parent,
        ancestors=tx.issues.ancestor_chain(parent.id),
    )
    return parent


def _resolve_labels(tx: Repos, workspace_id: str, team: Team, refs: tuple[str, ...]) -> list[Label]:
    """Deduplicates by id preserving order and validates each label's scope."""
    resolved: list[Label] = []
    seen: set[str] = set()
    for ref in refs:
        label = (
            tx.labels.get(ref)
            or tx.labels.get_by_name(workspace_id, team.id, ref)
            or tx.labels.get_by_name(workspace_id, None, ref)
        )
        if label is None:
            raise Validation(
                f"label not found: {ref}",
                rule="LABEL_NOT_FOUND",
                field="labels",
                value=ref,
                code="LABEL_NOT_FOUND",
            )
        if label.id in seen:
            continue
        rules.validate_label_scope(team.id, label)
        seen.add(label.id)
        resolved.append(label)
    return resolved


def _resolve_assignee(tx: Repos, ref: str | None) -> str | None:
    if ref is None:
        return None
    member: Member | None = tx.members.get(ref)
    if member is None:
        raise NotFound(resource="member", key=ref)
    if not member.is_active:
        raise Validation(
            "assignee is inactive",
            rule="MEMBER_INACTIVE",
            field="assignee_id",
            value=member.id,
            code="MEMBER_INACTIVE",
        )
    return member.id


def _reject_unlinked_relations(cmd: CreateIssue) -> None:
    for field_name, value in (
        ("project_id", cmd.project_id),
        ("milestone_id", cmd.milestone_id),
        ("cycle_id", cmd.cycle_id),
    ):
        if value is not None:
            raise HifaError(
                f"{field_name} is not supported yet",
                code="NOT_IMPLEMENTED",
                field=field_name,
                hint="projects and cycles land in a later task",
            )


def _milestones(
    state_type: WorkflowStateType, now: datetime
) -> tuple[datetime | None, datetime | None, datetime | None]:
    """Applies the milestone table with `current=None`."""
    return (
        now if state_type is WorkflowStateType.STARTED else None,
        now if state_type is WorkflowStateType.COMPLETED else None,
        now if state_type is WorkflowStateType.CANCELED else None,
    )


__all__ = ["create_issue"]
