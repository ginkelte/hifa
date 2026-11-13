"""`ListFilters`, the canonical cursor codec and the sort allowlist."""

from __future__ import annotations

import base64

import pytest

from hifa.adapters.sqlite.reads import (
    SORT_KEYS,
    IssuePage,
    ListFilters,
    decode_cursor,
    encode_cursor,
    sort_token,
)
from hifa.domain.errors import Validation

#: Fake ULID of the right shape (26 characters, uppercase Crockford-ish alphabet).
ULID_A = "01HIFA" + "0" * 19 + "A"


def _payload(raw: bytes) -> str:
    """Unpadded base64url of an arbitrary payload: the transport of a hand-made token."""
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def test_the_fake_ulid_has_the_expected_shape() -> None:
    assert len(ULID_A) == 26


def test_encode_cursor_is_base64url_without_padding() -> None:
    cursor = encode_cursor("due:desc", ULID_A)

    assert "=" not in cursor
    assert "+" not in cursor
    assert "/" not in cursor
    unpacked = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)).decode()
    assert unpacked == f"due:desc|{ULID_A}"


@pytest.mark.parametrize("order", sorted(SORT_KEYS))
@pytest.mark.parametrize(("reverse", "direction"), [(False, "asc"), (True, "desc")])
def test_cursor_round_trip_over_every_order_and_direction(
    order: str, reverse: bool, direction: str
) -> None:
    token = sort_token(order, reverse)

    assert token == f"{order}:{direction}"
    assert decode_cursor(encode_cursor(token, ULID_A)) == (token, ULID_A)


def test_direction_token_is_lowercase_while_the_sql_keyword_is_not() -> None:
    assert sort_token("due", True) == "due:desc"
    assert sort_token("due", False) == "due:asc"
    assert "DESC" not in sort_token("due", True)


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "   ",
        "no-es-un-cursor",
        encode_cursor("due:asc", ULID_A) + "=",
        _payload(f"due:DESC|{ULID_A}".encode()),
        _payload(f"name:asc|{ULID_A}".encode()),
        _payload(f"due|{ULID_A}".encode()),
        _payload(f"due:asc|{ULID_A}|EXTRA".encode()),
        _payload(f"|{ULID_A}".encode()),
        _payload(b"due:asc|"),
        _payload(b"due:asc|id"),
        _payload(f"due:asc|{ULID_A.lower()}".encode()),
        _payload(b"due:asc"),
    ],
)
def test_every_malformed_cursor_is_invalid_cursor(cursor: str) -> None:
    with pytest.raises(Validation) as exc:
        decode_cursor(cursor)

    assert exc.value.rule == "INVALID_CURSOR"


def test_cursor_from_another_order_is_not_this_ordering() -> None:
    token, _ = decode_cursor(encode_cursor("created:asc", ULID_A))

    assert token != sort_token("manual", False)


def test_default_filters_are_the_spec_ones() -> None:
    filters = ListFilters(team_id="tm")

    assert filters.limit == 50
    assert filters.order == "manual"
    assert filters.offset == 0
    assert filters.include_archived is False
    assert filters.include_trashed is False
    assert filters.cursor is None
    assert filters.label_ids == ()


def test_the_additive_filters_default_to_no_filter() -> None:
    filters = ListFilters(team_id="tm")

    assert filters.state_ids == ()
    assert filters.state_types == ()
    assert filters.priorities == ()
    assert filters.is_open is None
    assert filters.no_cycle is False
    assert filters.archived_only is False
    assert filters.trashed_only is False
    assert filters.reverse is False


def test_the_single_and_repeated_filter_fields_coexist() -> None:
    filters = ListFilters(
        team_id="tm",
        state_id="s1",
        state_ids=("s2",),
        state_type="started",
        state_types=("unstarted",),
        priority=2,
        priorities=(4,),
        is_open=True,
        no_cycle=True,
        archived_only=True,
        trashed_only=True,
        reverse=True,
    )

    assert filters.state_ids == ("s2",)
    assert filters.state_types == ("unstarted",)
    assert filters.priorities == (4,)
    assert filters.is_open is True
    assert filters.no_cycle is True
    assert filters.archived_only is True
    assert filters.trashed_only is True
    assert filters.reverse is True


def test_archived_only_wins_over_include_archived_without_raising() -> None:
    filters = ListFilters(team_id="tm", include_archived=True, archived_only=True)

    assert filters.archived_only is True


def test_trashed_only_wins_over_include_trashed_without_raising() -> None:
    filters = ListFilters(team_id="tm", include_trashed=True, trashed_only=True)

    assert filters.trashed_only is True


@pytest.mark.parametrize("value", [1, 2, 3, 4, 0])
def test_priorities_in_range_are_accepted(value: int) -> None:
    assert ListFilters(team_id="tm", priorities=(value,)).priorities == (value,)


@pytest.mark.parametrize("value", [-1, 5, 9])
def test_priority_out_of_range_is_priority_out_of_range(value: int) -> None:
    with pytest.raises(Validation) as exc:
        ListFilters(team_id="tm", priorities=(value,))

    assert exc.value.rule == "PRIORITY_OUT_OF_RANGE"


@pytest.mark.parametrize("limit", [0, -1, 201, 1000])
def test_limit_out_of_range_is_limit_range(limit: int) -> None:
    with pytest.raises(Validation) as exc:
        ListFilters(team_id="tm", limit=limit)

    assert exc.value.rule == "LIMIT_RANGE"


@pytest.mark.parametrize("limit", [1, 50, 200])
def test_limit_in_range_is_accepted(limit: int) -> None:
    assert ListFilters(team_id="tm", limit=limit).limit == limit


@pytest.mark.parametrize("offset", [-1, 501])
def test_offset_out_of_range_is_offset_range(offset: int) -> None:
    with pytest.raises(Validation) as exc:
        ListFilters(team_id="tm", offset=offset)

    assert exc.value.rule == "OFFSET_RANGE"


def test_unknown_order_is_order_invalid() -> None:
    with pytest.raises(Validation) as exc:
        ListFilters(team_id="tm", order="name")  # type: ignore[arg-type]

    assert exc.value.rule == "ORDER_INVALID"


def test_sort_keys_are_the_five_from_the_table() -> None:
    assert set(SORT_KEYS) == {"manual", "created", "updated", "priority", "due"}
    assert SORT_KEYS["manual"] == "i.sort_order"
    assert SORT_KEYS["created"] == "i.created_at"
    assert SORT_KEYS["updated"] == "i.updated_at"


def test_absence_is_null_in_the_key_and_never_a_sentinel() -> None:
    assert SORT_KEYS["due"] == "i.due_date"
    assert SORT_KEYS["priority"] == "CASE i.priority WHEN 0 THEN NULL ELSE i.priority END"
    assert "9999" not in SORT_KEYS["due"]
    assert "THEN 5" not in SORT_KEYS["priority"]


def test_issue_page_has_the_spec_envelope() -> None:
    page = IssuePage(items=(), next_cursor=None, has_more=False)

    assert page.items == ()
    assert page.has_more is False
