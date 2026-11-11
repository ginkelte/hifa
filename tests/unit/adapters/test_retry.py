"""`with_write_retry`: the only retry loop in the system (D-018, SPEC-040 §2.2)."""

from __future__ import annotations

import pytest

from hifa.app.retry import CREATE_RETRY_CODES, with_write_retry
from hifa.domain.errors import Conflict, NotFound, Validation


def _busy() -> Conflict:
    return Conflict("busy", rule="DB_BUSY", code="DB_BUSY")


def _duplicate_number() -> Conflict:
    return Conflict("dup", rule="DUPLICATE_NUMBER", field="number")


def test_retries_and_succeeds_on_the_third_attempt() -> None:
    calls: list[int] = []

    def fn() -> str:
        calls.append(len(calls) + 1)
        if len(calls) < 3:
            raise _busy()
        return "ok"

    assert with_write_retry(fn, base_delay=0) == "ok"
    assert calls == [1, 2, 3]


def test_does_not_retry_if_the_first_succeeds() -> None:
    calls: list[int] = []

    def fn() -> int:
        calls.append(1)
        return 42

    assert with_write_retry(fn, base_delay=0) == 42
    assert len(calls) == 1


def test_exhausted_attempts_propagate_the_last_conflict() -> None:
    calls: list[int] = []
    last: list[Conflict] = []

    def fn() -> None:
        calls.append(1)
        last.append(_busy())
        raise last[-1]

    with pytest.raises(Conflict) as exc:
        with_write_retry(fn, attempts=3, base_delay=0)

    assert len(calls) == 3
    assert exc.value is last[-1]
    assert exc.value.code == "DB_BUSY"


def test_does_not_retry_errors_outside_retry_on() -> None:
    calls: list[int] = []

    def fn() -> None:
        calls.append(1)
        raise Validation("bad", rule="SOMETHING")

    with pytest.raises(Validation):
        with_write_retry(fn, base_delay=0)

    assert len(calls) == 1


def test_does_not_retry_not_found() -> None:
    calls: list[int] = []

    def fn() -> None:
        calls.append(1)
        raise NotFound(resource="issue", key="X")

    with pytest.raises(NotFound):
        with_write_retry(fn, base_delay=0)

    assert len(calls) == 1


def test_duplicate_number_only_retries_when_requested() -> None:
    calls: list[int] = []

    def fn() -> str:
        calls.append(1)
        if len(calls) < 2:
            raise _duplicate_number()
        return "ok"

    with pytest.raises(Conflict):
        with_write_retry(fn, base_delay=0)

    assert len(calls) == 1
    assert with_write_retry(fn, base_delay=0, retry_on=CREATE_RETRY_CODES) == "ok"


def test_respects_attempts_one() -> None:
    calls: list[int] = []

    def fn() -> None:
        calls.append(1)
        raise _busy()

    with pytest.raises(Conflict):
        with_write_retry(fn, attempts=1, base_delay=0)

    assert len(calls) == 1


def test_invalid_attempts_fails_fast() -> None:
    with pytest.raises(ValueError):
        with_write_retry(lambda: None, attempts=0)


def test_retries_by_code_even_if_rule_differs() -> None:
    calls: list[int] = []

    def fn() -> str:
        calls.append(1)
        if len(calls) < 2:
            raise Conflict("busy", rule="WRITE_LOCK", code="DB_BUSY")
        return "ok"

    assert with_write_retry(fn, base_delay=0) == "ok"
