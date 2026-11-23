"""E2E of the per-field inline editor of the detail page and the panel (SPEC-070 §2.6.3).

One field is one control: a `PATCH` names the field it changed (`?view=field&field={name}`) and
the answer is that field's wrapper, never the page, the aside or the row. The tests are named
after the acceptance scenarios of `16-dashboard.feature`, and they read the database beside the
HTML — a `200` proves nothing if the column did not move, and a wrapper that answers the wrong
field is indistinguishable from one that answers the right one unless the two are read apart.
"""

from __future__ import annotations

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
    """Isolated cwd + HIFA_DB with one team (ENG) and one issue in Todo with every field set."""
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
    """The multi-line field saves as one field, and the answer renders the stored markdown."""
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
    """A refusal on a field that sits in the panel answers the wrapper, never the whole aside."""
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


def test_without_javascript_each_inline_control_still_saves_through_the_page_form(
    seeded: SimpleNamespace,
) -> None:
    """The field form is a real page form: the same node submits without a script."""
    response = seeded.client.post(
        f"/ENG/issue/{seeded.identifier}",
        data={"estimate": "3"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/ENG/issue/{seeded.identifier}"
    assert _value_of("estimate", seeded.db, seeded.identifier) == 3


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
