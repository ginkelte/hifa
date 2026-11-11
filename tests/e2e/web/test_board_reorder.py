"""E2E of the draggable board columns (SPEC-070 §2.1.5, D-035).

`POST /_/states/{id}/position` is the persistence half of the drag & drop: it is htmx-only
(400 without `HX-Request`), it answers `204` with `HX-Trigger: board-reordered`, it is refused
in read-only mode and the next `GET /{team}/board` renders the new persisted order.
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
# Scope to the column element: issue cards carry `data-state-id` too, so a bare
# `data-state-id="..."` search mixes columns and cards.
STATE_IDS = re.compile(r'<section class="column" data-state-id="([^"]+)"')
CANONICAL = ["Triage", "Todo", "Backlog", "In Progress", "Done", "Canceled"]


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolated cwd + HIFA_DB with one team (ENG) and one issue in Todo."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    _run("init", "--team-key", "ENG")
    _run("issue", "create", "Fix SSO login")
    db = tmp_path / "hifa.db"
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT id, name FROM workflow_state ORDER BY position").fetchall()
    states = {name: state_id for state_id, name in rows}
    return SimpleNamespace(
        db=db,
        states=states,
        client=TestClient(create_app(db, default_team="ENG")),
    )


@pytest.fixture
def read_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    project = tmp_path / "proj-ro"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "ro.db"))
    _run("init", "--team-key", "ENG")
    db = tmp_path / "ro.db"
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT id, name FROM workflow_state ORDER BY position").fetchall()
    return SimpleNamespace(
        db=db,
        states={name: state_id for state_id, name in rows},
        client=TestClient(create_app(db, default_team="ENG", read_only=True)),
    )


def _board_order(client: TestClient) -> list[str]:
    """`data-state-id` of every column of the board, in DOM order."""
    html = client.get("/ENG/board").text
    ids = STATE_IDS.findall(html)
    assert ids, "the board has no columns"
    return ids


def _move(client: TestClient, state_id: str, *, before: str | None, after: str | None):
    return client.post(
        f"/_/states/{state_id}/position",
        headers={**HTMX, "Content-Type": "application/json"},
        json={"before": before, "after": after},
    )


def test_board_columns_carry_their_state_id_and_position(seeded: SimpleNamespace) -> None:
    html = seeded.client.get("/ENG/board").text

    assert 'data-state-id="' in html
    assert 'data-position="1.0"' in html
    assert list(_board_order(seeded.client)) == [seeded.states[name] for name in CANONICAL]


def test_position_without_the_hx_request_header_is_four_hundred(
    seeded: SimpleNamespace,
) -> None:
    response = seeded.client.post(
        f"/_/states/{seeded.states['Todo']}/position",
        json={"before": None, "after": seeded.states["Triage"]},
    )

    assert response.status_code == 400


def test_position_with_the_header_answers_204_and_persists_the_order(
    seeded: SimpleNamespace,
) -> None:
    response = _move(
        seeded.client,
        seeded.states["Canceled"],
        before=seeded.states["Backlog"],
        after=None,
    )

    assert response.status_code == 204
    assert response.headers["HX-Trigger"] == "board-reordered"
    assert response.content == b""
    expected = [
        "Triage",
        "Todo",
        "Canceled",
        "Backlog",
        "In Progress",
        "Done",
    ]
    assert _board_order(seeded.client) == [seeded.states[name] for name in expected]
    with sqlite3.connect(seeded.db) as conn:
        position = conn.execute(
            "SELECT position FROM workflow_state WHERE id = ?", (seeded.states["Canceled"],)
        ).fetchone()[0]
    assert position == 2.5


def test_position_of_an_unknown_state_is_four_hundred_four(seeded: SimpleNamespace) -> None:
    response = _move(seeded.client, "01HNOPE000000000000000000", before=None, after=None)

    assert response.status_code == 404


def test_position_with_a_neighbour_of_another_team_is_four_hundred_twenty_two(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "two"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "two.db"))
    _run("init", "--team-key", "ENG")
    _run("init", "--team-key", "OPS", "--force")
    db = tmp_path / "two.db"
    with sqlite3.connect(db) as conn:
        eng = conn.execute(
            "SELECT s.id FROM workflow_state s JOIN team t ON t.id = s.team_id"
            " WHERE t.key = 'ENG' AND s.name = 'Canceled'"
        ).fetchone()[0]
        foreign = conn.execute(
            "SELECT s.id FROM workflow_state s JOIN team t ON t.id = s.team_id"
            " WHERE t.key = 'OPS' AND s.name = 'Triage'"
        ).fetchone()[0]
    client = TestClient(create_app(db, default_team="ENG"))

    response = _move(client, eng, before=foreign, after=None)

    assert response.status_code == 422


def test_position_in_read_only_mode_is_forbidden(read_only: SimpleNamespace) -> None:
    response = _move(
        read_only.client,
        read_only.states["Canceled"],
        before=read_only.states["Backlog"],
        after=None,
    )

    assert response.status_code == 403


def test_the_board_keeps_the_persisted_order_after_a_reload(seeded: SimpleNamespace) -> None:
    _move(seeded.client, seeded.states["Triage"], before=None, after=seeded.states["Done"])

    ids = _board_order(seeded.client)
    # `after=Done` places Triage between Done and Canceled: Canceled is still last.
    assert ids[-3:] == [
        seeded.states["Done"],
        seeded.states["Triage"],
        seeded.states["Canceled"],
    ]
    assert ids[0] == seeded.states["Todo"]


def test_position_accepts_two_neighbours_and_lands_between_them(
    seeded: SimpleNamespace,
) -> None:
    """The real client payload: both anchors, which is what a middle drop sends."""
    response = _move(
        seeded.client,
        seeded.states["Canceled"],
        before=seeded.states["Todo"],
        after=seeded.states["Backlog"],
    )

    assert response.status_code == 204
    expected = ["Triage", "Todo", "Canceled", "Backlog", "In Progress", "Done"]
    assert _board_order(seeded.client) == [seeded.states[name] for name in expected]
    with sqlite3.connect(seeded.db) as conn:
        position = conn.execute(
            "SELECT position FROM workflow_state WHERE id = ?", (seeded.states["Canceled"],)
        ).fetchone()[0]
    assert position == 2.5


def test_position_with_inverted_neighbours_is_four_hundred_twenty_two(
    seeded: SimpleNamespace,
) -> None:
    """`before` to the right of `after` is not a slot; it is a bug in the caller."""
    response = _move(
        seeded.client,
        seeded.states["Canceled"],
        before=seeded.states["Backlog"],
        after=seeded.states["Todo"],
    )

    assert response.status_code == 422
    assert _board_order(seeded.client) == [seeded.states[name] for name in CANONICAL]


def test_position_with_the_same_neighbour_on_both_sides_is_four_hundred_twenty_two(
    seeded: SimpleNamespace,
) -> None:
    response = _move(
        seeded.client,
        seeded.states["Canceled"],
        before=seeded.states["Todo"],
        after=seeded.states["Todo"],
    )

    assert response.status_code == 422


def test_position_of_the_state_itself_as_neighbour_is_four_hundred_twenty_two(
    seeded: SimpleNamespace,
) -> None:
    response = _move(
        seeded.client,
        seeded.states["Canceled"],
        before=seeded.states["Canceled"],
        after=None,
    )

    assert response.status_code == 422


def test_the_board_hides_the_drag_when_the_server_is_read_only(
    read_only: SimpleNamespace,
) -> None:
    """R18: a read-only board must not offer a gesture that can only answer 403."""
    html = read_only.client.get("/ENG/board").text

    assert 'data-can-write="false"' in html
    assert 'data-can-write="true"' not in html
