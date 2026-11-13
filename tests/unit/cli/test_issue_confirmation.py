"""The confirmation of the destructive verbs: what it asks, where it asks and how it refuses.

The prompt is observable text (it names the identifier, opens with a capital letter and carries
the `[y/N]` default) and it is a diagnostic, so it belongs to `stderr`. A refusal is one sentence
with no code prefix. Driving `_confirm_archive` / `_confirm_delete` directly keeps the terminal
out of the test: they read exactly two things, `sys.stdin` and `typer.echo`, and both are
replaced here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hifa.cli.commands import issue as issue_module
from hifa.cli.errors import CliError, format_human

CATALOG_REFUSAL = "hifa: operation canceled"


class Terminal:
    """Records every prompt written and answers `stdin` with a fixed line."""

    def __init__(self, *, interactive: bool, answer: str) -> None:
        self.interactive = interactive
        self.answer = answer
        self.written: list[tuple[str, bool]] = []

    def stdin(self) -> SimpleNamespace:
        return SimpleNamespace(isatty=lambda: self.interactive, readline=lambda: self.answer)

    def echo(self, text: str, *, err: bool = False, nl: bool = True) -> None:
        self.written.append((text, err))


def _terminal(
    monkeypatch: pytest.MonkeyPatch, *, interactive: bool = True, answer: str = "n\n"
) -> Terminal:
    """Replace the terminal and the echo; returns the record of what was written."""
    terminal = Terminal(interactive=interactive, answer=answer)
    monkeypatch.setattr(issue_module, "sys", SimpleNamespace(stdin=terminal.stdin()))
    monkeypatch.setattr(issue_module.typer, "echo", terminal.echo)
    return terminal


# --- what is asked ----------------------------------------------------------------------


def test_the_archive_prompt_capitalizes_the_verb_and_names_the_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal = _terminal(monkeypatch, answer="y\n")

    issue_module._confirm_archive("ENG-1", yes=False, no_input=False)

    assert terminal.written == [("Archive ENG-1? [y/N]: ", True)]


def test_the_delete_prompt_says_where_the_issue_goes(monkeypatch: pytest.MonkeyPatch) -> None:
    terminal = _terminal(monkeypatch, answer="y\n")

    issue_module._confirm_delete("ENG-1", yes=False, no_input=False)

    assert terminal.written == [("Send ENG-1 to trash? [y/N]: ", True)]


def test_both_prompts_go_to_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """The question is a diagnostic: no byte of it is allowed on the data stream."""
    for confirm in (issue_module._confirm_archive, issue_module._confirm_delete):
        terminal = _terminal(monkeypatch, answer="y\n")
        confirm("ENG-1", yes=False, no_input=False)
        assert terminal.written, confirm.__name__
        assert all(err for _, err in terminal.written), terminal.written


# --- how it refuses ---------------------------------------------------------------------


@pytest.mark.parametrize("confirm", [issue_module._confirm_archive, issue_module._confirm_delete])
def test_an_answered_no_refuses_with_the_catalog_sentence(
    monkeypatch: pytest.MonkeyPatch, confirm: object
) -> None:
    _terminal(monkeypatch, answer="n\n")

    with pytest.raises(CliError) as caught:
        confirm("ENG-1", yes=False, no_input=False)  # type: ignore[operator]

    assert caught.value.code == "confirmation_required"
    assert caught.value.exit_code == 2
    assert format_human(caught.value) == CATALOG_REFUSAL


@pytest.mark.parametrize("confirm", [issue_module._confirm_archive, issue_module._confirm_delete])
def test_an_empty_answer_is_the_default_no(
    monkeypatch: pytest.MonkeyPatch, confirm: object
) -> None:
    _terminal(monkeypatch, answer="\n")

    with pytest.raises(CliError) as caught:
        confirm("ENG-1", yes=False, no_input=False)  # type: ignore[operator]

    assert caught.value.code == "confirmation_required"


@pytest.mark.parametrize("confirm", [issue_module._confirm_archive, issue_module._confirm_delete])
def test_end_of_input_is_a_no(monkeypatch: pytest.MonkeyPatch, confirm: object) -> None:
    _terminal(monkeypatch, answer="")

    with pytest.raises(CliError) as caught:
        confirm("ENG-1", yes=False, no_input=False)  # type: ignore[operator]

    assert caught.value.code == "confirmation_required"


@pytest.mark.parametrize("confirm", [issue_module._confirm_archive, issue_module._confirm_delete])
def test_a_session_without_a_terminal_never_prompts(
    monkeypatch: pytest.MonkeyPatch, confirm: object
) -> None:
    terminal = _terminal(monkeypatch, interactive=False, answer="y\n")

    with pytest.raises(CliError) as caught:
        confirm("ENG-1", yes=False, no_input=False)  # type: ignore[operator]

    assert terminal.written == []
    assert format_human(caught.value) == CATALOG_REFUSAL


@pytest.mark.parametrize("confirm", [issue_module._confirm_archive, issue_module._confirm_delete])
def test_no_input_never_prompts_even_with_a_terminal(
    monkeypatch: pytest.MonkeyPatch, confirm: object
) -> None:
    terminal = _terminal(monkeypatch, interactive=True, answer="y\n")

    with pytest.raises(CliError) as caught:
        confirm("ENG-1", yes=False, no_input=True)  # type: ignore[operator]

    assert terminal.written == []
    assert caught.value.code == "confirmation_required"


@pytest.mark.parametrize("confirm", [issue_module._confirm_archive, issue_module._confirm_delete])
def test_yes_skips_the_question(monkeypatch: pytest.MonkeyPatch, confirm: object) -> None:
    terminal = _terminal(monkeypatch, interactive=True, answer="n\n")

    confirm("ENG-1", yes=True, no_input=False)  # type: ignore[operator]

    assert terminal.written == []
