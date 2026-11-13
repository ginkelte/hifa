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

from hifa.adapters.sqlite.connection import Database, RevisionWatch
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


def test_the_probe_executes_one_pragma(
    served: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One request, one read: the middleware caches the observation and the route reuses it.

    A second `PRAGMA` is not a performance detail — the body and the `HX-Revision` header would be
    two observations of a number that is only meaningful as one, so a commit landing between them
    answers a page it did not render.
    """
    statements: list[str] = []
    original = Database.ro_connection

    def traced(self: Database):  # type: ignore[no-untyped-def]
        connection = original(self)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(Database, "ro_connection", traced)
    response = served.client.get("/_/revision", headers=HTMX)

    assert response.status_code == 200
    assert statements == ["PRAGMA data_version"], statements
    assert response.headers["HX-Revision"] == str(response.json()["revision"])


def test_probe_without_a_number_is_a_503_and_a_null(
    served: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe that cannot answer says so: the client skips the tick instead of reloading."""
    monkeypatch.setattr(RevisionWatch, "read", lambda self: None)

    response = served.client.get("/_/revision", headers=HTMX)

    assert response.status_code == 503
    assert response.json() == {"revision": None}
    assert "no-store" in response.headers["cache-control"]


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


@pytest.mark.parametrize(("path", "view"), [("/ENG/board", "board"), ("/ENG/list", "list")])
def test_page_carries_the_revision_and_the_refresh_url(
    served: SimpleNamespace, path: str, view: str
) -> None:
    response = served.client.get(path)
    assert response.status_code == 200

    body = BODY_REVISION.search(response.text)
    assert body is not None, "the body must carry data-revision"
    assert body.group(1) == response.headers["HX-Revision"]

    refresh = BODY_REFRESH.search(response.text)
    assert refresh is not None, "the body must carry data-refresh"
    # The page reloads the collection it *is*: a list URL on a board page would swap the wrong
    # fragment into #board on the first foreign write.
    assert refresh.group(1).startswith(f"/_/issues?view={view}&"), refresh.group(1)

    probe = PROBE.search(response.text)
    assert probe is not None, "the page must carry the poller"
    assert "/_/revision" in probe.group(0)


def test_the_poller_inserts_nothing(served: SimpleNamespace) -> None:
    """`hx-swap="none"`: the number is read, never put anywhere in the page."""
    response = served.client.get("/ENG/board")
    probe = PROBE.search(response.text)
    assert probe is not None
    assert 'hx-swap="none"' in probe.group(0)


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


def test_the_fragment_that_reloads_the_collection_carries_its_own_target(
    served: SimpleNamespace,
) -> None:
    """The reload has to replace the node: the fragment is the target, id included.

    And the cards and rows inside it arrive with `hx-get`, `hx-target` and `hx-push-url`, which
    only an htmx swap binds — a fetch and an `innerHTML` leaves them inert.
    """
    board = served.client.get("/_/issues", params={"team": "ENG", "view": "board"}, headers=HTMX)
    assert board.status_code == 200
    assert board.text.startswith('<div class="board" id="board"')
    assert board.text.count('hx-target="#panel"') >= 2, "cards carry their own panel request"
    assert "hx-push-url=" in board.text

    rows = served.client.get("/_/issues", params={"team": "ENG", "view": "list"}, headers=HTMX)
    assert rows.status_code == 200
    assert rows.text.startswith('<tbody id="issues-tbody">')

    page = served.client.get("/ENG/board")
    assert 'hx-target="#board" hx-swap="outerHTML"' in page.text, "the filter bar replaces too"


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


def _block(marker: str) -> str:
    """The brace-matched block that follows `marker` in `app.js`.

    A substring search for a name proves nothing — the guard of this section passed with its
    condition inverted — so the assertions below run over one block each, and a name that moved
    into another handler is no longer inside the block that is being checked.
    """
    source = JS.read_text(encoding="utf-8")
    start = source.index(marker)
    opening = source.index("{", start)
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening : index + 1]
    raise AssertionError(f"unbalanced braces after {marker}")


def test_the_visibility_guard_cancels_the_poll_only_while_the_tab_is_hidden(
    served: SimpleNamespace,
) -> None:
    """The cancel has a direction: hidden cancels, visible runs. Both halves are pinned."""
    guard = _block('addEventListener("htmx:beforeRequest"')

    assert "isProbe(event.detail.elt)" in guard
    # the probe branch decides before the visibility condition is consulted
    assert guard.index("isProbe(") < guard.index("document.hidden")
    # `if (document.hidden) event.preventDefault()` and nothing looser
    assert re.search(r"if\s*\(\s*document\.hidden\s*\)\s*\{?\s*event\.preventDefault\(\)", guard)
    assert not re.search(r"!\s*document\.hidden", guard), "an inverted guard polls a hidden tab"
    assert guard.count("preventDefault") == 1, "an unconditional cancel never polls"


def test_the_collection_reload_is_an_htmx_swap_of_the_whole_node() -> None:
    """A plain `fetch` + `innerHTML` leaves the fragment's attributes bound to nothing."""
    swap = _block("function swapInto(")
    assert "htmx.ajax(" in swap
    assert "settle" in swap, "the busy flag is released and the pending value re-applied"

    flush = _block("function flushRevision(")
    collection = flush[flush.index("swapInto(target, fragment") :]
    assert '"outerHTML"' in collection, "the fragment carries #board and #issues-tbody itself"


def test_no_fragment_arrives_through_a_plain_fetch() -> None:
    """The only `fetch` left is the position write, which binds nothing."""
    calls = [
        match for match in re.finditer(r"window\s*\.\s*fetch\(", JS.read_text(encoding="utf-8"))
    ]
    assert calls, "the column drag still writes through fetch"
    source = JS.read_text(encoding="utf-8")
    for call in calls:
        assert 'method: "POST"' in source[call.start() : call.start() + 220]


def test_the_panel_re_render_spends_the_revision_before_it_asks() -> None:
    """One re-render per observed revision: the next tick cannot see the same difference."""
    flush = _block("function flushRevision(")
    swap = flush.index("swapInto(PANEL")
    branch = flush[:swap]
    assert "revision.pending = null;" in branch[branch.index("panelUrl(") :]
    # ...and the answer is adopted by the afterRequest hook, so `known` moves with the markup.
    assert 'adopt(xhr.getResponseHeader("HX-Revision"))' in JS.read_text(encoding="utf-8")


def test_the_drag_flag_is_written_in_on_start_and_cleared_in_on_end() -> None:
    """The board swap reads `data-dragging`; before this it was read and never written."""
    start = _block("onStart: function")
    end = _block("onEnd: function")
    assert re.search(r'dataset\.dragging\s*=\s*"true"', start)
    assert not re.search(r'dataset\.dragging\s*=\s*"true"', end)
    assert re.search(r'dataset\.dragging\s*=\s*"false"', end)


def test_the_drop_applies_the_revision_it_deferred() -> None:
    """The drop is what the deferred swap waits for, so its return flushes the pending number."""
    persist = _block("function persistColumn(")
    assert "flushRevision" in persist
    fetch_at = persist.index("window")
    flush_at = persist.index(".then(flushRevision)")
    assert fetch_at < flush_at, "the flush has to be on the way back from the write"
