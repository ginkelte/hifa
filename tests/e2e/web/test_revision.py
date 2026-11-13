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


def test_the_collection_fragment_carries_rows_and_not_the_page_s_form(
    served: SimpleNamespace,
) -> None:
    """The quick add belongs to the page: shipping it with the rows replaces the form a person is
    typing into with an empty shell on every automatic reload, and the controls end up
    foster-parented inside the table."""
    rows = served.client.get("/_/issues", params={"team": "ENG", "view": "list"}, headers=HTMX)
    assert rows.status_code == 200
    assert 'id="quick-add"' not in rows.text, "the fragment carries rows, not the page's form"
    assert "<form" not in rows.text
    assert "hx-swap-oob" not in rows.text, "nothing out of band travels with the rows"

    page = served.client.get("/ENG/list")
    assert page.text.count('id="quick-add"') == 1, "the page keeps its one form"
    assert "hx-swap-oob" not in page.text

    templates = (JS.parents[2] / "templates").rglob("*.html")
    for template in templates:
        assert "form_oob" not in template.read_text(encoding="utf-8"), template.name


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
    assert '"outerHTML"' in swap, "the fragment carries #board and #issues-tbody itself"
    assert "settle" in swap, "the in-flight flag is released by the answer"

    flush = _block("function flushRevision(")
    assert "swapInto(target," in flush, "the poller reloads the collection it found"


def test_no_fragment_arrives_through_a_plain_fetch() -> None:
    """The only `fetch` left is the position write, which binds nothing."""
    calls = [
        match for match in re.finditer(r"window\s*\.\s*fetch\(", JS.read_text(encoding="utf-8"))
    ]
    assert calls, "the column drag still writes through fetch"
    source = JS.read_text(encoding="utf-8")
    for call in calls:
        assert 'method: "POST"' in source[call.start() : call.start() + 220]


def test_only_the_answer_that_replaced_the_collection_adopts_its_revision(
    served: SimpleNamespace,
) -> None:
    """A panel answers with the number the *server* observed — foreign writes included — and did
    not update the board.

    Adopting that number spends the difference between the page and the database without
    updating the page: the board stays stale and the poll sees nothing left to do. The guard is
    the swap target, and only the two nodes that hold a collection pass it.
    """
    hook = _block('addEventListener("htmx:afterRequest"')
    assert "isCollectionTarget(event.detail.target)" in hook
    assert "adopt(" in hook
    assert hook.index("isCollectionTarget") < hook.index("adopt("), "the guard runs first"
    assert hook.count("adopt(") == 1, "one adoption, gated, not one per answer"

    guard = _block("function isCollectionTarget(")
    assert '"board"' in guard and '"issues-tbody"' in guard

    panel = served.client.get("/_/issues/ENG-1/panel", headers=HTMX)
    assert panel.status_code == 200
    # The server keeps reporting on every answer; what changed is what the page believes.
    assert panel.headers["HX-Revision"], panel.headers


def test_the_pending_revision_survives_an_open_panel() -> None:
    """An open panel is a blocking state like the others: nothing re-renders it, and the pending
    value is not spent on the deferral — it waits for the panel to close."""
    flush = _block("function flushRevision(")
    assert "blocked()" in flush
    assert "swapInto(PANEL" not in flush, "the panel is not re-rendered under the reader"
    assert "revision.pending = null" not in flush, "the deferral does not spend the number"

    source = JS.read_text(encoding="utf-8")
    assert "panelUrl" not in source, "the panel's URL is built by the click, not by the poller"
    assert "panelIsDirtyOrFocused" not in source, "it walked inputs the panel does not have"

    close = _block("function closePanel(")
    assert "flushRevision()" in close, "the pending value applies when the panel closes"


def test_the_pending_value_is_spent_by_the_answer_that_adopted_it() -> None:
    """Spent by the reload, not by the request: a swap that never lands cannot look like one
    that did, and a swap that lands cannot leave the same difference pending for the next tick."""
    adopt = _block("function adopt(")
    assert "revision.known = String(number)" in adopt
    assert "revision.pending = null;" in adopt
    assert not re.search(r"if \([^)]*revision\.pending[^)]*\) revision\.pending = null", adopt), (
        "a landed reload supersedes whatever was pending; it does not have to match it"
    )

    hook = _block('addEventListener("htmx:afterRequest"')
    assert 'adopt(xhr.getResponseHeader("HX-Revision"))' in hook


def test_a_reload_that_never_answers_does_not_silence_the_page() -> None:
    """htmx's own request timeout is 0 (none at all), so the bound is the client's: a reload still
    in flight after it is abandoned and asked again instead of blocking every later tick."""
    source = JS.read_text(encoding="utf-8")
    assert re.search(r"var RELOAD_MS = \d+;", source), "the wait has a bound"

    swap = _block("function swapInto(")
    assert "setTimeout(" in swap
    watchdog = swap[swap.index("setTimeout(") :]
    assert re.search(r"revision\.inFlight = false", watchdog), "the bound releases the flag"
    assert "flushRevision()" in watchdog, "and asks again"

    settle = _block("function settle(")
    assert re.search(r"if \(token !== reloads\) return;", settle), (
        "a late answer is not the current one"
    )
    assert "revision.inFlight = false" in settle


def test_the_reload_carries_the_values_typed_in_the_filter_bar() -> None:
    """The page's query predates what the reader typed: a reload without the form's values puts
    an unfiltered collection under a filtered input."""
    live = _block("function withLiveFilters(")
    assert 'querySelector("form.filter-bar")' in live
    assert "new FormData(bar)" in live
    assert "params.set(key, text)" in live
    assert "params.delete(key)" in live, "an emptied filter leaves the URL"

    flush = _block("function flushRevision(")
    assert "withLiveFilters(fragment)" in flush, "every reload goes through the live values"


def test_the_fragment_honours_the_filter_the_reload_sends(served: SimpleNamespace) -> None:
    """The URL the client builds is one the fragment answers with those rows and no others."""
    rows = served.client.get(
        "/_/issues", params={"team": "ENG", "view": "list", "q": "avatar"}, headers=HTMX
    )
    assert rows.status_code == 200
    assert "ENG-2" in rows.text
    assert "ENG-1" not in rows.text


def test_the_drag_flag_is_written_in_on_start_and_cleared_when_the_drop_returns() -> None:
    """The board swap reads `data-dragging`; before this it was read and never written.

    And it may not be cleared before the drop's own POST returns: the swap the drop defers would
    then fire in the middle of the write it is waiting for.
    """
    start = _block("onStart: function")
    end = _block("onEnd: function")
    assert re.search(r'dataset\.dragging\s*=\s*"true"', start)
    assert not re.search(r'dataset\.dragging\s*=\s*"true"', end)
    # A drop that moved nothing ends the interaction itself; a real one hands the flag to the
    # write it starts, and only that write clears it.
    assert end.index('dataset.dragging = "false"') < end.index("persistColumn(")

    persist = _block("function persistColumn(")
    released = persist.rindex('dataset.dragging = "false"')
    assert released < persist.index("flushRevision", released)


def test_the_drop_applies_the_revision_it_deferred() -> None:
    """The drop is what the deferred swap waits for, so its return releases the flag and flushes."""
    persist = _block("function persistColumn(")
    assert "flushRevision" in persist
    fetch_at = persist.index("window")
    released = persist.rindex('dataset.dragging = "false"')
    assert fetch_at < released, "the flag outlives the request, and not the other way around"
