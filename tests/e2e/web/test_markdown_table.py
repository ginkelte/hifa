"""`GET /ENG/issue/ENG-1`: a GFM table in a description is served as a table (HIFA-113).

SPEC-070 §2.3.6 and D-079: the `table` rule is enabled on top of the `commonmark` preset, the six
table elements join the allowlist, an aligned column keeps its one `style`, and the walker wraps the
`table` in its own `div.table-scroll`. The second half of the test is the guard on the first: the
rule that renders the structure must not have opened a hole, so a `<script>` and a `javascript:`
link written into the same description still arrive as text.
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
DESCRIPTION = (
    "| Name | Qty |\n|:--|:-:|\n| Bolt | 4 |\n\n"
    "<script>alert(1)</script> and [x](javascript:alert(1))"
)


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


@pytest.fixture
def table_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """One issue whose description is a table, followed by the payloads the spec forbids."""
    project = tmp_path / "proj-table"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "table.db"))
    _run("init", "--team-key", "ENG")
    _run("issue", "create", "Inventory table", "--description", DESCRIPTION)
    db = tmp_path / "table.db"
    return SimpleNamespace(db=db, client=TestClient(create_app(db, default_team="ENG")))


def test_a_markdown_table_in_a_description_is_served_as_a_table(
    table_issue: SimpleNamespace,
) -> None:
    """The scenario of 17-dashboard-hardening.feature: structure, alignment, and no hole."""
    body = table_issue.client.get("/ENG/issue/ENG-1").text

    assert '<div class="table-scroll"><table>' in body
    assert re.search(r"<th[^>]*>Name</th>", body)
    assert re.search(r"<td[^>]*>Bolt</td>", body)
    assert "text-align:center" in body
    # The pipes are structure now, not a paragraph of them (the edit form still holds the source).
    assert "<p>| Name |" not in body
    # And the description still cannot carry markup of its own.
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body
    assert 'href="javascript:' not in body
