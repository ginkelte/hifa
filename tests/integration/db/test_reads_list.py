"""`reads.list_view`: one query with aggregated labels, AND filters and keyset paging."""

from __future__ import annotations

import base64
from datetime import date

import pytest

from helpers_world import make_issue
from hifa.adapters.sqlite.reads import ListFilters, encode_cursor, list_view, sort_token
from hifa.domain.enums import Priority
from hifa.domain.errors import Validation


def _payload(raw: bytes) -> str:
    """Unpadded base64url of an arbitrary payload: a hand-made, non-canonical token."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _walk(conn, build, limit: int) -> list[str]:
    """Ids of every page from the first one to the last, following `next_cursor`."""
    seen: list[str] = []
    cursor: str | None = None
    while True:
        page = list_view(conn, build(limit=limit, cursor=cursor))
        seen.extend(item.id for item in page.items)
        if not page.has_more:
            return seen
        cursor = page.next_cursor


def _seed(world, count: int = 3) -> list:
    issues = []
    with world.uow.write() as tx:
        for _ in range(count):
            issues.append(make_issue(tx, world))
            world.clock.advance(seconds=1)
    return issues


def test_list_view_is_a_single_query(world) -> None:
    _seed(world, 3)
    statements: list[str] = []

    with world.uow.read() as tx:
        tx.issues.connection.set_trace_callback(statements.append)
        try:
            page = list_view(tx.issues.connection, ListFilters(team_id=world.team.id))
        finally:
            tx.issues.connection.set_trace_callback(None)

    assert len(statements) == 1
    assert len(page.items) == 3


def test_labels_are_aggregated_without_n_plus_one(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.issues.set_labels(
            issue.id, [world.labels["bug"].id, world.labels["feature"].id, world.labels["docs"].id]
        )
        for _ in range(4):
            make_issue(tx, world)
    statements: list[str] = []

    with world.uow.read() as tx:
        tx.issues.connection.set_trace_callback(statements.append)
        try:
            page = list_view(tx.issues.connection, ListFilters(team_id=world.team.id))
        finally:
            tx.issues.connection.set_trace_callback(None)

    assert len(statements) == 1
    labeled = next(item for item in page.items if item.id == issue.id)
    assert {label.name for label in labeled.labels} == {"bug", "feature", "docs"}


def test_issue_without_labels_returns_empty_tuple(world) -> None:
    _seed(world, 1)

    with world.uow.read() as tx:
        page = list_view(tx.issues.connection, ListFilters(team_id=world.team.id))

    assert page.items[0].labels == ()


def test_filter_by_state_id(world) -> None:
    with world.uow.write() as tx:
        todo = make_issue(tx, world)
        make_issue(tx, world, state_id=world.states["Done"].id)

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection,
            ListFilters(team_id=world.team.id, state_id=world.states["Todo"].id),
        )

    assert [item.id for item in page.items] == [todo.id]


def test_filter_by_state_type(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)
        done = make_issue(tx, world, state_id=world.states["Done"].id)

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, state_type="completed")
        )

    assert [item.id for item in page.items] == [done.id]


def test_filter_assignee_and_unassigned(world) -> None:
    with world.uow.write() as tx:
        mine = make_issue(tx, world, assignee_id=world.members["ana"].id)
        free = make_issue(tx, world)

    with world.uow.read() as tx:
        assigned = list_view(
            tx.issues.connection,
            ListFilters(team_id=world.team.id, assignee_id=world.members["ana"].id),
        )
        unassigned = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, unassigned=True)
        )

    assert [item.id for item in assigned.items] == [mine.id]
    assert [item.id for item in unassigned.items] == [free.id]
    assert assigned.items[0].assignee_name == "Ana Pérez"


def test_filter_priority_and_due_before(world) -> None:
    with world.uow.write() as tx:
        urgent = make_issue(tx, world, priority=Priority.URGENT, due_date=date(2026, 11, 1))
        make_issue(tx, world, due_date=date(2026, 12, 1))

    with world.uow.read() as tx:
        by_priority = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, priority=1)
        )
        by_due = list_view(
            tx.issues.connection,
            ListFilters(team_id=world.team.id, due_before=date(2026, 11, 15)),
        )

    assert [item.id for item in by_priority.items] == [urgent.id]
    assert [item.id for item in by_due.items] == [urgent.id]
    assert by_due.items[0].priority_label == "Urgent"


def test_filter_label_ids_is_and(world) -> None:
    with world.uow.write() as tx:
        both = make_issue(tx, world)
        only_bug = make_issue(tx, world)
        tx.issues.set_labels(both.id, [world.labels["bug"].id, world.labels["feature"].id])
        tx.issues.set_labels(only_bug.id, [world.labels["bug"].id])

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection,
            ListFilters(
                team_id=world.team.id,
                label_ids=(world.labels["bug"].id, world.labels["feature"].id),
            ),
        )

    assert [item.id for item in page.items] == [both.id]


def test_filter_parent_id(world) -> None:
    with world.uow.write() as tx:
        parent = make_issue(tx, world)
        child = make_issue(tx, world, parent_id=parent.id)

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, parent_id=parent.id)
        )

    assert [item.id for item in page.items] == [child.id]


def test_archived_and_trashed_are_excluded_by_default(world) -> None:
    with world.uow.write() as tx:
        live = make_issue(tx, world)
        archived = make_issue(tx, world)
        trashed = make_issue(tx, world)
        tx.issues.archive(archived.id, world.clock.now())
        tx.issues.trash(trashed.id, world.clock.now())

    with world.uow.read() as tx:
        default = list_view(tx.issues.connection, ListFilters(team_id=world.team.id))
        with_archived = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, include_archived=True)
        )
        with_trashed = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, include_trashed=True)
        )

    assert [item.id for item in default.items] == [live.id]
    assert {item.id for item in with_archived.items} == {live.id, archived.id}
    assert {item.id for item in with_trashed.items} == {live.id, trashed.id}


def test_keyset_does_not_share_ids_across_pages(world) -> None:
    issues = _seed(world, 5)

    with world.uow.read() as tx:
        first = list_view(tx.issues.connection, ListFilters(team_id=world.team.id, limit=2))
        second = list_view(
            tx.issues.connection,
            ListFilters(team_id=world.team.id, limit=2, cursor=first.next_cursor),
        )

    assert first.has_more is True
    assert len(first.items) == 2
    assert not ({item.id for item in first.items} & {item.id for item in second.items})
    assert [item.id for item in first.items + second.items] == [issue.id for issue in issues[:4]]


def test_last_page_has_no_cursor(world) -> None:
    _seed(world, 2)

    with world.uow.read() as tx:
        page = list_view(tx.issues.connection, ListFilters(team_id=world.team.id, limit=50))

    assert page.has_more is False
    assert page.next_cursor is None


def test_cursor_walks_all_pages_without_repeating(world) -> None:
    _seed(world, 7)
    seen: list[str] = []
    cursor = None

    with world.uow.read() as tx:
        while True:
            page = list_view(
                tx.issues.connection,
                ListFilters(team_id=world.team.id, limit=3, cursor=cursor),
            )
            seen.extend(item.id for item in page.items)
            if not page.has_more:
                break
            cursor = page.next_cursor

    assert len(seen) == 7
    assert len(set(seen)) == 7


def test_order_created_uses_the_timestamp(world) -> None:
    issues = _seed(world, 3)

    with world.uow.read() as tx:
        page = list_view(tx.issues.connection, ListFilters(team_id=world.team.id, order="created"))

    assert [item.id for item in page.items] == [issue.id for issue in issues]


def test_order_priority_leaves_no_priority_last(world) -> None:
    with world.uow.write() as tx:
        low = make_issue(tx, world, priority=Priority.LOW)
        urgent = make_issue(tx, world, priority=Priority.URGENT)
        none = make_issue(tx, world)

    with world.uow.read() as tx:
        page = list_view(tx.issues.connection, ListFilters(team_id=world.team.id, order="priority"))

    assert [item.id for item in page.items] == [urgent.id, low.id, none.id]


def test_order_due_leaves_no_due_date_last(world) -> None:
    with world.uow.write() as tx:
        undated = make_issue(tx, world)
        late = make_issue(tx, world, due_date=date(2026, 12, 1))
        soon = make_issue(tx, world, due_date=date(2026, 11, 15))

    with world.uow.read() as tx:
        page = list_view(tx.issues.connection, ListFilters(team_id=world.team.id, order="due"))

    assert [item.id for item in page.items] == [soon.id, late.id, undated.id]


def test_offset_is_used_only_in_the_web(world) -> None:
    issues = _seed(world, 4)

    with world.uow.read() as tx:
        page = list_view(tx.issues.connection, ListFilters(team_id=world.team.id, offset=2))

    assert [item.id for item in page.items] == [issue.id for issue in issues[2:]]


def test_invalid_cursor_is_validation(world) -> None:
    _seed(world, 1)

    with world.uow.read() as tx, pytest.raises(Validation) as exc:
        list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, cursor="no-es-un-cursor")
        )

    assert exc.value.rule == "INVALID_CURSOR"


def test_cursor_whose_payload_is_not_the_canonical_token_is_invalid_cursor(world) -> None:
    _seed(world, 1)

    with world.uow.read() as tx, pytest.raises(Validation) as exc:
        list_view(
            tx.issues.connection,
            ListFilters(team_id=world.team.id, cursor=_payload(b"1.5|01HIFA0000000000000000001")),
        )

    assert exc.value.rule == "INVALID_CURSOR"


def test_cursor_from_another_order_is_invalid_cursor(world) -> None:
    issues = _seed(world, 2)

    with world.uow.read() as tx, pytest.raises(Validation) as exc:
        list_view(
            tx.issues.connection,
            ListFilters(
                team_id=world.team.id,
                order="due",
                cursor=encode_cursor(sort_token("manual", False), issues[0].id),
            ),
        )

    assert exc.value.rule == "INVALID_CURSOR"


def test_cursor_from_the_other_direction_is_invalid_cursor(world) -> None:
    issues = _seed(world, 2)

    with world.uow.read() as tx, pytest.raises(Validation) as exc:
        list_view(
            tx.issues.connection,
            ListFilters(
                team_id=world.team.id,
                reverse=True,
                cursor=encode_cursor(sort_token("manual", False), issues[0].id),
            ),
        )

    assert exc.value.rule == "INVALID_CURSOR"


def test_cursor_whose_anchor_does_not_exist_is_invalid_cursor(world) -> None:
    _seed(world, 2)

    with world.uow.read() as tx, pytest.raises(Validation) as exc:
        list_view(
            tx.issues.connection,
            ListFilters(
                team_id=world.team.id,
                cursor=encode_cursor(sort_token("manual", False), "01HIFA" + "9" * 20),
            ),
        )

    assert exc.value.rule == "INVALID_CURSOR"


def test_cursor_whose_anchor_belongs_to_another_team_is_invalid_cursor(world) -> None:
    from hifa.domain.entities import Issue, Team, WorkflowState
    from hifa.domain.enums import WorkflowStateType

    _seed(world, 1)
    with world.uow.write() as tx:
        other = tx.teams.insert(
            Team(
                id=world.idgen.new(),
                workspace_id=world.workspace.id,
                key="OPS",
                name="Operations",
                created_at=world.clock.now(),
                updated_at=world.clock.now(),
            )
        )
        other_state = tx.states.insert(
            WorkflowState(
                id=world.idgen.new(),
                team_id=other.id,
                name="Todo",
                type=WorkflowStateType.UNSTARTED,
                position=1.0,
                is_default=True,
            )
        )
        foreign = tx.issues.insert(
            Issue(
                id=world.idgen.new(),
                workspace_id=world.workspace.id,
                team_id=other.id,
                number=1,
                identifier="OPS-1",
                title="Foreign",
                state_id=other_state.id,
                created_at=world.clock.now(),
                updated_at=world.clock.now(),
            )
        )

    with world.uow.read() as tx, pytest.raises(Validation) as exc:
        list_view(
            tx.issues.connection,
            ListFilters(
                team_id=world.team.id,
                cursor=encode_cursor(sort_token("manual", False), foreign.id),
            ),
        )

    assert exc.value.rule == "INVALID_CURSOR"


@pytest.mark.parametrize("reverse", [False, True])
def test_absence_stays_last_in_both_directions(world, reverse: bool) -> None:
    with world.uow.write() as tx:
        early = make_issue(tx, world, due_date=date(2026, 11, 5))
        late = make_issue(tx, world, due_date=date(2026, 11, 10))
        undated_first = make_issue(tx, world)
        undated_second = make_issue(tx, world)

    valued = [late.id, early.id] if reverse else [early.id, late.id]
    absent = (
        [undated_second.id, undated_first.id] if reverse else [undated_first.id, undated_second.id]
    )

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection,
            ListFilters(team_id=world.team.id, order="due", reverse=reverse),
        )

    assert [item.id for item in page.items] == valued + absent


@pytest.mark.parametrize("order", ["due", "priority"])
@pytest.mark.parametrize("reverse", [False, True])
def test_two_consecutive_pages_leave_no_hole_and_no_duplicate(
    world, order: str, reverse: bool
) -> None:
    with world.uow.write() as tx:
        if order == "due":
            first = make_issue(tx, world, due_date=date(2026, 11, 5))
            second = make_issue(tx, world, due_date=date(2026, 11, 10))
        else:
            first = make_issue(tx, world, priority=Priority.URGENT)
            second = make_issue(tx, world, priority=Priority.HIGH)
        absent_one = make_issue(tx, world)
        absent_two = make_issue(tx, world)

    valued = [second.id, first.id] if reverse else [first.id, second.id]
    absent = [absent_two.id, absent_one.id] if reverse else [absent_one.id, absent_two.id]
    expected = valued + absent

    def build(*, limit: int, cursor: str | None) -> ListFilters:
        return ListFilters(
            team_id=world.team.id,
            order=order,  # type: ignore[arg-type]
            reverse=reverse,
            limit=limit,
            cursor=cursor,
        )

    with world.uow.read() as tx:
        page_one = list_view(tx.issues.connection, build(limit=2, cursor=None))
        page_two = list_view(tx.issues.connection, build(limit=2, cursor=page_one.next_cursor))
        walked = _walk(tx.issues.connection, build, limit=1)

    first_ids = [item.id for item in page_one.items]
    second_ids = [item.id for item in page_two.items]

    assert page_one.has_more is True
    assert page_one.next_cursor is not None
    assert page_two.has_more is False
    assert page_two.next_cursor is None
    assert not (set(first_ids) & set(second_ids))
    assert first_ids + second_ids == expected
    assert walked == expected


def test_the_absent_section_continues_by_id_inside_itself(world) -> None:
    with world.uow.write() as tx:
        dated = make_issue(tx, world, due_date=date(2026, 11, 5))
        undated_one = make_issue(tx, world)
        undated_two = make_issue(tx, world)

    def build(*, limit: int, cursor: str | None) -> ListFilters:
        return ListFilters(team_id=world.team.id, order="due", limit=limit, cursor=cursor)

    with world.uow.read() as tx:
        walked = _walk(tx.issues.connection, build, limit=1)

    assert walked == [dated.id, undated_one.id, undated_two.id]


def test_state_ids_unite_with_state_id(world) -> None:
    with world.uow.write() as tx:
        todo = make_issue(tx, world)
        done = make_issue(tx, world, state_id=world.states["Done"].id)
        make_issue(tx, world, state_id=world.states["Backlog"].id)

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection,
            ListFilters(
                team_id=world.team.id,
                state_id=world.states["Todo"].id,
                state_ids=(world.states["Done"].id, world.states["Todo"].id),
            ),
        )

    assert {item.id for item in page.items} == {todo.id, done.id}


def test_state_types_unite_with_state_type(world) -> None:
    with world.uow.write() as tx:
        todo = make_issue(tx, world)
        done = make_issue(tx, world, state_id=world.states["Done"].id)
        canceled = make_issue(tx, world, state_id=world.states["Canceled"].id)

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection,
            ListFilters(
                team_id=world.team.id,
                state_type="unstarted",
                state_types=("completed", "canceled"),
            ),
        )

    assert {item.id for item in page.items} == {todo.id, done.id, canceled.id}


def test_priorities_unite_with_priority(world) -> None:
    with world.uow.write() as tx:
        urgent = make_issue(tx, world, priority=Priority.URGENT)
        high = make_issue(tx, world, priority=Priority.HIGH)
        low = make_issue(tx, world, priority=Priority.LOW)

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection,
            ListFilters(team_id=world.team.id, priority=4, priorities=(1, 2)),
        )

    assert {item.id for item in page.items} == {urgent.id, high.id, low.id}


def test_is_open_selects_the_open_or_the_closed_slice(world) -> None:
    with world.uow.write() as tx:
        open_issue = make_issue(tx, world)
        closed = make_issue(tx, world, state_id=world.states["Done"].id)

    with world.uow.read() as tx:
        only_open = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, is_open=True)
        )
        only_closed = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, is_open=False)
        )

    assert [item.id for item in only_open.items] == [open_issue.id]
    assert [item.id for item in only_closed.items] == [closed.id]


def test_no_cycle_and_cycle_id_select_the_two_sides_of_the_cycle_axis(world) -> None:
    issues = _seed(world, 2)

    with world.uow.read() as tx:
        without_cycle = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, no_cycle=True)
        )
        with_cycle = list_view(
            tx.issues.connection,
            ListFilters(team_id=world.team.id, cycle_id="01HIFA" + "8" * 20),
        )

    assert {item.id for item in without_cycle.items} == {issue.id for issue in issues}
    assert with_cycle.items == ()


def test_repeated_values_in_one_family_are_counted_once(world) -> None:
    with world.uow.write() as tx:
        both = make_issue(tx, world)
        only_bug = make_issue(tx, world)
        tx.issues.set_labels(both.id, [world.labels["bug"].id, world.labels["feature"].id])
        tx.issues.set_labels(only_bug.id, [world.labels["bug"].id])

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection,
            ListFilters(
                team_id=world.team.id,
                label_ids=(world.labels["bug"].id, world.labels["bug"].id),
            ),
        )

    assert {item.id for item in page.items} == {both.id, only_bug.id}


def test_archived_only_shows_the_archived_slice(world) -> None:
    with world.uow.write() as tx:
        live = make_issue(tx, world)
        archived = make_issue(tx, world)
        tx.issues.archive(archived.id, world.clock.now())

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, archived_only=True)
        )

    assert [item.id for item in page.items] == [archived.id]
    assert live.id != archived.id


def test_trashed_only_opens_the_archive_axis(world) -> None:
    with world.uow.write() as tx:
        live = make_issue(tx, world)
        trashed = make_issue(tx, world)
        archived_then_trashed = make_issue(tx, world)
        tx.issues.trash(trashed.id, world.clock.now())
        tx.issues.archive(archived_then_trashed.id, world.clock.now())
        tx.issues.trash(archived_then_trashed.id, world.clock.now())

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection, ListFilters(team_id=world.team.id, trashed_only=True)
        )

    assert {item.id for item in page.items} == {trashed.id, archived_then_trashed.id}
    assert live.id not in {item.id for item in page.items}


def test_trashed_only_with_archived_only_is_the_archived_slice_of_the_trash(world) -> None:
    with world.uow.write() as tx:
        only_trashed = make_issue(tx, world)
        archived_then_trashed = make_issue(tx, world)
        tx.issues.trash(only_trashed.id, world.clock.now())
        tx.issues.archive(archived_then_trashed.id, world.clock.now())
        tx.issues.trash(archived_then_trashed.id, world.clock.now())

    with world.uow.read() as tx:
        page = list_view(
            tx.issues.connection,
            ListFilters(team_id=world.team.id, trashed_only=True, archived_only=True),
        )

    assert [item.id for item in page.items] == [archived_then_trashed.id]


def test_reverse_flips_the_manual_order(world) -> None:
    issues = _seed(world, 3)

    with world.uow.read() as tx:
        page = list_view(tx.issues.connection, ListFilters(team_id=world.team.id, reverse=True))

    assert [item.id for item in page.items] == [issue.id for issue in reversed(issues)]


def test_issue_repo_list_page_delegates_to_list_view(world) -> None:
    _seed(world, 2)

    with world.uow.read() as tx:
        page = tx.issues.list_page(ListFilters(team_id=world.team.id))

    assert len(page.items) == 2
    assert page.has_more is False
