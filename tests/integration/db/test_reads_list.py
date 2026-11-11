"""`reads.list_view`: one query with aggregated labels, AND filters and keyset paging (D-011)."""

from __future__ import annotations

from datetime import date

import pytest

from helpers_world import make_issue
from hifa.adapters.sqlite.reads import ListFilters, encode_cursor, list_view
from hifa.domain.enums import Priority
from hifa.domain.errors import Validation


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


def test_cursor_with_non_numeric_sort_key_in_manual_is_validation(world) -> None:
    _seed(world, 1)

    with world.uow.read() as tx, pytest.raises(Validation) as exc:
        list_view(
            tx.issues.connection,
            ListFilters(team_id=world.team.id, cursor=encode_cursor("abc", "id")),
        )

    assert exc.value.rule == "INVALID_CURSOR"


def test_issue_repo_list_page_delegates_to_list_view(world) -> None:
    _seed(world, 2)

    with world.uow.read() as tx:
        page = tx.issues.list_page(ListFilters(team_id=world.team.id))

    assert len(page.items) == 2
    assert page.has_more is False
