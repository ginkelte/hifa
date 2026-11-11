"""Whole-transaction retry (SPEC-040 §2.2, D-018).

`with_write_retry` is the only retry loop in the system: `UnitOfWork.write()` does not
retry (a `@contextmanager` cannot be re-executed). `fn` must open its own
`uow.write()`, so each attempt is a new transaction with its own `BEGIN IMMEDIATE` and its
previous `ROLLBACK`.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

from hifa.domain.errors import Conflict, HifaError

T = TypeVar("T")

#: Codes/rules that justify retrying the whole transaction.
WRITE_RETRY_CODES: frozenset[str] = frozenset({"DB_BUSY"})
CREATE_RETRY_CODES: frozenset[str] = frozenset({"DB_BUSY", "DUPLICATE_NUMBER"})


def _matches(exc: HifaError, retry_on: frozenset[str]) -> bool:
    code = getattr(exc, "code", None)
    rule = getattr(exc, "rule", None)
    return (code is not None and code in retry_on) or (rule is not None and rule in retry_on)


def with_write_retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.05,
    retry_on: frozenset[str] = WRITE_RETRY_CODES,
) -> T:
    """Run `fn` up to `attempts` times on a retryable `Conflict`.

    Waits 50/100/200 ms with `base_delay=0.05`. Only a `Conflict` whose
    `code` or `rule` is in `retry_on` is retried; once attempts are exhausted the original
    exception is propagated (the same instance from the last attempt).
    """
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Conflict as exc:
            if not _matches(exc, retry_on) or attempt == attempts:
                raise
            time.sleep(base_delay * 2 ** (attempt - 1))
    raise AssertionError("unreachable")  # pragma: no cover


__all__ = ["CREATE_RETRY_CODES", "WRITE_RETRY_CODES", "with_write_retry"]
