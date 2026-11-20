"""`history_rows`: one row, one shape, and the wording of every field a writer stores."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta

import pytest

from hifa.app.history import (
    DUE_DATE_FORMAT,
    PRIORITY_LABELS,
    HistoryPage,
    HistoryRow,
    LastActivity,
    history_rows,
    wire_instant,
)
from hifa.domain.entities import IssueHistory

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=UTC)
STATES = {"S1": "Todo", "S2": "Done"}
MEMBERS = {"M1": "Beto Díaz", "M2": "Ana Pérez"}
PARENTS = {"P1": "ENG-8", "P2": "ENG-9"}


def _row(
    row_id: int,
    field: str,
    old: str | None,
    new: str | None,
    *,
    actor: str | None = "M1",
    at: datetime = NOW,
) -> IssueHistory:
    return IssueHistory(
        id=row_id,
        issue_id="is1",
        actor_id=actor,
        field=field,
        old_value=old,
        new_value=new,
        created_at=at,
    )


def _page(*rows: IssueHistory, now: datetime | None = NOW) -> HistoryPage:
    return history_rows(
        rows,
        states=STATES,
        members=MEMBERS,
        parents=PARENTS,
        state_colors={"S2": "#F2994A"},
        now=now,
    )


#: Every field a writer can store, with the one shape its producer writes and the wording the
#: grammar gives it.
SENTENCES: tuple[tuple[str, str | None, str | None, str], ...] = (
    ("created", None, None, "created the issue"),
    ("state_id", "S1", "S2", "moved from Todo to Done"),
    ("title", "Old", "New", "changed the title to \u201cNew\u201d"),
    ("description", "first body", "second body", "edited the description"),
    ("priority", None, "2", "set priority to High"),
    ("priority", "2", None, "cleared the priority"),
    ("priority", "2", "4", "changed the priority from High to Low"),
    ("assignee_id", None, "M2", "assigned to Ana P\u00e9rez"),
    ("assignee_id", "M2", None, "unassigned"),
    ("assignee_id", "M2", "M1", "reassigned from Ana P\u00e9rez to Beto D\u00edaz"),
    ("parent_id", None, "P1", "moved under ENG-8"),
    ("parent_id", "P1", None, "removed it from ENG-8"),
    ("parent_id", "P1", "P2", "moved it from ENG-8 to ENG-9"),
    ("label", None, "bug", "added the label bug"),
    ("label", "bug", None, "removed the label bug"),
    ("relation", None, "blocks HIFA-33", "marked it as blocking HIFA-33"),
    ("relation", "blocks HIFA-33", None, "removed the blocking relation to HIFA-33"),
    ("estimate", None, "3.0", "set the estimate to 3"),
    ("estimate", "3.0", None, "cleared the estimate"),
    ("due_date", None, "2026-11-20", "set the due date to 20 Nov 2026"),
    ("due_date", "2026-11-20", None, "removed the due date"),
    (
        "due_date",
        "2026-11-20",
        "2026-11-21",
        "moved the due date from 20 Nov 2026 to 21 Nov 2026",
    ),
    ("branch_name", "eng-1-old", "eng-1-new", "changed the branch name"),
    ("board_order", "1024.0", "8192.0", "reordered it in the column"),
    ("started_at", None, "2026-11-08T00:00:00Z", "started work"),
    ("completed_at", None, "2026-11-08T00:00:00Z", "completed it"),
    ("completed_at", "2026-11-08T00:00:00Z", None, "cleared the completion"),
    ("canceled_at", None, "2026-11-08T00:00:00Z", "canceled it"),
    ("canceled_at", "2026-11-08T00:00:00Z", None, "cleared the cancellation"),
    ("archived_at", None, "2026-11-08T00:00:00Z", "archived the issue"),
    ("archived_at", "2026-11-08T00:00:00Z", None, "restored the issue"),
    ("trashed", "0", "1", "deleted the issue"),
    ("trashed", "1", "0", "restored it from the trash"),
)


#: Every field `issue_history` can hold a row for — one row per changed field, written by the
#: issue writers. The three link columns are absent because nothing writes them
#: yet (GAP-08, HIFA-90): they render the fallback.
WRITER_FIELDS: frozenset[str] = frozenset(
    {
        "created",
        "state_id",
        "title",
        "description",
        "priority",
        "assignee_id",
        "parent_id",
        "label",
        "relation",
        "estimate",
        "due_date",
        "branch_name",
        "board_order",
        "started_at",
        "completed_at",
        "canceled_at",
        "archived_at",
        "trashed",
    }
)


def test_every_field_a_writer_stores_is_answered_by_the_grammar() -> None:
    """The table lists the fields; the grammar has to answer each one with wording of its own.

    This is the coverage assertion, and it asks `history_rows` — the code — for every sentence
    instead of comparing the table to itself: a branch deleted from `_sentence` leaves its field
    with no shape of its own and the row falls back to `changed <field>`, which fails here.
    """
    assert {field for field, _, _, _ in SENTENCES} == WRITER_FIELDS

    for field in sorted(WRITER_FIELDS):
        wordings = [
            _page(_row(1, candidate, old, new)).rows[0].sentence
            for candidate, old, new, _ in SENTENCES
            if candidate == field
        ]

        assert wordings, f"{field} has no documented shape"
        assert f"changed {field}" not in wordings, (field, wordings)


@pytest.mark.parametrize(("field", "old", "new", "sentence"), SENTENCES)
def test_the_sentence_of_every_documented_direction(
    field: str, old: str | None, new: str | None, sentence: str
) -> None:
    page = _page(_row(1, field, old, new))

    assert page.rows[0].sentence == sentence


def test_an_unknown_field_falls_back_to_its_own_name() -> None:
    page = _page(_row(1, "mystery", "a", "b"))

    assert page.rows[0].sentence == "changed mystery"


def test_the_three_link_fields_fall_back_until_their_writers_land() -> None:
    """No writer produces a value for these three (GAP-08, HIFA-90), so no sentence claims one."""
    for field in ("project_id", "milestone_id", "cycle_id"):
        assert _page(_row(1, field, "X", "Y")).rows[0].sentence == f"changed {field}"


def test_a_state_that_is_gone_renders_its_kind_and_not_the_key() -> None:
    page = _page(_row(1, "state_id", "S1", "gone"))

    assert page.rows[0].new_label == "state (deleted)"
    assert page.rows[0].sentence == "moved from Todo to state (deleted)"


def test_a_member_and_a_parent_that_are_gone_render_their_kind() -> None:
    assignee = _page(_row(1, "assignee_id", "M2", "gone")).rows[0]
    parent = _page(_row(2, "parent_id", "gone", "P1")).rows[0]

    assert assignee.new_label == "member (deleted)"
    assert parent.old_label == "issue (deleted)"


def test_an_actor_the_map_does_not_carry_is_someone() -> None:
    page = _page(_row(1, "title", "Old", "New", actor="gone"), _row(2, "created", None, None))

    assert [row.actor_label for row in page.rows] == ["someone", "Beto D\u00edaz"]


def test_a_row_without_an_actor_is_someone() -> None:
    assert _page(_row(1, "created", None, None, actor=None)).rows[0].actor_label == "someone"


def test_the_row_keeps_the_stored_pair_as_it_is() -> None:
    page = _page(_row(1, "priority", "2", "4"))

    row = page.rows[0]
    assert (row.old_value, row.new_value) == ("2", "4")
    assert (row.old_label, row.new_label) == ("High", "Low")


def test_a_description_row_carries_no_label() -> None:
    row = _page(_row(1, "description", "a", "b")).rows[0]

    assert (row.old_label, row.new_label) == (None, None)


def test_the_estimate_label_cuts_a_trailing_zero_and_nothing_else() -> None:
    row = _page(_row(1, "estimate", "3.0", "5.5")).rows[0]

    assert (row.old_label, row.new_label) == ("3", "5.5")


def test_the_due_date_label_uses_the_declared_format() -> None:
    row = _page(_row(1, "due_date", None, "2026-11-20")).rows[0]

    assert row.new_label == datetime(2026, 11, 20).strftime(DUE_DATE_FORMAT)


def test_a_caller_may_hand_its_own_word_maps() -> None:
    page = history_rows(
        [_row(1, "priority", None, "2"), _row(2, "due_date", None, "2026-11-20")],
        states=STATES,
        members=MEMBERS,
        state_colors={"S2": "#F2994A"},
        priorities={2: "Alta"},
        due_dates={"2026-11-20": "20/11/2026"},
    )

    assert [row.sentence for row in page.rows] == [
        "set priority to Alta",
        "set the due date to 20/11/2026",
    ]


def test_the_priority_labels_are_the_five_of_the_view() -> None:
    assert PRIORITY_LABELS == {
        0: "No priority",
        1: "Urgent",
        2: "High",
        3: "Medium",
        4: "Low",
    }


def test_the_dot_colour_is_the_state_row_and_nothing_else() -> None:
    state = _page(_row(1, "state_id", "S1", "S2")).rows[0]
    title = _page(_row(2, "title", "Old", "New")).rows[0]

    assert state.dot_color == "#F2994A"
    assert title.dot_color is None


def test_the_page_sorts_oldest_first_whatever_order_it_receives() -> None:
    rows = [
        _row(3, "title", "c", "d", at=NOW + timedelta(minutes=2)),
        _row(1, "created", None, None, at=NOW),
        _row(2, "title", "a", "b", at=NOW + timedelta(minutes=1)),
    ]

    page = _page(*rows)

    assert [row.id for row in page.rows] == [1, 2, 3]


def test_the_id_breaks_a_tie_of_the_instant() -> None:
    page = _page(_row(2, "title", "a", "b"), _row(1, "created", None, None))

    assert [row.id for row in page.rows] == [1, 2]


def test_the_window_size_and_the_floor_of_a_full_window() -> None:
    short = _page(*[_row(index, "title", "a", "b") for index in range(1, 100)])
    full = _page(*[_row(index, "title", "a", "b") for index in range(1, 101)])

    assert (short.window_size, short.truncated) == (99, False)
    assert (full.window_size, full.truncated) == (100, True)


def test_an_empty_page_has_no_last_activity() -> None:
    page = _page()

    assert page.rows == []
    assert page.window_size == 0
    assert page.truncated is False
    assert page.last_activity is None


def test_the_last_activity_is_the_newest_row() -> None:
    page = _page(
        _row(1, "created", None, None),
        _row(2, "state_id", "S1", "S2"),
    )

    assert page.last_activity == LastActivity(
        text="Beto D\u00edaz moved from Todo to Done \u00b7 just now", row_id=2
    )


def test_a_caller_without_a_clock_gets_no_summary_and_not_half_of_one() -> None:
    """The line's shape needs the stamp, so the whole line is what a clock buys (P7)."""
    page = _page(_row(1, "state_id", "S1", "S2"), now=None)

    assert page.rows[0].sentence == "moved from Todo to Done"
    assert page.last_activity is None


def test_the_last_activity_adds_the_relative_stamp_when_the_clock_is_known() -> None:
    page = history_rows(
        [_row(1, "state_id", "S1", "S2", at=NOW)],
        states=STATES,
        members=MEMBERS,
        state_colors={},
        now=NOW + timedelta(hours=2),
    )

    assert page.last_activity == LastActivity(
        text="Beto D\u00edaz moved from Todo to Done \u00b7 2 h ago", row_id=1
    )


def test_the_row_has_eleven_fields() -> None:
    assert [field.name for field in fields(HistoryRow)] == [
        "id",
        "field",
        "old_value",
        "new_value",
        "old_label",
        "new_label",
        "actor_id",
        "actor_label",
        "sentence",
        "dot_color",
        "created_at",
    ]


def test_the_wire_instant_is_seconds_utc_and_z() -> None:
    assert wire_instant(datetime(2026, 11, 11, 18, 35, 35, tzinfo=UTC)) == "2026-11-11T18:35:35Z"


def test_the_wire_instant_converts_an_offset_to_utc() -> None:
    from datetime import timezone

    moment = datetime(2026, 11, 11, 15, 35, 35, tzinfo=timezone(timedelta(hours=-3)))

    assert wire_instant(moment) == "2026-11-11T18:35:35Z"
