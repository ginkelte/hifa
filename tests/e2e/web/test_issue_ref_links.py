"""E2E of the reference autolink: an identifier written in the text links to the issue it names.

The eight scenarios of SPEC-070 §7 (`specs/acceptance/001-mvp-v1/16-dashboard.feature`, R30,
D-081), one test each and named after the scenario's own title: the spec-claims gate matches a
test to a scenario by that name, after stripping `test_`, and a renamed test silently unpins the
claim.

Every test asserts a link the read resolved, never a pattern that fired: the mapping is the
caller's, so each scenario creates the issue a reference names before naming it. The fixture
seeds through `hifa init` for the schema and inserts the issue rows directly, because a scenario
that names `ENG-12` or `HIFA-103` would otherwise cost one CLI run per skipped number.

Two tests outside the eight pin the statement's two halves — what it costs and when it costs
nothing — because §7's scenarios read the page, and the surfaces that print no markdown are the
ones that must not pay for text they do not render.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from typer.testing import CliRunner

from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite import connection as sqlite_connection
from hifa.cli.main import app as cli_app
from hifa.web.app import create_app

runner = CliRunner()
HTMX = {"HX-Request": "true"}
#: `(href, text)` of every identifier reference the page rendered.
LINK = re.compile(r'<a class="issue-ref" href="([^"]*)">([^<]*)</a>')
#: The shape of the one statement §7 counts, not its text: `issue` read joined to `team`.
REF_STATEMENT = re.compile(r"FROM\s+issue\s+JOIN\s+team\b", re.IGNORECASE)


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


def _seed_issue(
    db: Path,
    *,
    team_key: str,
    number: int,
    title: str,
    description: str | None = None,
) -> str:
    """Insert one issue of `team_key` numbered `number`; answer its identifier.

    The row is the one `hifa issue create` writes — every column the schema has no default for is
    supplied, the state is the team's first open one and the identifier is the key and the number
    — so a scenario can name `ENG-12` or `HIFA-103` without skipping the numbers in between.

    Args:
        db: The database `hifa init` created.
        team_key: The key of an existing team.
        number: The issue's number inside that team.
        title: A non-empty title.
        description: The stored description, or `None`.

    Returns:
        The issue's identifier, `{team_key}-{number}`.
    """
    identifier = f"{team_key}-{number}"
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    with sqlite3.connect(db) as conn:
        team_id, workspace_id = conn.execute(
            "SELECT id, workspace_id FROM team WHERE key = ?", (team_key,)
        ).fetchone()
        (state_id,) = conn.execute(
            "SELECT id FROM workflow_state WHERE team_id = ?"
            " AND type NOT IN ('completed','canceled') ORDER BY position, name LIMIT 1",
            (team_id,),
        ).fetchone()
        conn.execute(
            "INSERT INTO issue (id, workspace_id, team_id, number, identifier, title,"
            " description, state_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                UlidGen().new(),
                workspace_id,
                team_id,
                number,
                identifier,
                title,
                description,
                state_id,
                stamp,
                stamp,
            ),
        )
    return identifier


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """An isolated workspace with the two teams the scenarios name, and a client on its database."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    _run("init", "--team-key", "ENG")
    _run("init", "--team-key", "HIFA", "--force")
    db = tmp_path / "hifa.db"
    return SimpleNamespace(db=db, client=TestClient(create_app(db, default_team="ENG")))


@pytest.fixture
def trace(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Every connection the app opens from now on, and every statement each one issued.

    The app opens one connection per read (`Database.ro_connection`), so the trace is installed at
    the factory: one callback on each new connection sees the statement the request really sent,
    which is the only way to count statements instead of grepping the source for them. The paths
    are counted beside the statements because "*issues* no statement" and "*opens* no connection"
    are two claims, and a connection that issues nothing answers only the first.
    """
    statements: list[str] = []
    opens: list[str] = []
    original = sqlite_connection.open_db

    def traced(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
        opens.append(str(path))
        conn = original(path, readonly=readonly)
        conn.set_trace_callback(statements.append)
        return conn

    monkeypatch.setattr(sqlite_connection, "open_db", traced)
    return SimpleNamespace(statements=statements, opens=opens)


def _links(html: str) -> list[tuple[str, str]]:
    """`(href, text)` of every identifier reference of one response body."""
    return LINK.findall(html)


def _page(workspace: SimpleNamespace, identifier: str) -> Response:
    """The detail page of one issue, as the browser receives it."""
    return workspace.client.get(f"/ENG/issue/{identifier}")


def test_an_issue_reference_in_a_description_links_to_the_issue_it_names(
    workspace: SimpleNamespace,
) -> None:
    _seed_issue(workspace.db, team_key="ENG", number=12, title="Referenced")
    _seed_issue(
        workspace.db,
        team_key="ENG",
        number=13,
        title="Refers",
        description="follows ENG-12",
    )
    response = _page(workspace, "ENG-13")
    assert response.status_code == 200
    assert _links(response.text) == [("/ENG/issue/ENG-12", "ENG-12")]


def test_a_reference_typed_in_another_case_links_to_the_canonical_url(
    workspace: SimpleNamespace,
) -> None:
    _seed_issue(workspace.db, team_key="ENG", number=12, title="Referenced")
    _seed_issue(
        workspace.db,
        team_key="ENG",
        number=13,
        title="Refers",
        description="follows eng-12",
    )
    response = _page(workspace, "ENG-13")
    assert response.status_code == 200
    assert _links(response.text) == [("/ENG/issue/ENG-12", "eng-12")]


def test_a_token_that_names_no_issue_stays_text(workspace: SimpleNamespace) -> None:
    _seed_issue(
        workspace.db,
        team_key="ENG",
        number=13,
        title="Quotes the corpus",
        description="SPEC-050, SQL-7 and SPEC-070",
    )
    response = _page(workspace, "ENG-13")
    assert response.status_code == 200
    assert 'class="issue-ref"' not in response.text
    assert "SPEC-050" in response.text
    assert "SQL-7" in response.text


def test_a_reference_inside_code_is_not_linked(workspace: SimpleNamespace) -> None:
    _seed_issue(workspace.db, team_key="ENG", number=12, title="Referenced")
    _seed_issue(
        workspace.db,
        team_key="ENG",
        number=13,
        title="Refers",
        description="`ENG-12` in backticks",
    )
    response = _page(workspace, "ENG-13")
    assert response.status_code == 200
    assert "<code>ENG-12</code>" in response.text
    assert 'class="issue-ref"' not in response.text


def test_a_reference_glued_to_other_characters_does_not_link(workspace: SimpleNamespace) -> None:
    _seed_issue(workspace.db, team_key="HIFA", number=103, title="Glued neighbour")
    _seed_issue(
        workspace.db,
        team_key="ENG",
        number=13,
        title="Refers",
        description="HIFA-1034, XHIFA-103 and HIFA-103",
    )
    response = _page(workspace, "ENG-13")
    assert response.status_code == 200
    assert _links(response.text) == [("/HIFA/issue/HIFA-103", "HIFA-103")]
    assert "HIFA-1034" in response.text
    assert "XHIFA-103" in response.text


def test_a_reference_in_a_comment_body_links_to_the_issue_it_names(
    workspace: SimpleNamespace,
) -> None:
    _seed_issue(workspace.db, team_key="ENG", number=12, title="Referenced")
    _seed_issue(workspace.db, team_key="ENG", number=13, title="Refers", description="plain")
    _run("issue", "comment", "ENG-13", "blocked by ENG-12")
    response = _page(workspace, "ENG-13")
    assert response.status_code == 200
    assert _links(response.text) == [("/ENG/issue/ENG-12", "ENG-12")]


def test_a_reference_to_another_team_s_issue_links_to_that_team_s_page(
    workspace: SimpleNamespace,
) -> None:
    _seed_issue(workspace.db, team_key="HIFA", number=1, title="The blocker")
    _seed_issue(
        workspace.db,
        team_key="ENG",
        number=13,
        title="Refers",
        description="blocked by HIFA-1",
    )
    response = _page(workspace, "ENG-13")
    assert response.status_code == 200
    assert _links(response.text) == [("/HIFA/issue/HIFA-1", "HIFA-1")]


def test_the_references_of_a_surface_are_resolved_in_one_statement(
    workspace: SimpleNamespace,
    trace: SimpleNamespace,
) -> None:
    """Fourteen references, one statement: N references do not cost N statements (D-081)."""
    for number in range(1, 15):
        _seed_issue(
            workspace.db,
            team_key="ENG",
            number=number,
            title=f"Issue {number}",
            description=None if number != 13 else " ".join(f"ENG-{n}" for n in range(1, 13)),
        )
    _run("issue", "comment", "ENG-13", "one more in ENG-14")
    _run("issue", "comment", "ENG-13", "and ENG-14 again")
    _run("issue", "comment", "ENG-13", "no reference here")

    trace.statements.clear()
    response = _page(workspace, "ENG-13")

    assert response.status_code == 200
    links = _links(response.text)
    assert len(links) == 14
    assert [href for href, _ in links].count("/ENG/issue/ENG-14") == 2
    reading = [sql for sql in trace.statements if REF_STATEMENT.search(sql)]
    assert len(reading) == 1, reading
    # The one statement carries the description's candidates *and* the comments': a per-part
    # resolution would answer two statements, and a per-match one would answer fourteen.
    assert "'ENG-1'" in reading[0]
    assert "'ENG-14'" in reading[0]


def test_a_page_whose_text_names_no_candidate_pays_no_reference_statement(
    workspace: SimpleNamespace,
    trace: SimpleNamespace,
) -> None:
    """No candidate is no statement **and** no connection: the mapping is built only when asked.

    The detail read is the first statement of every render; the reference statement is the second,
    and a page that names nothing never pays it. The count of opened connections is what tells the
    two apart — a connection opened for an empty candidate set would issue no statement either, so
    the statement trace alone cannot answer this claim. The same page with one reference added is
    the control: it opens exactly one connection more, and that one carries the statement.
    """
    _seed_issue(workspace.db, team_key="ENG", number=12, title="Referenced")
    _seed_issue(workspace.db, team_key="ENG", number=13, title="Refers", description="plain text")

    # The revision watch opens its one connection lazily on the first read, so the warm-up page
    # pays it; the count below then measures the request's own connections and nothing else.
    _page(workspace, "ENG-13")

    trace.statements.clear()
    trace.opens.clear()
    quiet = _page(workspace, "ENG-13")
    assert quiet.status_code == 200
    assert [sql for sql in trace.statements if REF_STATEMENT.search(sql)] == []
    assert 'class="issue-ref"' not in quiet.text
    quiet_opens = len(trace.opens)

    with sqlite3.connect(workspace.db) as conn:
        conn.execute("UPDATE issue SET description = 'follows ENG-12' WHERE identifier = 'ENG-13'")

    trace.statements.clear()
    trace.opens.clear()
    loud = _page(workspace, "ENG-13")
    assert loud.status_code == 200
    assert _links(loud.text) == [("/ENG/issue/ENG-12", "ENG-12")]
    assert len([sql for sql in trace.statements if REF_STATEMENT.search(sql)]) == 1
    assert len(trace.opens) == quiet_opens + 1


def test_a_surface_that_renders_no_text_pays_no_reference_statement(
    workspace: SimpleNamespace,
    trace: SimpleNamespace,
) -> None:
    """The history list and six of the seven field wrappers print no markdown: they pay nothing.

    `history_list.html` prints the timeline and `field_{title,state,priority,assignee,due_date,
    estimate}.html` print one control each; only `field_description.html` prints
    `description_html`. The caller that knows its template says so (`renders_text`), and the
    statement is skipped before the connection is opened.
    """
    _seed_issue(workspace.db, team_key="ENG", number=12, title="Referenced")
    _seed_issue(
        workspace.db, team_key="ENG", number=13, title="Refers", description="follows ENG-12"
    )

    trace.statements.clear()
    history = workspace.client.get("/_/issues/ENG-13/history?limit=100", headers=HTMX)
    assert history.status_code == 200
    assert "<ol" in history.text
    assert 'class="issue-ref"' not in history.text
    assert [sql for sql in trace.statements if REF_STATEMENT.search(sql)] == []

    trace.statements.clear()
    title = workspace.client.patch(
        "/_/issues/ENG-13",
        params={"view": "field", "field": "title"},
        headers=HTMX,
        data={"title": "Refers"},
    )
    assert title.status_code == 200
    assert 'class="issue-ref"' not in title.text
    assert [sql for sql in trace.statements if REF_STATEMENT.search(sql)] == []

    trace.statements.clear()
    description = workspace.client.patch(
        "/_/issues/ENG-13",
        params={"view": "field", "field": "description"},
        headers=HTMX,
        data={"description": "follows ENG-12"},
    )
    assert description.status_code == 200
    assert _links(description.text) == [("/ENG/issue/ENG-12", "ENG-12")]
    assert len([sql for sql in trace.statements if REF_STATEMENT.search(sql)]) == 1
