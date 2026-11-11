"""`ListFilters`, keyset cursors and the sort allowlist (D-011, SPEC-030 §2.6)."""

from __future__ import annotations

import base64

import pytest

from hifa.adapters.sqlite.reads import (
    SORT_KEYS,
    IssuePage,
    ListFilters,
    decode_cursor,
    encode_cursor,
)
from hifa.domain.errors import Validation


def test_encode_cursor_is_base64url_without_padding() -> None:
    cursor = encode_cursor("1.5", "01HIFA0000000000000000001")

    assert "=" not in cursor
    assert "+" not in cursor
    assert "/" not in cursor


def test_cursor_round_trip() -> None:
    cursor = encode_cursor("2026-11-08T12:00:00Z", "01HIFA0000000000000000002")

    assert decode_cursor(cursor) == ("2026-11-08T12:00:00Z", "01HIFA0000000000000000002")


def test_cursor_round_trip_preserves_sort_key_pipes() -> None:
    cursor = encode_cursor("a|b", "id")

    assert decode_cursor(cursor) == ("a|b", "id")


def test_undecodable_cursor_is_invalid_cursor() -> None:
    with pytest.raises(Validation) as exc:
        decode_cursor("no-es-base64-!!!")

    assert exc.value.rule == "INVALID_CURSOR"


def test_cursor_without_pipe_is_invalid_cursor() -> None:
    payload = base64.urlsafe_b64encode(b"sinpipe").decode()

    with pytest.raises(Validation) as exc:
        decode_cursor(payload)

    assert exc.value.rule == "INVALID_CURSOR"


def test_cursor_with_empty_sort_key_is_invalid_cursor() -> None:
    payload = base64.urlsafe_b64encode(b"|id").decode()

    with pytest.raises(Validation) as exc:
        decode_cursor(payload)

    assert exc.value.rule == "INVALID_CURSOR"


def test_cursor_with_empty_id_is_invalid_cursor() -> None:
    payload = base64.urlsafe_b64encode(b"key|").decode()

    with pytest.raises(Validation) as exc:
        decode_cursor(payload)

    assert exc.value.rule == "INVALID_CURSOR"


def test_default_filters_are_the_spec_ones() -> None:
    filters = ListFilters(team_id="tm")

    assert filters.limit == 50
    assert filters.order == "manual"
    assert filters.offset == 0
    assert filters.include_archived is False
    assert filters.include_trashed is False
    assert filters.cursor is None
    assert filters.label_ids == ()


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
    assert "9999-12-31" in SORT_KEYS["due"]
    assert "CASE" in SORT_KEYS["priority"]


def test_issue_page_has_the_spec_envelope() -> None:
    page = IssuePage(items=(), next_cursor=None, has_more=False)

    assert page.items == ()
    assert page.has_more is False
