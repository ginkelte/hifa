"""Core ports: `Clock` and `IdGen`.

Stdlib only: time and identifiers enter the domain through these Protocols.
`adapters/` implements them (`SystemClock`, `UlidGen`); `domain/` names them in prose
without importing them; `app/` reads them from `uow.clock` / `uow.idgen` and never
instantiates one of its own.

The **repos** and the **UnitOfWork** are deliberately not declared here as Protocols:
the contract is the concrete classes in `hifa.adapters.sqlite` (`uow.UnitOfWork`,
`*_repo.*Repo`), which is what `app/` and the edges type against. A mirror Protocol over
seven repos would duplicate 60 signatures and drift at the first change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Current instant, aware and in UTC."""

    def now(self) -> datetime:
        """Return the current instant in UTC (never naive, never local time)."""
        ...


@runtime_checkable
class IdGen(Protocol):
    """ULID identifier generator."""

    def new(self) -> str:
        """Return a 26-character ULID, sortable by time and unique."""
        ...


__all__ = ["Clock", "IdGen"]
