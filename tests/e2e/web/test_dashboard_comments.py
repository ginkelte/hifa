"""The dashboard's comment write: the box, the two fragments, and the layers that refuse it.

Every test asserts a **positive** as well as an absence. A page that renders nothing satisfies
"carries no composer", so the two parked-issue tests assert `Restore` in the same response — the
control that proves the section was rendered at all and gated, instead of missing.

The fixture seeds through the CLI for the same reason the layer tests do: the CLI is the
reference surface, and a dashboard test that seeded through the dashboard would be testing its
own setup. The one write the CLI cannot do — a reply sent by a client that built its own request
— is the exception, and it goes through the HTTP form the way such a client would.
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


@pytest.fixture
def read_only(seeded: SimpleNamespace) -> SimpleNamespace:
    """The same workspace, served by a server that refuses every write."""
    return SimpleNamespace(
        db=seeded.db,
        client=TestClient(create_app(seeded.db, default_team="ENG", read_only=True)),
    )


def _comments(db: Path) -> list[sqlite3.Row]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        return list(conn.execute("SELECT * FROM comment ORDER BY created_at, id"))


def _page(seeded: SimpleNamespace, identifier: str | None = None):
    return seeded.client.get(f"/ENG/issue/{identifier or seeded.identifier}")


def _panel(seeded: SimpleNamespace, identifier: str | None = None):
    return seeded.client.get(f"/_/issues/{identifier or seeded.identifier}/panel", headers=HTMX)


# --- a visible issue: the box is there to type in -----------------------------------------


def test_a_visible_issue_carries_the_composer_on_the_page_and_on_the_panel(seeded) -> None:
    """The positive the two parked tests are the absence of: the box renders where it can write."""
    page = _page(seeded).text
    panel = _panel(seeded).text

    for body in (page, panel):
        assert 'class="comment-composer"' in body
        assert 'name="body"' in body
        assert 'id="comment-list"' in body
        assert "/ENG/issue/ENG-1/comment" in body
        assert "/ENG/issue/ENG-1/restore" not in body

    # The panel's copy posts the fragment and appends to the list; the page's copy is the
    # no-JavaScript path and carries no `hx-` at all.
    assert 'hx-post="/_/comments/ENG-1"' in panel
    assert 'hx-target="#comment-list"' in panel
    assert "hx-post" not in page


def test_a_trashed_issue_offers_restore_and_no_composer(seeded) -> None:
    _run("issue", "delete", "ENG-1", "--yes")

    page = _page(seeded)
    panel = _panel(seeded)

    assert page.status_code == 200
    assert panel.status_code == 200
    for body in (page.text, panel.text):
        assert "/ENG/issue/ENG-1/restore" in body
        assert 'name="body"' not in body
        assert 'class="comment-composer"' not in body


def test_an_archived_issue_offers_restore_and_no_composer(seeded) -> None:
    _run("issue", "archive", "ENG-1", "--yes")

    page = _page(seeded)
    panel = _panel(seeded)

    assert page.status_code == 200
    assert panel.status_code == 200
    for body in (page.text, panel.text):
        assert "/ENG/issue/ENG-1/restore" in body
        assert 'name="body"' not in body


# --- the write ---------------------------------------------------------------------------


def test_a_comment_from_the_page_redirects_to_the_section_that_holds_it(seeded) -> None:
    response = seeded.client.post(
        "/ENG/issue/ENG-1/comment",
        data={"body": "First look at this"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ENG/issue/ENG-1#comments"
    rows = _comments(seeded.db)
    assert len(rows) == 1
    assert rows[0]["body"] == "First look at this"
    assert rows[0]["parent_id"] is None
    # The dashboard has no sessions, so the author is the local member — the one member the
    # workspace holds, the same answer the CLI gives.
    with sqlite3.connect(seeded.db) as conn:
        (member_id,) = conn.execute("SELECT id FROM member").fetchone()
    assert rows[0]["author_id"] == member_id
    assert "First look at this" in _page(seeded).text


def test_the_panel_fragment_answers_the_one_item_it_wrote(seeded) -> None:
    response = seeded.client.post(
        "/_/comments/ENG-1", data={"body": "From the panel"}, headers=HTMX
    )

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "comment-created"
    assert response.headers["HX-Revision"]
    assert response.text.count('class="comment"') == 1
    assert "From the panel" in response.text
    # The item is the answer, not the list: the client appends it to the list it names.
    assert "<ol" not in response.text
    assert len(_comments(seeded.db)) == 1


def test_the_list_refreshes_itself_after_a_write(seeded) -> None:
    """The event re-reads the list from the server, so the placeholder cannot outlive it."""
    body = _panel(seeded).text

    assert 'hx-get="/_/comments/ENG-1"' in body
    assert 'hx-trigger="comment-created from:body"' in body
    assert 'hx-swap="outerHTML"' in body
    assert 'class="empty"' in body


def test_the_list_fragment_answers_the_list_and_not_the_box(seeded) -> None:
    seeded.client.post("/_/comments/ENG-1", data={"body": "One"}, headers=HTMX)

    response = seeded.client.get("/_/comments/ENG-1", headers=HTMX)

    assert response.status_code == 200
    assert '<ol id="comment-list"' in response.text
    assert "One" in response.text
    # The swap replaces this element and nothing else: a fragment that carried the composer
    # would empty the box the reader is typing in.
    assert 'class="comment-composer"' not in response.text


# --- refusals, each with the row count it did not move -----------------------------------


def test_an_empty_body_is_refused_with_the_control_marked_and_the_box_back(seeded) -> None:
    response = seeded.client.post("/ENG/issue/ENG-1/comment", data={"body": "   "})

    assert response.status_code == 422
    assert "a comment needs a body" in response.text
    assert 'class="comment-composer"' in response.text
    # The sentence and the mark are two separate promises: a page that printed the message and
    # left the box plain would read as a refusal of something the reader cannot see.
    assert "is-invalid" in response.text
    assert 'aria-invalid="true"' in response.text
    assert _comments(seeded.db) == []


def test_a_stale_client_that_comments_a_trashed_issue_is_refused(seeded) -> None:
    _run("issue", "delete", "ENG-1", "--yes")

    response = seeded.client.post("/_/comments/ENG-1", data={"body": "Anyone?"}, headers=HTMX)

    assert response.status_code == 422
    assert "ENG-1 is in the trash; restore it first" in response.text
    assert _comments(seeded.db) == []


def test_a_stale_client_that_comments_an_archived_issue_is_a_conflict(seeded) -> None:
    _run("issue", "archive", "ENG-1", "--yes")

    response = seeded.client.post("/_/comments/ENG-1", data={"body": "Hello?"}, headers=HTMX)

    assert response.status_code == 409
    assert "ENG-1 is archived; restore it first" in response.text
    assert _comments(seeded.db) == []


def test_a_reply_that_would_sit_deeper_than_three_is_refused(seeded) -> None:
    _run("issue", "comment", "ENG-1", "One")
    first = _comments(seeded.db)[0]["id"]
    _run("issue", "comment", "ENG-1", "Two", "--parent", first)
    second = _comments(seeded.db)[1]["id"]
    _run("issue", "comment", "ENG-1", "Three", "--parent", second)
    third = _comments(seeded.db)[2]["id"]

    # The third comment is depth 3, so a reply under it would be the fourth level.
    response = seeded.client.post(
        "/ENG/issue/ENG-1/comment",
        data={"body": "Four", "parent": str(third)},
    )

    assert response.status_code == 422
    assert "the thread is 3 deep; reply to a shallower comment" in response.text
    assert len(_comments(seeded.db)) == 3


def test_a_read_only_server_refuses_both_comment_routes(read_only) -> None:
    page = read_only.client.post("/ENG/issue/ENG-1/comment", data={"body": "Nope"})
    fragment = read_only.client.post("/_/comments/ENG-1", data={"body": "Nope"}, headers=HTMX)

    assert page.status_code == 403
    assert fragment.status_code == 403
    assert "server in read-only mode" in page.text
    # The fragment prints the module's one read-only sentence, not a second one that reads
    # almost the same: the page and the fragment must be distinguishable from the cross-site
    # refusal without reading the status code.
    assert "server is read-only" in fragment.text
    assert _comments(read_only.db) == []
