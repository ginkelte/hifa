"""`UnitOfWork`, `Repos` and `RetryPolicy` (SPEC-030 §2.2, D-018).

`write()` opens `BEGIN IMMEDIATE`, yields the repos and `COMMIT`s; on any exception it
`ROLLBACK`s and re-raises. **No internal retry**: the generator cannot be re-run, so the
only loop in the system is `app/retry.py::with_write_retry`.

T-04a builds six repos (`teams`, `states`, `labels`, `members`, `issues`, `comments`)
plus `history`; `projects` and `cycles` are added by T-04b.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from hifa.adapters.sqlite import connection as connection_module
from hifa.adapters.sqlite._base import UNSET, Unset
from hifa.adapters.sqlite.comment_repo import CommentRepo
from hifa.adapters.sqlite.errors import translate_sqlite_error
from hifa.adapters.sqlite.history_repo import HistoryRepo
from hifa.adapters.sqlite.issue_repo import IssueRepo
from hifa.adapters.sqlite.label_repo import LabelRepo
from hifa.adapters.sqlite.member_repo import MemberRepo
from hifa.adapters.sqlite.state_repo import StateRepo
from hifa.adapters.sqlite.team_repo import TeamRepo
from hifa.domain.errors import Conflict, HifaError
from hifa.ports import Clock, IdGen

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Retry parameters; applied by `app/retry.py`, not by the UoW (D-018)."""

    attempts: int = 3
    base_delay: float = 0.05  # 50 ms, 100 ms, 200 ms


DEFAULT_RETRY: RetryPolicy = RetryPolicy()


#: `UNSET`/`Unset` are re-exported from `_base` so that `hifa.adapters.sqlite.uow` is the
#: public owner of the sentinel (SPEC-030 §2.2); this module only documents the re-export.


@dataclass(frozen=True, slots=True)
class Repos:
    """The repositories of one transaction: all of them share the SAME connection."""

    teams: TeamRepo
    states: StateRepo
    labels: LabelRepo
    members: MemberRepo
    issues: IssueRepo
    comments: CommentRepo
    history: HistoryRepo


def safe_rollback(conn: sqlite3.Connection) -> None:
    """`ROLLBACK` that ignores 'no transaction is active' and never masks the real error."""
    try:
        conn.execute("ROLLBACK")
    except sqlite3.Error:  # there is no open transaction anymore
        logger.debug("rollback without an active transaction", exc_info=True)


class UnitOfWork:
    """One connection, one session, one transaction per use case (R3/R14)."""

    def __init__(
        self,
        db_path: str | Path | sqlite3.Connection,
        *,
        clock: Clock,
        idgen: IdGen,
        readonly: bool = False,
        retry: RetryPolicy = DEFAULT_RETRY,
    ) -> None:
        self._conn: sqlite3.Connection | None = (
            db_path if isinstance(db_path, sqlite3.Connection) else None
        )
        self._path: Path | None = None if isinstance(db_path, sqlite3.Connection) else Path(db_path)
        self._owns_connection = not isinstance(db_path, sqlite3.Connection)
        self._clock = clock
        self._idgen = idgen
        self._readonly = readonly
        self._retry = retry
        self._in_tx = False
        self._closed = False

    # --- properties -------------------------------------------------------------------
    @property
    def connection(self) -> sqlite3.Connection:
        """The open connection (it opens it when a path was given)."""
        return self._connection()

    @property
    def clock(self) -> Clock:
        """D-017: the only way `app/` obtains `now`."""
        return self._clock

    @property
    def idgen(self) -> IdGen:
        """D-017: the only way `app/` obtains IDs."""
        return self._idgen

    @property
    def readonly(self) -> bool:
        return self._readonly

    @property
    def retry(self) -> RetryPolicy:
        return self._retry

    @property
    def path(self) -> Path | None:
        """Database path, or `None` when the UoW wraps an already open connection."""
        return self._path

    # --- lifecycle ----------------------------------------------------------------
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            if self._closed:
                raise HifaError("unit of work is closed", code="UOW_CLOSED")
            assert self._path is not None
            self._conn = connection_module.connect(self._path, readonly=self._readonly)
        return self._conn

    def close(self) -> None:
        """Close the connection only if this UoW opened it. Idempotent."""
        conn, self._conn = self._conn, None
        self._closed = True
        if conn is not None and self._owns_connection:
            try:
                conn.close()
            except sqlite3.Error:  # pragma: no cover - defensive close
                logger.debug("error while closing the connection", exc_info=True)

    def __enter__(self) -> UnitOfWork:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - safety net (R14)
        try:
            if self._conn is not None and self._owns_connection and not self._closed:
                logger.warning("UnitOfWork released without close(); closing connection")
            self.close()
        except Exception:  # noqa: BLE001 - never fail inside the finalizer
            pass

    # --- repos ------------------------------------------------------------------------
    def _repos(self, conn: sqlite3.Connection, *, readonly: bool) -> Repos:
        kwargs = {"clock": self._clock, "idgen": self._idgen, "readonly": readonly}
        return Repos(
            teams=TeamRepo(conn, **kwargs),
            states=StateRepo(conn, **kwargs),
            labels=LabelRepo(conn, **kwargs),
            members=MemberRepo(conn, **kwargs),
            issues=IssueRepo(conn, **kwargs),
            comments=CommentRepo(conn, **kwargs),
            history=HistoryRepo(conn, **kwargs),
        )

    # --- transactions ----------------------------------------------------------------
    @contextmanager
    def write(self) -> Iterator[Repos]:
        """`BEGIN IMMEDIATE` → repos → `COMMIT`. No retry (D-018)."""
        if self._readonly:
            raise Conflict("read-only unit of work", rule="READ_ONLY_UOW", code="READ_ONLY_UOW")
        if self._in_tx:
            raise HifaError("nested write", code="UOW_NESTED")
        conn = self._connection()
        self._in_tx = True
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.Error as exc:
                raise translate_sqlite_error(exc) from exc
            try:
                yield self._repos(conn, readonly=False)
                conn.execute("COMMIT")
            except sqlite3.OperationalError as exc:
                safe_rollback(conn)
                raise translate_sqlite_error(exc) from exc
            except sqlite3.IntegrityError as exc:
                safe_rollback(conn)
                raise translate_sqlite_error(exc) from exc
            except BaseException:
                safe_rollback(conn)
                raise
        finally:
            self._in_tx = False

    @contextmanager
    def read(self) -> Iterator[Repos]:
        """Deferred `BEGIN` → `readonly=True` repos → `COMMIT` (R6)."""
        if self._in_tx:
            raise HifaError("nested transaction", code="UOW_NESTED")
        conn = self._connection()
        self._in_tx = True
        try:
            try:
                conn.execute("BEGIN")
            except sqlite3.Error as exc:
                raise translate_sqlite_error(exc) from exc
            try:
                yield self._repos(conn, readonly=True)
                conn.execute("COMMIT")
            except sqlite3.Error as exc:
                safe_rollback(conn)
                raise translate_sqlite_error(exc) from exc
            except BaseException:
                safe_rollback(conn)
                raise
        finally:
            self._in_tx = False

    # --- utilities -------------------------------------------------------------------
    def now(self) -> datetime:
        """Shortcut for `self.clock.now()`."""
        return self._clock.now()

    def new_id(self) -> str:
        """Shortcut for `self.idgen.new()`."""
        return self._idgen.new()


__all__ = [
    "DEFAULT_RETRY",
    "UNSET",
    "Repos",
    "RetryPolicy",
    "UnitOfWork",
    "Unset",
    "safe_rollback",
]
