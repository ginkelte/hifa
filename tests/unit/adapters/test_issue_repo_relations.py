"""`IssueRepo` writes go through the translator, including the ones that return no row.

The statement that deletes a relation has to fail like the one that inserts it: a locked
database is `DB_BUSY` at the adapter boundary, which is the code the write retry keys on.
"""

from __future__ import annotations

import sqlite3

import pytest

from helpers import FrozenClock, SeqIdGen
from hifa.adapters.sqlite.issue_repo import IssueRepo
from hifa.domain.errors import Conflict


class Locked:
    """Connection double whose every statement hits a database held by another writer."""

    def __init__(self, message: str = "database is locked") -> None:
        self.message = message
        self.statements: list[str] = []

    def execute(self, sql: str, params: object = ()) -> object:
        self.statements.append(sql)
        raise sqlite3.OperationalError(self.message)


def _repo(conn: object) -> IssueRepo:
    return IssueRepo(conn, clock=FrozenClock(), idgen=SeqIdGen())  # type: ignore[arg-type]


def test_remove_relation_translates_a_locked_database() -> None:
    repo = _repo(Locked())
    with pytest.raises(Conflict) as excinfo:
        repo.remove_relation("01RELATION")
    assert excinfo.value.code == "DB_BUSY"


def test_remove_relation_reports_whether_a_row_was_deleted() -> None:
    class Empty:
        def execute(self, sql: str, params: object = ()) -> object:
            class Cursor:
                rowcount = 0

            return Cursor()

    assert _repo(Empty()).remove_relation("01RELATION") is False
