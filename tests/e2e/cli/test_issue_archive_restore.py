"""E2E tests for the `hifa issue` write verbs that park an issue and the comment verb.

`archive` stamps `archived_at` without touching the state, is a no-op the second time, asks
for confirmation before the use case runs (so a non-interactive session without `--yes` is
refused with `confirmation_required`) and `--undo` is the reversible path. `restore` never
prompts and is a no-op on a visible issue. Error paths: 2 for the refusal, an issue in trash
and the physical-deletion flag; 3 for an unknown issue.

`comment` takes the body as an argument, from a file or from a pipe, refuses to take it from
two places at once or from a terminal that offers none, and prints the row it wrote with the
markdown folded into one cell. A reply names its parent, the fourth link of a chain is
refused, and the two layers of the issue refuse the write before any row exists: 2 in the
trash, 5 archived.
"""

from __future__ import annotations

import sqlite3

import pytest
import typer.testing

from conftest import (
    CANONICAL_KEYS,
    COMMENT_HEADER,
    COMMENT_KEYS,
    ISSUE_HEADER,
    Cli,
    error_json,
    json_stdout,
    plain,
)


def test_archive_sets_archived_at_and_keeps_the_state(cli: Cli) -> None:
    cli.create("Archivable")
    assert cli.run("issue", "move", "ENG-1", "--state", "In Progress").exit_code == 0
    result = cli.run("issue", "archive", "ENG-1", "--yes", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["archived_at"] is not None
    assert payload["state"]["name"] == "In Progress"
    assert payload["is_visible"] is False
    assert tuple(payload) == CANONICAL_KEYS
    assert cli.history_fields("ENG-1")[0] == "archived_at"


def test_archive_keeps_updated_at_and_the_second_one_writes_nothing(cli: Cli) -> None:
    cli.create("Twice")
    first = cli.run("issue", "archive", "ENG-1", "--yes", "--json")
    assert first.exit_code == 0, first.stdout + first.stderr
    archived_at = json_stdout(first)["archived_at"]
    updated_at = cli.updated_at("ENG-1")
    rows = len(cli.history("ENG-1"))
    second = cli.run("issue", "archive", "ENG-1", "--yes", "--json")
    assert second.exit_code == 0, second.stdout + second.stderr
    assert json_stdout(second)["archived_at"] == archived_at
    assert cli.updated_at("ENG-1") == updated_at
    assert len(cli.history("ENG-1")) == rows


def test_archive_prints_one_table_row(cli: Cli) -> None:
    cli.create("Archive row")
    result = cli.run("issue", "archive", "ENG-1", "--yes")
    assert result.exit_code == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].split() == ISSUE_HEADER.split()


def test_archive_quiet_prints_the_identifier(cli: Cli) -> None:
    cli.create("Archive quiet")
    result = cli.run("issue", "archive", "ENG-1", "--yes", "--quiet")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ENG-1"


def test_archive_without_yes_in_a_non_interactive_session_is_refused(cli: Cli) -> None:
    cli.create("Needs yes")
    result = cli.run("issue", "archive", "ENG-1", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "confirmation_required"
    assert cli.issue("ENG-1")["archived_at"] is None


def test_archive_with_no_input_is_refused(cli: Cli) -> None:
    cli.create("No input")
    result = cli.run("issue", "archive", "ENG-1", "--no-input", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "confirmation_required"
    assert cli.issue("ENG-1")["archived_at"] is None


def test_archive_refusal_prints_the_catalog_sentence(cli: Cli) -> None:
    cli.create("Needs yes")
    result = cli.run("issue", "archive", "ENG-1")
    assert result.exit_code == 2
    assert plain(result.stderr) == "hifa: operation canceled"
    assert result.stdout == ""
    assert cli.issue("ENG-1")["archived_at"] is None


def test_archive_undo_restores_the_issue(cli: Cli) -> None:
    cli.create("Undo")
    cli.archive("ENG-1")
    result = cli.run("issue", "archive", "ENG-1", "--undo", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["archived_at"] is None
    assert payload["is_visible"] is True
    last = cli.history("ENG-1")[0]
    assert last["field"] == "archived_at"
    assert last["new_value"] is None


def test_archive_of_an_issue_in_trash_exits_2(cli: Cli) -> None:
    cli.create("Trashed")
    cli.execute("UPDATE issue SET trashed = 1 WHERE identifier = 'ENG-1'")
    result = cli.run("issue", "archive", "ENG-1", "--yes", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "validation_error"
    assert cli.issue("ENG-1")["archived_at"] is None


def test_archive_of_an_unknown_issue_exits_3(cli: Cli) -> None:
    result = cli.run("issue", "archive", "ENG-999", "--yes", "--json")
    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_found"


def test_archive_rejects_the_physical_deletion_flag(cli: Cli) -> None:
    cli.create("No hard delete")
    result = cli.run("issue", "archive", "ENG-1", "--hard", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "invalid_option"
    assert "--hard" in plain(result.stderr)
    assert cli.issue("ENG-1")["archived_at"] is None


def test_restore_of_an_archived_issue_brings_it_back(cli: Cli) -> None:
    cli.create("Restorable")
    cli.archive("ENG-1")
    result = cli.run("issue", "restore", "ENG-1", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert payload["archived_at"] is None
    assert tuple(payload) == CANONICAL_KEYS


def test_restore_of_a_visible_issue_is_a_noop(cli: Cli) -> None:
    cli.create("Visible")
    before_rows = len(cli.history("ENG-1"))
    result = cli.run("issue", "restore", "ENG-1")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(cli.history("ENG-1")) == before_rows
    assert cli.issue("ENG-1")["trashed"] == 0


def test_restore_quiet_prints_the_identifier(cli: Cli) -> None:
    cli.create("Restore quiet")
    cli.archive("ENG-1")
    result = cli.run("issue", "restore", "ENG-1", "--quiet")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ENG-1"


def test_restore_of_an_unknown_issue_exits_3(cli: Cli) -> None:
    result = cli.run("issue", "restore", "ENG-999", "--json")
    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_found"


def test_restore_rejects_the_physical_deletion_flag(cli: Cli) -> None:
    cli.create("No hard restore")
    result = cli.run("issue", "restore", "ENG-1", "--hard", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "invalid_option"


# --- comment ----------------------------------------------------------------------------


def _comments(cli: Cli, identifier: str = "ENG-1") -> list[sqlite3.Row]:
    """Every stored comment of one issue, oldest first."""
    return cli.rows(
        """
        SELECT * FROM comment WHERE issue_id = (SELECT id FROM issue WHERE identifier = ?)
        ORDER BY created_at, id
        """,
        (identifier,),
    )


def _comment_id(cli: Cli, *args: str, input: str | None = None) -> str:
    """Id of the one comment an invocation writes."""
    result = cli.run("issue", "comment", "ENG-1", *args, "--quiet", input=input)
    assert result.exit_code == 0, result.stdout + result.stderr
    return result.stdout.strip()


def _terminal_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the runner's `stdin` report itself as a terminal.

    `CliRunner` always installs a pipe, so the one case the verb has to tell apart — a caller
    sitting at a terminal with nothing to hand over — is unreachable through `input=`. The
    wrapper typer installs is patched; when a future release renames it the harness can no
    longer fake a terminal and the test skips rather than pretending it proved anything.
    """
    wrapper = getattr(typer.testing, "_NamedTextIOWrapper", None)
    if wrapper is None:  # pragma: no cover - depends on the typer release
        pytest.skip("the CLI runner cannot be made to look like a terminal")
    monkeypatch.setattr(wrapper, "isatty", lambda self: self.name == "<stdin>")


def test_comment_with_a_body_argument_writes_one_row(cli: Cli) -> None:
    cli.create("Commented")
    result = cli.run("issue", "comment", "ENG-1", "Reproduced locally with Okta sandbox")
    assert result.exit_code == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0].split() == COMMENT_HEADER.split()
    rows = _comments(cli)
    assert len(rows) == 1
    assert rows[0]["body"] == "Reproduced locally with Okta sandbox"
    assert rows[0]["parent_id"] is None
    assert rows[0]["author_id"] is not None
    author = cli.rows("SELECT display_name FROM member WHERE id = ?", (rows[0]["author_id"],))
    assert lines[1].startswith(f"{rows[0]['id']}  ENG-1  {author[0]['display_name']} ")


def test_comment_keeps_the_body_as_given_and_the_core_trims_it(cli: Cli) -> None:
    cli.create("Trimmed")
    result = cli.run("issue", "comment", "ENG-1", "  padded on both sides  ")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _comments(cli)[0]["body"] == "padded on both sides"


def test_comment_table_folds_a_markdown_body_into_one_row(cli: Cli) -> None:
    cli.create("Markdown")
    body = "first paragraph\nsecond paragraph " + "x" * 70
    result = cli.run("issue", "comment", "ENG-1", body)
    assert result.exit_code == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert len(lines) == 2
    assert "\n" not in lines[1]
    assert "first paragraph second paragraph " in lines[1]
    # A markdown body is one row: the newline is a space, the cell keeps at most sixty cells,
    # and what it drops it announces with an ellipsis instead of cutting the text in silence.
    body_cell = lines[1].rsplit("  ", 1)[-1]
    assert body_cell.endswith("…")
    assert len(body_cell) <= 60
    assert "x" * 70 not in lines[1]


def test_comment_body_comes_from_a_file(cli: Cli) -> None:
    cli.create("From a file")
    path = cli.cwd / "body.md"
    path.write_text("from a file\nsecond line\n", encoding="utf-8")
    result = cli.run("issue", "comment", "ENG-1", "--body-file", str(path))
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _comments(cli)[0]["body"] == "from a file\nsecond line"


def test_comment_body_comes_from_stdin_through_the_file_flag(cli: Cli) -> None:
    cli.create("From the pipe")
    result = cli.run("issue", "comment", "ENG-1", "--body-file", "-", input="piped body\n")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _comments(cli)[0]["body"] == "piped body"


def test_comment_body_comes_from_stdin_with_no_flag_at_all(cli: Cli) -> None:
    cli.create("From the pipe")
    result = cli.run("issue", "comment", "ENG-1", input="body on the pipe\n")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert _comments(cli)[0]["body"] == "body on the pipe"


def test_comment_with_a_body_and_a_file_is_refused(cli: Cli) -> None:
    cli.create("Two sources")
    result = cli.run("issue", "comment", "ENG-1", "from the argument", "--body-file", "-", "--json")
    assert result.exit_code == 2
    error = error_json(result)
    assert error["code"] == "invalid_option"
    assert error["field"] == "body"
    assert _comments(cli) == []


def test_comment_file_that_cannot_be_read_is_refused(cli: Cli) -> None:
    cli.create("Missing file")
    result = cli.run("issue", "comment", "ENG-1", "--body-file", "nope.md", "--json")
    assert result.exit_code == 2
    assert error_json(result)["field"] == "body"
    assert _comments(cli) == []


def test_comment_without_a_body_at_a_terminal_is_refused(
    cli: Cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli.create("At a terminal")
    _terminal_stdin(monkeypatch)
    result = cli.run("issue", "comment", "ENG-1", "--json")
    assert result.exit_code == 2
    error = error_json(result)
    assert error["code"] == "invalid_option"
    assert error["field"] == "body"
    assert "missing comment body" in error["message"]
    assert _comments(cli) == []


@pytest.mark.parametrize("piped", ["", "   \n", "\n\t \n"])
def test_an_empty_body_on_stdin_is_refused_by_the_core(cli: Cli, piped: str) -> None:
    """The emptiness is the core's refusal, printed as the core worded it."""
    cli.create("Empty body")
    result = cli.run("issue", "comment", "ENG-1", input=piped)
    assert result.exit_code == 2
    assert "(field: body)" in plain(result.stderr)
    assert "a comment needs a body" in plain(result.stderr)
    assert _comments(cli) == []


def test_comment_with_a_parent_lands_under_it(cli: Cli) -> None:
    cli.create("Threaded")
    root = _comment_id(cli, "root")
    result = cli.run("issue", "comment", "ENG-1", "reply", "--parent", root, "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    rows = _comments(cli)
    assert len(rows) == 2
    assert rows[1]["parent_id"] == root
    assert json_stdout(result)["parent_id"] == root


def test_comment_on_an_unknown_parent_exits_3(cli: Cli) -> None:
    cli.create("No such parent")
    result = cli.run("issue", "comment", "ENG-1", "reply", "--parent", "01JCV7B", "--json")
    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_found"
    assert _comments(cli) == []


def test_comment_deeper_than_three_is_refused(cli: Cli) -> None:
    cli.create("Deep thread")
    first = _comment_id(cli, "one")
    second = _comment_id(cli, "two", "--parent", first)
    third = _comment_id(cli, "three", "--parent", second)
    result = cli.run("issue", "comment", "ENG-1", "four", "--parent", third)
    assert result.exit_code == 2
    assert plain(result.stderr) == (
        "hifa: the thread is 3 deep; reply to a shallower comment (field: parent_id)"
    )
    assert len(_comments(cli)) == 3


def test_comment_with_a_parent_of_another_issue_is_refused(cli: Cli) -> None:
    cli.create("Alpha")
    cli.create("Beta")
    foreign = _comment_id(cli, "on alpha")
    result = cli.run("issue", "comment", "ENG-2", "reply", "--parent", foreign, "--json")
    assert result.exit_code == 2
    assert error_json(result)["field"] == "parent_id"
    assert len(_comments(cli, "ENG-2")) == 0


def test_comment_on_a_trashed_issue_is_refused(cli: Cli) -> None:
    cli.create("Trashed")
    cli.execute("UPDATE issue SET trashed = 1 WHERE identifier = 'ENG-1'")
    result = cli.run("issue", "comment", "ENG-1", "anyone there?", "--json")
    assert result.exit_code == 2
    assert error_json(result)["code"] == "validation_error"
    assert "trash" in plain(result.stderr)
    assert _comments(cli) == []


def test_comment_on_an_archived_issue_exits_5(cli: Cli) -> None:
    cli.create("Archived")
    cli.archive("ENG-1")
    result = cli.run("issue", "comment", "ENG-1", "anyone there?", "--json")
    assert result.exit_code == 5
    assert error_json(result)["code"] == "invalid_transition"
    assert "archived" in plain(result.stderr)
    assert _comments(cli) == []


def test_comment_json_emits_the_canonical_object(cli: Cli) -> None:
    cli.create("Json comment")
    result = cli.run("issue", "comment", "ENG-1", "hello", "--json")
    assert result.exit_code == 0, result.stdout + result.stderr
    payload = json_stdout(result)
    assert tuple(payload) == COMMENT_KEYS
    stored = _comments(cli)[0]
    assert payload["id"] == stored["id"]
    assert payload["issue_id"] == stored["issue_id"]
    assert payload["author_id"] == stored["author_id"]
    assert payload["body"] == "hello"
    assert payload["parent_id"] is None
    assert payload["resolved_at"] is None
    assert payload["edited_at"] is None
    assert payload["created_at"].endswith("Z")
    assert payload["updated_at"] == payload["created_at"]


def test_comment_quiet_prints_the_id_it_wrote(cli: Cli) -> None:
    cli.create("Quiet comment")
    result = cli.run("issue", "comment", "ENG-1", "quiet one", "--quiet")
    assert result.exit_code == 0, result.stdout + result.stderr
    assert len(result.stdout.splitlines()) == 1
    assert result.stdout.strip() == _comments(cli)[0]["id"]
    assert result.stderr == ""


def test_comment_of_an_unknown_issue_exits_3(cli: Cli) -> None:
    result = cli.run("issue", "comment", "ENG-999", "hello", "--json")
    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_found"
