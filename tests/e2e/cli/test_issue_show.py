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
    it for the cap and the order to be observable at all.
    """
    with sqlite3.connect(db) as conn:
        (issue_id,) = conn.execute(
            "SELECT id FROM issue WHERE identifier = ?", (identifier,)
        ).fetchone()
        rows: list[tuple[str, str, str | None, str | None, str]] = [
            (issue_id, f"field{index:03d}", None, "value", _stamp(index))
            for index in range(count - 1)
        ]
        rows.append((issue_id, "title", "Old", "New", "2030-01-02T00:00:00Z"))
        conn.executemany(
            "INSERT INTO issue_history"
            " (issue_id, actor_id, field, old_value, new_value, created_at)"
            " VALUES (?, NULL, ?, ?, ?, ?)",
            rows,
        )


def _stamp(index: int) -> str:
    """An ISO-8601 instant that rises with `index`, all of them after the suite's clock."""
    return f"2030-01-01T{index // 60:02d}:{index % 60:02d}:00Z"


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


def test_the_history_section_is_newest_first_and_capped_at_100(cli: Cli) -> None:
    """The cap is the read's, and the order is `created_at` descending with the id beside it."""
    cli.create("Busy")
    add_history(cli.db, "ENG-1", 120)

    result = cli.run("issue", "show", "ENG-1", "--with", "history")

    assert result.exit_code == 0, out(result)
    rows = sections(result)["HISTORY"]
    assert len(rows) == 101
    assert "title" in rows[1]


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


def test_the_relations_come_out_in_the_declared_order(cli: Cli) -> None:
    """Reading first, then the far end's identifier as text; the fetch order is neither.

    The edges are inserted `related/ENG-30, blocks/ENG-20, duplicate/ENG-5, blocks/ENG-9` and
    `ENG-8 blocks ENG-1` last, so both the insertion order and its reverse differ from the answer:
    `blocks` first, `ENG-20` before `ENG-9` as text, then the inverse, the duplicate and the one
    symmetric edge.
    """
    inserted = [("related", "ENG-30"), ("blocks", "ENG-20"), ("duplicate", "ENG-5")]
    shown = _fan(cli, inserted)

    assert shown == ["ENG-20", "ENG-9", "ENG-8", "ENG-5", "ENG-30"]
    assert shown != [other for _type, other in inserted]
    assert shown != list(reversed([other for _type, other in inserted]))


def test_the_relations_section_and_issue_relations_print_one_order(cli: Cli) -> None:
    """The section is the row of `issue relations`, read through the same call."""
    _fan(cli, [("related", "ENG-30"), ("blocks", "ENG-20"), ("duplicate", "ENG-5")])

    section = cli.run("issue", "show", "ENG-1", "--with", "relations")
    listed = cli.run("issue", "relations", "ENG-1")

    assert section.exit_code == listed.exit_code == 0
    assert far_ends(sections(section)["RELATIONS"][1:]) == far_ends(listed.stdout.splitlines()[1:])
    assert [row.split()[0] for row in sections(section)["RELATIONS"][1:]] == [
        "blocks",
        "blocks",
        "blocked",
        "duplicate",
        "related",
    ]


def test_the_relation_elements_carry_the_stored_row_only(cli: Cli) -> None:
    """Five keys, and the type is one of the four stored values — never a direction."""
    _fan(cli, [("related", "ENG-30"), ("blocks", "ENG-20"), ("duplicate", "ENG-5")])

    document = payload(cli.run("issue", "show", "ENG-1", "--with", "relations", "--json"))

    assert len(document["relations"]) == 5
    for row in document["relations"]:
        assert tuple(row) == ("id", "issue_id", "related_issue_id", "type", "created_at")
        assert row["type"] in {"blocks", "duplicate", "related", "similar"}


def _fan(cli: Cli, inserted: list[tuple[str, str]]) -> list[str]:
    """Build the five-edge fixture and answer the far ends the detail publishes, in order.

    The identifiers are renamed by hand because the point of the fixture is their text: `ENG-20`
    sorts before `ENG-9`, and the assertion is about the comparison on the string.
    """
    for title in ("Asking", "A", "B", "C", "D", "E"):
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
    for relation_type, other in inserted:
        assert cli.run("issue", "relate", "ENG-1", relation_type, other).exit_code == 0
    assert cli.run("issue", "relate", "ENG-1", "blocks", "ENG-9").exit_code == 0
    assert cli.run("issue", "relate", "ENG-8", "blocks", "ENG-1").exit_code == 0

    identifiers = {
        row["id"]: row["identifier"] for row in cli.rows("SELECT id, identifier FROM issue")
    }
    asking = cli.issue("ENG-1")["id"]
    document = payload(cli.run("issue", "show", "ENG-1", "--with", "relations", "--json"))
    return [
        identifiers[
            str(row["related_issue_id"]) if row["issue_id"] == asking else str(row["issue_id"])
        ]
        for row in document["relations"]
    ]


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
    """A reference that resolves nowhere is the verb's one `3`, with the noun in `field`."""
    result = cli.run("issue", "show", "ENG-999", "--json")

    assert result.exit_code == 3, out(result)
    assert error_of(result)["code"] == "not_found"
    assert error_of(result)["field"] == "issue"


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
