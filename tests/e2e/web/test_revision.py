"""E2E of the change signal: the revision probe and the page that polls it.

The probe answers one opaque number read from the process's **own** read connection. The tests
that matter are the ones a connection-per-request implementation cannot pass: the number has to
move when a *different* connection commits, the process has to open its probe connection exactly
once, and closing the app has to close it.

The swap, the deferral and the panel re-render are checked by hand in a browser: this host has no
Playwright.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hifa.adapters.sqlite.connection import Database
from hifa.cli.main import app as cli_app
from hifa.web.app import REVISION_POLL, create_app

HTMX = {"HX-Request": "true"}
RUNNER = CliRunner()
JS = Path(__file__).resolve().parents[3] / "src" / "hifa" / "web" / "static" / "js" / "app.js"
BODY_REVISION = re.compile(r'<body[^>]*\bdata-revision="([^"]*)"')
BODY_REFRESH = re.compile(r'<body[^>]*\bdata-refresh="([^"]*)"')
PROBE = re.compile(r"<div\b[^>]*\bid=\"revision-probe\"[^>]*>")
TRIGGER = re.compile(r'hx-trigger="([^"]*)"')


def _run(*args: str) -> None:
    result = RUNNER.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """One team (ENG) with two issues, seeded by the real CLI."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    _run("init", "--team-key", "ENG")
    _run("issue", "create", "Fix SSO login")
    _run("issue", "create", "Avatar cache")
    return tmp_path / "hifa.db"


@pytest.fixture
def served(workspace: Path) -> SimpleNamespace:
    app = create_app(workspace, default_team="ENG")
    return SimpleNamespace(db=workspace, app=app, client=TestClient(app))


@pytest.fixture
def read_only(workspace: Path) -> SimpleNamespace:
    app = create_app(workspace, default_team="ENG", read_only=True)
    return SimpleNamespace(db=workspace, app=app, client=TestClient(app))


def _revision(client: TestClient) -> int:
    response = client.get("/_/revision", headers=HTMX)
    assert response.status_code == 200, response.text
    number = response.json()["revision"]
    assert isinstance(number, int)
    return number


def _write_from_another_connection(db: Path) -> None:
    """A commit by a connection the app does not own — what `hifa issue add` is."""
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE issue SET title = title || ' (edited)' WHERE identifier = 'ENG-1'")
        conn.commit()
    finally:
        conn.close()


# --- the probe route -------------------------------------------------------------------


def test_probe_requires_htmx(served: SimpleNamespace) -> None:
    response = served.client.get("/_/revision")
    assert response.status_code == 400
    assert response.text == "fragment requires htmx"
    assert response.headers["content-type"].startswith("text/plain")
    assert "no-store" in response.headers["cache-control"]


def test_probe_answers_json_and_is_not_cacheable(served: SimpleNamespace) -> None:
    response = served.client.get("/_/revision", headers=HTMX)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert "no-store" in response.headers["cache-control"]
    assert list(response.json()) == ["revision"]
    assert isinstance(response.json()["revision"], int)


def test_probe_is_stable_when_nothing_changed(served: SimpleNamespace) -> None:
    assert _revision(served.client) == _revision(served.client)


def test_probe_changes_after_a_write_from_another_connection(served: SimpleNamespace) -> None:
    """The whole point: a number read from a per-request connection never moves here."""
    before = _revision(served.client)

    _write_from_another_connection(served.db)

    after = _revision(served.client)
    assert after != before


def test_probe_opens_one_connection_for_the_process(
    served: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three probes, one connection: the counter it reads is per connection."""
    opened: list[Database] = []
    original = Database.ro_connection

    def counting(self: Database):  # type: ignore[no-untyped-def]
        opened.append(self)
        return original(self)

    monkeypatch.setattr(Database, "ro_connection", counting)
    for _ in range(3):
        assert served.client.get("/_/revision", headers=HTMX).status_code == 200

    assert len(opened) == 1


def test_lifespan_closes_the_probe_connection(workspace: Path) -> None:
    app = create_app(workspace, default_team="ENG")
    with TestClient(app) as client:
        assert client.get("/_/revision", headers=HTMX).status_code == 200
        assert app.state.revisions.is_open
    assert not app.state.revisions.is_open


def test_read_only_mode_does_not_change_the_probe(read_only: SimpleNamespace) -> None:
    before = _revision(read_only.client)
    after = _revision(read_only.client)
    assert before == after

    conn = sqlite3.connect(read_only.db)
    try:
        (count,) = conn.execute("SELECT COUNT(*) FROM issue").fetchone()
    finally:
        conn.close()
    assert count == 2


# --- the page carries the revision it was rendered at ----------------------------------


@pytest.mark.parametrize("path", ["/ENG/board", "/ENG/list"])
def test_page_carries_the_revision_and_the_refresh_url(served: SimpleNamespace, path: str) -> None:
    response = served.client.get(path)
    assert response.status_code == 200

    body = BODY_REVISION.search(response.text)
    assert body is not None, "the body must carry data-revision"
    assert body.group(1) == response.headers["HX-Revision"]

    refresh = BODY_REFRESH.search(response.text)
    assert refresh is not None, "the body must carry data-refresh"
    assert refresh.group(1).startswith("/_/issues?view=")

    probe = PROBE.search(response.text)
    assert probe is not None, "the page must carry the poller"
    assert "/_/revision" in probe.group(0)


def test_poller_trigger_carries_the_constant_and_no_filter(served: SimpleNamespace) -> None:
    """htmx compiles a `[...]` trigger filter with `Function`, which the CSP forbids."""
    response = served.client.get("/ENG/board")
    probe = PROBE.search(response.text)
    assert probe is not None
    trigger = TRIGGER.search(probe.group(0))
    assert trigger is not None
    assert trigger.group(1) == f"every {REVISION_POLL}"
    assert "[" not in trigger.group(1)
    assert REVISION_POLL == "5s"


def test_refresh_url_keeps_the_filters_of_the_request(served: SimpleNamespace) -> None:
    response = served.client.get("/ENG/list", params={"q": "avatar", "state": "Todo"})
    refresh = BODY_REFRESH.search(response.text)
    assert refresh is not None
    url = refresh.group(1)
    assert url.startswith("/_/issues?view=list")
    assert "q=avatar" in url
    assert "state=Todo" in url


def test_pages_without_a_collection_carry_no_poller(served: SimpleNamespace) -> None:
    response = served.client.get("/ENG/issue/ENG-1")
    assert response.status_code == 200
    assert "revision-probe" not in response.text
    refresh = BODY_REFRESH.search(response.text)
    assert refresh is not None
    assert refresh.group(1) == ""


# --- the header on the rest of the layer ------------------------------------------------


def test_fragments_carry_the_header(served: SimpleNamespace) -> None:
    response = served.client.get("/_/issues", params={"team": "ENG", "view": "list"}, headers=HTMX)
    assert response.status_code == 200
    assert response.headers["HX-Revision"] == str(_revision(served.client))


def test_a_write_of_the_page_adopts_the_number_it_produced(served: SimpleNamespace) -> None:
    """The quick add answers with the post-write number, so the next tick finds it equal."""
    response = served.client.post(
        "/_/issues", params={"team": "ENG"}, headers=HTMX, content=b"title=From+the+quick+add"
    )
    assert response.status_code == 201
    assert response.headers["HX-Revision"] == str(_revision(served.client))


# --- the client --------------------------------------------------------------------------


def test_the_visibility_guard_lives_in_the_client(served: SimpleNamespace) -> None:
    source = JS.read_text(encoding="utf-8")
    assert "document.hidden" in source
    assert "htmx:beforeRequest" in source
    assert "revision-probe" in source
