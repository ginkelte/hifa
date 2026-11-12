"""`reads.issue_detail`: the `include` set is validated, never silently ignored."""

from __future__ import annotations

import pytest

from helpers_world import make_issue
from hifa.adapters.sqlite.reads import DETAIL_INCLUDE, IssueDetail, issue_detail
from hifa.domain.errors import Validation


def test_issue_detail_rejects_an_unknown_include(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)

    with world.uow.read() as tx, pytest.raises(Validation) as caught:
        issue_detail(tx.issues.connection, "ENG-1", include=frozenset({"bogus"}))

    assert caught.value.rule == "INCLUDE_INVALID"
    assert caught.value.field == "include"
    assert caught.value.value == "bogus"


def test_issue_detail_rejects_one_unknown_name_among_known_ones(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)

    with world.uow.read() as tx, pytest.raises(Validation) as caught:
        issue_detail(tx.issues.connection, "ENG-1", include=frozenset({"comments", "children"}))

    assert caught.value.rule == "INCLUDE_INVALID"
    assert caught.value.value == "children"


def test_issue_detail_accepts_every_declared_include(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)

    with world.uow.read() as tx:
        detail = issue_detail(tx.issues.connection, "ENG-1", include=DETAIL_INCLUDE)

    assert isinstance(detail, IssueDetail)
    assert detail.comments == ()
    assert detail.members == {}


def test_issue_detail_without_include_loads_no_collection(world) -> None:
    with world.uow.write() as tx:
        make_issue(tx, world)

    with world.uow.read() as tx:
        detail = issue_detail(tx.issues.connection, "ENG-1")

    assert detail is not None
    assert detail.comments == ()
    assert detail.history == ()
    assert detail.attachments == ()
