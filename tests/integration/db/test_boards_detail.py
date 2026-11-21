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

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from helpers_world import World, make_issue
from hifa.adapters.sqlite.connection import Database
from hifa.adapters.sqlite.reads import IssueDetail
from hifa.app import boards
from hifa.domain.entities import Issue, IssueRelation, Team, WorkflowState
from hifa.domain.enums import RelationType, WorkflowStateType
from hifa.domain.errors import NotFound


@pytest.fixture
def fan(world: World) -> World:
    """`ENG-7` with six edges, inserted in an order that is neither the answer nor its reverse.

    Insertion: `related` to ENG-30, `blocks` to ENG-20, `blocks` to ENG-9, `blocks` to ENG-3,
    `duplicate` to ENG-5, and `ENG-8 blocks ENG-7` last — which reads as `blocked_by` from ENG-7.
    The declaration is `blocks`, `blocked_by`, `duplicate`, `related`, `similar`, then the far
    end's identifier as text.

    The three `blocks` far ends are the point of the fixture: the text order is `ENG-20`,
    `ENG-3`, `ENG-9`, the insertion order is `ENG-20`, `ENG-9`, `ENG-3` and the reverse is
    `ENG-3`, `ENG-9`, `ENG-20` — the middle one of the text order is not the middle one the rows
    arrive in, so the insertion order, its reverse and the fetch order all differ from the
    published trio, and only the comparison on the identifier produces it.
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
                ("ENG-3", 3),
                ("ENG-8", 8),
            )
        }
        for identifier, relation_type in (
            ("ENG-30", RelationType.RELATED),
            ("ENG-20", RelationType.BLOCKS),
            ("ENG-9", RelationType.BLOCKS),
            ("ENG-3", RelationType.BLOCKS),
            ("ENG-5", RelationType.DUPLICATE),
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


def test_the_relations_come_out_in_the_declared_reading_then_identifier_order(
    fan: World,
) -> None:
    """Reading first, then the far end's identifier compared as text — never the arrival order.

    The three `blocks` rows arrive `ENG-20, ENG-9, ENG-3` and are published `ENG-20, ENG-3,
    ENG-9`, which is neither that order nor its reverse: it is the lexicographic comparison of
    the identifiers, where `ENG-3` precedes `ENG-9` because the characters after `ENG-` are
    compared one by one.
    """
    detail = boards.issue_detail(Database(fan.path), "ENG-7")

    assert readings(detail) == [
        ("blocks", "ENG-20"),
        ("blocks", "ENG-3"),
        ("blocks", "ENG-9"),
        ("blocked_by", "ENG-8"),
        ("duplicate", "ENG-5"),
        ("related", "ENG-30"),
    ]


def test_the_published_order_is_neither_the_insertion_order_nor_its_reverse(fan: World) -> None:
    """The fixture's guard: an implementation that echoed the fetch order could not pass above.

    The rows are inserted `related/ENG-30, blocks/ENG-20, blocks/ENG-9, blocks/ENG-3,
    duplicate/ENG-5`, and the edge read from the other end last, so the insertion order and its
    reverse both differ from the published one. Within `blocks` the three orders are all distinct
    — fetched `ENG-20, ENG-9, ENG-3`, reversed `ENG-3, ENG-9, ENG-20`, published `ENG-20,
    ENG-3, ENG-9` — so a sort on the reading alone, which is stable and would echo the fetch
    order, cannot produce the answer. A fixture that happened to be inserted in the answer's
    order would pass without testing the tie-break at all.
    """
    inserted = ["ENG-30", "ENG-20", "ENG-9", "ENG-3", "ENG-5", "ENG-8"]
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
    published = readings(detail)

    assert fetched == inserted
    assert [far for _reading, far in published] != fetched
    assert [far for _reading, far in published] != list(reversed(fetched))
    assert sorted(far for _reading, far in published) == sorted(fetched)

    trio = {"ENG-20", "ENG-9", "ENG-3"}
    fetched_blocks = [far for far in fetched if far in trio]
    published_blocks = [far for reading, far in published if reading == "blocks"]

    assert fetched_blocks == ["ENG-20", "ENG-9", "ENG-3"]
    assert published_blocks == ["ENG-20", "ENG-3", "ENG-9"]
    assert published_blocks != fetched_blocks
    assert published_blocks != list(reversed(fetched_blocks))


def test_the_collections_are_keyed_in_the_order_the_object_carries(fan: World) -> None:
    """`comments`, `relations`, `history`, `children`, each one the tuple the model carries."""
    detail = boards.issue_detail(Database(fan.path), "ENG-7")

    collections = boards.detail_collections(detail)

    assert tuple(collections) == ("comments", "relations", "history", "children")
    assert collections["comments"] == detail.comments
    assert collections["relations"] == detail.relations
    assert collections["history"] == detail.history
    assert collections["children"] == detail.children


# --- the four ways a reference names an issue ----------------------------------------------


def new_team(tx: object, world: World, key: str) -> tuple[Team, WorkflowState]:
    """A second team with its one default state, inserted through the repositories."""
    now = world.clock.now()  # type: ignore[attr-defined]
    team = tx.teams.insert(  # type: ignore[attr-defined]
        Team(
            id=world.idgen.new(),  # type: ignore[attr-defined]
            workspace_id=world.workspace.id,
            key=key,
            name=key,
            created_at=now,
            updated_at=now,
        )
    )
    state = tx.states.insert(  # type: ignore[attr-defined]
        WorkflowState(
            id=world.idgen.new(),  # type: ignore[attr-defined]
            team_id=team.id,
            name="Todo",
            type=WorkflowStateType.UNSTARTED,
            position=1.0,
            is_default=True,
        )
    )
    return team, state


def rotate(
    tx: object,
    *,
    team: Team,
    old: str,
    new: str,
    at: datetime,
    issues: Sequence[Issue],
) -> None:
    """Give `team` the key `new`, record `old` as left behind, and rewrite its identifiers."""
    tx.teams.add_previous_key(team.id, old, at=at)  # type: ignore[attr-defined]
    # The team's own update writes every column but the key: a rotation is not an edit, so the
    # new key is written here, where the fixture builds the state the not-yet-contracted verb
    # would leave behind.
    tx.teams.connection.execute(  # type: ignore[attr-defined]
        "UPDATE team SET key = ? WHERE id = ?", (new, team.id)
    )
    for issue in issues:
        tx.issues.connection.execute(  # type: ignore[attr-defined]
            "UPDATE issue SET identifier = ? WHERE id = ?", (f"{new}-{issue.number}", issue.id)
        )


def test_a_current_identifier_is_read_without_regard_to_case(world: World) -> None:
    """`eng-1` and `ENG-1` are one name: the comparison folds the case of what was typed."""
    with world.uow.write() as tx:
        make_issue(tx, world, number=1, identifier="ENG-1")

    detail = boards.issue_detail(Database(world.path), "eng-1")

    assert detail.issue.identifier == "ENG-1"
    assert detail.previous_identifier is None


def test_a_number_resolves_inside_the_team_the_call_was_given(world: World) -> None:
    """Two teams that both number an issue 7: the team of the call picks the row."""
    with world.uow.write() as tx:
        make_issue(tx, world, number=7, identifier="ENG-7")
        ops, ops_state = new_team(tx, world, "OPS")
        make_issue(tx, world, team_id=ops.id, state_id=ops_state.id, number=7, identifier="OPS-7")

    db = Database(world.path)

    assert boards.issue_detail(db, "7", team="ENG").issue.identifier == "ENG-7"
    assert boards.issue_detail(db, "7", team="ops").issue.identifier == "OPS-7"


def test_a_number_the_team_does_not_wear_is_not_found(world: World) -> None:
    """A number no issue carries is the read's missing issue, not a second kind of answer."""
    with world.uow.write() as tx:
        make_issue(tx, world, number=1, identifier="ENG-1")

    with pytest.raises(NotFound) as raised:
        boards.issue_detail(Database(world.path), "999", team="ENG")

    assert raised.value.resource == "issue"
    assert raised.value.key == "999"


def test_a_team_the_workspace_does_not_hold_is_refused_as_a_team(world: World) -> None:
    """A wrong team is a missing team, so the refusal names `team` and the token that was given."""
    with world.uow.write() as tx:
        make_issue(tx, world, number=1, identifier="ENG-1")

    with pytest.raises(NotFound) as raised:
        boards.issue_detail(Database(world.path), "1", team="NOPE")

    assert raised.value.resource == "team"
    assert raised.value.key == "NOPE"


def test_a_previous_identifier_resolves_and_is_reported(world: World) -> None:
    """The key a team left behind answers with its issue, and the answer says what it was."""
    with world.uow.write() as tx:
        issue = make_issue(tx, world, number=3, identifier="ENG-3")
        rotate(
            tx,
            team=world.team,
            old="ENG",
            new="PLAT",
            at=datetime(2026, 11, 20, tzinfo=UTC),
            issues=[issue],
        )

    detail = boards.issue_detail(Database(world.path), "ENG-3")

    assert detail.issue.identifier == "PLAT-3"
    assert detail.previous_identifier == "ENG-3"


def test_a_current_identifier_shadows_a_previous_key_of_the_same_spelling(world: World) -> None:
    """Two teams, one wearing the key the other left: the issue that wears it today answers."""
    with world.uow.write() as tx:
        left = make_issue(tx, world, number=3, identifier="ENG-3")
        rotate(
            tx,
            team=world.team,
            old="ENG",
            new="PLAT",
            at=datetime(2026, 11, 1, tzinfo=UTC),
            issues=[left],
        )
        eng, eng_state = new_team(tx, world, "ENG")
        make_issue(tx, world, team_id=eng.id, state_id=eng_state.id, number=7, identifier="ENG-7")

    detail = boards.issue_detail(Database(world.path), "ENG-7")

    assert detail.issue.identifier == "ENG-7"
    assert detail.previous_identifier is None


def test_two_teams_that_left_the_same_key_are_settled_by_the_most_recent_rotation(
    world: World,
) -> None:
    """Both teams wore `ENG` and both number an issue 3; the later rotation answers, once."""
    with world.uow.write() as tx:
        earlier = make_issue(tx, world, number=3, identifier="ENG-3")
        rotate(
            tx,
            team=world.team,
            old="ENG",
            new="PLAT",
            at=datetime(2026, 11, 1, tzinfo=UTC),
            issues=[earlier],
        )
        later_team, later_state = new_team(tx, world, "ENG")
        later = make_issue(
            tx,
            world,
            team_id=later_team.id,
            state_id=later_state.id,
            number=3,
            identifier="ENG-3",
        )
        rotate(
            tx,
            team=later_team,
            old="ENG",
            new="OPS",
            at=datetime(2026, 11, 20, tzinfo=UTC),
            issues=[later],
        )

    detail = boards.issue_detail(Database(world.path), "ENG-3")

    assert detail.issue.identifier == "OPS-3"
    assert detail.previous_identifier == "ENG-3"


def test_an_exact_tie_of_the_rotation_instant_is_settled_by_the_greater_team_id(
    world: World,
) -> None:
    """The same instant still answers once: the greater team id wins, so every run agrees."""
    same = datetime(2026, 11, 20, tzinfo=UTC)
    with world.uow.write() as tx:
        earlier = make_issue(tx, world, number=3, identifier="ENG-3")
        rotate(tx, team=world.team, old="ENG", new="PLAT", at=same, issues=[earlier])
        later_team, later_state = new_team(tx, world, "ENG")
        later = make_issue(
            tx,
            world,
            team_id=later_team.id,
            state_id=later_state.id,
            number=3,
            identifier="ENG-3",
        )
        rotate(tx, team=later_team, old="ENG", new="OPS", at=same, issues=[later])

    detail = boards.issue_detail(Database(world.path), "ENG-3")

    assert later_team.id > world.team.id
    assert detail.issue.identifier == "OPS-3"
