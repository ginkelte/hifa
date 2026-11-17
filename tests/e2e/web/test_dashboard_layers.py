"""The dashboard's layer writes: they answer, they refuse, and they leave no control behind.

Every test asserts a **positive** as well as what a write must not do. A page that renders nothing
satisfies "carries no edit form" and "offers no state control", so an absence-only test would pass
on the exact bug these writes exist to fix — an issue in a layer whose action group was gated away
instead of being switched to its own branch.

The fixture seeds through the CLI, because the CLI is the reference surface for every one of these
writes and a dashboard test that seeds through the dashboard would be testing its own setup.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hifa.cli.main import app as cli_app
from hifa.web.app import create_app

runner = CliRunner()
HTMX = {"HX-Request": "true"}
TITLE = "Fix login"


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """An isolated workspace with one team, one issue in Todo and a client on its database."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    _run("init", "--team-key", "ENG")
    _run("issue", "create", TITLE, "--state", "Todo")
    db = tmp_path / "hifa.db"
    with sqlite3.connect(db) as conn:
        (identifier,) = conn.execute("SELECT identifier FROM issue").fetchone()
    return SimpleNamespace(
        db=db,
        identifier=identifier,
        client=TestClient(create_app(db, default_team="ENG")),
    )


def _column(db: Path, column: str, identifier: str = "ENG-1") -> object:
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            f"SELECT {column} FROM issue WHERE identifier = ?",  # noqa: S608 - a literal column
            (identifier,),
        ).fetchone()
    assert row is not None, f"{identifier} is not in the database"
    return row[0]


def _history(db: Path, identifier: str = "ENG-1") -> int:
    with sqlite3.connect(db) as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM issue_history h JOIN issue i ON i.id = h.issue_id"
            " WHERE i.identifier = ?",
            (identifier,),
        ).fetchone()
    return int(count)


def _page(seeded: SimpleNamespace, identifier: str = "ENG-1"):
    return seeded.client.get(f"/ENG/issue/{identifier}")


def _panel(seeded: SimpleNamespace, identifier: str = "ENG-1"):
    return seeded.client.get(f"/_/issues/{identifier}/panel", headers=HTMX)


# --- a visible issue: the group renders, and it renders the controls ---------------------


def test_a_visible_issue_carries_the_edit_form_and_both_confirmation_blocks(seeded) -> None:
    """The positive the layer tests are the absence of: a visible issue's group is not empty."""
    page = _page(seeded).text
    panel = _panel(seeded).text

    for body in (page, panel):
        assert 'class="action-group"' in body
        assert 'action="/ENG/issue/ENG-1"' in body
        assert 'name="state_id"' in body
        assert '<summary class="btn">Archive</summary>' in body
        assert '<summary class="btn">Delete</summary>' in body
        assert 'class="action-restore"' not in body


# --- archive -----------------------------------------------------------------------------


def test_archive_from_the_page_redirects_to_the_list(seeded) -> None:
    response = seeded.client.post(
        "/ENG/issue/ENG-1/archive", data={"confirm": "1"}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ENG/list"
    assert _column(seeded.db, "archived_at") is not None


def test_archive_from_the_panel_answers_a_bodyless_no_content(seeded) -> None:
    response = seeded.client.post("/_/issues/ENG-1/archive", data={"confirm": "1"}, headers=HTMX)

    assert response.status_code == 204
    assert response.content == b""
    assert response.headers["HX-Trigger"] == "issue-archived"
    assert response.headers["HX-Revision"]
    assert _column(seeded.db, "archived_at") is not None


def test_archive_without_the_confirmation_writes_nothing_and_reopens_the_block(seeded) -> None:
    before = _history(seeded.db)

    response = seeded.client.post("/ENG/issue/ENG-1/archive", data={}, follow_redirects=False)

    assert response.status_code == 422
    assert '<details class="confirm" open>' in response.text
    assert "confirm the archive of ENG-1" in response.text
    assert _history(seeded.db) == before
    assert _column(seeded.db, "archived_at") is None


def test_a_parent_with_open_children_cannot_be_archived(seeded) -> None:
    _run("issue", "create", "Child", "--state", "Todo", "--parent", "ENG-1")

    response = seeded.client.post(
        "/ENG/issue/ENG-1/archive", data={"confirm": "1"}, follow_redirects=False
    )

    assert response.status_code == 422
    assert "cannot archive ENG-1: it has 1 open sub-issues" in response.text
    assert _column(seeded.db, "archived_at") is None


# --- delete ------------------------------------------------------------------------------


def test_delete_from_the_panel_answers_no_content_with_its_own_trigger(seeded) -> None:
    response = seeded.client.post("/_/issues/ENG-1/delete", data={"confirm": "1"}, headers=HTMX)

    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "issue-deleted"
    assert _column(seeded.db, "trashed") == 1


def test_deleting_an_issue_already_in_the_trash_is_a_no_op(seeded) -> None:
    _run("issue", "delete", "ENG-1", "--yes")
    updated_before = _column(seeded.db, "updated_at")
    history_before = _history(seeded.db)

    response = seeded.client.post("/_/issues/ENG-1/delete", data={"confirm": "1"}, headers=HTMX)

    assert response.status_code == 204
    assert _column(seeded.db, "trashed") == 1
    assert _column(seeded.db, "updated_at") == updated_before
    assert _history(seeded.db) == history_before


def test_delete_still_asks_and_says_what_it_will_do(seeded) -> None:
    response = seeded.client.post("/ENG/issue/ENG-1/delete", data={}, follow_redirects=False)

    assert response.status_code == 422
    assert '<details class="confirm" open>' in response.text
    assert '<summary class="btn">Delete</summary>' in response.text
    assert "This sends ENG-1 to the trash. Restore brings it back." in response.text
    assert "Yes, delete ENG-1" in response.text
    assert "confirm the delete of ENG-1" in response.text
    assert _column(seeded.db, "trashed") == 0


# --- the layers' surfaces ----------------------------------------------------------------


def test_an_archived_issue_offers_restore_and_nothing_else(seeded) -> None:
    _run("issue", "archive", "ENG-1", "--yes")

    page = _page(seeded)
    panel = _panel(seeded)

    assert page.status_code == 200
    assert panel.status_code == 200
    for body in (page.text, panel.text):
        assert 'class="action-group"' in body
        assert 'class="action-restore"' in body
        assert 'action="/ENG/issue/ENG-1/restore"' in body
        assert ">Restore</button>" in body
        assert 'name="title"' not in body
        assert '<summary class="btn">Archive</summary>' not in body
        assert '<summary class="btn">Delete</summary>' not in body


def test_a_trashed_issue_is_served_with_restore_as_its_only_control(seeded) -> None:
    """The trash is a page, not a `404`: this route is the whole way back from a delete."""
    _run("issue", "delete", "ENG-1", "--yes")

    page = _page(seeded)
    panel = _panel(seeded)

    assert page.status_code == 200
    assert panel.status_code == 200
    for body in (page.text, panel.text):
        assert TITLE in body
        assert 'class="action-restore"' in body
        assert ">Restore</button>" in body
        assert 'name="title"' not in body
        assert '<summary class="btn">Archive</summary>' not in body
        assert '<summary class="btn">Delete</summary>' not in body


def test_the_row_and_card_fragments_keep_the_trash_out(seeded) -> None:
    """The asymmetry is the contract: two routes serve a trashed issue, no other one does."""
    _run("issue", "delete", "ENG-1", "--yes")

    assert seeded.client.get("/_/issues/ENG-1/row", headers=HTMX).status_code == 404
    assert seeded.client.get("/_/issues/ENG-1/card", headers=HTMX).status_code == 404


def test_restore_clears_the_layer_and_lands_on_the_detail(seeded) -> None:
    _run("issue", "delete", "ENG-1", "--yes")

    response = seeded.client.post("/ENG/issue/ENG-1/restore", data={}, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/ENG/issue/ENG-1"
    assert _column(seeded.db, "trashed") == 0
    assert _column(seeded.db, "archived_at") is None


def test_restore_from_the_panel_answers_no_content(seeded) -> None:
    _run("issue", "archive", "ENG-1", "--yes")

    response = seeded.client.post("/_/issues/ENG-1/restore", data={}, headers=HTMX)

    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "issue-restored"
    assert _column(seeded.db, "archived_at") is None


# --- move ---------------------------------------------------------------------------------


def _state_id(db: Path, name: str) -> str:
    with sqlite3.connect(db) as conn:
        (state_id,) = conn.execute(
            "SELECT id FROM workflow_state WHERE name = ? AND team_id ="
            " (SELECT id FROM team WHERE key = 'ENG')",
            (name,),
        ).fetchone()
    return str(state_id)


def test_move_from_the_page_lands_on_the_detail_with_the_state_written(seeded) -> None:
    target = _state_id(seeded.db, "Done")

    response = seeded.client.post(
        "/ENG/issue/ENG-1/move", data={"state_id": target}, follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ENG/issue/ENG-1"
    with sqlite3.connect(seeded.db) as conn:
        (name,) = conn.execute(
            "SELECT s.name FROM issue i JOIN workflow_state s ON s.id = i.state_id"
            " WHERE i.identifier = 'ENG-1'"
        ).fetchone()
    assert name == "Done"


def test_move_from_the_panel_answers_a_bodyless_no_content(seeded) -> None:
    target = _state_id(seeded.db, "In Progress")

    response = seeded.client.post("/_/issues/ENG-1/move", data={"state_id": target}, headers=HTMX)

    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "issue-moved"
    assert response.headers["HX-Revision"]


def test_a_state_that_names_nothing_is_a_not_found(seeded) -> None:
    """The form carries ids the server offered; an id it did not offer names no column."""
    response = seeded.client.post(
        "/ENG/issue/ENG-1/move",
        data={"state_id": "01J8ZQ7K3M4N5P6Q7R8S9T0V1W"},
        follow_redirects=False,
    )

    assert response.status_code == 404
    # The catalogue words this refusal `workflow state does not exist`; the handler prints the
    # domain's own message, which names the reference it could not resolve. The status is the
    # contract here and the wording is the one the handler already gives every `NotFound`.
    assert "does not exist" in response.text


def test_an_archived_issue_cannot_be_moved(seeded) -> None:
    _run("issue", "archive", "ENG-1", "--yes")
    target = _state_id(seeded.db, "Done")

    response = seeded.client.post(
        "/ENG/issue/ENG-1/move", data={"state_id": target}, follow_redirects=False
    )

    assert response.status_code == 409
    assert "ENG-1 is archived; restore it first" in response.text


def test_the_panel_retargets_a_refused_move(seeded) -> None:
    _run("issue", "delete", "ENG-1", "--yes")

    response = seeded.client.post(
        "/_/issues/ENG-1/move", data={"state_id": _state_id(seeded.db, "Done")}, headers=HTMX
    )

    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#panel"
    assert "ENG-1 is in the trash; restore it first" in response.text
