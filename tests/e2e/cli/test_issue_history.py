"""`hifa issue history`: the story of one issue, in sentences, oldest first.

The verb is a projection of `issue show --with history` — one read, one window — and the only
thing it adds is the cut `--limit` makes and the two diagnostics that report it. Every assertion
here is on what a reader sees: the header, the cells, the order and the code.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from conftest import Cli

#: Keys of `HistoryEntry`, in the order the payload declares them.
HISTORY_KEYS: tuple[str, ...] = (
    "id",
    "issue_id",
    "actor_id",
    "field",
    "old_value",
    "new_value",
    "created_at",
    "old_label",
    "new_label",
    "actor_label",
)

#: The three columns of the verb's table and of the `HISTORY` section of `issue show --with`.
HISTORY_COLUMNS: tuple[str, ...] = ("WHEN", "ACTOR", "EVENT")

#: The sentence `--limit` outside the window is refused with, byte for byte.
LIMIT_REFUSAL = "hifa: --limit must be between 1 and 100 (field: limit)"


def out(result: Any) -> str:
    """`stdout` plus `stderr`, for a failure that shows the whole invocation."""
    return f"{result.stdout}\n{result.stderr}"


def issue_id(db: Path, identifier: str) -> str:
    with sqlite3.connect(db) as conn:
        (value,) = conn.execute(
            "SELECT id FROM issue WHERE identifier = ?", (identifier,)
        ).fetchone()
    return str(value)


def add_history(
    db: Path,
    identifier: str,
    count: int,
    *,
    last: tuple[str, str | None, str | None] = ("title", "Old", "New"),
) -> None:
    """`count` rows stamped in the future, the last one being `last`.

    The writes the commands make are stamped by the clock of the machine, so the fixture has to
    sit after it for the window, the cap and the order to be observable at all.
    """
    rows: list[tuple[str, str | None, str | None, str]] = [
        (f"field{index:03d}", None, "value", f"2030-01-01T{index // 60:02d}:{index % 60:02d}:00Z")
        for index in range(count - 1)
    ]
    field, old, new = last
    rows.append((field, old, new, "2030-01-02T00:00:00Z"))
    row_issue = issue_id(db, identifier)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO issue_history"
            " (issue_id, actor_id, field, old_value, new_value, created_at)"
            " VALUES (?, NULL, ?, ?, ?, ?)",
            [(row_issue, field, old, new, at) for field, old, new, at in rows],
        )


def add_row(
    db: Path,
    identifier: str,
    *,
    field: str,
    old: str | None,
    new: str | None,
    actor_id: str | None = None,
    created_at: str = "2030-01-03T00:00:00Z",
) -> None:
    """One row with every side spelled out, for the fallbacks a writer never produces."""
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO issue_history"
            " (issue_id, actor_id, field, old_value, new_value, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (issue_id(db, identifier), actor_id, field, old, new, created_at),
        )


def local_member_name(db: Path) -> str:
    """The display name of the one member the workspace bootstrapped."""
    with sqlite3.connect(db) as conn:
        (name,) = conn.execute("SELECT display_name FROM member LIMIT 1").fetchone()
    return str(name)


def data_rows(result: Any) -> list[str]:
    """The table's rows, header excluded."""
    return result.stdout.splitlines()[1:]


def cells(row: str) -> tuple[str, str, str]:
    """The three cells of one printed row: two runs of padding separate them."""
    when, actor, event = re.split(r"\s{2,}", row.strip(), maxsplit=2)
    return when, actor, event


# --- the story ---------------------------------------------------------------------------


def test_one_sentence_per_event_and_the_stored_columns_never_reach_the_table(cli: Cli) -> None:
    """`created` first, then the two moves, and no field name or ULID on screen."""
    cli.create("Fix SSO login")
    assert cli.run("issue", "move", "ENG-1", "-s", "In Progress").exit_code == 0
    assert cli.run("issue", "move", "ENG-1", "-s", "Done").exit_code == 0

    result = cli.run("issue", "history", "ENG-1")

    assert result.exit_code == 0, out(result)
    assert result.stdout.splitlines()[0].split() == list(HISTORY_COLUMNS)
    assert [cells(row)[2] for row in data_rows(result)] == [
        "created the issue",
        "moved from Todo to In Progress",
        "started work",
        "moved from In Progress to Done",
        "completed it",
    ]
    assert {cells(row)[1] for row in data_rows(result)} == {local_member_name(cli.db)}
    assert "state_id" not in result.stdout
    assert "01M" not in result.stdout


def test_the_instant_is_the_utc_stamp_the_row_carries(cli: Cli) -> None:
    """`YYYY-MM-DDTHH:MM:SSZ`, seconds and no offset: the one shape this edge prints."""
    cli.create("Fix SSO login")
    add_row(
        cli.db,
        "ENG-1",
        field="title",
        old="Old",
        new="New",
        created_at="2026-11-11T16:38:25Z",
    )

    result = cli.run("issue", "history", "ENG-1")

    assert result.exit_code == 0, out(result)
    assert cells(data_rows(result)[0])[0] == "2026-11-11T16:38:25Z"


def test_the_story_is_oldest_first_by_instant_and_then_by_id(cli: Cli) -> None:
    """Two rows written in one transaction keep their written order: `id` breaks the tie."""
    cli.create("Fix SSO login")
    add_row(cli.db, "ENG-1", field="title", old=None, new="A", created_at="2030-01-01T00:00:00Z")
    add_row(cli.db, "ENG-1", field="title", old="A", new="B", created_at="2030-01-01T00:00:00Z")

    result = cli.run("issue", "history", "ENG-1")

    assert result.exit_code == 0, out(result)
    assert [cells(row)[2] for row in data_rows(result)][-2:] == [
        "changed the title to \u201cA\u201d",
        "changed the title to \u201cB\u201d",
    ]


# --- the cut -----------------------------------------------------------------------------


def test_limit_keeps_the_newest_rows_and_prints_them_forward(cli: Cli) -> None:
    """The cut is a log's, not the panel's: the tail of the story, still read forward."""
    cli.create("Busy")
    add_history(cli.db, "ENG-1", 37, last=("title", "Old", "Newest"))

    result = cli.run("issue", "history", "ENG-1", "--limit", "8")

    assert result.exit_code == 0, out(result)
    rows = data_rows(result)
    assert len(rows) == 8
    assert [cells(row)[0] for row in rows] == sorted(cells(row)[0] for row in rows)
    assert cells(rows[-1])[2] == "changed the title to \u201cNewest\u201d"
    assert "showing 8 of 38 events" in result.stderr


def test_the_default_cut_says_what_it_hides_when_it_hides_anything(cli: Cli) -> None:
    """Default 50 of a 38-row story (the `created` row included) hides nothing."""
    cli.create("Busy")
    add_history(cli.db, "ENG-1", 37)

    result = cli.run("issue", "history", "ENG-1")

    assert result.exit_code == 0, out(result)
    assert len(data_rows(result)) == 38
    assert "showing" not in result.stderr


def test_the_window_is_a_floor_and_the_cut_never_claims_the_total(cli: Cli) -> None:
    """120 rows return a window of 100, and the sentence reports the window, not a count."""
    cli.create("Busy")
    add_history(cli.db, "ENG-1", 120)

    result = cli.run("issue", "history", "ENG-1")

    assert result.exit_code == 0, out(result)
    assert len(data_rows(result)) == 50
    assert "showing 50 of 100 events" in result.stderr


def test_a_full_window_prints_every_row_it_has(cli: Cli) -> None:
    """`--limit 100` is the window itself: nothing is hidden from it."""
    cli.create("Busy")
    add_history(cli.db, "ENG-1", 120)

    result = cli.run("issue", "history", "ENG-1", "--limit", "100")

    assert result.exit_code == 0, out(result)
    assert len(data_rows(result)) == 100
    assert "showing" not in result.stderr


def test_quiet_prints_the_event_ids_and_no_diagnostic(cli: Cli) -> None:
    """A row's `id` is a number and `--quiet` prints it; diagnostics are suppressed."""
    cli.create("Busy")
    add_history(cli.db, "ENG-1", 37)

    result = cli.run("issue", "history", "ENG-1", "--limit", "8", "--quiet")

    assert result.exit_code == 0, out(result)
    lines = result.stdout.splitlines()
    assert len(lines) == 8
    assert all(line.isdigit() for line in lines)
    assert result.stderr == ""


# --- the boundaries ----------------------------------------------------------------------


def test_a_limit_outside_the_read_window_is_refused_with_its_own_sentence(cli: Cli) -> None:
    """0 and 101 are the same refusal, with the flag's code, field and exit."""
    cli.create("Fix SSO login")

    for value in ("0", "101", "500"):
        result = cli.run("issue", "history", "ENG-1", "--limit", value)

        assert result.exit_code == 2, out(result)
        assert result.stdout == ""
        assert result.stderr.strip() == LIMIT_REFUSAL


def test_the_refusal_is_the_same_envelope_under_json(cli: Cli) -> None:
    """`--json` prints this edge's one error document, not a usage panel."""
    cli.create("Fix SSO login")

    result = cli.run("issue", "history", "ENG-1", "--limit", "101", "--json")

    assert result.exit_code == 2, out(result)
    assert result.stdout == ""
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "validation_error"
    assert error["field"] == "limit"
    assert error["message"] == "--limit must be between 1 and 100"


def test_both_ends_of_the_window_are_accepted(cli: Cli) -> None:
    """1 and 100 are inside the read's window and the verb refuses to clamp only outside it."""
    cli.create("Busy")
    add_history(cli.db, "ENG-1", 37)

    one = cli.run("issue", "history", "ENG-1", "--limit", "1")
    window = cli.run("issue", "history", "ENG-1", "--limit", "100")

    assert one.exit_code == window.exit_code == 0, out(one) + out(window)
    assert len(data_rows(one)) == 1
    assert len(data_rows(window)) == 38


# --- the empty story and the absent reference --------------------------------------------


def test_an_issue_without_history_prints_nothing_and_reports_zero(cli: Cli) -> None:
    """`0 events` on `stderr`, empty `stdout`, exit 0: a collection with nothing to say."""
    cli.create("Fix SSO login")
    cli.execute("DELETE FROM issue_history")

    result = cli.run("issue", "history", "ENG-1")

    assert result.exit_code == 0, out(result)
    assert result.stdout == ""
    assert result.stderr.strip() == "0 events"


def test_an_issue_without_history_is_an_empty_items_envelope(cli: Cli) -> None:
    cli.create("Fix SSO login")
    cli.execute("DELETE FROM issue_history")

    result = cli.run("issue", "history", "ENG-1", "--json")

    assert result.exit_code == 0, out(result)
    assert json.loads(result.stdout) == {"items": []}


def test_a_reference_that_resolves_nowhere_is_not_found(cli: Cli) -> None:
    cli.create("Fix SSO login")

    result = cli.run("issue", "history", "ENG-999")

    assert result.exit_code == 3, out(result)
    assert result.stdout == ""
    assert result.stderr.strip() == "hifa: issue 'ENG-999' does not exist (field: issue)"


def test_a_trashed_issue_answers_as_absent(cli: Cli) -> None:
    """The window is the detail read's, and that read does not see an issue in the trash."""
    cli.create("Gone")
    assert cli.run("issue", "delete", "ENG-1", "--yes").exit_code == 0

    result = cli.run("issue", "history", "ENG-1", "--json")

    assert result.exit_code == 3, out(result)
    error = json.loads(result.stderr)["error"]
    assert error["code"] == "not_found"
    assert error["field"] == "issue"


# --- the payload -------------------------------------------------------------------------


def test_json_is_one_items_envelope_of_ten_keys_oldest_first(cli: Cli) -> None:
    """The element is the ten keys of the object, and its order is the table's."""
    cli.create("Fix SSO login")
    assert cli.run("issue", "move", "ENG-1", "-s", "In Progress").exit_code == 0

    document = json.loads(cli.run("issue", "history", "ENG-1", "--json").stdout)

    assert list(document) == ["items"]
    assert [element["field"] for element in document["items"]] == [
        "created",
        "state_id",
        "started_at",
    ]
    assert {tuple(element) for element in document["items"]} == {HISTORY_KEYS}
    instants = [element["created_at"] for element in document["items"]]
    assert instants == sorted(instants)
    assert all(instant.endswith("Z") and len(instant) == 20 for instant in instants)


def test_the_element_carries_the_stored_pair_beside_the_resolved_labels(cli: Cli) -> None:
    """A state change publishes both ULIDs and both names, so no caller joins."""
    cli.create("Fix SSO login")
    assert cli.run("issue", "move", "ENG-1", "-s", "In Progress").exit_code == 0
    todo = cli.state_id("Todo")
    in_progress = cli.state_id("In Progress")

    document = json.loads(cli.run("issue", "history", "ENG-1", "--json").stdout)
    element = next(item for item in document["items"] if item["field"] == "state_id")

    assert element["old_value"] == todo
    assert element["new_value"] == in_progress
    assert element["old_label"] == "Todo"
    assert element["new_label"] == "In Progress"
    assert element["actor_label"] == local_member_name(cli.db)
    assert element["issue_id"] == issue_id(cli.db, "ENG-1")


def test_the_limit_cuts_the_payload_the_same_way_it_cuts_the_table(cli: Cli) -> None:
    """One payload, one order: the envelope is the table's list, not a second reading."""
    cli.create("Busy")
    add_history(cli.db, "ENG-1", 37, last=("title", "Old", "Newest"))

    document = json.loads(cli.run("issue", "history", "ENG-1", "--limit", "3", "--json").stdout)

    assert [element["id"] for element in document["items"]] == sorted(
        element["id"] for element in document["items"]
    )
    assert document["items"][-1]["new_label"] == "Newest"


def test_the_fallbacks_of_a_row_nothing_can_resolve(cli: Cli) -> None:
    """An unknown field and an unknown actor are answers: `changed mood`, `someone`."""
    cli.create("Fix SSO login")
    add_row(cli.db, "ENG-1", field="mood", old="happy", new="sad")
    add_row(
        cli.db,
        "ENG-1",
        field="state_id",
        old="01M75KV68MF9TYWEK2GHWPNBZZ",
        new=cli.state_id("Todo"),
        actor_id="01M71T2XA1DS4WJ2Z7ZXBTNWZZ",
        created_at="2030-01-04T00:00:00Z",
    )

    envelope = json.loads(cli.run("issue", "history", "ENG-1", "--json").stdout)
    table = cli.run("issue", "history", "ENG-1")

    mood = next(element for element in envelope["items"] if element["field"] == "mood")
    assert mood["old_label"] is None
    assert mood["new_label"] is None
    assert mood["actor_label"] == "someone"
    assert "changed mood" in table.stdout
    assert "someone" in table.stdout
    assert "state (deleted)" in table.stdout
