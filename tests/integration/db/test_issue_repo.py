"""`IssueRepo`: SQL-1/SQL-2/SQL-3, posiciones, labels, relaciones e historia (SPEC-030 §2.4)."""

from __future__ import annotations

from datetime import date

import pytest

from helpers_world import make_issue
from hifa.domain.entities import IssueRelation
from hifa.domain.enums import Priority
from hifa.domain.errors import Conflict, NotFound, Validation


def test_insert_returns_the_row_with_defaults(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)

    assert issue.number == 1
    assert issue.identifier == "ENG-1"
    assert issue.priority is Priority.NO_PRIORITY
    assert issue.trashed is False
    assert issue.archived_at is None
    assert issue.sort_order == 0.0


def test_next_number_is_max_plus_one(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)
        make_issue(tx, world)
        assert tx.issues.next_number(world.team.id) == 3


def test_get_and_get_by_identifier(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)

    with world.uow.read() as tx:
        assert tx.issues.get(issue.id).identifier == "ENG-1"
        assert tx.issues.get_by_identifier("ENG-1").id == issue.id
        assert tx.issues.get_by_identifier("eng-1").id == issue.id
        assert tx.issues.get("nope") is None


def test_require_accepts_ulid_and_identifier(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        assert tx.issues.require(issue.id).id == issue.id
        assert tx.issues.require("ENG-1").id == issue.id
        with pytest.raises(NotFound) as exc:
            tx.issues.require("ENG-999")

    assert exc.value.resource == "issue"


def test_update_keeps_created_at_and_changes_title(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        updated = tx.issues.update(issue.model_copy(update={"title": "New title"}))

    assert updated.title == "New title"
    assert updated.created_at == issue.created_at
    assert updated.identifier == issue.identifier


def test_update_missing_row_raises_not_found(world) -> None:
    with pytest.raises(NotFound), world.uow.write() as tx:
        ghost = make_issue(tx, world).model_copy(update={"id": "ghost"})
        tx.issues.update(ghost)


def test_duplicate_identifier_is_conflict(world) -> None:
    with pytest.raises(Conflict) as exc, world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.issues.insert(issue.model_copy(update={"id": "otro", "number": 99}))

    assert exc.value.rule == "UNIQUE_IDENTIFIER"


def test_repeated_number_is_duplicate_number(world) -> None:
    with pytest.raises(Conflict) as exc, world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.issues.insert(issue.model_copy(update={"id": "otro2", "identifier": "ENG-999"}))

    assert exc.value.rule == "DUPLICATE_NUMBER"
    assert exc.value.field == "number"


def test_set_state_only_touches_state_id_and_updated_at(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        world.clock.advance(seconds=5)
        moved = tx.issues.set_state(issue.id, world.states["Done"].id, now=world.clock.now())

    assert moved.state_id == world.states["Done"].id
    assert moved.updated_at == world.clock.now()
    assert moved.title == issue.title
    assert moved.created_at == issue.created_at


def test_set_positions_only_touches_requested_columns(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.issues.set_positions(issue.id, board_order=2.5, now=world.clock.now())
        loaded = tx.issues.get(issue.id)

    assert loaded.board_order == 2.5
    assert loaded.sort_order == 0.0
    assert loaded.sub_issue_sort_order == 0.0


def test_positions_ordered_by_sort_order(world) -> None:
    with world.uow.write() as tx:
        first = make_issue(tx, world)
        second = make_issue(tx, world)
        tx.issues.set_positions(second.id, sort_order=1.0, now=world.clock.now())

    with world.uow.read() as tx:
        assert tx.issues.positions(world.team.id, "sort") == [(first.id, 0.0), (second.id, 1.0)]
        assert tx.issues.positions(world.team.id, "backlog") == [(first.id, 0.0), (second.id, 1.0)]


def test_positions_board_filters_by_state(world) -> None:
    with world.uow.write() as tx:
        todo = make_issue(tx, world)
        doing = make_issue(tx, world, state_id=world.states["In Progress"].id)
        tx.issues.set_positions(todo.id, board_order=1.0, now=world.clock.now())
        tx.issues.set_positions(doing.id, board_order=0.5, now=world.clock.now())

    with world.uow.read() as tx:
        board = tx.issues.positions(world.team.id, "board")
        only_doing = tx.issues.positions(
            world.team.id, "board", None, world.states["In Progress"].id
        )

    assert [issue_id for issue_id, _ in board] == [doing.id, todo.id]
    assert only_doing == [(doing.id, 0.5)]


def test_positions_sub_filters_by_parent(world) -> None:
    with world.uow.write() as tx:
        parent = make_issue(tx, world)
        other_parent = make_issue(tx, world)
        child = make_issue(tx, world, parent_id=parent.id)
        make_issue(tx, world, parent_id=other_parent.id)

    with world.uow.read() as tx:
        assert tx.issues.positions(world.team.id, "sub", parent.id) == [(child.id, 0.0)]


def test_invalid_positions_scope_is_validation(world) -> None:
    with world.uow.read() as tx, pytest.raises(Validation) as exc:
        tx.issues.positions(world.team.id, "diagonal")

    assert exc.value.rule == "INVALID_SCOPE"


def test_positions_excludes_trashed(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.issues.trash(issue.id, world.clock.now())

    with world.uow.read() as tx:
        assert tx.issues.positions(world.team.id, "sort") == []


def test_list_children_is_ordered(world) -> None:
    with world.uow.write() as tx:
        parent = make_issue(tx, world)
        second = make_issue(tx, world, parent_id=parent.id)
        first = make_issue(tx, world, parent_id=parent.id)
        tx.issues.set_positions(first.id, sub_issue_sort_order=1.0, now=world.clock.now())
        tx.issues.set_positions(second.id, sub_issue_sort_order=2.0, now=world.clock.now())

    with world.uow.read() as tx:
        children = tx.issues.list_children(parent.id)
        assert [child.id for child in children] == [first.id, second.id]
        assert [child.id for child in tx.issues.list_children(parent.id)] == [first.id, second.id]


def test_list_children_include_closed_false(world) -> None:
    with world.uow.write() as tx:
        parent = make_issue(tx, world)
        open_child = make_issue(tx, world, parent_id=parent.id)
        make_issue(tx, world, parent_id=parent.id, state_id=world.states["Done"].id)

    with world.uow.read() as tx:
        assert len(tx.issues.list_children(parent.id)) == 2
        assert [c.id for c in tx.issues.list_children(parent.id, include_closed=False)] == [
            open_child.id
        ]


def test_count_open_children_and_count_open(world) -> None:
    with world.uow.write() as tx:
        parent = make_issue(tx, world)
        make_issue(tx, world, parent_id=parent.id)
        make_issue(tx, world, parent_id=parent.id, state_id=world.states["Canceled"].id)

    with world.uow.read() as tx:
        assert tx.issues.count_open_children(parent.id) == 1
        assert tx.issues.count_open(world.team.id) == 2


def test_ancestor_chain_stops_at_max_depth(world) -> None:
    with world.uow.write() as tx:
        root = make_issue(tx, world)
        middle = make_issue(tx, world, parent_id=root.id)
        leaf = make_issue(tx, world, parent_id=middle.id)
        chain = tx.issues.ancestor_chain(leaf.id)
        short = tx.issues.ancestor_chain(leaf.id, max_depth=1)

    assert len(chain) == 3
    assert chain[leaf.id] == middle.id
    assert chain[middle.id] == root.id
    assert chain[root.id] is None
    assert short == {leaf.id: middle.id, middle.id: root.id}


def test_archive_unarchive_trash_restore(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.issues.archive(issue.id, world.clock.now())
        assert tx.issues.get(issue.id).archived_at == world.clock.now()
        tx.issues.unarchive(issue.id, world.clock.now())
        assert tx.issues.get(issue.id).archived_at is None
        tx.issues.trash(issue.id, world.clock.now())
        assert tx.issues.get(issue.id).trashed is True
        tx.issues.restore(issue.id, world.clock.now())
        assert tx.issues.get(issue.id).trashed is False


def test_labels_add_remove_and_set(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.issues.add_label(issue.id, world.labels["bug"].id)
        tx.issues.add_label(issue.id, world.labels["bug"].id)
        tx.issues.add_label(issue.id, world.labels["feature"].id)
        assert set(tx.issues.label_ids(issue.id)) == {
            world.labels["bug"].id,
            world.labels["feature"].id,
        }
        assert tx.issues.remove_label(issue.id, world.labels["bug"].id) is True
        assert tx.issues.remove_label(issue.id, world.labels["bug"].id) is False
        tx.issues.set_labels(issue.id, [world.labels["docs"].id])
        assert tx.issues.label_ids(issue.id) == (world.labels["docs"].id,)


def test_label_ids_ordered_by_name(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.issues.set_labels(issue.id, [world.labels["feature"].id, world.labels["bug"].id])
        assert [label_id for label_id in tx.issues.label_ids(issue.id)] == [
            world.labels["bug"].id,
            world.labels["feature"].id,
        ]


def test_relations_in_both_directions_and_duplicate(world) -> None:
    with world.uow.write() as tx:
        first = make_issue(tx, world)
        second = make_issue(tx, world)
        relation = tx.issues.add_relation(
            IssueRelation(
                id=world.idgen.new(),
                issue_id=first.id,
                related_issue_id=second.id,
                type="blocks",
                created_at=world.clock.now(),
            )
        )
        assert [r.id for r in tx.issues.relations(first.id)] == [relation.id]
        assert [r.id for r in tx.issues.relations(second.id)] == [relation.id]
        assert tx.issues.get_relation(relation.id).type.value == "blocks"
        with pytest.raises(Conflict) as exc:
            tx.issues.add_relation(
                IssueRelation(
                    id=world.idgen.new(),
                    issue_id=first.id,
                    related_issue_id=second.id,
                    type="blocks",
                    created_at=world.clock.now(),
                )
            )
        assert exc.value.rule == "RELATION_DUPLICATE"
        assert tx.issues.remove_relation(relation.id) is True
        assert tx.issues.remove_relation(relation.id) is False


def test_relation_to_itself_is_validation(world) -> None:
    with pytest.raises(Validation) as exc, world.uow.write() as tx:
        issue = make_issue(tx, world)
        tx.issues.add_relation(
            IssueRelation(
                id="rel_self",
                issue_id=issue.id,
                related_issue_id=issue.id,
                type="related",
                created_at=world.clock.now(),
            )
        )

    assert exc.value.rule == "CHECK_CONSTRAINT"


def test_history_record_and_list(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        inserted = tx.history.record(
            issue.id,
            [("state_id", None, world.states["Done"].id), ("priority", 0, 2)],
            actor_id=world.members["ana"].id,
            now=world.clock.now(),
        )
        assert inserted == 2
        assert tx.history.record(issue.id, [], actor_id=None, now=world.clock.now()) == 0

    with world.uow.read() as tx:
        rows = tx.history.list_for_issue(issue.id)
        assert len(rows) == 2
        assert {row.field for row in rows} == {"state_id", "priority"}
        assert rows[0].actor_id == world.members["ana"].id


def test_comments_crud(world) -> None:
    from hifa.domain.entities import Comment

    with world.uow.write() as tx:
        issue = make_issue(tx, world)
        comment = tx.comments.insert(
            Comment(
                id="cm_1",
                issue_id=issue.id,
                author_id=world.members["ana"].id,
                body="hola",
                created_at=world.clock.now(),
                updated_at=world.clock.now(),
            )
        )
        assert tx.comments.list_for_issue(issue.id) == [comment]
        edited = tx.comments.update_body(comment.id, "hola editado", world.clock.now())
        assert edited.body == "hola editado"
        assert edited.edited_at == world.clock.now()
        tx.comments.resolve(comment.id, world.clock.now())
        assert tx.comments.get(comment.id).resolved_at == world.clock.now()
        assert tx.comments.list_for_issue(issue.id, include_resolved=False) == []
        tx.comments.unresolve(comment.id)
        assert tx.comments.get(comment.id).resolved_at is None


def test_due_date_maps_to_date(world) -> None:
    with world.uow.write() as tx:
        issue = make_issue(tx, world, due_date=date(2026, 11, 10), priority=Priority.URGENT)

    assert issue.due_date == date(2026, 11, 10)
    assert issue.priority is Priority.URGENT
