"""E2E of the per-field inline editor of the detail page and the panel (SPEC-070 §2.6.3).

One field is one control: a `PATCH` names the field it changed (`?view=field&field={name}`) and
the answer is that field's wrapper, never the page, the aside or the row. The tests are named
after the acceptance scenarios of `16-dashboard.feature`, and they read the database beside the
HTML — a `200` proves nothing if the column did not move, and a wrapper that answers the wrong
field is indistinguishable from one that answers the right one unless the two are read apart.

One test at the end reads `app.js` as text: the repository has no JavaScript harness, so the four
client rules of §2.1.3/§2.6.2/§2.6.3 are pinned by their source and not by a browser, and the
test says so in its own docstring.
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
#: `app.js`, read as text by the one static test of the client.
JS = Path(__file__).resolve().parents[3] / "src" / "hifa" / "web" / "static" / "js" / "app.js"
#: The title the fixture's issue already holds — the value a refused write must keep.
STORED_TITLE = "Fix login"
#: The description the fixture's issue already holds.
STORED_DESCRIPTION = "first draft"
#: The estimate the fixture's issue already holds; the no-JS scenario saves `3` over it.
STORED_ESTIMATE = 1
HISTORY = (
    "SELECT h.field FROM issue_history h JOIN issue i ON i.id = h.issue_id"
    " WHERE i.identifier = ? ORDER BY h.id"
)
#: One `<form>` opening tag of a wrapper, with every attribute it carries.
FORM_TAG = re.compile(r"<form\b[^>]*>", re.DOTALL)
#: The two attributes a form submits by, read from that tag.
FORM_ATTRIBUTE = re.compile(r'\b(action|method)="([^"]*)"')
#: One `<option>` of a `<select>` and whether it is the selected one.
OPTION = re.compile(r'<option value="([^"]*)"( selected)?>')
#: The seven wrappers, in the order §2.3.4 declares them.
FIELD_NAMES = (
    "title",
    "description",
    "priority",
    "state",
    "assignee",
    "due_date",
    "estimate",
)


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolated cwd + HIFA_DB with one team (ENG) and one issue in Todo with every field set.

    Every field the editor writes is set on the row, the assignee included: the no-JS scenario
    saves each control through its own `<form>`, and a write over an empty column proves nothing.
    """
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    _run("init", "--team-key", "ENG")
    _run(
        "issue",
        "create",
        STORED_TITLE,
        "--state",
        "Todo",
        "--description",
        STORED_DESCRIPTION,
        "--estimate",
        str(STORED_ESTIMATE),
        "--assignee",
        "ME",
    )
    db = tmp_path / "hifa.db"
    with sqlite3.connect(db) as conn:
        (identifier,) = conn.execute("SELECT identifier FROM issue").fetchone()
    return SimpleNamespace(
        db=db,
        identifier=identifier,
        client=TestClient(create_app(db, default_team="ENG")),
    )


def _value_of(column: str, db: Path, identifier: str) -> object:
    """One column of the issue row the test is about."""
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            f"SELECT {column} FROM issue WHERE identifier = ?",  # noqa: S608 - a literal column
            (identifier,),
        ).fetchone()
    assert row is not None, f"{identifier} is not in the database"
    return row[0]


def _history(db: Path, identifier: str) -> list[str]:
    """`field` of every history row of the issue, oldest first."""
    with sqlite3.connect(db) as conn:
        return [field for (field,) in conn.execute(HISTORY, (identifier,))]


def test_the_detail_page_renders_each_field_as_an_inline_control_and_no_pooled_edit_form(
    seeded: SimpleNamespace,
) -> None:
    """Each of the seven fields is its own wrapper, and the pooled form of the page is gone."""
    response = seeded.client.get(f"/ENG/issue/{seeded.identifier}")

    assert response.status_code == 200
    html = response.text
    for name in FIELD_NAMES:
        assert f'id="field-{name}-{seeded.identifier}"' in html, name
    assert "hx-patch" in html
    assert "?view=field&field=title" in html
    assert "form-title" not in html
    assert 'class="issue-edit"' not in html


def test_an_inline_title_edit_on_the_detail_page_answers_the_field_it_changed(
    seeded: SimpleNamespace,
) -> None:
    """One `PATCH` names the field, and the answer is that wrapper — not the page nor the aside."""
    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "field", "field": "title"},
        headers=HTMX,
        data={"title": "Fix SSO login"},
    )

    assert response.status_code == 200
    assert response.headers["HX-Trigger"] == "issue-updated"
    assert f'id="field-title-{seeded.identifier}"' in response.text
    assert "Fix SSO login" in response.text
    assert "<html>" not in response.text
    assert "<aside" not in response.text
    assert _value_of("title", seeded.db, seeded.identifier) == "Fix SSO login"
    assert _history(seeded.db, seeded.identifier).count("title") == 1


def test_a_refused_inline_edit_keeps_the_control_and_the_message_beside_it(
    seeded: SimpleNamespace,
) -> None:
    """A `422` comes back as the wrapper, open, marked, with the message and the stored value."""
    before = _history(seeded.db, seeded.identifier)

    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "field", "field": "title"},
        headers=HTMX,
        data={"title": ""},
    )

    assert response.status_code == 422
    html = response.text
    assert "is-invalid" in html
    assert "<details open" in html
    assert 'class="form-error"' in html
    assert f'value="{STORED_TITLE}"' in html
    assert _value_of("title", seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier) == before, "a refused edit wrote a history row"


def _field_markup(html: str, identifier: str, name: str) -> str:
    """The wrapper of one field, from its opening tag to the next wrapper's."""
    start = html.index(f'id="field-{name}-{identifier}"')
    end = html.find('id="field-', start + 1)
    return html[start:] if end < 0 else html[start:end]


def test_the_description_is_edited_in_place_and_ctrl_enter_saves_it(
    seeded: SimpleNamespace,
) -> None:
    """The multi-line field saves as one field, and the answer renders the stored markdown.

    The title names the scenario of `16-dashboard.feature`, but this test pins the **server's**
    half: the `PATCH` one field sends and the answer it gets back. `Cmd/Ctrl+Enter` is the
    client's half — a keystroke no request of this test performs — and it is pinned by
    `test_the_client_rules_of_the_inline_editor_are_what_app_js_says` below, which reads the
    branch out of `app.js`. A test that posted the `PATCH` and claimed the shortcut would pass
    with the keystroke deleted.
    """
    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "field", "field": "description"},
        headers=HTMX,
        data={"description": "**final** draft"},
    )

    assert response.status_code == 200
    assert "<strong>final</strong>" in response.text
    assert f'id="field-description-{seeded.identifier}"' in response.text
    assert _value_of("description", seeded.db, seeded.identifier) == "**final** draft"
    assert _history(seeded.db, seeded.identifier).count("description") == 1


def test_the_description_s_control_is_a_textarea_with_a_save_and_no_blur_trigger(
    seeded: SimpleNamespace,
) -> None:
    """`Enter` is a newline in a textarea: the field saves on its `Save` and on `Cmd/Ctrl+Enter`."""
    response = seeded.client.get(f"/ENG/issue/{seeded.identifier}")

    assert response.status_code == 200
    assert "<textarea" in response.text
    markup = _field_markup(response.text, seeded.identifier, "description")
    assert 'name="description"' in markup
    assert "field-save" in markup
    assert "?view=field&field=description" in markup
    assert "blur changed" not in markup


def test_the_panel_renders_one_inline_control_per_field_and_no_edit_heading(
    seeded: SimpleNamespace,
) -> None:
    """The panel is the same seven wrappers, and the `dl.meta` line it replaced is gone."""
    response = seeded.client.get(f"/_/issues/{seeded.identifier}/panel", headers=HTMX)

    assert response.status_code == 200
    html = response.text
    assert "form-title" not in html
    assert f'hx-patch="/_/issues/{seeded.identifier}?view=field&field=title"' in html
    assert f'hx-patch="/_/issues/{seeded.identifier}?view=field&field=estimate"' in html
    assert "<dt>State</dt>" not in html
    assert "<dt>Assignee</dt>" not in html
    for name in FIELD_NAMES:
        assert f'id="field-{name}-{seeded.identifier}"' in html, name


def test_a_refused_inline_edit_on_the_panel_answers_that_field_and_not_the_aside(
    seeded: SimpleNamespace,
) -> None:
    """A refusal on a field that sits in the panel answers the wrapper, never the whole aside.

    The row is read beside the HTML: the answer renders the stored state and no history row was
    written, because a `422` that marked the control and still wrote would be a refusal in name
    only.
    """
    stored_state = _value_of("state_id", seeded.db, seeded.identifier)
    before = _history(seeded.db, seeded.identifier)

    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "field", "field": "state"},
        headers=HTMX,
        data={"state": "No Such State"},
    )

    assert response.status_code == 422
    assert f'id="field-state-{seeded.identifier}"' in response.text
    assert "is-invalid" in response.text
    assert "<aside" not in response.text
    assert _value_of("state_id", seeded.db, seeded.identifier) == stored_state
    assert _history(seeded.db, seeded.identifier) == before, "a refused edit wrote a history row"


def test_an_inline_edit_of_an_archived_issue_is_refused_with_the_message_and_no_control(
    seeded: SimpleNamespace,
) -> None:
    """The archived issue's `409` comes back as the value, the message and no disclosure at all."""
    _run("issue", "archive", seeded.identifier, "--yes")

    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "field", "field": "title"},
        headers=HTMX,
        data={"title": "Fix SSO login"},
    )

    assert response.status_code == 409
    html = response.text
    assert f'id="field-title-{seeded.identifier}"' in html
    assert "archived" in html
    assert "data-readonly" in html
    assert "<details" not in html
    assert "hx-patch" not in html
    assert _value_of("title", seeded.db, seeded.identifier) == STORED_TITLE


def _form_of(html: str, identifier: str, name: str) -> dict[str, str]:
    """The `action` and `method` of the `<form>` inside one field wrapper, read from the page."""
    match = FORM_TAG.search(_field_markup(html, identifier, name))
    assert match is not None, f"the {name} wrapper renders no form to submit without JavaScript"
    attributes = dict(FORM_ATTRIBUTE.findall(match.group(0)))
    missing = [attribute for attribute in ("method", "action") if attribute not in attributes]
    assert not missing, f"the {name} form carries no {missing[0]}: {match.group(0)}"
    return attributes


def test_without_javascript_each_inline_control_still_saves_through_the_page_form(
    seeded: SimpleNamespace,
) -> None:
    """Each wrapper's own `<form>` is the no-JS path, and the page is what states its URL.

    The detail page is read first and every write is taken from the markup the renderer
    produced: the `action` and `method` of the `<form>` inside each of the seven wrappers, and
    for the two `<select>`s an option the control does not have `selected`. A hardcoded URL
    passed with every one of them pointing elsewhere; reading the page is the only way the
    `action` and the `method` are what is being tested. The row is read back after each post,
    because a `303` proves a route answered and not that the column moved.
    """
    page = seeded.client.get(f"/ENG/issue/{seeded.identifier}")
    assert page.status_code == 200
    detail_url = f"/ENG/issue/{seeded.identifier}"

    # The select that is not the stored one: any of its options is a value the control itself
    # offers, so the write is one a reader could have made without typing a reference.
    next_state = next(
        option
        for option, selected in OPTION.findall(_field_markup(page.text, seeded.identifier, "state"))
        if not selected
    )

    # field -> the column it writes, the value the form sends, and what the column must hold.
    writes = (
        ("title", "title", "Fix login without JS", "Fix login without JS"),
        ("description", "description", "second draft", "second draft"),
        ("priority", "priority", "2", 2),
        ("state", "state_id", next_state, next_state),
        ("assignee", "assignee_id", "", None),
        ("due_date", "due_date", "2026-12-31", "2026-12-31"),
        ("estimate", "estimate", "3", 3),
    )

    for field, column, value, stored in writes:
        form = _form_of(page.text, seeded.identifier, field)
        assert form["method"] == "post", f"{field} posts with {form['method']!r}, not post"
        assert form["action"] == detail_url, f"{field} posts to {form['action']!r}"

        response = seeded.client.post(form["action"], data={field: value}, follow_redirects=False)

        assert response.status_code == 303, field
        assert response.headers["location"] == detail_url, field
        assert _value_of(column, seeded.db, seeded.identifier) == stored, field


def test_the_list_row_keeps_its_own_inline_title_control_and_is_not_a_field_wrapper(
    seeded: SimpleNamespace,
) -> None:
    """`view=list` still answers the `<tr>`: the row is one line and the wrapper is a block."""
    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "list"},
        headers=HTMX,
        data={"title": "Fix SSO login"},
    )

    assert response.status_code == 200
    assert f'<tr id="issue-{seeded.identifier}"' in response.text
    assert "closest tr" in response.text
    assert "field-inline" not in response.text
    assert "view=field" not in response.text


def test_a_field_the_route_does_not_know_is_refused_with_404(seeded: SimpleNamespace) -> None:
    """A `field` outside the seven controls is the toast's `404`, not a wrapper nobody prints."""
    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "field", "field": "duedate"},
        headers=HTMX,
        data={"title": "Fix SSO login"},
    )

    assert response.status_code == 404
    assert response.headers["HX-Retarget"] == "#toasts"
    assert response.headers["HX-Reswap"] == "beforeend"
    assert "is not a field of" in response.text
    assert "field-inline" not in response.text
    assert _value_of("title", seeded.db, seeded.identifier) == STORED_TITLE


def test_an_inline_edit_of_a_trashed_issue_is_refused_with_the_message_and_the_open_wrapper(
    seeded: SimpleNamespace,
) -> None:
    """The trash's `422` is the open wrapper, marked, with the message and no write control."""
    _run("issue", "delete", seeded.identifier, "--yes")

    response = seeded.client.patch(
        f"/_/issues/{seeded.identifier}",
        params={"view": "field", "field": "title"},
        headers=HTMX,
        data={"title": "Fix SSO login"},
    )

    assert response.status_code == 422
    html = response.text
    assert f'id="field-title-{seeded.identifier}"' in html
    assert "<details open" in html
    assert "data-readonly" in html
    assert f"{seeded.identifier} is in the trash; restore it first" in html
    assert "hx-patch" not in html
    assert _value_of("title", seeded.db, seeded.identifier) == STORED_TITLE


# --- the client ---------------------------------------------------------------------------


def _brace_block(source: str, opening: int) -> str:
    """The block a `{` at `opening` opens, up to its matching `}`.

    Counting braces from the opener is what makes one rule's block addressable: a substring
    searched over the whole file passes with the rule moved into another handler, or quoted in a
    comment, and neither is the code that runs.
    """
    depth = 0
    for index in range(opening, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[opening : index + 1]
    raise AssertionError(f"unbalanced braces at offset {opening}")


def _handlers(source: str, event: str) -> list[str]:
    """The body of every `addEventListener("<event>", function (…) { … })` of `app.js`."""
    blocks: list[str] = []
    start = 0
    while True:
        index = source.find(f'addEventListener("{event}"', start)
        if index < 0:
            return blocks
        # A named handler (`addEventListener("…", initBoardSortable)`) has no body here.
        statement = source.index(")", index)
        handler = source.find("function", index, statement)
        start = index + 1
        if handler < 0:
            continue
        opening = source.index("{", handler)
        blocks.append(_brace_block(source, opening))
        start = opening


#: Block and line comments of `app.js`. The file carries no `//` inside a string or a regular
#: expression, so both patterns can be removed without reading the script.
BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
LINE_COMMENT = re.compile(r"//[^\n]*")


def _code(source: str) -> str:
    """`app.js` with its comments removed, so no rule can be matched in the prose that explains it.

    The rules below are substring tests, and a substring is satisfied by the comment beside a
    branch as readily as by the branch — the shell's `keydown` once described `requestSubmit()` in
    a comment while returning before it ran. Stripping the comments first is what makes "the rule
    is written in this block" a statement about the block; `_handlers()` and `_brace_block()` then
    read the stripped source, so a brace in a comment cannot open or close a block either.
    """
    return LINE_COMMENT.sub("", BLOCK_COMMENT.sub("", source))


def test_the_client_rules_of_the_inline_editor_are_what_app_js_says() -> None:
    """The four client rules of §2.1.3/§2.6.2/§2.6.3, pinned by reading `app.js` as text.

    **This test does not run the client.** The repository has no JavaScript harness — no Node
    build step, no bundler and no browser in the test job — so what is asserted here is that each
    rule is written in the block that implements it, and no more: the listener that enables the
    swap of a `409` and of an `HX-Retarget` body, `requestSubmit()` in the `Cmd/Ctrl+Enter`
    branch and the scope that keeps a confirmation `<form>` out of it, the field's
    `details[open]` read **before** the panel branch, and the two focus destinations
    (`data-field` and the panel). The comments of the script are stripped before anything is
    matched: a rule quoted in prose is not a rule that runs. A keystroke, a swap and a focus are
    the browser's half; the server's half is the tests above.
    """
    raw = JS.read_text(encoding="utf-8")
    source = _code(raw)

    # 1. A refusal that replaces a node is swapped in — `422`, `409` and every `HX-Retarget`.
    swaps = [block for block in _handlers(source, "htmx:beforeSwap") if "shouldSwap" in block]
    assert len(swaps) == 1, "the listener that enables the swap of a refusal is gone or doubled"
    assert "xhr.status === 409" in swaps[0]
    assert 'getResponseHeader("HX-Retarget")' in swaps[0]
    assert "shouldSwap = true" in swaps[0]
    assert "isError = false" in swaps[0]

    # 2. `Cmd/Ctrl+Enter` submits the form of the focused control, and only the three scopes of
    #    §2.6.2: a field wrapper, the comment composer and the quick-add — never a confirmation.
    (keydown,) = _handlers(source, "keydown")
    start = keydown.index('event.key === "Enter" && (event.metaKey || event.ctrlKey)')
    enter = _brace_block(keydown, keydown.index("{", start))
    assert "requestSubmit()" in enter
    assert "closest(SUBMIT_SCOPE)" in enter
    declared = source.index("var SUBMIT_SCOPE")
    scope = source[declared : source.index(";", declared)]
    assert ".field-inline" in scope
    assert ".comment-composer" in scope, "the comment composer left the shortcut's scope"
    assert "#quick-add" in scope
    assert "confirm" not in scope, (
        'a `<details class="confirm">` form is inside the shortcut\'s scope: one key would '
        "submit a confirmation"
    )
    # The exclusion is stated where the scope is declared, in the comment above it, and it names
    # the block it excludes — read from the file and not from the stripped source, because a
    # comment is exactly what it is.
    raw_declared = raw.index("var SUBMIT_SCOPE")
    above = raw[max(0, raw_declared - 400) : raw_declared]
    assert 'class="confirm"' in above and "not one of them" in above, (
        "SUBMIT_SCOPE no longer documents why the confirmation form is out of scope"
    )

    # 3. `Esc` closes the field's `<details>` before the panel branch runs: one key, one thing.
    start = keydown.index('event.key === "Escape"')
    escape = _brace_block(keydown, keydown.index("{", start))
    assert "details[open]" in escape
    assert "panelIsOpen()" in escape
    assert escape.index("details[open]") < escape.index("panelIsOpen()")

    # 4. Focus returns to the wrapper the answer replaced, and to the panel when it opened.
    (focus_field,) = [
        block for block in _handlers(source, "htmx:afterSwap") if "focusField" in block
    ]
    assert '[data-field="' in focus_field
    assert "panel.focus()" in focus_field
    (focus_source,) = [
        block for block in _handlers(source, "htmx:beforeSwap") if "focusField" in block
    ]
    assert 'getAttribute("data-field")' in focus_source
    assert "=== PANEL" in focus_source
