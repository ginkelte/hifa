"""`hifa db reindex`: the rebuild, its one line and the four verdicts before it.

The verb's contract is a sequence: the path, the read-only verdict, the writable transaction. A
refusal therefore has to leave the file byte-identical, which is what the tests below measure
rather than the exit code alone.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3

from conftest import Cli, error_json, json_stdout, plain


def _digest(cli: Cli) -> str:
    return hashlib.sha256(cli.db.read_bytes()).hexdigest()


def _indexed(cli: Cli, term: str) -> int:
    """How many rows the index answers for `term`.

    A `COUNT(*)` of `issue_fts` reads the content table of the external index and counts the
    same rows whether it is populated or not; what the index *answers* is the observable.
    """
    return int(
        cli.rows("SELECT COUNT(*) AS n FROM issue_fts WHERE issue_fts MATCH ?", (term,))[0]["n"]
    )


def test_reindex_prints_one_line_and_exits_zero(cli: Cli) -> None:
    for index in range(12):
        cli.create(f"Issue {index}")

    result = cli.run("db", "reindex")

    assert result.exit_code == 0
    assert plain(result.stdout) == "reindexed 12 issues"


def test_reindex_json_is_the_published_payload(cli: Cli) -> None:
    cli.create("Deploy roto")

    result = cli.run("db", "reindex", "--json")

    assert result.exit_code == 0
    assert json_stdout(result) == {"reindexed": 1}


def test_a_dropped_index_is_rebuilt_and_search_uses_it_again(cli: Cli) -> None:
    cli.create("Deploy roto en producción")
    cli.execute("DROP TABLE issue_fts")
    assert json_stdout(cli.run("search", "deploy", "--json"))["backend"] == "like"

    rebuilt = cli.run("db", "reindex", "--json")

    assert rebuilt.exit_code == 0
    assert json_stdout(rebuilt) == {"reindexed": 1}
    assert _indexed(cli, "deploy") == 1
    assert json_stdout(cli.run("search", "deploy", "--json"))["backend"] == "fts"


def test_an_emptied_index_is_refilled(cli: Cli) -> None:
    cli.create("Deploy roto")
    cli.create("Deploy otra vez")
    cli.execute("DELETE FROM issue_fts")
    assert _indexed(cli, "deploy") == 0

    result = cli.run("db", "reindex", "--json")

    assert result.exit_code == 0
    assert json_stdout(result) == {"reindexed": 2}
    assert _indexed(cli, "deploy") == 2


def test_a_missing_path_is_exit_three_and_creates_nothing(cli: Cli) -> None:
    cli.db.unlink()

    result = cli.run("db", "reindex", "--json")

    assert result.exit_code == 3, result.stdout + result.stderr
    payload = error_json(result)
    assert payload["code"] == "database_not_found"
    assert str(cli.db) in payload["message"]
    assert not cli.db.exists()


def test_an_empty_file_is_not_a_hifa_database_and_is_left_alone(cli: Cli) -> None:
    cli.db.unlink()
    cli.db.write_bytes(b"")

    result = cli.run("db", "reindex")

    assert result.exit_code == 3
    assert plain(result.stderr).startswith(f"hifa: not a hifa database: {cli.db}")
    assert plain(result.stderr).endswith("run 'hifa init'")
    assert cli.db.read_bytes() == b""


def test_a_foreign_database_is_refused_without_touching_it(cli: Cli) -> None:
    cli.db.unlink()
    conn = sqlite3.connect(cli.db)
    try:
        conn.execute("CREATE TABLE notes (x INTEGER)")
        conn.execute("INSERT INTO notes VALUES (1)")
        conn.commit()
    finally:
        conn.close()
    before = _digest(cli)

    result = cli.run("db", "reindex", "--json")

    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_a_hifa_database"
    assert _digest(cli) == before


def test_a_file_with_the_marker_as_a_view_is_refused(cli: Cli) -> None:
    cli.db.unlink()
    conn = sqlite3.connect(cli.db)
    try:
        conn.execute("CREATE TABLE base (id TEXT)")
        conn.execute("CREATE TABLE issue (id TEXT)")
        conn.execute("CREATE VIEW workspace AS SELECT id FROM base")
        conn.commit()
    finally:
        conn.close()

    result = cli.run("db", "reindex", "--json")

    assert result.exit_code == 3
    assert error_json(result)["code"] == "not_a_hifa_database"


def test_corrupt_bytes_are_exit_six_with_the_detail(cli: Cli) -> None:
    cli.db.unlink()
    cli.db.write_bytes(b"this is not a database" * 40)
    before = _digest(cli)

    result = cli.run("db", "reindex", "--json")

    assert result.exit_code == 6
    payload = error_json(result)
    assert payload["code"] == "corrupt_database"
    assert "file is not a database" in payload["message"]
    assert _digest(cli) == before


def test_a_wal_database_in_a_directory_it_cannot_write_is_exit_four(cli: Cli) -> None:
    cli.create("Deploy roto")
    cli.execute("PRAGMA journal_mode = WAL")
    directory = cli.db.parent
    before = _digest(cli)
    os.chmod(directory, 0o500)
    try:
        result = cli.run("db", "reindex", "--json")
    finally:
        os.chmod(directory, 0o700)

    assert result.exit_code == 4
    payload = error_json(result)
    assert payload["code"] == "DB_READONLY"
    assert "cannot be opened read-only" in payload["message"]
    assert _digest(cli) == before


def test_json_with_quiet_is_a_validation_error(cli: Cli) -> None:
    result = cli.run("db", "reindex", "--json", "--quiet")

    assert result.exit_code == 2
    assert error_json(result)["code"] == "invalid_option"


def test_quiet_prints_nothing(cli: Cli) -> None:
    cli.create("Deploy roto")

    result = cli.run("db", "reindex", "--quiet")

    assert result.exit_code == 0
    assert result.stdout == ""


def test_the_group_help_names_the_one_verb_it_has(cli: Cli) -> None:
    result = cli.run("db", "-h")

    assert result.exit_code == 0
    assert "reindex" in plain(result.stdout)
