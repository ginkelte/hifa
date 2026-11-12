"""Unit tests of the pure view models.

Nothing here touches SQLite or Jinja: every derived value the templates print is computed by
`hifa.web.readmodels`, so this is where the formatting and the "never guess an author" rule are
pinned.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest

from hifa.adapters.sqlite.reads import IssueListItem, IssuePage, LabelRef
from hifa.web.readmodels import (
    author_name,
    build_issue_vm,
    build_list_vm,
    build_row_vm,
    due_label,
    initials,
    is_overdue,
    priority_glyph,
)

TODAY = date(2026, 11, 11)
NOW = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def make_item(**overrides: object) -> IssueListItem:
    """An `IssueListItem` with sane defaults, so each test states only what it cares about."""
    base: dict[str, object] = {
        "id": "01ISSUE0000000000000000001",
        "identifier": "ENG-1",
        "title": "Fix SSO login",
        "priority": 1,
        "priority_label": "Urgent",
        "state_id": "STATE-TODO",
        "state_name": "Todo",
        "state_type": "unstarted",
        "team_key": "ENG",
        "assignee_id": None,
        "assignee_name": None,
        "project_id": None,
        "cycle_id": None,
        "due_date": None,
        "estimate": None,
        "parent_id": None,
        "updated_at": NOW,
        "is_open": True,
        "labels": (),
    }
    base.update(overrides)
    return IssueListItem(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("priority", "glyph"), [(0, "\u2013"), (1, "U"), (2, "H"), (3, "M"), (4, "L")]
)
def test_priority_glyph_covers_the_whole_scale(priority: int, glyph: str) -> None:
    assert priority_glyph(priority) == glyph


def test_priority_glyph_falls_back_to_dash_out_of_range() -> None:
    assert priority_glyph(9) == "\u2013"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (date(2026, 11, 10), True),
        (date(2026, 11, 11), False),
        (date(2026, 11, 12), False),
        (None, False),
    ],
)
def test_is_overdue(value: date | None, expected: bool) -> None:
    assert is_overdue(value, TODAY) is expected


def test_due_label_is_the_short_month_and_day() -> None:
    assert due_label(date(2026, 11, 20)) == "Nov 20"
    assert due_label(date(2026, 1, 1)) == "Jan 1"
    assert due_label(None) is None


@pytest.mark.parametrize(
    ("name", "expected"),
    [("Ginkelte", "GI"), ("ada lovelace", "AD"), ("  ", None), (None, None)],
)
def test_initials_are_the_first_two_alphanumeric_characters(
    name: str | None, expected: str | None
) -> None:
    assert initials(name) == expected


def test_row_vm_sorts_labels_and_formats_the_due_date() -> None:
    item = make_item(
        assignee_name="Ginkelte",
        due_date=date(2026, 11, 20),
        labels=(
            LabelRef(id="l2", name="zeta", color="#000", is_group=False, parent_id=None),
            LabelRef(id="l1", name="Bug", color="#f00", is_group=False, parent_id=None),
        ),
    )
    row = build_row_vm(item, today=TODAY)
    assert [label["name"] for label in row["labels"]] == ["Bug", "zeta"]
    assert row["due_label"] == "Nov 20"
    assert row["is_overdue"] is False
    assert row["assignee_initials"] == "GI"
    assert row["has_assignee"] is True
    assert row["url"] == "/ENG/issue/ENG-1"


def _page(items: tuple[IssueListItem, ...], *, cursor: str | None, has_more: bool) -> IssuePage:
    return IssuePage(items=items, next_cursor=cursor, has_more=has_more)


def _list_vm(page: IssuePage) -> dict[str, object]:
    return build_list_vm(
        page,
        team={"key": "ENG", "name": "Engineering", "icon": "", "color": "", "url": "/ENG/board"},
        filters={"state": None, "assignee": None, "label": None, "cycle": None, "project": None},
        sort="manual",
        limit=2,
        offset=0,
        counts={"open": 3, "urgent": 1, "total": 3},
        today=TODAY,
        can_write=True,
    )


def test_next_url_carries_the_cursor_and_the_limit() -> None:
    vm = _list_vm(_page((make_item(),), cursor="CURSOR-1", has_more=True))
    assert vm["next_url"] == "/ENG/list?limit=2&cursor=CURSOR-1"
    assert vm["has_more"] is True


def test_next_url_is_none_on_the_last_page() -> None:
    vm = _list_vm(_page((make_item(),), cursor=None, has_more=False))
    assert vm["next_url"] is None
    assert vm["has_more"] is False


def test_author_name_is_the_real_member_or_none_never_a_guess() -> None:
    members = {"u-1": "Ginkelte", "u-2": "Mo"}
    assert author_name(members, "u-1") == "Ginkelte"
    assert author_name(members, None) is None
    assert author_name(members, "u-missing") is None


def test_author_of_a_comment_is_not_the_assignee() -> None:
    """R20: the author comes from `members[author_id]`, never from the assignee of the issue."""
    item = make_item(assignee_id="u-owner", assignee_name="Alice")
    detail = SimpleNamespace(
        issue=item,
        description="hello",
        team=SimpleNamespace(key="ENG"),
        state=None,
        assignee=None,
        creator=None,
        project=None,
        cycle=None,
        parent=None,
        children=(),
        relations=(),
        comments=(
            SimpleNamespace(
                id="c-1",
                author_id="u-author",
                body="looks good",
                created_at=NOW - timedelta(hours=2),
                edited_at=None,
                is_resolved=False,
            ),
        ),
        history=(
            SimpleNamespace(
                field="state",
                actor_id="u-author",
                old_value="Todo",
                new_value="Done",
                created_at=NOW - timedelta(hours=1),
            ),
        ),
        attachments=(),
        members={"u-owner": "Alice", "u-author": "Bob"},
    )
    vm = build_issue_vm(detail, today=TODAY, now=NOW)  # type: ignore[arg-type]
    assert vm["assignee_name"] == "Alice"
    assert vm["comments"][0]["author_name"] == "Bob"
    assert vm["comments"][0]["author_name"] != vm["assignee_name"]
    assert vm["history"][0]["actor_name"] == "Bob"
    assert vm["history"][0]["old_label"] == "Todo"
