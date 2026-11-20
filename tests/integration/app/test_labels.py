"""`app.labels` over a migrated database: the catalogue and the two issue links.

The rules of this layer are decisions about rows, so every test here reads the database back
instead of trusting the returned entity: the link a write leaves, the history rows it wrote and
their order, the column it did *not* touch, and the refusal that left the row exactly as it was.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from helpers import FrozenClock, SeqIdGen
from hifa.adapters.sqlite.connection import Database, open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.commands import (
    Actor,
    AddLabel,
    CreateIssue,
    CreateLabel,
    RemoveLabel,
    RestoreLabel,
    RetireLabel,
    UpdateLabel,
)
from hifa.app.issues import create_issue
from hifa.app.labels import (
    add_label,
    create_label,
    label_detail,
    list_labels,
    remove_label,
    restore_label,
    retire_label,
    update_label,
)
from hifa.domain.entities import Member, Team, WorkflowState
from hifa.domain.enums import WorkflowStateType
from hifa.domain.errors import Conflict, InvalidTransition, NotFound, Validation

#: (name, type, position, is_default) — `Todo` is the team default.
STATES = (
    ("Backlog", WorkflowStateType.BACKLOG, 1.0, False),
    ("Todo", WorkflowStateType.UNSTARTED, 2.0, True),
    ("In Progress", WorkflowStateType.STARTED, 3.0, False),
    ("Done", WorkflowStateType.COMPLETED, 4.0, False),
)

RETIRED_AT = "2026-11-20T00:00:00Z"


class Seed:
    """Migrated database with team ENG, its four states, one member and one issue."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.clock = FrozenClock()
        self.idgen = SeqIdGen()
        conn = open_db(path)
        try:
            apply_migrations(conn)
        finally:
            conn.close()
        uow = UnitOfWork(path, clock=self.clock, idgen=self.idgen)
        now = self.clock.now()
        with uow.write() as tx:
            self.workspace = tx.teams.ensure_workspace(name="Hifa", url_key="hifa")
            self.team = tx.teams.insert(
                Team(
                    id=self.idgen.new(),
                    workspace_id=self.workspace.id,
                    key="ENG",
                    name="Engineering",
                    created_at=now,
                    updated_at=now,
                )
            )
            self.states: dict[str, WorkflowState] = {}
            for name, state_type, position, is_default in STATES:
                self.states[name] = tx.states.insert(
                    WorkflowState(
                        id=self.idgen.new(),
                        team_id=self.team.id,
                        name=name,
                        type=state_type,
                        position=position,
                        is_default=is_default,
                    )
                )
            self.member = tx.members.insert(
                Member(
                    id=self.idgen.new(),
                    workspace_id=self.workspace.id,
                    name="ana",
                    display_name="Ana",
                    email="ana@example.com",
                    created_at=now,
                )
            )
        self.issue = create_issue(uow, CreateIssue(team="ENG", title="Alpha"), self.actor)
        uow.close()

    @property
    def actor(self) -> Actor:
        return Actor(member_id=self.member.id)

    def other_team(self, key: str = "OPS") -> Team:
        """A second team of the same workspace, with one state of its own."""
        now = self.clock.now()
        uow = UnitOfWork(self.path, clock=self.clock, idgen=self.idgen)
        try:
            return self._insert_team(uow, key, now)
        finally:
            uow.close()

    def _insert_team(self, uow: UnitOfWork, key: str, now: datetime) -> Team:
        with uow.write() as tx:
            team = tx.teams.insert(
                Team(
                    id=self.idgen.new(),
                    workspace_id=self.workspace.id,
                    key=key,
                    name=key.title(),
                    created_at=now,
                    updated_at=now,
                )
            )
            tx.states.insert(
                WorkflowState(
                    id=self.idgen.new(),
                    team_id=team.id,
                    name="Todo",
                    type=WorkflowStateType.UNSTARTED,
                    position=1.0,
                    is_default=True,
                )
            )
            return team


@pytest.fixture
def seed(db_path: Path) -> Seed:
    return Seed(db_path)


def _uow(seed: Seed) -> UnitOfWork:
    """A fresh connection on the same file, sharing the seed's clock and id generator.

    Sharing both is what makes the time controllable and the ids unique: a generator per
    connection would restart at 1 and collide on `label.id`.
    """
    return UnitOfWork(seed.path, clock=seed.clock, idgen=seed.idgen)


def _rows(seed: Seed, sql: str, params: tuple[object, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(seed.path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def _write(seed: Seed, sql: str, params: tuple[object, ...] = ()) -> None:
    """A committed write used to build a state the port cannot reach."""
    conn = sqlite3.connect(seed.path)
    try:
        conn.execute(sql, params)
        conn.commit()
    finally:
        conn.close()


def _label_named(seed: Seed, name: str, team_id: str | None = None) -> sqlite3.Row:
    rows = _rows(seed, "SELECT * FROM label WHERE name = ? AND team_id IS ?", (name, team_id))
    assert len(rows) == 1, f"expected one label {name!r}, found {len(rows)}"
    return rows[0]


def _links(seed: Seed, identifier: str) -> list[str]:
    """Names of the labels the issue carries, by name ASC."""
    return [
        row["name"]
        for row in _rows(
            seed,
            "SELECT l.name FROM issue_label il JOIN label l ON l.id = il.label_id"
            " WHERE il.issue_id = (SELECT id FROM issue WHERE identifier = ?) ORDER BY l.name",
            (identifier,),
        )
    ]


def _link_ids(seed: Seed) -> list[str]:
    return [row["label_id"] for row in _rows(seed, "SELECT label_id FROM issue_label")]


def _history(seed: Seed, identifier: str) -> list[tuple[str, str | None, str | None]]:
    """`(field, old, new)` of the issue's history, oldest first, without the `created` row."""
    return [
        (row["field"], row["old_value"], row["new_value"])
        for row in _rows(
            seed,
            "SELECT field, old_value, new_value FROM issue_history"
            " WHERE issue_id = (SELECT id FROM issue WHERE identifier = ?) AND field <> 'created'"
            " ORDER BY id",
            (identifier,),
        )
    ]


def _count(seed: Seed, table: str) -> int:
    return int(_rows(seed, f"SELECT COUNT(*) AS n FROM {table}")[0]["n"])


def _make(
    seed: Seed,
    name: str,
    *,
    team_id: str | None = None,
    color: str | None = None,
    description: str | None = None,
    parent_id: str | None = None,
    is_group: bool = False,
    group_type: str | None = None,
) -> str:
    """A label inserted through the catalogue use case; returns its id."""
    uow = _uow(seed)
    try:
        created = create_label(
            uow,
            CreateLabel(
                name=name,
                team=team_id,
                color=color,
                description=description,
                parent=parent_id,
                is_group=is_group,
                group_type=group_type,
            ),
            seed.actor,
        )
    finally:
        uow.close()
    return created.id


def _attach(seed: Seed, label_id: str) -> None:
    uow = _uow(seed)
    try:
        add_label(uow, AddLabel(issue=seed.issue.identifier, label=label_id), seed.actor)
    finally:
        uow.close()


def _retire(seed: Seed, ref: str) -> None:
    uow = _uow(seed)
    try:
        retire_label(uow, RetireLabel(label=ref), seed.actor)
    finally:
        uow.close()


# --- create_label --------------------------------------------------------------------------


def test_a_workspace_label_lands_at_the_null_level_with_the_default_color(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        label = create_label(uow, CreateLabel(name="  infra  "), seed.actor)
    finally:
        uow.close()

    row = _label_named(seed, "infra")
    assert row["id"] == label.id
    assert row["team_id"] is None
    assert row["color"] == "#6B6F76"
    assert row["description"] is None
    assert row["parent_id"] is None
    assert row["is_group"] == 0
    assert row["group_type"] is None
    assert row["retired_at"] is None


def test_a_team_label_lands_at_the_team_level(seed: Seed) -> None:
    _make(seed, "infra", team_id=seed.team.id, color="#5E6AD2")

    assert _label_named(seed, "infra", seed.team.id)["color"] == "#5E6AD2"


def test_an_archived_team_is_a_validation_error(seed: Seed) -> None:
    _write(
        seed,
        "UPDATE team SET archived_at = ? WHERE id = ?",
        (RETIRED_AT, seed.team.id),
    )
    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as caught:
            create_label(uow, CreateLabel(name="infra", team="ENG"), seed.actor)
    finally:
        uow.close()

    assert caught.value.rule == "TEAM_ARCHIVED"
    assert _count(seed, "label") == 0


def test_an_unknown_team_is_not_found(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(NotFound) as caught:
            create_label(uow, CreateLabel(name="infra", team="NOPE"), seed.actor)
    finally:
        uow.close()

    assert (caught.value.resource, caught.value.field) == ("team", "team")


def test_a_repeated_name_at_the_same_level_is_a_conflict(seed: Seed) -> None:
    _make(seed, "infra")
    uow = _uow(seed)
    try:
        with pytest.raises(Conflict) as caught:
            create_label(uow, CreateLabel(name="infra"), seed.actor)
    finally:
        uow.close()

    assert (caught.value.rule, caught.value.field) == ("UNIQUE_LABEL_NAME", "name")
    assert _count(seed, "label") == 1


def test_the_same_name_at_two_levels_is_two_labels(seed: Seed) -> None:
    """The partial unique indexes count one workspace row and one row per team."""
    _make(seed, "Bug")
    _make(seed, "Bug", team_id=seed.team.id)

    assert _count(seed, "label") == 2


def test_a_blank_name_and_a_bad_color_are_refused_before_the_insert(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as blank:
            create_label(uow, CreateLabel(name="   "), seed.actor)
        with pytest.raises(Validation) as color:
            create_label(uow, CreateLabel(name="infra", color="red"), seed.actor)
    finally:
        uow.close()

    assert (blank.value.rule, blank.value.field) == ("LABEL_NAME_EMPTY", "name")
    assert (color.value.rule, color.value.field) == ("COLOR_FORMAT", "color")
    assert _count(seed, "label") == 0


def test_a_group_carries_its_type_and_no_parent(seed: Seed) -> None:
    group_id = _make(seed, "Severity", is_group=True, group_type="singleSelect")

    row = _label_named(seed, "Severity")
    assert (row["is_group"], row["group_type"], row["id"]) == (1, "singleSelect", group_id)


def test_a_child_resolves_its_parent_at_its_own_level(seed: Seed) -> None:
    group_id = _make(seed, "Severity", is_group=True, group_type="singleSelect")
    _make(seed, "High", parent_id=group_id)

    assert _label_named(seed, "High")["parent_id"] == group_id


def test_a_parent_of_the_other_level_is_invalid(seed: Seed) -> None:
    """A workspace group cannot parent a team label: the reference lives at one level."""
    group_id = _make(seed, "Severity", is_group=True, group_type="multiSelect")

    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as caught:
            create_label(uow, CreateLabel(name="High", team="ENG", parent=group_id), seed.actor)
    finally:
        uow.close()

    assert (caught.value.rule, caught.value.field) == ("LABEL_GROUP_INVALID", "parent")


def test_a_parent_that_is_not_a_group_is_invalid(seed: Seed) -> None:
    _make(seed, "flat")

    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as caught:
            create_label(uow, CreateLabel(name="High", parent="flat"), seed.actor)
    finally:
        uow.close()

    assert (caught.value.rule, caught.value.field) == ("LABEL_GROUP_INVALID", "parent")


# --- update_label --------------------------------------------------------------------------


def test_a_rename_keeps_every_link_and_writes_no_issue_history(seed: Seed) -> None:
    label_id = _make(seed, "Bug")
    _attach(seed, label_id)
    before = _history(seed, seed.issue.identifier)

    uow = _uow(seed)
    try:
        update_label(uow, UpdateLabel(label="Bug", name="Defect"), seed.actor)
    finally:
        uow.close()

    assert _rows(seed, "SELECT id FROM label WHERE name = 'Bug'") == []
    assert _label_named(seed, "Defect")["id"] == label_id
    assert _links(seed, seed.issue.identifier) == ["Defect"]
    assert _history(seed, seed.issue.identifier) == before


def test_a_recolor_changes_one_column_and_keeps_the_name(seed: Seed) -> None:
    _make(seed, "Bug", color="#EB5757")
    uow = _uow(seed)
    try:
        updated = update_label(uow, UpdateLabel(label="Bug", color="#BB87FC"), seed.actor)
    finally:
        uow.close()

    assert (updated.name, updated.color) == ("Bug", "#BB87FC")
    assert _label_named(seed, "Bug")["color"] == "#BB87FC"


def test_a_clear_description_writes_null(seed: Seed) -> None:
    _make(seed, "Bug", description="renders wrong")
    assert _label_named(seed, "Bug")["description"] == "renders wrong"

    uow = _uow(seed)
    try:
        update_label(uow, UpdateLabel(label="Bug", description=None), seed.actor)
    finally:
        uow.close()

    assert _label_named(seed, "Bug")["description"] is None


def test_an_update_whose_values_already_match_writes_nothing(seed: Seed) -> None:
    _make(seed, "Bug", color="#EB5757")
    uow = _uow(seed)
    try:
        unchanged = update_label(uow, UpdateLabel(label="Bug", color="#EB5757"), seed.actor)
    finally:
        uow.close()

    assert (unchanged.name, unchanged.color) == ("Bug", "#EB5757")
    assert _label_named(seed, "Bug")["color"] == "#EB5757"


def test_a_rename_onto_a_name_of_the_same_level_is_a_conflict(seed: Seed) -> None:
    _make(seed, "Bug")
    _make(seed, "Defect")

    uow = _uow(seed)
    try:
        with pytest.raises(Conflict) as caught:
            update_label(uow, UpdateLabel(label="Bug", name="Defect"), seed.actor)
    finally:
        uow.close()

    assert caught.value.rule == "UNIQUE_LABEL_NAME"
    assert _label_named(seed, "Bug")["name"] == "Bug"
    assert _count(seed, "label") == 2


def test_a_blank_new_name_and_a_bad_color_are_refused(seed: Seed) -> None:
    _make(seed, "Bug")

    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as blank:
            update_label(uow, UpdateLabel(label="Bug", name="  "), seed.actor)
        with pytest.raises(Validation) as color:
            update_label(uow, UpdateLabel(label="Bug", color="#12345"), seed.actor)
    finally:
        uow.close()

    assert blank.value.rule == "LABEL_NAME_EMPTY"
    assert color.value.rule == "COLOR_FORMAT"
    assert _label_named(seed, "Bug")["color"] == "#6B6F76"


def test_a_retired_label_is_renamed_by_the_name_it_resolves_to(seed: Seed) -> None:
    """The catalogue scope reads retired rows: a rename of one is reachable by name."""
    _make(seed, "infra")
    uow = _uow(seed)
    try:
        retire_label(uow, RetireLabel(label="infra"), seed.actor)
        renamed = update_label(uow, UpdateLabel(label="infra", name="platform"), seed.actor)
    finally:
        uow.close()

    assert renamed.name == "platform"
    assert renamed.retired_at is not None
    assert _rows(seed, "SELECT id FROM label WHERE name = 'infra'") == []


# --- retire_label / restore_label ----------------------------------------------------------


def test_a_label_no_issue_wears_is_retired_without_deleting_the_row(seed: Seed) -> None:
    _make(seed, "infra")
    uow = _uow(seed)
    try:
        retired = retire_label(uow, RetireLabel(label="infra"), seed.actor)
    finally:
        uow.close()

    assert _label_named(seed, "infra")["retired_at"] is not None
    assert retired.retired_at is not None
    assert _count(seed, "label") == 1


def test_a_label_an_issue_wears_is_not_retired(seed: Seed) -> None:
    label_id = _make(seed, "Bug")
    _attach(seed, label_id)

    uow = _uow(seed)
    try:
        with pytest.raises(Conflict) as caught:
            retire_label(uow, RetireLabel(label="Bug"), seed.actor)
    finally:
        uow.close()

    assert caught.value.rule == "LABEL_IN_USE"
    assert _label_named(seed, "Bug")["retired_at"] is None


def test_a_label_worn_only_by_a_trashed_issue_is_still_in_use(seed: Seed) -> None:
    """The trash counts: a restored issue comes back wearing the chip."""
    label_id = _make(seed, "Bug")
    _attach(seed, label_id)
    _write(seed, "UPDATE issue SET trashed = 1 WHERE identifier = ?", (seed.issue.identifier,))

    uow = _uow(seed)
    try:
        with pytest.raises(Conflict) as caught:
            retire_label(uow, RetireLabel(label="Bug"), seed.actor)
    finally:
        uow.close()

    assert caught.value.rule == "LABEL_IN_USE"


def test_retiring_twice_is_a_conflict(seed: Seed) -> None:
    _make(seed, "infra")
    _retire(seed, "infra")

    uow = _uow(seed)
    try:
        with pytest.raises(Conflict) as caught:
            retire_label(uow, RetireLabel(label="infra"), seed.actor)
    finally:
        uow.close()

    assert caught.value.rule == "LABEL_RETIRED"
    assert _label_named(seed, "infra")["retired_at"] is not None


def test_the_undo_clears_retired_at_and_nothing_else(seed: Seed) -> None:
    _make(seed, "infra", color="#4EA7FC")
    _retire(seed, "infra")

    uow = _uow(seed)
    try:
        restored = restore_label(uow, RestoreLabel(label="infra"), seed.actor)
    finally:
        uow.close()

    row = _label_named(seed, "infra")
    assert row["retired_at"] is None
    assert row["color"] == "#4EA7FC"
    assert restored.retired_at is None


def test_the_undo_of_a_retire_that_never_happened_is_a_conflict(seed: Seed) -> None:
    _make(seed, "Bug")
    uow = _uow(seed)
    try:
        with pytest.raises(Conflict) as caught:
            restore_label(uow, RestoreLabel(label="Bug"), seed.actor)
    finally:
        uow.close()

    assert caught.value.rule == "LABEL_NOT_RETIRED"
    assert _label_named(seed, "Bug")["retired_at"] is None


def test_a_name_two_teams_share_is_ambiguous_on_the_catalogue_writes(seed: Seed) -> None:
    other = seed.other_team()
    _make(seed, "shared", team_id=seed.team.id)
    _make(seed, "shared", team_id=other.id)

    uow = _uow(seed)
    try:
        calls = (
            lambda: update_label(uow, UpdateLabel(label="shared", name="x"), seed.actor),
            lambda: retire_label(uow, RetireLabel(label="shared"), seed.actor),
            lambda: restore_label(uow, RestoreLabel(label="shared"), seed.actor),
        )
        for call in calls:
            with pytest.raises(Validation) as caught:
                call()
            assert (caught.value.rule, caught.value.field) == ("LABEL_AMBIGUOUS", "ref")
            assert other.key in caught.value.message
    finally:
        uow.close()


def test_a_name_two_teams_share_is_ambiguous_on_the_catalogue_read(seed: Seed) -> None:
    other = seed.other_team()
    _make(seed, "shared", team_id=seed.team.id)
    _make(seed, "shared", team_id=other.id)

    with pytest.raises(Validation) as caught:
        label_detail(Database(seed.path), "shared")

    assert (caught.value.rule, caught.value.field) == ("LABEL_AMBIGUOUS", "ref")


def test_ambiguity_is_judged_over_retired_rows_too(seed: Seed) -> None:
    """A retired row of one team and a live row of another is ambiguous, not a silent pick."""
    other = seed.other_team()
    _make(seed, "shared", team_id=seed.team.id)
    _make(seed, "shared", team_id=other.id)
    _write(
        seed,
        "UPDATE label SET retired_at = ? WHERE name = 'shared' AND team_id = ?",
        (RETIRED_AT, seed.team.id),
    )

    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as caught:
            retire_label(uow, RetireLabel(label="shared"), seed.actor)
    finally:
        uow.close()

    assert caught.value.rule == "LABEL_AMBIGUOUS"


def test_an_unknown_reference_is_not_found_by_name_and_by_ulid(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(NotFound) as by_name:
            update_label(uow, UpdateLabel(label="nope", name="x"), seed.actor)
        with pytest.raises(NotFound) as by_id:
            update_label(uow, UpdateLabel(label="01HZZZZZZZZZZZZZZZZZZZZZZZ", name="x"), seed.actor)
    finally:
        uow.close()

    assert (by_name.value.resource, by_name.value.field) == ("label", "label")
    assert by_id.value.resource == "label"


# --- list_labels / label_detail ------------------------------------------------------------


def test_the_listing_is_ordered_by_name_and_hides_retired_rows(seed: Seed) -> None:
    _make(seed, "zeta")
    _make(seed, "Alpha")
    _make(seed, "retired-one")
    _retire(seed, "retired-one")

    db = Database(seed.path)
    assert [label.name for label in list_labels(db)] == ["Alpha", "zeta"]
    assert [label.name for label in list_labels(db, include_retired=True)] == [
        "Alpha",
        "retired-one",
        "zeta",
    ]


def test_the_listing_of_a_team_is_its_own_labels_plus_the_workspace_ones(seed: Seed) -> None:
    other = seed.other_team()
    _make(seed, "workspace-one")
    _make(seed, "eng-one", team_id=seed.team.id)
    _make(seed, "ops-one", team_id=other.id)

    db = Database(seed.path)
    assert [label.name for label in list_labels(db)] == ["eng-one", "ops-one", "workspace-one"]
    assert [label.name for label in list_labels(db, team="ENG")] == ["eng-one", "workspace-one"]
    with pytest.raises(NotFound) as caught:
        list_labels(db, team="NOPE")
    assert caught.value.resource == "team"


def test_the_detail_reads_a_retired_row_and_a_team_label(seed: Seed) -> None:
    other = seed.other_team()
    label_id = _make(seed, "ops-one", team_id=other.id)
    _write(seed, "UPDATE label SET retired_at = ? WHERE id = ?", (RETIRED_AT, label_id))

    db = Database(seed.path)
    by_name = label_detail(db, "ops-one")
    by_id = label_detail(db, label_id)
    assert by_name.id == by_id.id == label_id
    assert by_name.retired_at is not None
    assert by_name.team_id == other.id


# --- add_label: the plain link -------------------------------------------------------------


def test_attaching_a_plain_label_writes_the_link_and_one_attach_row(seed: Seed) -> None:
    label_id = _make(seed, "infra")

    uow = _uow(seed)
    try:
        result = add_label(uow, AddLabel(issue=seed.issue.identifier, label=label_id), seed.actor)
    finally:
        uow.close()

    assert result.issue.identifier == seed.issue.identifier
    assert result.replaced is None
    assert _links(seed, seed.issue.identifier) == ["infra"]
    assert _history(seed, seed.issue.identifier) == [("label", None, "infra")]


def test_attaching_by_name_and_by_ulid_reach_the_same_row(seed: Seed) -> None:
    label_id = _make(seed, "Infra")

    uow = _uow(seed)
    try:
        add_label(uow, AddLabel(issue=seed.issue.identifier, label="INFRA"), seed.actor)
    finally:
        uow.close()

    assert _links(seed, seed.issue.identifier) == ["Infra"]
    assert _link_ids(seed) == [label_id]


def test_adding_the_same_label_twice_writes_nothing_and_reads_no_clock(seed: Seed) -> None:
    label_id = _make(seed, "infra")
    _attach(seed, label_id)
    history = _history(seed, seed.issue.identifier)

    class _NoClock:
        """A clock that fails the test if the idempotent path asks it for the time."""

        def now(self) -> datetime:  # pragma: no cover - it must never be called
            raise AssertionError("the idempotent add read the clock")

    uow = UnitOfWork(seed.path, clock=_NoClock(), idgen=seed.idgen)
    try:
        result = add_label(uow, AddLabel(issue=seed.issue.identifier, label=label_id), seed.actor)
    finally:
        uow.close()

    assert result.replaced is None
    assert _links(seed, seed.issue.identifier) == ["infra"]
    assert _history(seed, seed.issue.identifier) == history


# --- add_label: the singleSelect replacement ------------------------------------------------


def test_a_single_select_group_replaces_the_sibling_the_issue_carries(seed: Seed) -> None:
    group = _make(seed, "Severity", is_group=True, group_type="singleSelect")
    high = _make(seed, "High", parent_id=group)
    low = _make(seed, "Low", parent_id=group)
    _attach(seed, high)

    uow = _uow(seed)
    try:
        result = add_label(uow, AddLabel(issue=seed.issue.identifier, label=low), seed.actor)
    finally:
        uow.close()

    assert result.replaced is not None and result.replaced.name == "High"
    assert _links(seed, seed.issue.identifier) == ["Low"]
    # The displaced label keeps existing: the link is what went away, not the row.
    assert _label_named(seed, "High")["id"] == high
    # The detach row comes before the attach row, and both share the one `field`.
    assert _history(seed, seed.issue.identifier)[-2:] == [
        ("label", "High", None),
        ("label", None, "Low"),
    ]


def test_a_multi_select_group_keeps_every_child(seed: Seed) -> None:
    group = _make(seed, "Area", is_group=True, group_type="multiSelect")
    back = _make(seed, "backend", parent_id=group)
    front = _make(seed, "frontend", parent_id=group)
    _attach(seed, back)

    uow = _uow(seed)
    try:
        result = add_label(uow, AddLabel(issue=seed.issue.identifier, label=front), seed.actor)
    finally:
        uow.close()

    assert result.replaced is None
    assert _links(seed, seed.issue.identifier) == ["backend", "frontend"]


def test_re_adding_the_child_the_group_already_holds_is_a_no_op(seed: Seed) -> None:
    """The sibling set excludes the incoming label: a linked child is never its own replacement."""
    group = _make(seed, "Severity", is_group=True, group_type="singleSelect")
    low = _make(seed, "Low", parent_id=group)
    _attach(seed, low)
    history = _history(seed, seed.issue.identifier)

    uow = _uow(seed)
    try:
        result = add_label(uow, AddLabel(issue=seed.issue.identifier, label=low), seed.actor)
    finally:
        uow.close()

    assert result.replaced is None
    assert _links(seed, seed.issue.identifier) == ["Low"]
    assert _history(seed, seed.issue.identifier) == history


def test_an_inconsistent_set_is_cleaned_and_names_the_first_sibling(seed: Seed) -> None:
    """Two siblings are only reachable by editing the database by hand; the write cleans it."""
    group = _make(seed, "Severity", is_group=True, group_type="singleSelect")
    high = _make(seed, "High", parent_id=group)
    low = _make(seed, "Low", parent_id=group)
    for label_id in (high, low):
        _write(
            seed,
            "INSERT INTO issue_label (issue_id, label_id) VALUES (?, ?)",
            (seed.issue.id, label_id),
        )

    uow = _uow(seed)
    try:
        result = add_label(uow, AddLabel(issue=seed.issue.identifier, label=low), seed.actor)
    finally:
        uow.close()

    assert result.replaced is not None and result.replaced.name == "High"
    assert _links(seed, seed.issue.identifier) == ["Low"]
    # The attach row is skipped: the label was already linked.
    assert _history(seed, seed.issue.identifier) == [("label", "High", None)]


def test_the_first_removed_sibling_is_the_first_by_name(seed: Seed) -> None:
    group = _make(seed, "Severity", is_group=True, group_type="singleSelect")
    zeta = _make(seed, "Zeta", parent_id=group)
    alpha = _make(seed, "Alpha", parent_id=group)
    incoming = _make(seed, "Incoming", parent_id=group)
    for label_id in (zeta, alpha):
        _write(
            seed,
            "INSERT INTO issue_label (issue_id, label_id) VALUES (?, ?)",
            (seed.issue.id, label_id),
        )

    uow = _uow(seed)
    try:
        result = add_label(uow, AddLabel(issue=seed.issue.identifier, label=incoming), seed.actor)
    finally:
        uow.close()

    assert result.replaced is not None and result.replaced.name == "Alpha"
    assert _links(seed, seed.issue.identifier) == ["Incoming"]
    assert [row[1] for row in _history(seed, seed.issue.identifier)] == ["Alpha", "Zeta", None]


def test_the_group_type_is_read_from_the_stored_row(seed: Seed) -> None:
    """A group is `singleSelect` because the row says so, not because the caller said so."""
    group = _make(seed, "Severity", is_group=True, group_type="multiSelect")
    low = _make(seed, "Low", parent_id=group)
    high = _make(seed, "High", parent_id=group)
    _write(seed, "UPDATE label SET group_type = 'singleSelect' WHERE id = ?", (group,))
    _attach(seed, low)

    uow = _uow(seed)
    try:
        result = add_label(uow, AddLabel(issue=seed.issue.identifier, label=high), seed.actor)
    finally:
        uow.close()

    assert result.replaced is not None and result.replaced.name == "Low"
    assert _links(seed, seed.issue.identifier) == ["High"]


def test_a_retired_label_is_not_attachable_and_replaces_nothing(seed: Seed) -> None:
    group = _make(seed, "Severity", is_group=True, group_type="singleSelect")
    high = _make(seed, "High", parent_id=group)
    old = _make(seed, "Old", parent_id=group)
    _write(seed, "UPDATE label SET retired_at = ? WHERE id = ?", (RETIRED_AT, old))
    _attach(seed, high)

    uow = _uow(seed)
    try:
        with pytest.raises(Conflict) as caught:
            add_label(uow, AddLabel(issue=seed.issue.identifier, label=old), seed.actor)
    finally:
        uow.close()

    assert caught.value.rule == "LABEL_RETIRED"
    assert _links(seed, seed.issue.identifier) == ["High"]


def test_a_label_of_another_team_is_a_validation_error(seed: Seed) -> None:
    other = seed.other_team()
    label_id = _make(seed, "ops-one", team_id=other.id)

    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as caught:
            add_label(uow, AddLabel(issue=seed.issue.identifier, label=label_id), seed.actor)
    finally:
        uow.close()

    assert (caught.value.rule, caught.value.field) == ("LABEL_TEAM_MISMATCH", "label")
    assert _link_ids(seed) == []


def test_the_team_name_wins_over_the_workspace_one(seed: Seed) -> None:
    """The issue's team is the tie-breaker: no name is ambiguous on this verb."""
    other = seed.other_team()
    _make(seed, "Bug")
    team_bug = _make(seed, "Bug", team_id=seed.team.id)
    _make(seed, "Bug", team_id=other.id)

    uow = _uow(seed)
    try:
        add_label(uow, AddLabel(issue=seed.issue.identifier, label="bug"), seed.actor)
    finally:
        uow.close()

    assert _link_ids(seed) == [team_bug]


def test_a_name_that_resolves_nowhere_is_a_validation_error(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as caught:
            add_label(uow, AddLabel(issue=seed.issue.identifier, label="infra"), seed.actor)
    finally:
        uow.close()

    assert (caught.value.rule, caught.value.field) == ("LABEL_NOT_FOUND", "label")


def test_a_ulid_that_exists_nowhere_is_not_found(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(NotFound) as caught:
            add_label(
                uow,
                AddLabel(issue=seed.issue.identifier, label="01HZZZZZZZZZZZZZZZZZZZZZZZ"),
                seed.actor,
            )
    finally:
        uow.close()

    assert caught.value.resource == "label"


def test_create_if_missing_creates_a_label_of_the_issues_team(seed: Seed) -> None:
    other = seed.other_team()

    uow = _uow(seed)
    try:
        add_label(
            uow,
            AddLabel(
                issue=seed.issue.identifier,
                label="infra",
                create_if_missing=True,
                color="#5E6AD2",
            ),
            seed.actor,
        )
    finally:
        uow.close()

    row = _label_named(seed, "infra", seed.team.id)
    assert row["team_id"] == seed.team.id
    assert row["color"] == "#5E6AD2"
    assert (row["is_group"], row["parent_id"], row["description"]) == (0, None, None)
    assert _rows(seed, "SELECT id FROM label WHERE team_id = ?", (other.id,)) == []
    assert _links(seed, seed.issue.identifier) == ["infra"]


def test_create_if_missing_uses_a_name_that_already_resolves(seed: Seed) -> None:
    workspace_label = _make(seed, "infra")

    uow = _uow(seed)
    try:
        add_label(
            uow,
            AddLabel(
                issue=seed.issue.identifier,
                label="infra",
                create_if_missing=True,
                color="#5E6AD2",
            ),
            seed.actor,
        )
    finally:
        uow.close()

    assert _count(seed, "label") == 1
    assert _link_ids(seed) == [workspace_label]


def test_create_if_missing_refuses_a_bad_color(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as caught:
            add_label(
                uow,
                AddLabel(
                    issue=seed.issue.identifier,
                    label="infra",
                    create_if_missing=True,
                    color="blue",
                ),
                seed.actor,
            )
    finally:
        uow.close()

    assert caught.value.rule == "COLOR_FORMAT"
    assert _count(seed, "label") == 0


def test_a_trashed_issue_is_refused_before_the_label_is_read(seed: Seed) -> None:
    _write(seed, "UPDATE issue SET trashed = 1 WHERE identifier = ?", (seed.issue.identifier,))

    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as caught:
            add_label(uow, AddLabel(issue=seed.issue.identifier, label="nope"), seed.actor)
    finally:
        uow.close()

    assert caught.value.rule == "ISSUE_TRASHED"


def test_an_archived_issue_is_refused_before_the_label_is_read(seed: Seed) -> None:
    _write(
        seed,
        "UPDATE issue SET archived_at = ? WHERE identifier = ?",
        (RETIRED_AT, seed.issue.identifier),
    )

    uow = _uow(seed)
    try:
        with pytest.raises(InvalidTransition) as caught:
            add_label(uow, AddLabel(issue=seed.issue.identifier, label="nope"), seed.actor)
    finally:
        uow.close()

    assert (caught.value.rule, caught.value.field) == ("ISSUE_ARCHIVED", "ref")


def test_an_unknown_issue_is_not_found(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(NotFound) as caught:
            add_label(uow, AddLabel(issue="ENG-999", label="infra"), seed.actor)
    finally:
        uow.close()

    assert caught.value.resource == "issue"


# --- remove_label --------------------------------------------------------------------------


def test_detaching_writes_one_detach_row_and_keeps_a_sibling(seed: Seed) -> None:
    group = _make(seed, "Area", is_group=True, group_type="multiSelect")
    back = _make(seed, "backend", parent_id=group)
    front = _make(seed, "frontend", parent_id=group)
    _attach(seed, back)
    _attach(seed, front)

    uow = _uow(seed)
    try:
        issue = remove_label(
            uow, RemoveLabel(issue=seed.issue.identifier, label="backend"), seed.actor
        )
    finally:
        uow.close()

    assert issue.identifier == seed.issue.identifier
    assert _links(seed, seed.issue.identifier) == ["frontend"]
    assert _history(seed, seed.issue.identifier)[-1] == ("label", "backend", None)


def test_detaching_a_label_the_issue_does_not_wear_is_a_missing_link(seed: Seed) -> None:
    bug = _make(seed, "Bug")
    _make(seed, "infra")
    _attach(seed, bug)

    uow = _uow(seed)
    try:
        with pytest.raises(NotFound) as caught:
            remove_label(uow, RemoveLabel(issue=seed.issue.identifier, label="infra"), seed.actor)
    finally:
        uow.close()

    assert (caught.value.resource, caught.value.field) == ("label_link", "label_link")
    assert _links(seed, seed.issue.identifier) == ["Bug"]


def test_detaching_a_name_that_exists_nowhere_is_not_found(seed: Seed) -> None:
    uow = _uow(seed)
    try:
        with pytest.raises(NotFound) as caught:
            remove_label(uow, RemoveLabel(issue=seed.issue.identifier, label="infra"), seed.actor)
    finally:
        uow.close()

    assert caught.value.resource == "label"


def test_a_retired_label_stays_detachable(seed: Seed) -> None:
    """Retiring must not strand the chips it left behind."""
    label_id = _make(seed, "Bug")
    _attach(seed, label_id)
    _write(seed, "UPDATE label SET retired_at = ? WHERE id = ?", (RETIRED_AT, label_id))

    uow = _uow(seed)
    try:
        remove_label(uow, RemoveLabel(issue=seed.issue.identifier, label="Bug"), seed.actor)
    finally:
        uow.close()

    assert _links(seed, seed.issue.identifier) == []


def test_a_trashed_issue_takes_no_detach(seed: Seed) -> None:
    label_id = _make(seed, "Bug")
    _attach(seed, label_id)
    _write(seed, "UPDATE issue SET trashed = 1 WHERE identifier = ?", (seed.issue.identifier,))

    uow = _uow(seed)
    try:
        with pytest.raises(Validation) as caught:
            remove_label(uow, RemoveLabel(issue=seed.issue.identifier, label="Bug"), seed.actor)
    finally:
        uow.close()

    assert caught.value.rule == "ISSUE_TRASHED"
    assert _links(seed, seed.issue.identifier) == ["Bug"]
