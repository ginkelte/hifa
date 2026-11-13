"""E2E of the read-only dashboard.

Every test drives the real application factory through `TestClient` against a database seeded
by the real CLI, and reads the HTML that a browser would receive.
"""

from __future__ import annotations

import gzip
import html as html_lib
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import typer
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hifa.cli.main import app as cli_app
from hifa.web import app as web_app
from hifa.web.app import CSP, create_app

runner = CliRunner()
STATIC = Path(__file__).resolve().parents[3] / "src" / "hifa" / "web" / "static"
TEMPLATES = Path(__file__).resolve().parents[3] / "src" / "hifa" / "web" / "templates"
HTMX = {"HX-Request": "true"}
#: Forbidden in templates by SPEC-070 R2/R3: filters (including `|default`, which would hide an
#: incomplete read model instead of failing in CI), statements with expressions, queries,
#: comparisons and arithmetic (the read model computes every derived value).
JINJA_LOGIC = re.compile(
    r"\|length|\|default\(|\{%-?\s*(set|with)\s|\{\{[^}]*\.query|\{\{[^}]*\s[+\-*/%<>]\s[^}]*\}\}"
)
#: `/_/*` is htmx-only; none of these may answer a plain browser GET.
FRAGMENT_ROUTES = (
    "/_/issues?team=ENG",
    "/_/issues/ENG-1/row",
    "/_/issues/ENG-1/card",
    "/_/issues/ENG-1/panel",
    "/_/counts?team=ENG",
)
ON_ATTRIBUTE = re.compile(r"<[^>]+\son[a-z]+\s*=", re.IGNORECASE)
SCRIPT_TAG = re.compile(r"<script\b[^>]*>", re.IGNORECASE)
COLUMN = re.compile(
    r'<section class="column" data-state-id="([^"]+)" data-position="([^"]+)">(.*?)</section>', re.S
)
ROW_IDS = re.compile(r'<tr id="issue-([A-Z]+-\d+)"')
NEXT_URL = re.compile(r'class="btn pager-next" href="([^"]+)"')


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


def _seed(project: Path) -> None:
    _run("init", "--team-key", "ENG")
    _run(
        "issue",
        "create",
        "Fix SSO login",
        "--priority",
        "P1",
        "--label",
        "Bug",
        "--due",
        "2026-11-20",
        "--assignee",
        "ME",
    )
    _run("issue", "create", "Avatar cache", "--priority", "P2")
    _run("issue", "create", "Migrate to WAL", "--state", "Done")


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolated cwd + HIFA_DB with one team (ENG) and three issues."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    _seed(project)
    db = tmp_path / "hifa.db"
    # There is no `issue move` command yet, so the history entry of the detail page is seeded
    # directly: the read model must render what the table holds.
    with sqlite3.connect(db) as conn:
        member_id = conn.execute("SELECT id FROM member LIMIT 1").fetchone()[0]
        issue_id = conn.execute("SELECT id FROM issue WHERE identifier = 'ENG-1'").fetchone()[0]
        conn.execute(
            "INSERT INTO issue_history (issue_id, actor_id, field, old_value, new_value,"
            " created_at) VALUES (?, ?, 'state', 'Todo', 'In Progress', '2026-11-08T12:00:00Z')",
            (issue_id, member_id),
        )
    return SimpleNamespace(db=db, client=TestClient(create_app(db, default_team="ENG")))


@pytest.fixture
def two_teams(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Two teams and no default: `/` cannot guess."""
    project = tmp_path / "proj2"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "two.db"))
    _run("init", "--team-key", "ENG")
    _run("init", "--team-key", "OPS", "--force")
    db = tmp_path / "two.db"
    return SimpleNamespace(db=db, client=TestClient(create_app(db)))


@pytest.fixture
def read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """The same seeded database served with `--read-only`."""
    project = tmp_path / "proj-ro"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "ro.db"))
    _seed(project)
    db = tmp_path / "ro.db"
    return SimpleNamespace(
        db=db, client=TestClient(create_app(db, default_team="ENG", read_only=True))
    )


@pytest.fixture
def hostile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """One issue whose description and attachments try every scheme the spec forbids."""
    project = tmp_path / "proj-xss"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "xss.db"))
    _run("init", "--team-key", "ENG")
    _run(
        "issue",
        "create",
        "Hostile payloads",
        "--description",
        "<script>alert(1)</script> and [x](javascript:alert(1))",
    )
    db = tmp_path / "xss.db"
    with sqlite3.connect(db) as conn:
        issue_id = conn.execute("SELECT id FROM issue WHERE identifier = 'ENG-1'").fetchone()[0]
        rows = [
            ("a1", "Evil js", "javascript:alert(1)"),
            ("a2", "Evil data", "data:text/html,<script>alert(1)</script>"),
            ("a3", "Evil host", "//evil.com/x"),
            ("a4", "Good file", "https://example.com/report.pdf"),
        ]
        conn.executemany(
            "INSERT INTO attachment (id, issue_id, title, url, created_at)"
            " VALUES (?, ?, ?, ?, '2026-11-08T12:00:00Z')",
            [(key, issue_id, title, url) for key, title, url in rows],
        )
    return SimpleNamespace(db=db, client=TestClient(create_app(db, default_team="ENG")))


@pytest.fixture
def wildcards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Titles with the characters `LIKE` treats as wildcards."""
    project = tmp_path / "proj-like"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "like.db"))
    _run("init", "--team-key", "ENG")
    _run("issue", "create", "Battery 100%")
    _run("issue", "create", "Battery 100 percent")
    _run("issue", "create", "Under_score")
    db = tmp_path / "like.db"
    return SimpleNamespace(db=db, client=TestClient(create_app(db, default_team="ENG")))


def _rows(html: str) -> int:
    return html.count('<tr id="issue-')


def _row_ids(html: str) -> list[str]:
    return ROW_IDS.findall(html)


def _row_html(html: str, identifier: str) -> str:
    match = re.search(rf'<tr id="issue-{re.escape(identifier)}".*?</tr>', html, re.S)
    assert match is not None, f"{identifier} is not in the listing"
    return match.group(0)


def _columns(html: str) -> dict[str, str]:
    """HTML of every board column, keyed by the `data-state-id` of its `<section>`."""
    return {match.group(1): match.group(3) for match in COLUMN.finditer(html)}


def _next_url(html: str) -> str | None:
    match = NEXT_URL.search(html)
    return None if match is None else html_lib.unescape(match.group(1))


def test_root_redirects_to_the_default_team_board(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/ENG/board"


def test_root_without_default_and_two_teams_shows_the_picker(two_teams: SimpleNamespace) -> None:
    """R24: two teams and no default is a `200` picker, never a `404` nor a guess."""
    response = two_teams.client.get("/", follow_redirects=False)
    assert response.status_code == 200
    assert 'class="team-picker"' in response.text
    assert 'href="/ENG/board"' in response.text
    assert 'href="/OPS/board"' in response.text


def test_board_renders_every_issue_in_the_column_of_its_state(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/ENG/board")
    assert response.status_code == 200
    assert 'class="board"' in response.text
    assert "In Progress" in response.text
    columns = _columns(response.text)
    assert columns, "the board has no columns"
    with sqlite3.connect(seeded.db) as conn:
        expected = dict(conn.execute("SELECT title, state_id FROM issue").fetchall())
    assert len(expected) == 3
    for title, state_id in expected.items():
        assert title in columns[state_id], f"{title} is not in its own column"
        for other_id, block in columns.items():
            if other_id != state_id:
                assert title not in block, f"{title} leaked into the column {other_id}"


def test_list_renders_identifiers_and_state(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/ENG/list")
    assert response.status_code == 200
    assert "issues-tbody" in response.text
    row = _row_html(response.text, "ENG-1")
    assert "Fix SSO login" in row
    assert '<span class="state-name">Todo</span>' in row
    assert _rows(response.text) == 3


def test_detail_renders_the_issue(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/ENG/issue/ENG-1")
    assert response.status_code == 200
    assert "detail-title" in response.text
    assert "Fix SSO login" in response.text
    with sqlite3.connect(seeded.db) as conn:
        member_name = conn.execute("SELECT display_name FROM member LIMIT 1").fetchone()[0]
    # assignee, due date and labels come from the read model, not from the template
    assert member_name in response.text
    assert "Nov 20" in response.text
    assert '<span class="chip chip-label"' in response.text
    assert "Bug" in response.text
    # history: the seeded state change is rendered with its actor
    history = re.search(r'<section class="history">(.*?)</section>', response.text, re.S)
    assert history is not None, "the history section is missing"
    assert "state" in history.group(1)
    assert "In Progress" in history.group(1)
    assert member_name in history.group(1)


def test_detail_by_ulid_redirects_to_the_identifier(seeded: SimpleNamespace) -> None:
    with sqlite3.connect(seeded.db) as conn:
        (ulid,) = conn.execute("SELECT id FROM issue WHERE identifier = 'ENG-1'").fetchone()
    response = seeded.client.get(f"/ENG/issue/{ulid}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/ENG/issue/ENG-1"


def test_unknown_identifier_is_404_with_the_error_template(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/ENG/issue/ENG-999")
    assert response.status_code == 404
    assert "error-page" in response.text
    assert "ENG-999" in response.text
    assert "does not exist" in response.text


def test_unknown_team_is_404(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/OPS/board")
    assert response.status_code == 404
    assert "OPS" in response.text
    assert "does not exist" in response.text


@pytest.mark.parametrize("path", FRAGMENT_ROUTES)
def test_every_fragment_requires_htmx(seeded: SimpleNamespace, path: str) -> None:
    response = seeded.client.get(path)
    assert response.status_code == 400, path
    assert response.text == "fragment requires htmx"
    assert response.headers["content-type"].startswith("text/plain")


def test_fragment_without_htmx_header_is_400(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/_/issues", params={"team": "ENG", "view": "list"})
    assert response.status_code == 400
    assert response.text == "fragment requires htmx"


@pytest.mark.parametrize("view", ["list", "board"])
def test_fragment_with_htmx_returns_partial_html(seeded: SimpleNamespace, view: str) -> None:
    response = seeded.client.get("/_/issues", params={"team": "ENG", "view": view}, headers=HTMX)
    assert response.status_code == 200
    assert "<!DOCTYPE" not in response.text
    assert "<html" not in response.text
    if view == "list":
        assert response.text.lstrip().startswith("<tbody")
    else:
        assert 'class="board"' in response.text


def test_row_card_and_panel_fragments(seeded: SimpleNamespace) -> None:
    row = seeded.client.get("/_/issues/ENG-1/row", headers=HTMX)
    assert row.status_code == 200
    assert row.text.lstrip().startswith('<tr id="issue-ENG-1"')
    card = seeded.client.get("/_/issues/ENG-1/card", headers=HTMX)
    assert card.status_code == 200
    assert card.text.lstrip().startswith('<article class="card"')
    panel = seeded.client.get("/_/issues/ENG-1/panel", headers=HTMX)
    assert panel.status_code == 200
    assert panel.text.lstrip().startswith('<aside id="panel"')
    assert panel.headers["HX-Push-Url"] == "/ENG/issue/ENG-1"


def test_the_panel_can_be_closed_without_navigating(seeded: SimpleNamespace) -> None:
    """The panel covers the screen on a phone; a control that also navigates is not a way out."""
    panel = seeded.client.get("/_/issues/ENG-1/panel", headers=HTMX).text
    closers = re.findall(r"<(a|button)\b[^>]*data-close-panel[^>]*>", panel)
    assert closers == ["button"], f"a close control must be a button, got {closers}"
    assert ">Close</button>" in panel


def test_list_filters_by_state(seeded: SimpleNamespace) -> None:
    done = seeded.client.get("/ENG/list", params={"state": "Done"})
    assert done.status_code == 200
    assert _rows(done.text) == 1
    assert "Migrate to WAL" in done.text
    todo = seeded.client.get("/ENG/list", params={"state": "Todo"})
    assert _rows(todo.text) == 2


def test_list_filters_by_title(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/ENG/list", params={"q": "SSO"})
    assert response.status_code == 200
    assert _rows(response.text) == 1
    assert "Fix SSO login" in response.text


def test_keyset_pagination_reports_has_more(seeded: SimpleNamespace) -> None:
    page = seeded.client.get("/ENG/list", params={"limit": "2"})
    assert page.status_code == 200
    assert _rows(page.text) == 2
    assert "More" in page.text
    next_url = _next_url(page.text)
    assert next_url is not None
    assert "cursor=" in next_url


def test_the_cursor_walks_the_whole_listing_without_gaps_or_repeats(
    seeded: SimpleNamespace,
) -> None:
    first = seeded.client.get("/ENG/list", params={"limit": "2"})
    seen = _row_ids(first.text)
    assert len(seen) == 2
    next_url = _next_url(first.text)
    assert next_url is not None
    second = seeded.client.get(next_url)
    assert second.status_code == 200
    tail = _row_ids(second.text)
    assert tail, "the second page is empty"
    assert not set(seen) & set(tail), "the cursor repeated an issue"
    assert set(seen) | set(tail) == {"ENG-1", "ENG-2", "ENG-3"}
    assert _next_url(second.text) is None
    assert "More" not in second.text


@pytest.mark.parametrize(
    "params",
    [
        {"cursor": "abc", "offset": "1"},
        {"limit": "abc"},
        {"limit": "999"},
        {"limit": "0"},
        {"offset": "501"},
        {"cursor": "not-a-cursor"},
    ],
)
def test_invalid_pagination_is_422(seeded: SimpleNamespace, params: dict[str, str]) -> None:
    response = seeded.client.get("/ENG/list", params=params)
    assert response.status_code == 422, params
    assert "error-page" in response.text


def test_invalid_sort_is_422(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/ENG/list", params={"sort": "sideways"})
    assert response.status_code == 422
    assert "error-page" in response.text


def test_like_wildcards_are_escaped(wildcards: SimpleNamespace) -> None:
    """`?q=%` must not return every issue and `?q=100%` only the literal one (LIKE ESCAPE)."""
    everything = wildcards.client.get("/ENG/list", params={"q": "%"})
    assert everything.status_code == 200
    # `%` is escaped: only the title with a literal `%` matches, not the whole listing
    assert _row_ids(everything.text) == ["ENG-1"]
    literal = wildcards.client.get("/ENG/list", params={"q": "100%"})
    assert _row_ids(literal.text) == ["ENG-1"]
    assert "Battery 100 percent" not in literal.text
    underscore = wildcards.client.get("/ENG/list", params={"q": "_"})
    assert _row_ids(underscore.text) == ["ENG-3"]


def test_quick_add_creates_the_issue_and_returns_the_row(seeded: SimpleNamespace) -> None:
    response = seeded.client.post(
        "/_/issues",
        params={"team": "ENG"},
        data={"title": "Quick add me", "priority": "P1"},
        headers=HTMX,
    )
    assert response.status_code == 201
    assert response.headers["HX-Trigger"] == "issue-created"
    assert response.text.lstrip().startswith('<tr id="issue-ENG-4"')
    with sqlite3.connect(seeded.db) as conn:
        row = conn.execute(
            "SELECT identifier, priority FROM issue WHERE title = 'Quick add me'"
        ).fetchone()
    assert row == ("ENG-4", 1)


@pytest.mark.parametrize("value", ["9", "P9", "urgentish", "high5"])
def test_quick_add_rejects_an_unparseable_priority(seeded: SimpleNamespace, value: str) -> None:
    """`P9`/`urgentish` is a `422`, not a silent "No priority"."""
    response = seeded.client.post(
        "/_/issues",
        params={"team": "ENG"},
        data={"title": "Bad priority", "priority": value},
        headers=HTMX,
    )
    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#quick-add"
    assert response.headers["HX-Reswap"] == "outerHTML"
    assert "priority must be" in response.text
    assert "<html" not in response.text
    with sqlite3.connect(seeded.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0] == 3


def test_quick_add_reads_p2_as_priority_two(seeded: SimpleNamespace) -> None:
    response = seeded.client.post(
        "/_/issues",
        params={"team": "ENG"},
        data={"title": "With priority", "priority": "P2"},
        headers=HTMX,
    )
    assert response.status_code == 201
    with sqlite3.connect(seeded.db) as conn:
        row = conn.execute("SELECT priority FROM issue WHERE title = 'With priority'").fetchone()
    assert row == (2,)


def test_quick_add_with_an_empty_priority_is_no_priority(seeded: SimpleNamespace) -> None:
    """An empty field is the one case that legitimately means `0`."""
    response = seeded.client.post(
        "/_/issues",
        params={"team": "ENG"},
        data={"title": "No priority field", "priority": ""},
        headers=HTMX,
    )
    assert response.status_code == 201
    with sqlite3.connect(seeded.db) as conn:
        row = conn.execute(
            "SELECT priority FROM issue WHERE title = 'No priority field'"
        ).fetchone()
    assert row == (0,)


def test_quick_add_without_js_rejects_an_unparseable_priority(
    seeded: SimpleNamespace,
) -> None:
    """The no-JS path puts the message next to the field and creates nothing."""
    response = seeded.client.post(
        "/ENG/issue",
        data={"title": "Bad priority", "priority": "9"},
        follow_redirects=False,
    )
    assert response.status_code == 422
    assert "priority must be" in response.text
    with sqlite3.connect(seeded.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0] == 3


def test_quick_add_validation_error_retargets_the_form(seeded: SimpleNamespace) -> None:
    """Without `HX-Retarget` htmx would inject the whole document inside the `<tbody>`."""
    response = seeded.client.post(
        "/_/issues", params={"team": "ENG"}, data={"title": "  "}, headers=HTMX
    )
    assert response.status_code == 422
    assert response.headers["HX-Retarget"] == "#quick-add"
    assert response.headers["HX-Reswap"] == "outerHTML"
    assert "title is required" in response.text
    assert "<html" not in response.text


def test_quick_add_without_htmx_is_400(seeded: SimpleNamespace) -> None:
    response = seeded.client.post("/_/issues", params={"team": "ENG"}, data={"title": "no htmx"})
    assert response.status_code == 400
    assert response.text == "fragment requires htmx"


def test_quick_add_form_posts_to_the_page_route_and_keeps_htmx(seeded: SimpleNamespace) -> None:
    body = seeded.client.get("/ENG/list").text
    assert 'action="/ENG/issue"' in body
    assert 'hx-post="/_/issues?team=ENG"' in body
    assert 'method="post"' in body


def test_quick_add_works_without_js(seeded: SimpleNamespace) -> None:
    """R4: the form is a real form post; success is a `303` to the detail."""
    response = seeded.client.post(
        "/ENG/issue", data={"title": "From no JS", "priority": "P2"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/ENG/issue/ENG-4"
    with sqlite3.connect(seeded.db) as conn:
        row = conn.execute(
            "SELECT title, priority FROM issue WHERE identifier = 'ENG-4'"
        ).fetchone()
    assert row == ("From no JS", 2)
    assert "From no JS" in seeded.client.get(response.headers["location"]).text


def test_quick_add_without_js_and_an_empty_title_is_422(seeded: SimpleNamespace) -> None:
    response = seeded.client.post("/ENG/issue", data={"title": "   "}, follow_redirects=False)
    assert response.status_code == 422
    assert '<span class="form-error" role="alert">title is required</span>' in response.text
    assert 'action="/ENG/issue"' in response.text
    with sqlite3.connect(seeded.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0] == 3


def test_read_only_hides_every_write_control(read_only: SimpleNamespace) -> None:
    body = read_only.client.get("/ENG/list").text
    assert 'id="quick-add"' not in body
    assert 'action="/ENG/issue"' not in body
    board = read_only.client.get("/ENG/board").text
    assert 'id="quick-add"' not in board
    assert 'draggable="true"' not in board


@pytest.mark.parametrize("path", ["/_/issues?team=ENG", "/ENG/issue"])
def test_read_only_rejects_mutations_with_403(read_only: SimpleNamespace, path: str) -> None:
    response = read_only.client.post(
        path, data={"title": "not allowed"}, headers=HTMX, follow_redirects=False
    )
    assert response.status_code == 403
    assert "server in read-only mode" in response.text
    with sqlite3.connect(read_only.db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM issue").fetchone()[0] == 3


def test_the_description_is_escaped_and_carries_no_javascript_link(
    hostile: SimpleNamespace,
) -> None:
    """REQ-S070-01: `<script>` and `javascript:` never reach the served HTML."""
    body = hostile.client.get("/ENG/issue/ENG-1").text
    assert "<script>alert(1)</script>" not in body
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert 'href="javascript:' not in body


def test_attachment_urls_outside_the_allowlist_are_not_links(hostile: SimpleNamespace) -> None:
    """REQ-S070-05: `javascript:`, `data:` and `//evil.com` are shown as text."""
    body = hostile.client.get("/ENG/issue/ENG-1").text
    assert 'href="javascript:' not in body
    assert 'href="data:' not in body
    assert 'href="//evil.com' not in body
    # the titles are still visible, just without an <a>
    assert "Evil js" in body and "Evil data" in body and "Evil host" in body
    assert '<a href="https://example.com/report.pdf">Good file</a>' in body


def test_an_unhandled_exception_is_a_clean_500(seeded: SimpleNamespace) -> None:
    """SPEC-070 §6: the 500 page never leaks the traceback nor the internal message."""

    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("secret internal detail")

    original = web_app.boards.issue_detail
    web_app.boards.issue_detail = boom  # type: ignore[assignment]
    try:
        client = TestClient(seeded.client.app, raise_server_exceptions=False)
        response = client.get("/ENG/issue/ENG-1")
    finally:
        web_app.boards.issue_detail = original  # type: ignore[assignment]
    assert response.status_code == 500
    assert "internal error" in response.text
    assert "Traceback" not in response.text
    assert "secret internal detail" not in response.text


def test_vendor_assets_are_immutable(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/static/vendor/htmx.min.js")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_own_assets_are_short_lived(seeded: SimpleNamespace) -> None:
    css = seeded.client.get("/static/css/app.css")
    assert css.status_code == 200
    assert css.headers["cache-control"] == "public, max-age=60"
    js = seeded.client.get("/static/js/app.js")
    assert js.status_code == 200
    assert js.headers["cache-control"] == "public, max-age=60"


def test_pages_are_not_cacheable_and_carry_the_csp(seeded: SimpleNamespace) -> None:
    response = seeded.client.get("/ENG/board")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == CSP
    assert "unsafe-eval" not in response.headers["content-security-policy"]


@pytest.mark.parametrize("path", ["/ENG/list", "/ENG/board", "/ENG/issue/ENG-1"])
def test_served_html_has_no_inline_script_nor_on_attributes(
    seeded: SimpleNamespace, path: str
) -> None:
    body = seeded.client.get(path).text
    assert "onclick=" not in body
    assert "onmouseover=" not in body
    assert "onerror=" not in body
    assert ON_ATTRIBUTE.search(body) is None
    assert "<script>" not in body
    # a `<script src=...>` is legitimate (vendored assets); an inline one is not.
    tags = SCRIPT_TAG.findall(body)
    assert tags, "the page does not load its scripts at all"
    for tag in tags:
        assert re.search(r'\bsrc="/static/', tag), tag


def test_the_board_instance_does_not_eat_the_touch_of_a_card() -> None:
    """`filter` names what cannot be dragged; with the default the instance also cancels the
    gesture there, and a phone cannot scroll from a card at all."""
    js = (STATIC / "js" / "app.js").read_text()
    board = js[js.index('draggable: ".column"') :]
    board = board[: board.index("onEnd:")]
    assert 'filter: ".card"' in board
    assert "preventOnFilter: false" in board


def test_asset_budget_is_under_75_kb_gzip() -> None:
    total = 0
    for path in STATIC.rglob("*"):
        if path.suffix in {".js", ".css", ".svg"}:
            total += len(gzip.compress(path.read_bytes(), compresslevel=9))
    assert total <= 75 * 1024, total
    # The budget of the table is gzip -9 for every file, the vendor ones included; a raw byte
    # comparison would hold the own files to a ceiling a third of the declared one.
    assert (
        len(gzip.compress((STATIC / "css" / "app.css").read_bytes(), compresslevel=9)) <= 20 * 1024
    )
    assert len(gzip.compress((STATIC / "js" / "app.js").read_bytes(), compresslevel=9)) <= 12 * 1024


def test_templates_carry_no_logic() -> None:
    for path in TEMPLATES.rglob("*.html"):
        match = JINJA_LOGIC.search(path.read_text(encoding="utf-8"))
        assert match is None, f"{path}: {match.group(0) if match else ''}"


def test_no_template_injects_script_tags() -> None:
    for path in TEMPLATES.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        if path.name == "base.html":
            assert text.count("<script") == 4  # htmx, alpine, sortable, app.js
            continue
        assert "<script" not in text, path


def test_serve_help_is_available() -> None:
    # The flags are asserted on the command's parameters, not on the rendered help: Rich wraps
    # the panel to the terminal width, and on a narrow CI runner "--port" is split across lines,
    # so a substring check passes locally and fails there. The rendering is checked only for a
    # clean exit, which is what "--help" has to guarantee.
    result = runner.invoke(cli_app, ["serve", "--help"])
    assert result.exit_code == 0

    command = typer.main.get_command(cli_app)
    # `get_command` is typed as `click.Command`; the real object is the `TyperGroup`, so the
    # subcommands are read through `getattr` instead of an `isinstance` check that fails when
    # Typer wraps a different `click` module instance.
    serve = command.commands["serve"]
    names = {opt for param in serve.params for opt in (*param.opts, *param.secondary_opts)}
    assert {"--port", "--host", "--read-only", "--reload", "--json"} <= names
    assert "--no-open" in names


def test_serve_without_the_web_extra_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "uvicorn", None)
    result = runner.invoke(cli_app, ["serve", "--no-open"])
    assert result.exit_code == 2
    assert "uv sync --extra web" in result.output


def test_the_core_cli_does_not_import_fastapi() -> None:
    probe = (
        "import sys, hifa.cli.main;"
        "assert 'fastapi' not in sys.modules, 'fastapi leaked into the core';"
        "assert 'uvicorn' not in sys.modules, 'uvicorn leaked into the core'"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
