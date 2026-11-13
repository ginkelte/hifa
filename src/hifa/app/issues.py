"""Issue use cases.

Every write runs inside **one** `uow.write()` and reads the clock once. Every use case is
invoked through `with_write_retry` (retrying `DB_BUSY`); `create_issue` additionally retries
`DUPLICATE_NUMBER`. The retry loop lives outside the context manager, so every attempt is a
fresh transaction.

`identifier` atomicity: `number` comes from `IssueRepo.next_number` (MAX+1) under the
UoW's `BEGIN IMMEDIATE`, and `UNIQUE(team_id, number)` closes the race; a collision
arrives translated as `Conflict(rule='DUPLICATE_NUMBER')` and the retry gets the next
transaction. No new locks: SQLite's write lock is the only one.

The edit path diffs the stored row against a candidate copy, so a column is written only
when its value really changes and every changed column gets exactly one history row. The
archive axis (`archived_at`, `trashed`) is a set of layers: each one is applied once, a
layer that is already set is a no-op, and restoring clears them in a fixed order.

`project_id` / `milestone_id` / `cycle_id` are accepted by the commands but have no
repository yet; passing them raises `HifaError(NOT_IMPLEMENTED)` instead of ignoring them
silently. Clearing them (`None`) needs no read and is accepted.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal

from hifa.adapters.sqlite.uow import Repos, UnitOfWork
from hifa.app.commands import (
    Actor,
    ArchiveIssue,
    CreateIssue,
    MoveIssue,
    RelateIssues,
    RestoreIssue,
    TrashIssue,
    UnrelateIssues,
    Unset,
    UpdateIssue,
)
from hifa.app.retry import CREATE_RETRY_CODES, with_write_retry
from hifa.app.rules import apply_milestones, branch_name
from hifa.domain import identifier as identifier_module
from hifa.domain import ranking, rules
from hifa.domain.entities import Issue, IssueRelation, Label, Member, Team, WorkflowState
from hifa.domain.enums import RelationType, WorkflowStateType
from hifa.domain.errors import HifaError, InvalidTransition, NotFound, Validation

#: Columns an edit can change, in the order their history rows are written.
UPDATE_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "priority",
    "state_id",
    "assignee_id",
    "project_id",
    "milestone_id",
    "cycle_id",
    "parent_id",
    "estimate",
    "due_date",
    "branch_name",
    "board_order",
    "started_at",
    "completed_at",
    "canceled_at",
)

#: Milestone columns, in history order.
MILESTONE_FIELDS: tuple[str, ...] = ("started_at", "completed_at", "canceled_at")


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
            started_at=None,
            completed_at=None,
            canceled_at=None,
            branch_name=branch_name(identifier, title),
            created_at=now,
            updated_at=now,
        )
        issue = _copy(
            issue,
            **apply_milestones(current=None, target=state.type, issue=issue, now=now),
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


def _validate_parent(
    tx: Repos,
    parent: Issue,
    *,
    child_id: str,
    child_team_id: str,
    subtree_height: int = 0,
) -> None:
    """Applies the hierarchy rules with the parent's own chain, read from the tree.

    The `ancestors` map handed to the rule is always the parent's upward chain, the parent
    included: the level the parent already occupies, which is what both the depth bound and
    the cycle walk read. Creation passes no height because the issue has no descendants yet;
    a reparent passes the height of the subtree it carries.
    """
    rules.validate_parent(
        child_id=child_id,
        child_team_id=child_team_id,
        parent=parent,
        ancestors=tx.issues.ancestor_chain(parent.id),
        subtree_height=subtree_height,
    )


def _resolve_parent(tx: Repos, ref: str | None, team: Team, child_id: str) -> Issue | None:
    if ref is None:
        return None
    parent = tx.issues.get_by_identifier(ref) or tx.issues.get(ref)
    if parent is None:
        raise NotFound(resource="parent", key=ref)
    _validate_parent(tx, parent, child_id=child_id, child_team_id=team.id)
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


def update_issue(uow: UnitOfWork, cmd: UpdateIssue, actor: Actor) -> Issue:
    """Edit one issue; one history row per column whose value really changes.

    Every reference is resolved inside the transaction, in the fixed order of the command's
    fields, and `UNSET` leaves a column untouched while `None` writes NULL. Setting `state`
    appends the issue to the target column, exactly like a move to its last position. A
    command that changes nothing returns the stored row without writing.

    raises: `NotFound(issue|state|member|parent)`, `Validation`, `InvalidTransition(ISSUE_ARCHIVED)`
    """
    return with_write_retry(lambda: _update_once(uow, cmd, actor))


def _update_once(uow: UnitOfWork, cmd: UpdateIssue, actor: Actor) -> Issue:
    with uow.write() as tx:
        issue = tx.issues.require(cmd.issue)
        _reject_trashed(issue)
        _reject_archived(issue)

        team = tx.teams.get(issue.team_id)
        if team is None:
            raise HifaError("issue references a missing team")
        _reject_archived_team(team)

        updates: dict[str, Any] = {}
        target: WorkflowState | None = None
        title, description, priority = cmd.title, cmd.description, cmd.priority
        state, assignee, parent = cmd.state, cmd.assignee_id, cmd.parent
        estimate, due = cmd.estimate, cmd.due_date
        if not isinstance(title, Unset):
            updates["title"] = rules.validate_title(title)
        if not isinstance(description, Unset):
            updates["description"] = description
        if not isinstance(priority, Unset):
            updates["priority"] = rules.validate_priority(priority)
        if not isinstance(state, Unset):
            target = _target_state(tx, issue, state)
            current = _current_state(tx, issue)
            open_children = _open_children(tx, issue, target)
            rules.validate_transition(current, target, open_children)
            updates["state_id"] = target.id
            if target.id != issue.state_id:
                updates["board_order"] = _append_position(tx, issue, target)
        if not isinstance(assignee, Unset):
            updates["assignee_id"] = _resolve_assignee(tx, assignee)
        _resolve_unsupported_links(cmd, updates)
        if not isinstance(parent, Unset):
            updates["parent_id"] = _resolve_parent_id(tx, parent, issue)
        if not isinstance(estimate, Unset):
            updates["estimate"] = rules.validate_estimate(estimate, team.issue_estimation_type)
        if not isinstance(due, Unset):
            updates["due_date"] = rules.validate_due_date(due)

        now = uow.clock.now()
        candidate = _copy(issue, **updates)
        if target is not None and target.id != issue.state_id:
            current = _current_state(tx, issue)
            candidate = _copy(
                candidate,
                **apply_milestones(current=current.type, target=target.type, issue=issue, now=now),
            )
        if "title" in updates:
            derived = branch_name(issue.identifier, issue.title)
            if issue.branch_name is None or issue.branch_name == derived:
                candidate = _copy(
                    candidate, branch_name=branch_name(issue.identifier, candidate.title)
                )

        changes = _text_rows(
            [
                (column, getattr(issue, column), getattr(candidate, column))
                for column in UPDATE_FIELDS
                if getattr(issue, column) != getattr(candidate, column)
            ]
        )
        if not changes:
            return issue
        saved = tx.issues.update(_copy(candidate, updated_at=now))
        tx.history.record(issue.id, changes, actor_id=actor.member_id, now=now)
        return saved


def move_issue(uow: UnitOfWork, cmd: MoveIssue, actor: Actor) -> Issue:
    """Move one issue inside its column or into another column of the same team.

    The target state decides the transition and its milestones; `target_state=None` only
    reorders inside the current column. The position comes from the neighbors of the target
    column, and a column whose float gap is gone is rebalanced once in the same transaction.

    raises: `NotFound(issue|workflow_state|neighbor)`,
    `Validation(STATE_TEAM_MISMATCH|STATE_ARCHIVED|CLOSE_WITH_OPEN_CHILDREN|POSITION_NEIGHBOR`
    `|POSITION_OUT_OF_ORDER|RANKING_EXHAUSTED|ISSUE_TRASHED)`, `InvalidTransition(ISSUE_ARCHIVED)`
    """
    return with_write_retry(lambda: _move_once(uow, cmd, actor))


def _move_once(uow: UnitOfWork, cmd: MoveIssue, actor: Actor) -> Issue:
    with uow.write() as tx:
        issue = tx.issues.require(cmd.issue)
        _reject_trashed(issue)
        _reject_archived(issue)

        current = _current_state(tx, issue)
        target = (
            current
            if cmd.target_state is None
            else _target_state(tx, issue, cmd.target_state, resource="workflow_state")
        )
        rules.validate_transition(current, target, _open_children(tx, issue, target))

        now = uow.clock.now()
        anchor = None if cmd.sort_order is not None else _anchor(tx, cmd, issue, target)
        new_pos = _place(tx, cmd, issue, target, anchor, now=now)

        moved = issue
        milestones = apply_milestones(
            current=current.type, target=target.type, issue=issue, now=now
        )
        if target.id != issue.state_id:
            moved = tx.issues.set_state(issue.id, target.id, now=now)
            if milestones:
                moved = tx.issues.update(_copy(moved, **milestones))
        if new_pos is not None:
            tx.issues.set_positions(issue.id, board_order=new_pos, now=now)
            moved = _copy(moved, board_order=new_pos)

        changes: list[tuple[str, Any, Any]] = []
        if target.id != issue.state_id:
            changes.append(("state_id", issue.state_id, target.id))
        if new_pos is not None and new_pos != issue.board_order:
            changes.append(("board_order", issue.board_order, new_pos))
        for column in MILESTONE_FIELDS:
            if column in milestones and getattr(issue, column) != milestones[column]:
                changes.append((column, getattr(issue, column), milestones[column]))
        if changes:
            tx.history.record(issue.id, _text_rows(changes), actor_id=actor.member_id, now=now)
        return moved


def archive_issue(uow: UnitOfWork, cmd: ArchiveIssue, actor: Actor) -> Issue:
    """Stamp the archive layer of one issue, without touching `updated_at`.

    An issue that is already archived is a no-op: the stored row is returned and nothing is
    written. A parent with open sub-issues cannot leave every listing while its children stay.

    raises: `NotFound(issue)`, `Validation(CLOSE_WITH_OPEN_CHILDREN|ISSUE_TRASHED)`
    """
    return with_write_retry(lambda: _archive_once(uow, cmd, actor))


def _archive_once(uow: UnitOfWork, cmd: ArchiveIssue, actor: Actor) -> Issue:
    with uow.write() as tx:
        issue = tx.issues.require(cmd.issue)
        _reject_trashed(issue)
        if issue.archived_at is not None:
            return issue
        rules.validate_close(WorkflowStateType.CANCELED, tx.issues.count_open_children(issue.id))
        now = uow.clock.now()
        saved = tx.issues.update(_copy(issue, archived_at=now))
        tx.history.record(
            issue.id, _text_rows([("archived_at", None, now)]), actor_id=actor.member_id, now=now
        )
        return saved


def trash_issue(uow: UnitOfWork, cmd: TrashIssue, actor: Actor) -> Issue:
    """Set the trash layer of one issue, moving `updated_at` with the flag.

    An issue that is already trashed is a no-op, and an archived issue can be trashed.

    raises: `NotFound(issue)`
    """
    return with_write_retry(lambda: _trash_once(uow, cmd, actor))


def _trash_once(uow: UnitOfWork, cmd: TrashIssue, actor: Actor) -> Issue:
    with uow.write() as tx:
        issue = tx.issues.require(cmd.issue)
        if issue.trashed:
            return issue
        now = uow.clock.now()
        saved = tx.issues.update(_copy(issue, trashed=True, updated_at=now))
        tx.history.record(
            issue.id,
            _text_rows([("trashed", issue.trashed, True)]),
            actor_id=actor.member_id,
            now=now,
        )
        return saved


def restore_issue(uow: UnitOfWork, cmd: RestoreIssue, actor: Actor) -> Issue:
    """Clear the archive and trash layers of one issue; one history row per layer cleared.

    `archived_at` is cleared first and `trashed` second. An issue with neither layer set is a
    no-op. This is the only write that accepts an archived issue.

    raises: `NotFound(issue)`
    """
    return with_write_retry(lambda: _restore_once(uow, cmd, actor))


def _restore_once(uow: UnitOfWork, cmd: RestoreIssue, actor: Actor) -> Issue:
    with uow.write() as tx:
        issue = tx.issues.require(cmd.issue)
        changes: list[tuple[str, Any, Any]] = []
        patch: dict[str, Any] = {}
        if issue.archived_at is not None:
            changes.append(("archived_at", issue.archived_at, None))
            patch["archived_at"] = None
        if issue.trashed:
            changes.append(("trashed", issue.trashed, False))
            patch["trashed"] = False
        if not changes:
            return issue
        now = uow.clock.now()
        if issue.trashed:
            patch["updated_at"] = now
        saved = tx.issues.update(_copy(issue, **patch))
        tx.history.record(issue.id, _text_rows(changes), actor_id=actor.member_id, now=now)
        return saved


# --- relations ----------------------------------------------------------------------------


def relate_issues(uow: UnitOfWork, cmd: RelateIssues, actor: Actor) -> IssueRelation:
    """Join two issues with one edge of the requested type and leave its history row.

    The type is parsed before the first read, so an unknown token is a field error even when
    neither reference exists. `blocked_by` is the input alias of the inverse direction: it
    parses to `blocks` plus a swap, and the swapped pair is what the caller meant. The stored
    order of the pair is not decided here — the type itself normalises it in the repository,
    which is also what makes the reversed spelling of a symmetric edge the same row.

    raises: `NotFound(issue)`, `Validation(COMMAND_FIELD|RELATION_SELF|`
    `RELATION_WORKSPACE_MISMATCH)`,
    `Conflict(RELATION_DUPLICATE)`
    """
    return with_write_retry(lambda: _relate_once(uow, cmd, actor))


def _relate_once(uow: UnitOfWork, cmd: RelateIssues, actor: Actor) -> IssueRelation:
    relation_type, swap = RelationType.parse(cmd.type)
    with uow.write() as tx:
        issue = tx.issues.require(cmd.issue)
        related = tx.issues.require(cmd.related)
        source_id, target_id = _edge_triple(issue, related, relation_type, swap)[:2]
        rules.validate_relation_scope(issue, related)
        rules.validate_relation(
            source_id,
            target_id,
            relation_type,
            [
                (row.issue_id, row.related_issue_id, row.type)
                for row in tx.issues.relations(issue.id)
            ],
        )

        now = uow.clock.now()
        stored = tx.issues.add_relation(
            IssueRelation(
                id=uow.idgen.new(),
                issue_id=source_id,
                related_issue_id=target_id,
                type=relation_type,
                created_at=now,
            )
        )
        tx.history.record(
            issue.id,
            [("relation", None, _relation_value(stored, issue, related))],
            actor_id=actor.member_id,
            now=now,
        )
        return stored


def unrelate_issues(uow: UnitOfWork, cmd: UnrelateIssues, actor: Actor) -> None:
    """Delete the edge between two issues and leave its history row.

    `type=None` deletes whichever edge joins the pair; a type matches the normalised triple,
    so `blocked_by` addresses the row `blocks` created. A pair without an edge is
    `NotFound(resource='relation')`: a delete cannot be undone silently, and the caller that
    asked to remove something has to learn that there was nothing to remove.

    raises: `NotFound(issue|relation)`, `Validation(COMMAND_FIELD)`
    """
    with_write_retry(lambda: _unrelate_once(uow, cmd, actor))


def _unrelate_once(uow: UnitOfWork, cmd: UnrelateIssues, actor: Actor) -> None:
    relation_type: RelationType | None = None
    swap = False
    if cmd.type is not None:
        relation_type, swap = RelationType.parse(cmd.type)
    with uow.write() as tx:
        issue = tx.issues.require(cmd.issue)
        related = tx.issues.require(cmd.related)
        wanted = (
            None if relation_type is None else _edge_triple(issue, related, relation_type, swap)
        )
        existing = tx.issues.relations(issue.id)
        edge = next((row for row in existing if _matches_edge(row, issue, related, wanted)), None)
        if edge is None:
            raise NotFound(resource="relation", key=cmd.related)

        now = uow.clock.now()
        tx.issues.remove_relation(edge.id)
        tx.history.record(
            issue.id,
            [("relation", _relation_value(edge, issue, related), None)],
            actor_id=actor.member_id,
            now=now,
        )


def _edge_triple(
    issue: Issue, related: Issue, type: RelationType, swap: bool
) -> tuple[str, str, RelationType]:
    """The `(issue_id, related_issue_id, type)` this request stores.

    `swap` is the `blocked_by` spelling: the far end is the source, because `issue` being
    blocked by `related` is the row `related blocks issue`. The order of the pair inside a
    type is not decided here either: the type answers it, and that one answer is what the
    reverse spelling of a symmetric edge collapses onto.
    """
    source, target = (related.id, issue.id) if swap else (issue.id, related.id)
    return (*type.canonical_pair(source, target), type)


def _matches_edge(
    row: IssueRelation,
    issue: Issue,
    related: Issue,
    wanted: tuple[str, str, RelationType] | None,
) -> bool:
    """True when `row` joins this pair, with `wanted`'s type and order when one was given."""
    if {row.issue_id, row.related_issue_id} != {issue.id, related.id}:
        return False
    if wanted is None:
        return True
    return (row.issue_id, row.related_issue_id, row.type) == wanted


def _relation_value(row: IssueRelation, issue: Issue, other: Issue) -> str:
    """Value of the `relation` history row: the type as `issue` reads it, plus the far end."""
    direction: Literal["out", "in"] = "out" if row.issue_id == issue.id else "in"
    return f"{row.type.name_as_seen(direction)} {other.identifier}"


# --- history values ----------------------------------------------------------------------


def _copy(issue: Issue, **changes: Any) -> Issue:
    """Copy of an issue with the given columns replaced; entities are frozen models."""
    return issue.model_copy(update=changes)


def _text(value: Any) -> str | None:
    """Column value as the text the history row stores (never a `datetime` nor a `float`)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime):
        moment = value.astimezone(UTC) if value.tzinfo is not None else value
        return moment.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        return str(value)
    if isinstance(value, int):
        return str(int(value))
    return str(value)


def _text_rows(changes: Sequence[tuple[str, Any, Any]]) -> list[tuple[str, str | None, str | None]]:
    """Convert a change list to `(field, old_text, new_text)` tuples, preserving order."""
    return [(field, _text(old), _text(new)) for field, old, new in changes]


# --- guards ------------------------------------------------------------------------------


def _reject_trashed(issue: Issue) -> None:
    if not issue.trashed:
        return
    raise Validation(
        "issue is in the trash",
        rule="ISSUE_TRASHED",
        field="trashed",
        value=issue.id,
        code="ISSUE_TRASHED",
    )


def _reject_archived(issue: Issue) -> None:
    if issue.archived_at is None:
        return
    raise InvalidTransition(
        "issue is archived",
        rule="ISSUE_ARCHIVED",
        field="ref",
        current=issue.state_id,
        target=issue.state_id,
        value=issue.identifier,
    )


def _reject_archived_team(team: Team) -> None:
    if team.archived_at is None:
        return
    raise Validation(
        f"team {team.key} is archived",
        rule="TEAM_ARCHIVED",
        field="team",
        value=team.key,
        code="TEAM_ARCHIVED",
    )


# --- reference resolution ----------------------------------------------------------------


def _current_state(tx: Repos, issue: Issue) -> WorkflowState:
    """The state the issue is in; a dangling reference is a broken invariant."""
    state = tx.teams.get_state(issue.state_id)
    if state is None:
        raise HifaError("issue references a missing workflow state")
    return state


def _target_state(tx: Repos, issue: Issue, ref: str, *, resource: str = "state") -> WorkflowState:
    """Requested state by name (within the issue's team) or by ULID."""
    state = tx.teams.get_state_by_name(issue.team_id, ref) or tx.teams.get_state(ref)
    if state is None:
        raise NotFound(resource=resource, key=ref)
    return state


def _open_children(tx: Repos, issue: Issue, target: WorkflowState) -> int:
    """Open sub-issue count; only paid when the target type is terminal."""
    return tx.issues.count_open_children(issue.id) if target.type.is_terminal else 0


def _resolve_parent_id(tx: Repos, ref: str | None, issue: Issue) -> str | None:
    """New parent of an edit; `None` unparents and the hierarchy rules still apply in full.

    A reparent carries its descendants, so the rules are checked against the height of the
    subtree that moves and not only against the moved issue.
    """
    if ref is None:
        return None
    parent = tx.issues.get_by_identifier(ref) or tx.issues.get(ref)
    if parent is None:
        raise NotFound(resource="parent", key=ref)
    _validate_parent(
        tx,
        parent,
        child_id=issue.id,
        child_team_id=issue.team_id,
        subtree_height=tx.issues.subtree_height(issue.id),
    )
    return parent.id


def _resolve_unsupported_links(cmd: UpdateIssue, updates: dict[str, Any]) -> None:
    """`None` clears a link that has no repository yet; a value raises instead of being lost."""
    for field_name in ("project_id", "milestone_id", "cycle_id"):
        value = getattr(cmd, field_name)
        if isinstance(value, Unset):
            continue
        if value is None:
            updates[field_name] = None
            continue
        raise HifaError(
            f"{field_name} is not supported yet",
            code="NOT_IMPLEMENTED",
            field=field_name,
            hint="projects and cycles land in a later task",
        )


# --- positions ---------------------------------------------------------------------------


def _column(tx: Repos, issue: Issue, target: WorkflowState) -> list[tuple[str, float]]:
    """Positions of the target column, ascending, without the moving issue."""
    return [
        (issue_id, position)
        for issue_id, position in tx.issues.positions(
            team_id=issue.team_id, scope="board", state_id=target.id
        )
        if issue_id != issue.id
    ]


def _rebalance_column(
    tx: Repos, issue: Issue, target: WorkflowState, *, now: datetime
) -> list[tuple[str, float]]:
    """Rewrite the target column with multiples of the initial gap, in the same transaction."""
    rows = _column(tx, issue, target)
    spaced = ranking.rebalance([position for _, position in rows])
    for (issue_id, _), position in zip(rows, spaced, strict=True):
        tx.issues.set_positions(issue_id, board_order=position, now=now)
    return [(issue_id, position) for (issue_id, _), position in zip(rows, spaced, strict=True)]


def _anchor(tx: Repos, cmd: MoveIssue, issue: Issue, target: WorkflowState) -> Issue | None:
    """Neighbor the `before`/`after` position refers to; it must live in the target column."""
    ref = cmd.before_id if cmd.position == "before" else cmd.after_id
    if ref is None:
        return None
    neighbor = tx.issues.get(ref) or tx.issues.get_by_identifier(ref)
    if neighbor is None:
        raise NotFound(resource="neighbor", key=ref)
    if neighbor.id == issue.id or neighbor.team_id != issue.team_id or neighbor.trashed:
        raise _position_neighbor(ref)
    if neighbor.state_id != target.id:
        raise _position_neighbor(ref)
    return neighbor


def _position_neighbor(ref: str) -> Validation:
    return Validation(
        "the anchor issue is not part of the target column",
        rule="POSITION_NEIGHBOR",
        field="before_id",
        value=ref,
        code="POSITION_NEIGHBOR",
    )


def _place(
    tx: Repos,
    cmd: MoveIssue,
    issue: Issue,
    target: WorkflowState,
    anchor: Issue | None,
    *,
    now: datetime,
) -> float | None:
    """Position the move asks for, rebalancing the column once when the float gap is gone."""
    column = _column(tx, issue, target)
    degraded = ranking.needs_rebalance([position for _, position in column])
    if cmd.rebalance or (cmd.sort_order is None and degraded):
        column = _rebalance_column(tx, issue, target, now=now)
    try:
        position, low, high = _position(cmd, issue, target, column, anchor)
    except Validation as exc:
        if exc.rule != "RANKING_EXHAUSTED":
            raise
        column = _rebalance_column(tx, issue, target, now=now)
        position, low, high = _position_or_degraded(cmd, issue, target, column, anchor, exc)
    if position is not None and _gap_exhausted(position, low, high):
        column = _rebalance_column(tx, issue, target, now=now)
        position, low, high = _position_or_degraded(cmd, issue, target, column, anchor, None)
        if position is not None and _gap_exhausted(position, low, high):
            raise _ranking_degraded()
    return position


def _position_or_degraded(
    cmd: MoveIssue,
    issue: Issue,
    target: WorkflowState,
    column: list[tuple[str, float]],
    anchor: Issue | None,
    cause: Exception | None,
) -> tuple[float | None, float | None, float | None]:
    """Second attempt at the position: a repeated exhaustion is a broken invariant."""
    try:
        return _position(cmd, issue, target, column, anchor)
    except Validation as exc:
        if exc.rule != "RANKING_EXHAUSTED":
            raise
        raise _ranking_degraded() from cause or exc


def _ranking_degraded() -> HifaError:
    return HifaError("ranking is degraded after a rebalance", code="RANKING_DEGRADED")


def _position(
    cmd: MoveIssue,
    issue: Issue,
    target: WorkflowState,
    column: list[tuple[str, float]],
    anchor: Issue | None,
) -> tuple[float | None, float | None, float | None]:
    """`(position, lower_neighbor, upper_neighbor)`; `None` position means "already there"."""
    positions = [position for _, position in column]
    if cmd.sort_order is not None:
        value = cmd.sort_order
        if value != value or value <= 0 or any(position == value for position in positions):
            raise Validation(
                "the requested position is not strictly between its neighbors",
                rule="POSITION_OUT_OF_ORDER",
                field="board_order",
                value=value,
                code="POSITION_OUT_OF_ORDER",
            )
        low = max((position for position in positions if position < value), default=None)
        high = min((position for position in positions if position > value), default=None)
        return value, low, high

    if cmd.position == "first":
        if target.id == issue.state_id and not any(p < issue.board_order for p in positions):
            return None, None, None
        low, high = None, (min(positions) if positions else None)
    elif cmd.position == "last":
        if target.id == issue.state_id and not any(p > issue.board_order for p in positions):
            return None, None, None
        low, high = (max(positions) if positions else None), None
    elif anchor is not None:
        anchor_position = next(
            (position for issue_id, position in column if issue_id == anchor.id),
            anchor.board_order,
        )
        if cmd.position == "before":
            low = max((p for p in positions if p < anchor_position), default=None)
            high = anchor_position
        else:
            low = anchor_position
            high = min((p for p in positions if p > anchor_position), default=None)
    else:  # pragma: no cover - the command guarantees an anchor for before/after
        raise HifaError("a move with an anchor position needs a neighbor")
    return ranking.between(low, high), low, high


def _gap_exhausted(position: float, low: float | None, high: float | None) -> bool:
    """True when the new position cannot be distinguished from either neighbor."""
    return (low is not None and position - low < ranking.MIN_GAP) or (
        high is not None and high - position < ranking.MIN_GAP
    )


def _append_position(tx: Repos, issue: Issue, target: WorkflowState) -> float:
    """Last position of the target column; an empty column starts at the initial gap."""
    positions = [position for _, position in _column(tx, issue, target)]
    return ranking.between(max(positions), None) if positions else ranking.last([])


__all__ = [
    "MILESTONE_FIELDS",
    "UPDATE_FIELDS",
    "archive_issue",
    "create_issue",
    "move_issue",
    "relate_issues",
    "restore_issue",
    "trash_issue",
    "unrelate_issues",
    "update_issue",
]
