"""E2E of the two edit paths of the dashboard.

The detail form is a real form post: it saves and redirects. The inline control of a row and the
form of the panel are htmx requests: they answer the element they changed, never a page. Both go
through the same write, so these tests read the database beside the HTML — a `303` proves nothing
if the row was not written, and a `200` proves nothing if it re-renders the whole document under
an element the client swaps in place.
"""

from __future__ import annotations

import re
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
#: The title the fixture's issue already holds — the value a no-op edit sends back.
STORED_TITLE = "Fix login"
TITLE_INPUT = re.compile(r'<input\b[^>]*\bname="title"[^>]*>')
HISTORY = (
    "SELECT h.field FROM issue_history h JOIN issue i ON i.id = h.issue_id"
    " WHERE i.identifier = ? ORDER BY h.id"
)


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolated cwd + HIFA_DB with one team (ENG) and one issue in Todo with a description."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    _run("init", "--team-key", "ENG")
    _run("issue", "create", STORED_TITLE, "--state", "Todo", "--description", "first draft")
    db = tmp_path / "hifa.db"
    with sqlite3.connect(db) as conn:
        (identifier,) = conn.execute("SELECT identifier FROM issue").fetchone()
    return SimpleNamespace(
        db=db,
        identifier=identifier,
        client=TestClient(create_app(db, default_team="ENG")),
    )


def _value_of(column: str, db: Path, identifier: str) -> object:
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            f"SELECT {column} FROM issue WHERE identifier = ?",  # noqa: S608 - a literal column
            (identifier,),
        ).fetchone()
    assert row is not None, f"{identifier} is not in the database"
    return row[0]


def _history(db: Path, identifier: str) -> list[str]:
    """`field` of every history row of the issue, oldest first — the fixture's `created` included.

    The write under test is read as a count of one field, so the row `hifa issue create` left
    behind (and the rename of the branch slug the same update records) never turns a one-row
    assertion into a coincidence.
    """
    with sqlite3.connect(db) as conn:
        return [field for (field,) in conn.execute(HISTORY, (identifier,))]


def _title_input(html: str) -> str:
    match = TITLE_INPUT.search(html)
    assert match is not None, "the edit control of the title is not in the answer"
    return match.group(0)


def test_the_edit_form_saves_the_field_and_redirects_to_the_detail(
    seeded: SimpleNamespace,
) -> None:
    response = seeded.client.post(
        f"/ENG/issue/{seeded.identifier}",
        data={"title": "Fix SSO login"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/ENG/issue/{seeded.identifier}"
    assert _value_of("title", seeded.db, seeded.identifier) == "Fix SSO login"
    assert _history(seeded.db, seeded.identifier).count("title") == 1


def test_an_edit_that_changes_nothing_writes_no_history_row(seeded: SimpleNamespace) -> None:
    before = _history(seeded.db, seeded.identifier)

    response = seeded.client.post(
        f"/ENG/issue/{seeded.identifier}",
        data={"title": STORED_TITLE},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/ENG/issue/{seeded.identifier}"
    assert _history(seeded.db, seeded.identifier) == before, "a no-op edit wrote a history row"
    assert _value_of("title", seeded.db, seeded.identifier) == STORED_TITLE


def test_the_inline_edit_answers_the_row_it_changed(seeded: SimpleNamespace) -> None:
    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "list"},
        headers=HTMX,
        data={"title": "Fix SSO login"},
    )

    assert response.status_code == 200
    # The answer carries the event of the layer write, so the page's listeners hear the edit.
    assert response.headers["HX-Trigger"] == "issue-updated"
    # The answer is the row, not a document that happens to contain one: htmx swaps it into the
    # table body, so a layout arriving here would nest a page inside a `<tr>`.
    assert response.text.lstrip().startswith("<tr")
    assert f'id="issue-{seeded.identifier}"' in response.text
    assert "Fix SSO login" in response.text
    assert "<html" not in response.text
    assert _value_of("title", seeded.db, seeded.identifier) == "Fix SSO login"
    assert _history(seeded.db, seeded.identifier).count("title") == 1


def test_the_panel_edit_answers_the_panel(seeded: SimpleNamespace) -> None:
    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "panel"},
        headers=HTMX,
        data={"description": "**final** draft"},
    )

    assert response.status_code == 200
    # Same handler, other view: the event is announced here too.
    assert response.headers["HX-Trigger"] == "issue-updated"
    assert '<aside id="panel"' in response.text
    assert "<strong>final</strong>" in response.text
    assert "<html" not in response.text
    assert _value_of("description", seeded.db, seeded.identifier) == "**final** draft"


def test_an_empty_title_is_refused_and_nothing_is_written(seeded: SimpleNamespace) -> None:
    before = _history(seeded.db, seeded.identifier)

    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "list"},
        headers=HTMX,
        data={"title": ""},
    )

    assert response.status_code == 422
    # A refusal keeps the element it refused: the row comes back marked, not replaced by a page.
    assert f'id="issue-{seeded.identifier}"' in response.text
    assert "<html" not in response.text
    control = _title_input(response.text)
    assert "is-invalid" in control
    assert 'aria-invalid="true"' in control
    # The control shows the stored value, never the one that was refused.
    assert f'value="{STORED_TITLE}"' in control
    assert 'value=""' not in control
    assert _value_of("title", seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier) == before, "a refused edit wrote a history row"


def test_an_edit_of_an_unknown_identifier_is_404_and_creates_nothing(
    seeded: SimpleNamespace,
) -> None:
    with sqlite3.connect(seeded.db) as conn:
        before = conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0]

    response = seeded.client.post(
        "/ENG/issue/ENG-999", data={"title": "ghost"}, follow_redirects=False
    )

    assert response.status_code == 404
    assert "ENG-999" in response.text
    with sqlite3.connect(seeded.db) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM issue WHERE identifier = 'ENG-999'").fetchone()[0]
            == 0
        )
        assert conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0] == before
    assert _value_of("title", seeded.db, seeded.identifier) == STORED_TITLE


def test_the_edit_fragment_without_htmx_is_four_hundred(seeded: SimpleNamespace) -> None:
    """`/_/*` is htmx-only: a plain request would be answered with a fragment to nobody."""
    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "list"},
        data={"title": "Fix SSO login"},
    )

    assert response.status_code == 400
    assert response.text == "fragment requires htmx"
    assert _value_of("title", seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier).count("title") == 0
