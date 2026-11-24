"""Unit tests of the reference resolution: what it asks for, and when it asks nothing.

`_resolved_refs` is the one place the web layer turns the text of a detail into the mapping
`render_markdown` links through (SPEC-070 §2.3.6, R30, D-081), so this file pins its two edges:
the candidate set it sends to the one statement — folded, distinct and in first-appearance order,
description before comments — and the detail that names nothing at all, which must pay neither a
statement nor the connection that would carry it.

Nothing here opens SQLite: the read it calls is fed a connection that records the values it was
bound with, and the empty case is fed a database that fails the test if it is touched.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import pytest

from hifa.adapters.sqlite.reads import IssueDetail
from hifa.web.app import _resolved_refs
from hifa.web.markdown import render_markdown


class _Answer:
    """The empty answer of one recorded statement: no identifier of the set names a row."""

    def fetchall(self) -> list[tuple[Any, ...]]:
        """No row resolved."""
        return []


class _RecordingConnection:
    """The one statement `issue_ref_links` sends, with the values it binds and whether it closed."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: tuple[str, ...] = ()
        self.closed = False

    def execute(self, sql: str, params: Sequence[str]) -> _Answer:
        """Record the statement and the values, and answer nothing."""
        self.statements.append(sql)
        self.params = tuple(params)
        return _Answer()

    def close(self) -> None:
        """Record that the caller closed what it opened."""
        self.closed = True


class _RecordingDatabase:
    """A database whose one read connection is the recording connection above."""

    def __init__(self) -> None:
        self.connection = _RecordingConnection()
        self.opened = 0

    def ro_connection(self) -> sqlite3.Connection:
        """Answer the recording connection, counting how often it was asked."""
        self.opened += 1
        return cast(sqlite3.Connection, self.connection)


class _UntouchableDatabase:
    """A database that fails the test if anything asks it for a connection."""

    def ro_connection(self) -> sqlite3.Connection:
        """Fail: the resolution had nothing to ask, so it had no reason to open a connection."""
        raise AssertionError("a detail that names no candidate opened a connection")


def _detail(description: str | None, *bodies: str) -> IssueDetail:
    """A detail read carrying only the two parts the resolution reads: text and comments."""
    return cast(
        IssueDetail,
        SimpleNamespace(
            description=description,
            comments=tuple(SimpleNamespace(body=body) for body in bodies),
        ),
    )


def _request(database: object) -> Any:
    """The request `_resolved_refs` reads the database off, and nothing else of it."""
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(database=database)))


def test_a_detail_that_names_no_candidate_opens_no_connection() -> None:
    """No candidate is no connection and no statement: the mapping is built only when asked."""
    database = _UntouchableDatabase()

    assert _resolved_refs(_request(database), _detail("plain text", "no reference here")) == {}


def test_the_candidates_of_the_description_and_every_comment_are_asked_once() -> None:
    """One statement for the whole surface, folded and distinct, description before comments."""
    database = _RecordingDatabase()

    mapping = _resolved_refs(
        _request(database),
        _detail("see ENG-12 and HIFA-103, again ENG-12", "hifa-103 blocks it", "ENG-12"),
    )

    assert mapping == {}
    assert database.opened == 1
    assert database.connection.closed
    assert len(database.connection.statements) == 1
    assert database.connection.params == ("ENG-12", "HIFA-103")


def test_a_mapping_value_that_is_not_a_pair_names_the_candidate_and_the_shape() -> None:
    """A value of another shape has no path to write: the renderer says which candidate and why."""
    with pytest.raises(ValueError) as refusal:
        render_markdown("ENG-12", {"ENG-12": ("ENG",)})  # type: ignore[dict-item]

    assert "ENG-12" in str(refusal.value)
    assert "(team_key, identifier)" in str(refusal.value)
