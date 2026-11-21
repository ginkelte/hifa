"""E2E tests of `hifa issue show` and its `--with` payloads.

Every test runs the real Typer app over a database seeded by `hifa init`, so what is asserted is
the bytes a caller receives: the block, the sections, the envelope and the two sentences the flag
refuses a value with. The exit set of the verb is `0`, `2` and `3` and no `--with` value adds a
fourth: the only `2` is a token the command declines, and the only `3` is a reference that resolves
nowhere or an issue the trash layer holds.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from conftest import CANONICAL_KEYS, COMMENT_KEYS, Cli

#: Keys of the block, in the order `issue show` prints them.
BLOCK_KEYS: tuple[str, ...] = (
    "State",
    "Priority",
    "Assignee",
    "Project",
    "Cycle",
    "Due",
    "Estimate",
    "Labels",
    "Parent",
    "Created",
    "Updated",
)

#: The four collection keys of the detail payload, in the order it appends them.
COLLECTION_KEYS: tuple[str, ...] = ("comments", "relations", "history", "children")

#: Keys of `HistoryEntry`, the element of the `history` collection, in the order it declares.
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

#: The sentence `--with attachments` is refused with, byte for byte.
ATTACHMENTS_REFUSAL = "hifa: --with attachments has no payload in this version (field: with)"

#: The sentence a token outside the closed five is refused with.
UNKNOWN_REFUSAL = (
    "hifa: unknown --with value 'bogus'; expected comments, relations, history, children"
    " or attachments (field: with)"
)


def out(result: Any) -> str:
    """`stdout` plus `stderr`, for an assertion failure that shows the whole invocation."""
    return f"{result.stdout}\n{result.stderr}"


def payload(result: Any) -> dict:
    """The `--json` document of a successful invocation."""
    return json.loads(result.stdout)


def error_of(result: Any) -> dict:
    """The error envelope, which a failed invocation writes to `stderr`."""
    return json.loads(result.stderr)["error"]


def block(result: Any) -> dict[str, str]:
    """The eleven rows of the block, key by key, as they were printed."""
    lines = result.stdout.splitlines()[1:12]
    return {line[:10].strip(): line[10:] for line in lines}


def sections(result: Any) -> dict[str, list[str]]:
    """`{section title: [header, *rows]}`; the block is the first chunk and is not a section."""
    chunks = result.stdout.strip().split("\n\n")[1:]
    return {chunk.splitlines()[0]: chunk.splitlines()[1:] for chunk in chunks if chunk}


def far_ends(rows: list[str]) -> list[str]:
    """The identifier of each relation row, read out of the padded cells."""
    return [token for row in rows for token in row.split() if token.startswith("ENG-")]


def add_history(db: Path, identifier: str, count: int) -> None:
    """`count` history rows stamped in the future, the newest carrying the field `title`.

    The writes the commands make are stamped by the system clock, so the fixture has to sit after
    it for the cap and the order to be observable at all. The newest row is a title change, so the
    forward section ends with the sentence that change reads as.
    """
    with sqlite3.connect(db) as conn:
        (issue_id,) = conn.execute(
            "SELECT id FROM issue WHERE identifier = ?", (identifier,)
        ).fetchone()
        rows: list[tuple[str, str, str | None, str | None, str]] = [
            (issue_id, f"field{index:03d}", None, "value", _stamp(index))
            for index in range(count - 1)
        ]
        rows.append((issue_id, "title", "Old", "Fix SSO login", "2030-01-02T00:00:00Z"))
        conn.executemany(
            "INSERT INTO issue_history"
            " (issue_id, actor_id, field, old_value, new_value, created_at)"
            " VALUES (?, NULL, ?, ?, ?, ?)",
            rows,
        )


def _stamp(index: int) -> str:
    """An ISO-8601 instant that rises with `index`, all of them after the suite's clock."""
    return f"2030-01-01T{index // 60:02d}:{index % 60:02d}:00Z"


def add_team(cli: Cli, key: str) -> None:
    """A second team of the workspace, with the workflow states `team create` gives it."""
    result = cli.run("team", "create", key, key.title())
    assert result.exit_code == 0, out(result)


def renumber(cli: Cli, identifier: str, number: int) -> None:
    """Give one issue a number of the fixture's choosing; no verb sets a number."""
    key = identifier.rsplit("-", 1)[0]
    cli.execute(
        "UPDATE issue SET number = ?, identifier = ? WHERE identifier = ?",
        (number, f"{key}-{number}", identifier),
    )


def leave_key_behind(
    cli: Cli, *, old: str, new: str, changed_at: str = "2026-11-21T00:00:00Z"
) -> None:
    """Rotate a team's key the way the not-yet-contracted verb would: key, rows and identifiers.

    The team keeps its issues and its numbers; only the key and the identifiers change, and the
    row in `team_previous_key` is the record that the old key still names this team.
    """
    rows = cli.rows("SELECT id FROM team WHERE key = ?", (old,))
    assert rows, f"team {old} is not in the database"
    team_id = str(rows[0]["id"])
    cli.execute("UPDATE team SET key = ? WHERE id = ?", (new, team_id))
    cli.execute(
        "UPDATE issue SET identifier = ? || '-' || number WHERE team_id = ?", (new, team_id)
    )
    cli.execute(
        "INSERT INTO team_previous_key (team_id, key, changed_at) VALUES (?, ?, ?)",
        (team_id, old, changed_at),
    )


def forget_default_team(cli: Cli) -> None:
    """Drop `[cli].default_team`, leaving the workspace's own team as the only source."""
    path = cli.cwd / ".hifa.toml"
    kept = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("default_team")
    ]
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


# --- the block ---------------------------------------------------------------------------


def test_the_block_prints_the_header_and_the_eleven_rows(cli: Cli) -> None:
    """The identifier and the title, then one `key  value` line per field, in that order."""
    cli.create("Fix SSO login")

    result = cli.run("issue", "show", "ENG-1")

    assert result.exit_code == 0, out(result)
    lines = result.stdout.splitlines()
    assert lines[0] == "ENG-1  Fix SSO login"
    assert [line[:10].strip() for line in lines[1:12]] == list(BLOCK_KEYS)
    assert len(lines) == 12
    assert block(result)["State"] == "Todo (unstarted)"


def test_every_value_of_the_block_starts_in_column_11(cli: Cli) -> None:
    """The key column is ten cells wide: the widest key is 8 and the separator is two spaces."""
    cli.create("Fix SSO login")

    result = cli.run("issue", "show", "ENG-1")

    for line in result.stdout.splitlines()[1:12]:
        assert line[9] == " ", line
        assert line[10] != " ", line


def test_the_block_is_a_summary_and_the_body_travels_in_json(cli: Cli) -> None:
    """`description` is a key of the payload and not a row: a body has no line of its own."""
    cli.create("With a body", "-d", "line one\nline two")

    table = cli.run("issue", "show", "ENG-1")
    envelope = cli.run("issue", "show", "ENG-1", "--json")

    assert "line one" not in table.stdout
    assert payload(envelope)["description"] == "line one\nline two"


def test_the_parent_row_carries_the_identifier_of_the_parent(cli: Cli) -> None:
    """The block names the parent by the name a reader types, not by its ULID."""
    cli.create("Parent")
    cli.create("Child", "--parent", "ENG-1")

    result = cli.run("issue", "show", "ENG-2")

    assert result.exit_code == 0, out(result)
    assert block(result)["Parent"] == "ENG-1"


# --- the sections ------------------------------------------------------------------------


def test_the_sections_follow_the_object_order_not_the_flag_order(cli: Cli) -> None:
    """`--with relations --with comments` prints `COMMENTS` first, with its own columns."""
    cli.create("Fix SSO login")
    cli.create("Session refresh")
    assert cli.run("issue", "comment", "ENG-1", "Reproduced locally").exit_code == 0
    assert cli.run("issue", "relate", "ENG-1", "blocks", "ENG-2").exit_code == 0

    result = cli.run("issue", "show", "ENG-1", "--with", "relations", "--with", "comments")

    assert result.exit_code == 0, out(result)
    printed = sections(result)
    assert list(printed) == ["COMMENTS", "RELATIONS"]
    assert printed["COMMENTS"][0].split() == ["ID", "AUTHOR", "CREATED", "BODY"]
    assert printed["RELATIONS"][0].split() == ["READS", "AS", "IDENTIFIER", "TITLE", "STATE"]
    assert printed["RELATIONS"][1].split()[0] == "blocks"
    assert far_ends(printed["RELATIONS"][1:]) == ["ENG-2"]


def test_the_same_set_of_flags_in_another_order_prints_the_same_bytes(cli: Cli) -> None:
    """A set, not a sequence: the order the flags were typed never reaches the output."""
    cli.create("Fix SSO login")
    assert cli.run("issue", "comment", "ENG-1", "Reproduced locally").exit_code == 0

    first = cli.run("issue", "show", "ENG-1", "--with", "comments", "--with", "relations")
    second = cli.run("issue", "show", "ENG-1", "--with", "relations", "--with", "comments")

    assert first.exit_code == second.exit_code == 0
    assert first.stdout == second.stdout


def test_a_repeated_with_value_is_one_collection(cli: Cli) -> None:
    """`--with comments --with comments` is `--with comments`: one section, one read."""
    cli.create("Fix SSO login")
    assert cli.run("issue", "comment", "ENG-1", "Reproduced locally").exit_code == 0

    table = cli.run("issue", "show", "ENG-1", "--with", "comments", "--with", "comments")
    envelope = cli.run(
        "issue", "show", "ENG-1", "--with", "comments", "--with", "comments", "--json"
    )

    assert table.stdout.count("COMMENTS") == 1
    assert list(sections(table)) == ["COMMENTS"]
    assert len(payload(envelope)["comments"]) == 1


def test_a_requested_collection_with_no_rows_prints_no_section_and_reports_zero(cli: Cli) -> None:
    """Nothing to say is said on `stderr`: no section, one diagnostic, exit `0`."""
    cli.create("Fix SSO login")

    result = cli.run("issue", "show", "ENG-1", "--with", "children")

    assert result.exit_code == 0, out(result)
    assert "CHILDREN" not in result.stdout
    assert result.stderr.strip() == "0 children"


def test_the_children_section_lists_the_live_children_only(cli: Cli) -> None:
    """One row per live direct child, and the trashed one is not the section's business."""
    cli.create("Parent")
    cli.create("Live child", "--parent", "ENG-1")
    cli.create("Trashed child", "--parent", "ENG-1")
    assert cli.run("issue", "delete", "ENG-3", "--yes").exit_code == 0

    table = cli.run("issue", "show", "ENG-1", "--with", "children")
    envelope = cli.run("issue", "show", "ENG-1", "--with", "children", "--json")

    assert table.exit_code == 0, out(table)
    printed = sections(table)["CHILDREN"]
    assert printed[0].split() == ["IDENTIFIER", "TITLE"]
    assert len(printed) == 2
    assert far_ends(printed[1:]) == ["ENG-2"]
    assert "ENG-3" not in table.stdout
    children = payload(envelope)["children"]
    assert [child["identifier"] for child in children] == ["ENG-2"]
    assert set(children[0]) == {"id", "identifier", "title"}


def test_the_history_section_prints_the_story_oldest_first_and_at_most_100(cli: Cli) -> None:
    """The cap is the read's window, the order is forward, and the cells are sentences."""
    cli.create("Busy")
    add_history(cli.db, "ENG-1", 120)

    result = cli.run("issue", "show", "ENG-1", "--with", "history")

    assert result.exit_code == 0, out(result)
    rows = sections(result)["HISTORY"]
    assert rows[0].split() == ["WHEN", "ACTOR", "EVENT"]
    assert len(rows) == 101
    assert rows[-1].endswith("changed the title to \u201cFix SSO login\u201d")
    assert "field000" not in result.stdout
    assert "state_id" not in result.stdout


def test_a_short_history_reads_as_a_story_oldest_first(cli: Cli) -> None:
    """Two events, two rows, oldest first — and the stored ULIDs never reach the output.

    The 120-row test pins the cap and the direction; this one pins that a history short enough to
    have no cut is still a story: `created` first, the move second, each cell a sentence.
    """
    cli.create("Fix SSO login", "--state", "Backlog")
    assert cli.run("issue", "move", "ENG-1", "-s", "Todo").exit_code == 0

    result = cli.run("issue", "show", "ENG-1", "--with", "history")

    assert result.exit_code == 0, out(result)
    rows = sections(result)["HISTORY"]
    assert rows[0].split() == ["WHEN", "ACTOR", "EVENT"]
    assert len(rows) == 3
    assert rows[1].endswith("created the issue")
    assert rows[2].endswith("moved from Backlog to Todo")
    for state in cli.rows("SELECT id FROM workflow_state"):
        assert str(state["id"]) not in result.stdout


# --- the collections, through the envelope -----------------------------------------------


def test_json_without_with_emits_the_33_key_issue(cli: Cli) -> None:
    """No collection keys, and the whole object this edge publishes for every verb."""
    cli.create("Fix SSO login")

    document = payload(cli.run("issue", "show", "ENG-1", "--json"))

    assert len(document) == 33
    assert tuple(document) == CANONICAL_KEYS
    assert "comments" not in document


def test_json_with_with_emits_the_37_key_detail(cli: Cli) -> None:
    """The four keys appended after `updated_at`, all of them, and no `attachments`."""
    cli.create("Fix SSO login")
    assert cli.run("issue", "comment", "ENG-1", "Reproduced locally").exit_code == 0

    document = payload(cli.run("issue", "show", "ENG-1", "--with", "comments", "--json"))

    assert len(document) == 37
    assert tuple(document)[:33] == CANONICAL_KEYS
    assert tuple(document)[-4:] == COLLECTION_KEYS
    assert "attachments" not in document
    assert len(document["comments"]) == 1
    assert document["relations"] == []
    assert document["history"] == []
    assert document["children"] == []


def test_a_collection_that_was_not_requested_is_an_empty_list(cli: Cli) -> None:
    """One shape: an unasked collection and one with no rows are both `[]`."""
    cli.create("Fix SSO login")
    cli.create("Session refresh")
    assert cli.run("issue", "relate", "ENG-1", "blocks", "ENG-2").exit_code == 0

    document = payload(cli.run("issue", "show", "ENG-1", "--with", "children", "--json"))

    assert document["children"] == []
    assert document["relations"] == []  # requested nowhere, and the edge exists


def test_the_history_element_carries_the_ten_keys_and_keeps_the_reads_order(cli: Cli) -> None:
    """The envelope's `history` is newest-first because the payload is the envelope's."""
    cli.create("Fix SSO login")
    assert cli.run("issue", "edit", "ENG-1", "-t", "Fix SSO login again").exit_code == 0

    document = payload(cli.run("issue", "show", "ENG-1", "--with", "history", "--json"))
    elements = document["history"]

    assert tuple(elements[0]) == HISTORY_KEYS
    assert [element["field"] for element in elements] == ["branch_name", "title", "created"]
    assert elements[1]["new_label"] == "Fix SSO login again"
    assert elements[0]["actor_label"] != ""


def test_the_comment_element_carries_the_canonical_keys(cli: Cli) -> None:
    """The element is the `Comment` of the payload builders, in the order they declare."""
    cli.create("Fix SSO login")
    assert cli.run("issue", "comment", "ENG-1", "Reproduced locally").exit_code == 0

    document = payload(cli.run("issue", "show", "ENG-1", "--with", "comments", "--json"))
    comment = document["comments"][0]

    assert tuple(comment) == COMMENT_KEYS
    assert comment["body"] == "Reproduced locally"


# --- the refusal -------------------------------------------------------------------------


def test_with_attachments_is_refused_with_its_own_sentence(cli: Cli) -> None:
    """Nothing can write an attachment row, so the flag declines instead of printing `[]`."""
    cli.create("Fix SSO login")

    result = cli.run("issue", "show", "ENG-1", "--with", "attachments")

    assert result.exit_code == 2, out(result)
    assert result.stdout == ""
    assert result.stderr.strip() == ATTACHMENTS_REFUSAL


def test_with_attachments_is_refused_in_the_json_envelope_too(cli: Cli) -> None:
    """`--json` prints this edge's one error document, not a usage line."""
    cli.create("Fix SSO login")

    result = cli.run("issue", "show", "ENG-1", "--with", "attachments", "--json")

    assert result.exit_code == 2, out(result)
    assert result.stdout == ""
    error = error_of(result)
    assert error["code"] == "invalid_option"
    assert error["field"] == "with"
    assert error["message"] == "--with attachments has no payload in this version"
    assert error["details"] is None


def test_a_value_outside_the_five_is_refused_by_the_command(cli: Cli) -> None:
    """The value set is closed: a token the tree does not declare is a typo, not a request.

    The refusal is the command's, so `--json` prints this edge's error document and not a parser
    usage panel: `stdout` stays empty either way.
    """
    cli.create("Fix SSO login")

    table = cli.run("issue", "show", "ENG-1", "--with", "bogus")
    envelope = cli.run("issue", "show", "ENG-1", "--with", "bogus", "--json")

    assert table.exit_code == envelope.exit_code == 2
    assert table.stdout == envelope.stdout == ""
    assert table.stderr.strip() == UNKNOWN_REFUSAL
    error = error_of(envelope)
    assert error["code"] == "invalid_option"
    assert error["field"] == "with"


# --- the trash and the reference ---------------------------------------------------------


def test_a_trashed_reference_is_not_found(cli: Cli) -> None:
    """The verb answers for one issue, and the trash layer holds it: exit `3`, one code."""
    cli.create("Gone")
    assert cli.run("issue", "delete", "ENG-1", "--yes").exit_code == 0

    plain = cli.run("issue", "show", "ENG-1", "--json")
    with_with = cli.run("issue", "show", "ENG-1", "--with", "comments", "--json")

    assert plain.exit_code == with_with.exit_code == 3
    assert error_of(plain)["code"] == "not_found"
    assert error_of(plain)["field"] == "issue"
    assert error_of(with_with)["code"] == "not_found"


def test_a_trashed_parent_answers_no_parent(cli: Cli) -> None:
    """The child keeps its pointer and loses its parent: the pointer is not reparented."""
    cli.create("Parent")
    cli.create("Child", "--parent", "ENG-1")
    before = payload(cli.run("issue", "show", "ENG-2", "--json"))

    assert cli.run("issue", "delete", "ENG-1", "--yes").exit_code == 0

    after = payload(cli.run("issue", "show", "ENG-2", "--json"))
    stored = cli.issue("ENG-2")["parent_id"]

    assert before["parent"]["identifier"] == "ENG-1"
    assert after["parent"] is None
    assert stored is not None


def test_a_trashed_far_end_is_absent_from_the_relations(cli: Cli) -> None:
    """One edge is hidden and the other is read; both rows are still stored."""
    for title in ("Asking", "Live", "Gone"):
        cli.create(title)
    assert cli.run("issue", "relate", "ENG-1", "related", "ENG-2").exit_code == 0
    assert cli.run("issue", "relate", "ENG-1", "related", "ENG-3").exit_code == 0
    assert cli.run("issue", "delete", "ENG-3", "--yes").exit_code == 0

    result = cli.run("issue", "show", "ENG-1", "--with", "relations")

    assert result.exit_code == 0, out(result)
    assert far_ends(sections(result)["RELATIONS"][1:]) == ["ENG-2"]
    assert "ENG-3" not in result.stdout
    assert len(cli.rows("SELECT id FROM issue_relation")) == 2


# --- the inherited order -----------------------------------------------------------------

#: The six edges of the fan fixture, in the order they are inserted: `(asking, type, far end)`.
#: Neither the published order nor its reverse: `blocks` holds three rows inserted `ENG-20,
#: ENG-9, ENG-3` and published `ENG-20, ENG-3, ENG-9`, and the row read from the other end
#: (`ENG-8 blocks ENG-1`, which is `blocked_by` here) is inserted last and published third.
FAN_EDGES: tuple[tuple[str, str, str], ...] = (
    ("ENG-1", "related", "ENG-30"),
    ("ENG-1", "blocks", "ENG-20"),
    ("ENG-1", "blocks", "ENG-9"),
    ("ENG-1", "blocks", "ENG-3"),
    ("ENG-1", "duplicate", "ENG-5"),
    ("ENG-8", "blocks", "ENG-1"),
)


def test_the_relations_come_out_in_the_declared_reading_then_identifier_order(
    cli: Cli,
) -> None:
    """Reading first, then the far end's identifier compared as text; the fetch order is neither.

    The three `blocks` rows arrive `ENG-20, ENG-9, ENG-3` and are published `ENG-20, ENG-3,
    ENG-9`: the middle one of the text order is not the middle one the rows arrive in, so the
    insertion order, its reverse and the order the rows are fetched in all differ from the
    answer, and only the lexicographic comparison of the identifiers produces it.
    """
    shown, fetched = _fan(cli)
    # The far end of each edge as ENG-1 sees it: the one row read from the other end names ENG-8.
    inserted = [other if asking == "ENG-1" else asking for asking, _type, other in FAN_EDGES]

    assert shown == ["ENG-20", "ENG-3", "ENG-9", "ENG-8", "ENG-5", "ENG-30"]
    assert fetched == inserted
    assert shown != inserted
    assert shown != list(reversed(inserted))

    trio = {"ENG-20", "ENG-9", "ENG-3"}
    assert [far for far in fetched if far in trio] == ["ENG-20", "ENG-9", "ENG-3"]
    assert [far for far in shown if far in trio] == ["ENG-20", "ENG-3", "ENG-9"]


def test_the_relations_section_and_issue_relations_print_one_order(cli: Cli) -> None:
    """The section is the row of `issue relations`, read through the same call."""
    _fan(cli)

    section = cli.run("issue", "show", "ENG-1", "--with", "relations")
    listed = cli.run("issue", "relations", "ENG-1")

    assert section.exit_code == listed.exit_code == 0
    assert far_ends(sections(section)["RELATIONS"][1:]) == far_ends(listed.stdout.splitlines()[1:])
    assert [row.split()[0] for row in sections(section)["RELATIONS"][1:]] == [
        "blocks",
        "blocks",
        "blocks",
        "blocked",
        "duplicate",
        "related",
    ]


def test_the_relation_elements_carry_the_stored_row_only(cli: Cli) -> None:
    """Five keys, and the type is one of the four stored values — never a direction."""
    _fan(cli)

    document = payload(cli.run("issue", "show", "ENG-1", "--with", "relations", "--json"))

    assert len(document["relations"]) == 6
    for row in document["relations"]:
        assert tuple(row) == ("id", "issue_id", "related_issue_id", "type", "created_at")
        assert row["type"] in {"blocks", "duplicate", "related", "similar"}


def _fan(cli: Cli) -> tuple[list[str], list[str]]:
    """Build the six-edge fixture; answer `(published far ends, fetched far ends)`, in order.

    The identifiers are renamed by hand because the point of the fixture is their text: `ENG-3`
    sorts before `ENG-9` and `ENG-20` before `ENG-3`, so the answer is the identifier compared
    character by character and cannot be the order the rows were inserted or fetched in.
    """
    for title in ("Asking", "A", "B", "C", "D", "E", "F"):
        cli.create(title)
    numbers = {
        row["identifier"]: row["number"] for row in cli.rows("SELECT identifier, number FROM issue")
    }
    renames = (
        ("ENG-2", "ENG-30"),
        ("ENG-3", "ENG-5"),
        ("ENG-4", "ENG-9"),
        ("ENG-5", "ENG-20"),
        ("ENG-6", "ENG-8"),
        ("ENG-7", "ENG-3"),
    )
    for old, _new in renames:
        cli.execute(
            "UPDATE issue SET identifier = 'TMP-' || ? WHERE identifier = ?",
            (numbers[old], old),
        )
    for old, new in renames:
        cli.execute(
            "UPDATE issue SET identifier = ? WHERE identifier = 'TMP-' || ?",
            (new, numbers[old]),
        )
    for asking, relation_type, other in FAN_EDGES:
        assert cli.run("issue", "relate", asking, relation_type, other).exit_code == 0

    identifiers = {
        row["id"]: row["identifier"] for row in cli.rows("SELECT id, identifier FROM issue")
    }
    asking = cli.issue("ENG-1")["id"]
    document = payload(cli.run("issue", "show", "ENG-1", "--with", "relations", "--json"))
    published = [
        identifiers[
            str(row["related_issue_id"]) if row["issue_id"] == asking else str(row["issue_id"])
        ]
        for row in document["relations"]
    ]
    fetched = [
        str(
            identifiers[
                str(row["related_issue_id"]) if row["issue_id"] == asking else str(row["issue_id"])
            ]
        )
        for row in cli.rows("SELECT issue_id, related_issue_id FROM issue_relation ORDER BY rowid")
    ]
    return published, fetched


# --- the format --------------------------------------------------------------------------


def test_quiet_prints_the_identifier_with_and_without_with(cli: Cli) -> None:
    """`--quiet` is the identifier alone: the collections are a rendering of the detail."""
    cli.create("Fix SSO login")
    assert cli.run("issue", "comment", "ENG-1", "Reproduced locally").exit_code == 0

    plain = cli.run("issue", "show", "ENG-1", "--quiet")
    with_with = cli.run("issue", "show", "ENG-1", "--with", "comments", "--quiet")

    assert plain.stdout == with_with.stdout == "ENG-1\n"
    assert with_with.stderr == ""


# --- the reference and the history corpus ------------------------------------------------


def test_show_accepts_the_ulid_of_the_issue(cli: Cli) -> None:
    """A ULID is a `REF` like an identifier: one object, whichever name was typed."""
    cli.create("Fix SSO login")

    by_identifier = payload(cli.run("issue", "show", "ENG-1", "--json"))
    by_ulid = payload(cli.run("issue", "show", str(cli.issue("ENG-1")["id"]), "--json"))

    assert by_ulid["identifier"] == "ENG-1"
    assert by_ulid == by_identifier


def test_show_with_a_nonexistent_reference_is_not_found(cli: Cli) -> None:
    """A reference that resolves nowhere is the verb's one `3`, with the noun in `field`.

    The recorded scenario asserts the sentence `stderr` carries, not only the code, so the human
    line is read here byte for byte — `does not exist`, the quoted reference and the `field` the
    catalogue's `NotFound` row publishes — and `stdout` is empty because a refusal writes nothing a
    caller could mistake for an object.
    """
    result = cli.run("issue", "show", "ENG-999")

    assert result.exit_code == 3, out(result)
    assert result.stdout == ""
    assert result.stderr == "hifa: issue 'ENG-999' does not exist (field: issue)\n"

    envelope = cli.run("issue", "show", "ENG-999", "--json")

    assert envelope.exit_code == 3, out(envelope)
    assert error_of(envelope)["code"] == "not_found"
    assert error_of(envelope)["field"] == "issue"
    assert error_of(envelope)["message"] == "issue 'ENG-999' does not exist"
    assert error_of(envelope)["details"] is None


def test_the_history_entries_resolve_the_actor_and_keep_the_stored_values(cli: Cli) -> None:
    """Each entry carries the member who wrote it and the two values as the column stored them."""
    cli.create("Fix SSO login")
    assert cli.run("issue", "edit", "ENG-1", "--priority", "high").exit_code == 0

    document = payload(cli.run("issue", "show", "ENG-1", "--with", "history", "--json"))
    entries = document["history"]
    priority = next(entry for entry in entries if entry["field"] == "priority")
    member = cli.rows("SELECT id, display_name FROM member ORDER BY created_at")[0]

    assert {entry["actor_id"] for entry in entries} == {member["id"]}
    assert json.loads(priority["old_value"]) == 0
    assert json.loads(priority["new_value"]) == 2
    assert member["display_name"] in cli.run("issue", "show", "ENG-1", "--with", "history").stdout


def test_the_comment_body_folds_its_lines_and_is_capped_at_60_cells(cli: Cli) -> None:
    """A body is one cell: its line breaks become spaces and the cell gives way with an ellipsis."""
    cli.create("Fix SSO login")
    body = "first line\n" + "x" * 80
    assert cli.run("issue", "comment", "ENG-1", body).exit_code == 0

    result = cli.run("issue", "show", "ENG-1", "--with", "comments")

    header, *rows = sections(result)["COMMENTS"]
    cell = rows[0][header.index("BODY") :]
    assert len(rows) == 1
    assert "\n" not in cell
    assert cell.startswith("first line ")
    assert len(cell) <= 60
    assert cell.endswith("…")


# --- the number and the team --------------------------------------------------------------


def test_a_bare_number_resolves_inside_the_team_of_the_invocation(cli: Cli) -> None:
    """The number belongs to a team: the same `7` is a different issue in ENG and in OPS."""
    cli.create("Fix SSO login")
    renumber(cli, "ENG-1", 7)
    add_team(cli, "OPS")
    assert cli.run("issue", "create", "Ops issue", "--team", "OPS", "--json").exit_code == 0
    renumber(cli, "OPS-1", 7)

    mine = cli.run("issue", "show", "7", "--team", "ENG", "--json")
    theirs = cli.run("issue", "show", "7", "--team", "OPS", "--json")

    assert mine.exit_code == theirs.exit_code == 0, out(mine) + out(theirs)
    assert payload(mine)["identifier"] == "ENG-7"
    assert payload(theirs)["identifier"] == "OPS-7"

    table = cli.run("issue", "show", "7", "--team", "ENG")

    assert table.stdout.splitlines()[0].startswith("ENG-7  Fix SSO login")
    assert table.stderr == ""


def test_show_accepts_the_identifier_or_the_number_with_team(cli: Cli) -> None:
    """Both spellings of one issue answer the same object when the team is given."""
    cli.create("Fix SSO login")
    renumber(cli, "ENG-1", 7)

    for reference in ("ENG-7", "7"):
        result = cli.run("issue", "show", reference, "--team", "ENG", "--json")

        assert result.exit_code == 0, out(result)
        assert payload(result)["identifier"] == "ENG-7"


def test_a_bare_number_resolves_with_one_team_and_no_flag(cli: Cli) -> None:
    """A workspace with a single team answers the number with no flag and no configuration."""
    cli.create("Fix SSO login")
    renumber(cli, "ENG-1", 7)
    forget_default_team(cli)

    result = cli.run("issue", "show", "7", "--json")

    assert result.exit_code == 0, out(result)
    assert payload(result)["identifier"] == "ENG-7"


def test_a_bare_number_with_no_team_to_resolve_is_a_missing_context(cli: Cli) -> None:
    """Zero or two teams and no flag leaves the number unplaced: exit 2, and nothing is read."""
    cli.create("Fix SSO login")
    renumber(cli, "ENG-1", 7)
    add_team(cli, "OPS")
    forget_default_team(cli)

    result = cli.run("issue", "show", "7", "--json")

    error = error_of(result)

    assert result.exit_code == 2, out(result)
    assert result.stdout == ""
    assert error["code"] == "missing_context"
    assert error["field"] == "team"
    assert "team required" in error["message"]


def test_an_identifier_is_resolved_without_a_team(cli: Cli) -> None:
    """Only the number form needs a team: an identifier names its own in a workspace of two."""
    cli.create("Fix SSO login")
    add_team(cli, "OPS")
    forget_default_team(cli)

    result = cli.run("issue", "show", "ENG-1", "--json")

    assert result.exit_code == 0, out(result)
    assert payload(result)["identifier"] == "ENG-1"

    unplaced = cli.run("issue", "show", "1", "--json")

    assert unplaced.exit_code == 2, out(unplaced)
    assert error_of(unplaced)["code"] == "missing_context"
    assert error_of(unplaced)["field"] == "team"


def test_a_current_identifier_is_read_without_regard_to_case(cli: Cli) -> None:
    """`eng-1` names `ENG-1`: the identifier is compared with its case folded."""
    cli.create("Fix SSO login")

    result = cli.run("issue", "show", "eng-1", "--json")

    assert result.exit_code == 0, out(result)
    assert payload(result)["identifier"] == "ENG-1"


def test_a_number_the_team_does_not_wear_is_not_found_with_3(cli: Cli) -> None:
    """The number is looked up inside one team: `1` is ENG's issue and OPS's missing one."""
    cli.create("Fix SSO login")
    add_team(cli, "OPS")

    worn = cli.run("issue", "show", "1", "--team", "ENG", "--json")

    assert worn.exit_code == 0, out(worn)
    assert payload(worn)["identifier"] == "ENG-1"

    result = cli.run("issue", "show", "1", "--team", "OPS", "--json")

    assert result.exit_code == 3, out(result)
    assert result.stdout == ""
    assert error_of(result)["code"] == "not_found"
    assert error_of(result)["field"] == "issue"
    assert "issue '1' does not exist" in error_of(result)["message"]


def test_a_team_the_workspace_does_not_hold_is_not_found_with_3(cli: Cli) -> None:
    """The context was given and is wrong, so the refusal names the team and not the issue."""
    cli.create("Fix SSO login")

    result = cli.run("issue", "show", "7", "--team", "NOPE", "--json")

    assert result.exit_code == 3, out(result)
    assert error_of(result)["code"] == "not_found"
    assert error_of(result)["field"] == "team"


# --- the key a team left behind ----------------------------------------------------------


def test_a_previous_identifier_resolves_and_the_rename_is_a_warning_on_stderr(cli: Cli) -> None:
    """A key the team left answers with its issue, and the one warning goes to `stderr`."""
    for title in ("one", "two", "Fix SSO login"):
        cli.create(title)
    leave_key_behind(cli, old="ENG", new="PLAT")
    assert cli.run("issue", "comment", "PLAT-3", "Reproduced locally").exit_code == 0

    result = cli.run("issue", "show", "ENG-3", "--json")

    assert result.exit_code == 0, out(result)
    assert json.loads(result.stdout)["identifier"] == "PLAT-3"
    assert "identifier renamed: ENG-3 is now PLAT-3" in result.stderr

    quiet = cli.run("issue", "show", "ENG-3", "--quiet")

    assert quiet.exit_code == 0, out(quiet)
    assert quiet.stdout == "PLAT-3\n"
    assert "identifier renamed: ENG-3 is now PLAT-3" in quiet.stderr

    with_sections = cli.run("issue", "show", "ENG-3", "--with", "comments", "--json")
    renamed = [
        line for line in with_sections.stderr.splitlines() if line.startswith("identifier renamed:")
    ]

    assert with_sections.exit_code == 0, out(with_sections)
    assert renamed == ["identifier renamed: ENG-3 is now PLAT-3"]


def test_a_current_identifier_shadows_a_previous_key_of_the_same_spelling(cli: Cli) -> None:
    """The issue that wears `ENG-7` today answers, not the one of the team that left `ENG`."""
    for title in ("one", "two", "Fix SSO login"):
        cli.create(title)
    leave_key_behind(cli, old="ENG", new="PLAT")
    add_team(cli, "ENG")
    assert cli.run("issue", "create", "New ENG issue", "--team", "ENG", "--json").exit_code == 0
    renumber(cli, "ENG-1", 7)

    result = cli.run("issue", "show", "ENG-7", "--json")

    assert result.exit_code == 0, out(result)
    assert payload(result)["identifier"] == "ENG-7"
    assert "identifier renamed" not in result.stderr

    # The shadow only means something if the form it shadows answers: `ENG-3` is the team that
    # left `ENG` behind, and reaching it is what makes `ENG-7` above a decision and not a default.
    renamed = cli.run("issue", "show", "ENG-3", "--json")

    assert renamed.exit_code == 0, out(renamed)
    assert payload(renamed)["identifier"] == "PLAT-3"
    assert "identifier renamed: ENG-3 is now PLAT-3" in renamed.stderr


def test_two_teams_that_left_the_same_key_are_settled_by_the_most_recent_rotation(
    cli: Cli,
) -> None:
    """Two teams wore `ENG` and both number an issue 3: the rotation that happened last answers."""
    for title in ("one", "two", "Fix SSO login"):
        cli.create(title)
    leave_key_behind(cli, old="ENG", new="PLAT", changed_at="2026-11-01T00:00:00Z")
    add_team(cli, "ENG")
    for title in ("four", "five", "six"):
        assert cli.run("issue", "create", title, "--team", "ENG", "--json").exit_code == 0
    leave_key_behind(cli, old="ENG", new="OPS", changed_at="2026-11-20T00:00:00Z")

    result = cli.run("issue", "show", "ENG-3", "--json")

    assert result.exit_code == 0, out(result)
    assert payload(result)["identifier"] == "OPS-3"
    assert "identifier renamed: ENG-3 is now OPS-3" in result.stderr
