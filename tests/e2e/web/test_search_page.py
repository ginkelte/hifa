"""`GET /search`: the four states of the page, the escaping of the snippet and the keyboard path.

The page is read through `TestClient`, so what the assertions see is the HTML a browser receives:
the empty state, the no-result state, the result list and the refusal — plus the two failures the
snippet escaping exists to forbid (`&lt;mark&gt;` and a title carrying markup into the page).
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
TEMPLATES = Path(__file__).resolve().parents[3] / "src" / "hifa" / "web" / "templates"
CSS = Path(__file__).resolve().parents[3] / "src" / "hifa" / "web" / "static" / "css" / "app.css"
JINJA_LOGIC = re.compile(
    r"\|length|\|default\(|\{%-?\s*(set|with)\s|\{\{[^}]*\.query|\{\{[^}]*\s[+\-*/%<>]\s[^}]*\}\}"
)
SNIPPET = re.compile(r'<span class="hit-snippet">(.*?)</span>', re.S)


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


def _seed(project: Path) -> None:
    _run("init", "--team-key", "ENG")
    _run("issue", "create", "Broken login on Safari", "--description", "no entra nadie")
    _run("issue", "create", "Avatar cache", "--priority", "P2")
    _run("issue", "create", "Migrate to WAL", "--state", "Done")


@pytest.fixture
def page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """One team, three issues and a client for the real application."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    _seed(project)
    db = tmp_path / "hifa.db"
    return SimpleNamespace(db=db, client=TestClient(create_app(db, default_team="ENG")))


@pytest.fixture
def hostile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A title that carries markup and a description that carries a script tag."""
    project = tmp_path / "proj-xss"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "xss.db"))
    _run("init", "--team-key", "ENG")
    _run(
        "issue",
        "create",
        "<script>alert('xss')</script> login",
        "--description",
        "<img src=x onerror=alert(1)> login",
    )
    db = tmp_path / "xss.db"
    return SimpleNamespace(db=db, client=TestClient(create_app(db, default_team="ENG")))


def test_the_empty_state_asks_for_a_query_and_focuses_the_box(page: SimpleNamespace) -> None:
    response = page.client.get("/search")

    assert response.status_code == 200
    assert "search the workspace" in response.text
    assert 'action="/search"' in response.text
    assert 'name="q"' in response.text
    assert "autofocus" in response.text
    assert 'class="search-results"' not in response.text


def test_the_sidebar_links_the_page_from_every_view(page: SimpleNamespace) -> None:
    board = page.client.get("/")

    assert 'href="/search"' in board.text
    assert 'href="/search"' in page.client.get("/search").text


def test_a_query_with_no_hit_says_so_and_names_the_query(page: SimpleNamespace) -> None:
    response = page.client.get("/search", params={"q": "zzz"})

    assert response.status_code == 200
    assert "nothing matches" in response.text
    assert "zzz" in response.text
    assert 'class="search-results"' not in response.text


def test_a_hit_is_a_link_wrapping_a_three_line_block(page: SimpleNamespace) -> None:
    response = page.client.get("/search", params={"q": "login"})

    assert response.status_code == 200
    hits = re.findall(r'<li class="hit">.*?</li>', response.text, re.S)
    assert len(hits) == 1
    assert '<a href="/ENG/issue/ENG-1"' in hits[0]
    assert "ENG-1" in hits[0]
    assert "Broken login on Safari" in hits[0]
    assert "1 results" in response.text


def test_the_heading_carries_the_count_and_takes_focus(page: SimpleNamespace) -> None:
    response = page.client.get("/search", params={"q": "login"})

    assert '<h2 class="search-heading" tabindex="-1" data-count="1">1 results</h2>' in response.text


def test_the_snippet_marks_the_term_once_and_is_not_escaped_twice(
    page: SimpleNamespace,
) -> None:
    response = page.client.get("/search", params={"q": "login"})

    snippet = SNIPPET.search(response.text)
    assert snippet is not None
    assert "<mark>login</mark>" in snippet.group(1)
    assert "&lt;mark&gt;" not in response.text


def test_a_title_carrying_markup_is_escaped_in_the_page(hostile: SimpleNamespace) -> None:
    response = hostile.client.get("/search", params={"q": "login"})

    assert response.status_code == 200
    assert "<script>alert('xss')</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "<img src=x onerror" not in response.text
    # …and the snippet still marks the term: escaping once keeps both halves.
    snippet = SNIPPET.search(response.text)
    assert snippet is not None
    assert "<mark>login</mark>" in snippet.group(1)


def test_the_script_tag_of_a_title_and_of_a_description_never_reaches_the_page(
    hostile: SimpleNamespace,
) -> None:
    body = hostile.client.get("/search", params={"q": "login"}).text

    assert "<script>alert" not in body
    assert "onerror=alert" not in body


def test_a_refused_query_is_422_and_prints_the_sentence(page: SimpleNamespace) -> None:
    response = page.client.get("/search", params={"q": "x" * 600})

    assert response.status_code == 422
    assert 'class="search-message"' in response.text
    assert "invalid value" in response.text
    assert 'class="search-results"' not in response.text
    # The reader's query is still in the box: the page they can fix keeps it.
    assert f'value="{"x" * 600}"' in response.text


def test_a_refused_filter_names_the_field_in_the_page(page: SimpleNamespace) -> None:
    response = page.client.get("/search", params={"q": "priority:urgentísimo"})

    assert response.status_code == 422
    assert "urgentísimo" in response.text
    assert "priority" in response.text


def test_an_empty_query_is_an_empty_state_and_not_a_refusal(page: SimpleNamespace) -> None:
    for value in ("", "   "):
        response = page.client.get("/search", params={"q": value})

        assert response.status_code == 200
        assert "search the workspace" in response.text


def test_trashed_issues_do_not_reach_the_page(page: SimpleNamespace) -> None:
    _run("issue", "delete", "ENG-1", "--yes")

    response = page.client.get("/search", params={"q": "login"})

    assert response.status_code == 200
    assert "Broken login on Safari" not in response.text
    assert "nothing matches" in response.text


def test_an_unknown_team_key_filters_to_nothing_and_is_not_an_error(
    page: SimpleNamespace,
) -> None:
    response = page.client.get("/search", params={"q": "login", "team": "NOPE"})

    assert response.status_code == 200
    assert "nothing matches" in response.text


def test_the_team_parameter_renders_a_static_chip_the_query_does_not_need(
    page: SimpleNamespace,
) -> None:
    without = page.client.get("/search", params={"q": "login"})
    with_team = page.client.get("/search", params={"q": "login", "team": "eng"})

    assert "search-team-chip" not in without.text
    assert "search-team-chip" in with_team.text
    assert "ENG" in with_team.text
    assert "Broken login on Safari" in with_team.text
    assert 'href="/search?q=login"' in with_team.text


def test_the_page_uses_the_list_the_contract_names_and_no_table(page: SimpleNamespace) -> None:
    body = page.client.get("/search", params={"q": "login"}).text

    assert '<ul class="search-results">' in body
    assert "<table" not in body
    assert 'id="search-q"' in body


def test_the_template_carries_no_logic() -> None:
    source = (TEMPLATES / "search.html").read_text(encoding="utf-8")

    assert JINJA_LOGIC.search(source) is None


def test_the_stylesheet_hugs_the_hit_and_lets_the_title_wrap() -> None:
    css = CSS.read_text(encoding="utf-8")
    block = css[css.index(".hit-link") : css.index(".hit-snippet mark")]

    assert "min-width: 0" in block
    assert "overflow-wrap: anywhere" in block
    assert "white-space: nowrap" in css[css.index(".hit-snippet {") :][:400]


def test_the_page_is_served_with_the_security_headers_of_the_dashboard(
    page: SimpleNamespace,
) -> None:
    response = page.client.get("/search", params={"q": "login"})

    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_the_query_never_reaches_the_database_as_a_second_statement(
    page: SimpleNamespace,
) -> None:
    """A quote in the query is text: the issues of the workspace survive it."""
    before = _count(page.db, "issue")

    page.client.get("/search", params={"q": "'; DROP TABLE issue; --"})

    assert _count(page.db, "issue") == before


def _count(db: Path, table: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()
