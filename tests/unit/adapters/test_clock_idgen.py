"""`SystemClock` and `UlidGen` satisfy the SPEC-030 §2.7 Protocols."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import ULID_LENGTH, UlidGen
from hifa.ports import Clock, IdGen


def test_system_clock_returns_utc_aware() -> None:
    now = SystemClock().now()

    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)
    assert now.tzinfo is UTC


def test_system_clock_satisfies_the_protocol() -> None:
    assert isinstance(SystemClock(), Clock)


def test_system_clock_advances_between_calls() -> None:
    clock = SystemClock()

    first = clock.now()
    second = clock.now()

    assert second >= first


def test_fixed_system_clock_returns_the_same_instant() -> None:
    frozen = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)
    clock = SystemClock(frozen)

    assert clock.now() == frozen
    assert clock.now() is clock.now()


def test_system_clock_normalizes_naive_to_utc() -> None:
    clock = SystemClock(datetime(2026, 11, 8, 9, 30))

    assert clock.now().tzinfo is UTC
    assert clock.now().hour == 9


def test_system_clock_converts_another_zone_to_utc() -> None:
    santiago = datetime(2026, 11, 8, 9, 30, tzinfo=timezone(timedelta(hours=-3)))

    assert SystemClock(santiago).now().hour == 12


def test_ulid_gen_satisfies_the_protocol() -> None:
    assert isinstance(UlidGen(), IdGen)


def test_ulid_gen_generates_26_crockford_characters() -> None:
    value = UlidGen().new()

    assert len(value) == ULID_LENGTH == 26
    assert value == value.upper()
    assert all(char in "0123456789ABCDEFGHJKMNPQRSTVWXYZ" for char in value)


def test_ulid_gen_does_not_repeat_or_go_backwards() -> None:
    gen = UlidGen()
    values = [gen.new() for _ in range(200)]

    assert len(set(values)) == len(values)
    assert values == sorted(values)


def test_ulid_gen_reuses_the_same_instance() -> None:
    gen = UlidGen()
    first = gen.new()
    second = gen.new()

    assert first[:10] <= second[:10]
