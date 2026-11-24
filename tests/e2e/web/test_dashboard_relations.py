"""E2E of the Relations block: the far end's title is an anchor of its own, to that issue.

The page is driven through the real factory and `TestClient` against a database seeded by the
real CLI, so the assertion is about the markup a browser receives: two anchors to the far end's
page, one named by the identifier and one by the title, and no title left as bare text.
"""

from __future__ import annotations

import html as html_lib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from hifa.cli.main import app as cli_app
from hifa.web.app import create_app

runner = CliRunner()

#: The block, its one row and its anchors, as `issue_detail.html` renders them.
SECTION = re.compile(r'<section class="relations">(.*?)</section>', re.S)
ROW = re.compile(r"<li>(.*?)</li>", re.S)
ANCHOR = re.compile(r'<a class="([^"]*)" href="([^"]*)">(.*?)</a>', re.S)

FAR_TITLE = "Rotate the session keys"


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


def _person(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A workspace with two issues and one `related` edge, and a client over the same file."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    _run("init", "--team-key", "ENG")
    _run("issue", "create", "Fix SSO login")
    _run("issue", "create", FAR_TITLE)
    _run("issue", "relate", "ENG-1", "related", "ENG-2")
    return SimpleNamespace(
        db=tmp_path / "hifa.db",
        client=TestClient(create_app(tmp_path / "hifa.db", default_team="ENG")),
        identifier="ENG-1",
    )


def _row(response_text: str) -> str:
    """The one `<li>` of the Relations block; the block itself is asserted to exist."""
    section = SECTION.search(response_text)
    assert section is not None, "the relations block is missing"
    rows = ROW.findall(section.group(1))
    assert len(rows) == 1, rows
    return rows[0]


def test_the_title_of_a_related_issue_links_to_that_issue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two anchors to the far end's page — by identifier and by title — and no title beside them.

    The row is three elements and the title is one of them as an anchor of its own: one anchor
    around both would fold the title into the identifier's link and change the row's markup.
    """
    world = _person(tmp_path, monkeypatch)

    page = world.client.get(f"/ENG/issue/{world.identifier}")

    assert page.status_code == 200
    row = _row(page.text)
    anchors = ANCHOR.findall(row)

    assert [href for _, href, _ in anchors] == ["/ENG/issue/ENG-2", "/ENG/issue/ENG-2"]
    assert [html_lib.unescape(text) for _, _, text in anchors] == ["ENG-2", FAR_TITLE]
    assert [classes.split() for classes, _, _ in anchors] == [["identifier"], ["relation-title"]]
    # The row's reading order, on its visible text: every tag is dropped without standing in for
    # a space, so the only separators left are the template's own. Label, identifier, title, in
    # that order and each apart from the next; joining the two anchors on one line renders
    # `ENG-2Rotate the session keys` and fails here, where the anchors' texts would not notice.
    assert " ".join(re.sub(r"<[^>]+>", "", row).split()) == f"related ENG-2 {FAR_TITLE}"
    # The title lives inside its anchor: with the anchors taken away, the row still carries the
    # label as an element of its own, and neither the identifier nor the title survives as loose
    # text beside the anchor.
    loose = ANCHOR.sub("", row)
    assert FAR_TITLE not in loose
    assert "ENG-2" not in loose
    assert re.search(r'<span class="relation-type">\s*related\s*</span>', loose), loose
