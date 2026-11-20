"""`app.boards.issue_detail`: the order and the shape of the collections it publishes.

Two facts are produced by this layer and by no statement below it. The first is the order of
`relations`: the rows come back as a set from both ends of the edge, and how one reads is a
function of the asking issue, so the sort lives here, on the reading and then the far end's
identifier. The fixture is inserted in an order that is neither the answer nor its reverse, so a
read that handed over the fetch order could not pass.

The second is the order of the four collections themselves, which every edge formats from this
one place.
"""

from __future__ import annotations

import pytest

from helpers_world import World, make_issue
from hifa.adapters.sqlite.connection import Database
from hifa.adapters.sqlite.reads import IssueDetail
from hifa.app import boards
from hifa.domain.entities import IssueRelation
from hifa.domain.enums import RelationType


@pytest.fixture
def fan(world: World) -> World:
    """`ENG-7` with five edges, inserted in an order that is neither the answer nor its reverse.

    Insertion: `related` to ENG-30, `blocks` to ENG-20, `duplicate` to ENG-5, `blocks` to ENG-9,
    and `ENG-8 blocks ENG-7` last — which reads as `blocked_by` from ENG-7. The declaration is
    `blocks`, `blocked_by`, `duplicate`, `related`, `similar`, then the far end's identifier as
    text, so `ENG-20` precedes `ENG-9` inside `blocks`.
    """
    now = world.clock.now()  # type: ignore[attr-defined]
    with world.uow.write() as tx:
        asking = make_issue(tx, world, identifier="ENG-7", number=7)
        others = {
            identifier: make_issue(tx, world, identifier=identifier, number=number)
            for identifier, number in (
                ("ENG-30", 30),
                ("ENG-20", 20),
                ("ENG-5", 5),
                ("ENG-9", 9),
                ("ENG-8", 8),
            )
        }
        for identifier, relation_type in (
            ("ENG-30", RelationType.RELATED),
            ("ENG-20", RelationType.BLOCKS),
            ("ENG-5", RelationType.DUPLICATE),
            ("ENG-9", RelationType.BLOCKS),
        ):
            tx.issues.add_relation(
                IssueRelation(
                    id=world.idgen.new(),  # type: ignore[attr-defined]
                    issue_id=asking.id,
                    related_issue_id=others[identifier].id,
                    type=relation_type,
                    created_at=now,
                )
            )
        tx.issues.add_relation(
            IssueRelation(
                id=world.idgen.new(),  # type: ignore[attr-defined]
                issue_id=others["ENG-8"].id,
                related_issue_id=asking.id,
                type=RelationType.BLOCKS,
                created_at=now,
            )
        )
    return world


def readings(detail: IssueDetail) -> list[tuple[str, str]]:
    """`(what the row reads as from the asking issue, the far end's identifier)`, in order."""
    pairs: list[tuple[str, str]] = []
    for relation, far in detail.relations:
        direction = "out" if relation.issue_id == detail.issue.id else "in"
        pairs.append((relation.type.name_as_seen(direction), far.identifier))
    return pairs


def test_the_relations_come_out_in_the_declared_order(fan: World) -> None:
    """Reading first, then the far end's identifier as text — never the order the rows arrive in."""
    detail = boards.issue_detail(Database(fan.path), "ENG-7")

    assert readings(detail) == [
        ("blocks", "ENG-20"),
        ("blocks", "ENG-9"),
        ("blocked_by", "ENG-8"),
        ("duplicate", "ENG-5"),
        ("related", "ENG-30"),
    ]


def test_the_published_order_is_neither_the_insertion_order_nor_its_reverse(fan: World) -> None:
    """The fixture's guard: an implementation that echoed the fetch order could not pass above.

    The rows are inserted `related/ENG-30, blocks/ENG-20, duplicate/ENG-5, blocks/ENG-9`, and the
    edge read from the other end last, so the insertion order and its reverse both differ from the
    published one. A fixture that happened to be inserted in the answer's order would pass without
    testing the sort at all.
    """
    inserted = ["ENG-30", "ENG-20", "ENG-5", "ENG-9", "ENG-8"]
    with fan.uow.read() as tx:
        identifiers = {
            str(row["id"]): str(row["identifier"])
            for row in tx.issues.connection.execute("SELECT id, identifier FROM issue").fetchall()
        }
        rows = tx.issues.connection.execute(
            "SELECT issue_id, related_issue_id FROM issue_relation ORDER BY rowid"
        ).fetchall()
    detail = boards.issue_detail(Database(fan.path), "ENG-7")

    asking_id = detail.issue.id
    fetched = [identifiers[str(row[1] if str(row[0]) == asking_id else row[0])] for row in rows]
    published = [identifier for _reading, identifier in readings(detail)]

    assert fetched == inserted
    assert published != fetched
    assert published != list(reversed(fetched))
    assert sorted(published) == sorted(fetched)


def test_the_collections_are_keyed_in_the_order_the_object_carries(fan: World) -> None:
    """`comments`, `relations`, `history`, `children`, each one the tuple the model carries."""
    detail = boards.issue_detail(Database(fan.path), "ENG-7")

    collections = boards.detail_collections(detail)

    assert tuple(collections) == ("comments", "relations", "history", "children")
    assert collections["comments"] == detail.comments
    assert collections["relations"] == detail.relations
    assert collections["history"] == detail.history
    assert collections["children"] == detail.children
