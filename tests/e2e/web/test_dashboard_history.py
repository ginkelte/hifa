"""E2E of the activity timeline: the panel, the page and the fragment that expands it.

Every test drives the real factory through `TestClient` against a database seeded by the real
CLI and reads the HTML a browser would receive, so an assertion here is about what a reader
sees: the sentence, the order, the cut and the control.
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

#: The section, the list and one event, as the templates render them.
SECTION = re.compile(r'<section class="history">(.*?)</section>', re.S)
LIST = re.compile(r'<ol id="history-list"[^>]*>(.*?)</ol>', re.S)
EVENT = re.compile(r'<li class="history-item".*?</li>', re.S)
SENTENCE = re.compile(r'<span class="history-sentence">(.*?)</span>\s*<time', re.S)
TIME = re.compile(r'<time class="history-time" datetime="([^"]+)"')
#: A ULID of the table: 26 characters of Crockford's base32.
ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
#: One tag, opening or closing: what `_visible` takes away.
TAG = re.compile(r"<[^>]+>")


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


def _person(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, rows: int = 0) -> SimpleNamespace:
    """A workspace with one issue, `rows` extra events and a client over the same file."""
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    _run("init", "--team-key", "ENG")
    _run("issue", "create", "Fix SSO login")
    db = tmp_path / "hifa.db"
    if rows:
        _seed(db, rows)
    return SimpleNamespace(
        db=db,
        client=TestClient(create_app(db, default_team="ENG")),
        identifier="ENG-1",
    )


def _stamp(index: int) -> str:
    """An ISO-8601 instant that rises with `index`, all of them in the future."""
    return f"2030-03-{1 + index // 60:02d}T00:{index % 60:02d}:00Z"


def _seed(db: Path, count: int, *, last: tuple[str, str, str] = ("title", "Old", "New")) -> None:
    """`count` events stamped in the future, so the window and the cut are observable."""
    with sqlite3.connect(db) as conn:
        (issue_id,) = conn.execute("SELECT id FROM issue WHERE identifier = 'ENG-1'").fetchone()
        rows = [(issue_id, None, f"field{i:03d}", None, "v", _stamp(i)) for i in range(count - 1)]
        field, old, new = last
        rows.append((issue_id, None, field, old, new, "2030-04-01T00:00:00Z"))
        conn.executemany(
            "INSERT INTO issue_history"
            " (issue_id, actor_id, field, old_value, new_value, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def _add(
    db: Path, *, field: str, old: str | None, new: str | None, actor: str | None = None
) -> None:
    """One event with every side spelled out, stamped after every other row of the fixture."""
    with sqlite3.connect(db) as conn:
        (issue_id,) = conn.execute("SELECT id FROM issue WHERE identifier = 'ENG-1'").fetchone()
        conn.execute(
            "INSERT INTO issue_history"
            " (issue_id, actor_id, field, old_value, new_value, created_at)"
            " VALUES (?, ?, ?, ?, ?, '2030-05-01T00:00:00Z')",
            (issue_id, actor, field, old, new),
        )


def _member(db: Path) -> str:
    """The display name of the one member the workspace bootstrapped."""
    with sqlite3.connect(db) as conn:
        (name,) = conn.execute("SELECT display_name FROM member LIMIT 1").fetchone()
    return str(name)


def _section(response_text: str) -> str:
    match = SECTION.search(response_text)
    assert match is not None, "the timeline is missing"
    return match.group(0)


def _visible(markup: str) -> str:
    """The text a reader sees: every tag — and so every `data-*` a tag carries — taken away."""
    return TAG.sub(" ", markup)


def _sentences(response_text: str) -> list[str]:
    """The text of every event of the timeline, in the order it is printed."""
    return [
        " ".join(re.sub(r"<[^>]+>", "", SENTENCE.search(event).group(1)).split())  # type: ignore[union-attr]
        for event in EVENT.findall(response_text)
    ]


# --- the story ---------------------------------------------------------------------------


def test_the_timeline_reads_as_sentences_oldest_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`created` first, then the two moves: no field name, no previous/next pair, no ULID."""
    world = _person(tmp_path, monkeypatch)
    _run("issue", "move", "ENG-1", "-s", "Backlog")
    _run("issue", "move", "ENG-1", "-s", "Todo")

    member = _member(world.db)
    page = world.client.get(f"/ENG/issue/{world.identifier}")
    section = _section(page.text)

    assert page.status_code == 200
    assert _sentences(section)[-3:] == [
        f"{member} created the issue",
        f"{member} moved from Todo to Backlog",
        f"{member} moved from Backlog to Todo",
    ]
    assert "state_id" not in section
    # The raw pair is on the row as `data-old`/`data-new`, so a reader of the DOM can still see
    # the ULID the row held: what this asserts is that no ULID reaches the *text*.
    assert 'data-old="' in section and 'data-new="' in section
    assert ULID.search(_visible(section)) is None
    assert "→" not in section


def test_the_actor_and_the_sentence_are_two_strings_juxtaposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The row reads as one line because the actor precedes the formatter's sentence."""
    world = _person(tmp_path, monkeypatch)
    _run("issue", "move", "ENG-1", "-s", "Backlog")

    sentences = _sentences(_section(world.client.get(f"/ENG/issue/{world.identifier}").text))

    assert sentences[-1] == f"{_member(world.db)} moved from Todo to Backlog"


def test_the_last_activity_line_names_the_newest_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one dim line under the title repeats the last row, and no row is replaced by it."""
    world = _person(tmp_path, monkeypatch)
    _run("issue", "move", "ENG-1", "-s", "Backlog")

    page = world.client.get(f"/ENG/issue/{world.identifier}")

    line = re.search(r'<p class="dim last-activity">(.*?)</p>', page.text, re.S)
    assert line is not None, "the last-activity line is missing"
    assert f"{_member(world.db)} moved from Todo to Backlog" in line.group(1)
    assert " · " in line.group(1)
    # The line is the anchor a click scrolls to: the id of the newest row of the list.
    assert re.search(r'<a href="#event-\d+">', line.group(1)) is not None


# --- the cut and the expansion -----------------------------------------------------------


def test_the_panel_shows_eight_rows_and_offers_the_rest_of_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eight rows and `Show all N`, where N is what the read returned: 12 here, not a count."""
    world = _person(tmp_path, monkeypatch, rows=11)  # 12 with the `created` row

    panel = world.client.get(f"/_/issues/{world.identifier}/panel", headers=HTMX)
    section = _section(panel.text)

    assert panel.status_code == 200
    assert len(EVENT.findall(section)) == 8
    assert "Show all 12" in section


def test_the_page_prints_the_whole_window_and_offers_no_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page is not cut at eight and has nothing to expand: the control belongs to the panel.

    The cut is eight rows *of the panel*; the page prints the window it was given, so a `Show all`
    there would replace twelve rows with the same twelve. The scene that asks the page for the
    control is wrong and HIFA-100 carries it.
    """
    world = _person(tmp_path, monkeypatch, rows=11)

    section = _section(world.client.get(f"/ENG/issue/{world.identifier}").text)

    assert len(EVENT.findall(section)) == 12
    assert "Show all" not in section


def test_show_all_is_one_button_whose_triple_names_a_real_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`hx-get` the fragment, `hx-target` the list, `hx-swap=outerHTML`: and the route answers.

    `070` §7 asserts every `Then` of the expansion and nothing about how the control asks for it;
    three attributes that disagree with the fragment's route expand nothing at all.
    """
    world = _person(tmp_path, monkeypatch, rows=11)

    section = _section(world.client.get(f"/_/issues/{world.identifier}/panel", headers=HTMX).text)
    button = re.search(r'<button[^>]*class="btn history-all"[^>]*>.*?</button>', section, re.S)

    assert button is not None, "the panel lost its `Show all`"
    markup = button.group(0)
    assert 'hx-target="#history-list"' in markup
    assert 'hx-swap="outerHTML"' in markup
    assert '<ol id="history-list"' in section
    asked = re.search(r'hx-get="([^"]+)"', markup)
    assert asked is not None
    assert asked.group(1) == f"/_/issues/{world.identifier}/history?limit=100"
    assert "Show all 12" in markup

    answer = world.client.get(asked.group(1), headers=HTMX)

    assert answer.status_code == 200
    assert len(EVENT.findall(answer.text)) == 12


def test_show_all_answers_the_whole_window_without_a_control_of_its_own(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fragment is the `<ol>` and nothing else: the swap already offered the expansion."""
    world = _person(tmp_path, monkeypatch, rows=11)

    fragment = world.client.get(f"/_/issues/{world.identifier}/history?limit=100", headers=HTMX)

    assert fragment.status_code == 200
    assert len(EVENT.findall(fragment.text)) == 12
    assert "Show all" not in fragment.text
    assert "<section" not in fragment.text


def test_a_full_window_is_a_floor_and_both_hosts_say_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """120 rows return 100: the panel's button names the window and the dim line names the reach."""
    world = _person(tmp_path, monkeypatch, rows=119)  # 120 with the `created` row

    page = _section(world.client.get(f"/ENG/issue/{world.identifier}").text)
    panel = _section(world.client.get(f"/_/issues/{world.identifier}/panel", headers=HTMX).text)
    fragment = world.client.get(f"/_/issues/{world.identifier}/history?limit=100", headers=HTMX)

    assert len(EVENT.findall(page)) == 100
    assert "Show all" not in page
    assert page.count("the last 100 events") == 1
    assert "Show all 100" in panel
    assert len(EVENT.findall(fragment.text)) == 100
    assert fragment.text.count("the last 100 events") == 1
    assert "Show all" not in fragment.text


def test_the_floor_line_travels_inside_the_list_the_swap_replaces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One element carries the rows and the line, so the swap cannot leave the reader two lines.

    The target is `#history-list`, so a line printed *beside* the `<ol>` survives the swap and
    the panel ends up showing its own copy and the fragment's.
    """
    world = _person(tmp_path, monkeypatch, rows=119)

    section = _section(world.client.get(f"/_/issues/{world.identifier}/panel", headers=HTMX).text)
    listing = LIST.search(section)

    assert listing is not None
    assert "the last 100 events" in listing.group(1)
    assert re.search(r'<li class="dim history-floor" role="note">the last 100 events</li>',
                     listing.group(1)) is not None
    # The line is inside the list and not in any row's accessible name.
    assert all("the last 100 events" not in row for row in EVENT.findall(section))


def test_the_fragment_ignores_a_limit_bigger_than_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`limit` is the control's window and not an argument: it cannot ask for more than 100."""
    world = _person(tmp_path, monkeypatch, rows=119)

    fragment = world.client.get(f"/_/issues/{world.identifier}/history?limit=500", headers=HTMX)

    assert fragment.status_code == 200
    assert len(EVENT.findall(fragment.text)) == 100


# --- the empty and the unreadable --------------------------------------------------------


def test_an_issue_without_a_history_renders_no_timeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No section, no list and no `No activity`: a gap is better than a sentence about nothing."""
    world = _person(tmp_path, monkeypatch)
    with sqlite3.connect(world.db) as conn:
        conn.execute("DELETE FROM issue_history")

    page = world.client.get(f"/ENG/issue/{world.identifier}")

    assert page.status_code == 200
    assert "history-list" not in page.text
    assert '<section class="history">' not in page.text
    assert "No activity" not in page.text


def test_a_state_that_is_gone_reads_as_the_kind_and_the_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The label is `state (deleted)`, never the stored ULID and never an arrow."""
    world = _person(tmp_path, monkeypatch)
    _add(world.db, field="state_id", old="01M75KV68MF9TYWEK2GHWPNBZZ", new=None)
    with sqlite3.connect(world.db) as conn:
        (todo,) = conn.execute("SELECT id FROM workflow_state WHERE name = 'Todo'").fetchone()
    _add(world.db, field="state_id", old="01M75KV68MF9TYWEK2GHWPNBZY", new=todo)

    section = _section(world.client.get(f"/ENG/issue/{world.identifier}").text)

    assert "state (deleted)" in section
    # The two unknown ULIDs are what the row stored: they stay on `data-old`/`data-new` and never
    # enter the text, so the assertion is about the visible words and not about the markup.
    assert 'data-old="01M75KV68MF9TYWEK2GHWPNBZZ"' in section
    assert ULID.search(_visible(section)) is None
    assert "→" not in section


def test_an_unknown_field_and_an_unknown_actor_are_named_and_never_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The formatter answers `someone` and `changed mood`; the page is still a `200`."""
    world = _person(tmp_path, monkeypatch)
    _add(world.db, field="mood", old="happy", new="sad", actor="01M71T2XA1DS4WJ2Z7ZXBTNWZZ")

    page = world.client.get(f"/ENG/issue/{world.identifier}")
    section = _section(page.text)

    assert page.status_code == 200
    assert "someone" in section
    assert "changed mood" in section


# --- the markup --------------------------------------------------------------------------


def test_the_row_carries_the_stored_pair_and_the_text_carries_only_labels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`data-old`/`data-new` hold what the row stored; the sentence holds the two labels only.

    The pair is the one place a ULID is allowed to be — on the DOM, never in the text a CSS
    `content:` or a screen reader could pick up (§2.5.7.1 rule 1). A side the row does not have
    is an empty attribute and never the string `None`.
    """
    world = _person(tmp_path, monkeypatch)
    _run("issue", "move", "ENG-1", "-s", "Backlog")
    with sqlite3.connect(world.db) as conn:
        ids = dict(conn.execute("SELECT name, id FROM workflow_state").fetchall())

    section = _section(world.client.get(f"/ENG/issue/{world.identifier}").text)
    rows = EVENT.findall(section)
    created, moved = rows[0], rows[-1]

    assert 'data-old=""' in created and 'data-new=""' in created
    assert f'data-old="{ids["Todo"]}"' in moved
    assert f'data-new="{ids["Backlog"]}"' in moved
    assert "moved from Todo to Backlog" in _visible(moved)
    assert ULID.search(_visible(moved)) is None


def test_a_title_row_keeps_the_old_title_in_its_title_attribute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The replaced title is the row's `title`, and the row is focusable — not a hover-only note."""
    world = _person(tmp_path, monkeypatch)
    _add(world.db, field="title", old="Old title", new="New title")

    section = _section(world.client.get(f"/ENG/issue/{world.identifier}").text)
    row = EVENT.findall(section)[-1]

    assert 'title="Old title"' in row
    assert 'tabindex="0"' in row
    assert "changed the title to \u201cNew title\u201d" in _visible(row)
    assert "Old title" not in _visible(row)


def test_the_timeline_is_a_list_of_times_and_not_a_table(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One `<li>` per event, one `<time datetime>` per row, and no column header of a table."""
    world = _person(tmp_path, monkeypatch)
    _run("issue", "move", "ENG-1", "-s", "Backlog")

    section = _section(world.client.get(f"/ENG/issue/{world.identifier}").text)

    assert LIST.search(section) is not None
    assert len(EVENT.findall(section)) == 2
    instants = TIME.findall(section)
    assert len(instants) == 2
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) for value in instants)
    assert "FROM" not in section
    assert "TO" not in section
    assert "<table" not in section


# --- the trash ---------------------------------------------------------------------------


def test_the_fragment_answers_for_an_issue_in_the_trash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The page and the panel are the two surfaces that read the trash; this route re-renders one.

    It is the panel's own read, so `Show all` on a trashed issue answers `200`, while the row
    and card fragments keep answering `404`.
    """
    world = _person(tmp_path, monkeypatch)
    _run("issue", "delete", "ENG-1", "--yes")

    fragment = world.client.get(f"/_/issues/{world.identifier}/history?limit=100", headers=HTMX)

    assert fragment.status_code == 200
    assert len(EVENT.findall(fragment.text)) == 2
    assert world.client.get(f"/ENG/issue/{world.identifier}").status_code == 200
    assert world.client.get(f"/_/issues/{world.identifier}/row", headers=HTMX).status_code == 404


def test_the_fragment_is_htmx_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bare browser hit is a `400`: the route is part of a page, not a page."""
    world = _person(tmp_path, monkeypatch)

    assert world.client.get(f"/_/issues/{world.identifier}/history").status_code == 400
