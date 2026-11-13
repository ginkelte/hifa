"""E2E tests for the milestone table, shared by `issue create` and `issue move`.

One table maps the type of the target state to the milestone columns it stamps. The tests
below drive both verbs over the six state types the default workflow carries and assert the
same sealed columns, so a change made on one side only shows up here.
"""

from __future__ import annotations

import pytest

from conftest import Cli, json_stdout

MILESTONES: tuple[str, ...] = ("started_at", "completed_at", "canceled_at")

#: State name of the default workflow → the milestone columns it stamps, in order.
CASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Triage", ()),
    ("Todo", ()),
    ("Backlog", ()),
    ("In Progress", ("started_at",)),
    ("Done", ("completed_at",)),
    ("Canceled", ("canceled_at",)),
)


def _stamped(payload: dict[str, object]) -> tuple[str, ...]:
    return tuple(column for column in MILESTONES if payload[column] is not None)


@pytest.mark.parametrize(("state", "expected"), CASES)
def test_create_and_move_stamp_the_same_milestones(
    cli: Cli, state: str, expected: tuple[str, ...]
) -> None:
    cli.create("Moved")
    created = cli.run("issue", "create", "Created", "--state", state, "--json")
    moved = cli.run("issue", "move", "ENG-1", "--state", state, "--json")
    assert created.exit_code == 0, created.stdout + created.stderr
    assert moved.exit_code == 0, moved.stdout + moved.stderr
    created_payload = json_stdout(created)
    moved_payload = json_stdout(moved)
    assert _stamped(created_payload) == expected
    assert _stamped(moved_payload) == expected
    assert _stamped(created_payload) == _stamped(moved_payload)
