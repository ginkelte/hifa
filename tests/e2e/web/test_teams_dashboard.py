"""E2E of the team sidebar and the landing team picker (SPEC-070 §2.3.1, §2.3.8, R23/R24).

The first group drives the real application factory and creates the team **after** `create_app`
returned: that is the regression test of the removed startup cache. The rest pins the landing:
`200` picker with two teams and no default, `303` when a default exists.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hifa.cli.main import app as cli_app
from hifa.web.app import create_app

runner = CliRunner()
TEAM_LINK = re.compile(r'<a href="/([A-Z0-9]+)/board" class="team-link([^"]*)">')


@pytest.fixture
def live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """One initialized team (ENG) and an app built **before** any other team exists."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "live.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    run("init", "--team-key", "ENG")
    db = tmp_path / "live.db"
    dashboard = create_app(db)
    return SimpleNamespace(db=db, app=dashboard, client=TestClient(dashboard))


def run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.stdout + result.stderr


def sidebar_links(html: str) -> dict[str, str]:
    """`{team key: class}` of the sidebar entries, in document order."""
    return {match.group(1): match.group(2) for match in TEAM_LINK.finditer(html)}


# --- the cache fix ------------------------------------------------------------------------


def test_sidebar_lists_a_team_created_after_create_app(live: SimpleNamespace) -> None:
    before = live.client.get("/ENG/board")
    assert "OPS" not in before.text

    run("team", "create", "OPS", "Operations")

    after = live.client.get("/ENG/board")
    assert after.status_code == 200
    assert list(sidebar_links(after.text)) == ["ENG", "OPS"]
    assert "/OPS/board" in after.text


def test_sidebar_marks_the_active_team(live: SimpleNamespace) -> None:
    run("team", "create", "OPS", "Operations")

    html = live.client.get("/OPS/board").text

    classes = sidebar_links(html)
    assert "is-active" in classes["OPS"]
    assert "is-active" not in classes["ENG"]


def test_sidebar_shows_the_issue_count_of_each_team(live: SimpleNamespace) -> None:
    run("team", "create", "OPS", "Operations")
    run("issue", "create", "Fix login", "--team", "OPS")
    run("issue", "create", "Fix logout", "--team", "OPS")

    html = live.client.get("/ENG/board").text

    assert 'class="team-count">0<' in html
    assert 'class="team-count">2<' in html


# --- landing ------------------------------------------------------------------------------


def test_root_with_two_teams_and_no_default_is_the_picker(live: SimpleNamespace) -> None:
    run("team", "create", "OPS", "Operations")

    response = live.client.get("/", follow_redirects=False)

    assert response.status_code == 200
    assert 'class="team-picker"' in response.text
    assert 'data-reason="no_default_team"' in response.text
    assert 'href="/ENG/board"' in response.text
    assert 'href="/OPS/board"' in response.text
    assert "Operations" in response.text
    assert "<form" not in response.text


def test_root_with_one_team_still_redirects(live: SimpleNamespace) -> None:
    response = live.client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/ENG/board"


def test_root_with_a_default_team_redirects_to_it(live: SimpleNamespace) -> None:
    run("team", "create", "OPS", "Operations")
    client = TestClient(create_app(live.db, default_team="OPS"))

    response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/OPS/board"


def test_picker_has_no_active_team(live: SimpleNamespace) -> None:
    run("team", "create", "OPS", "Operations")

    html = live.client.get("/").text

    assert all("is-active" not in classes for classes in sidebar_links(html).values())
    assert "Views" not in html
