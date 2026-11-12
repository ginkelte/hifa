"""Contract tests that keep `--help` honest.

A group `help` string is free text, and free text lies: `hifa issue` announced
`"Issues: create, list, move."` while only `create` was registered, so the help promised two verbs
that answered "No such command". The help of a command surface is part of the CLI contract
(SPEC-050 §2.5 verb contract, §4 R17), and a contract that is not executable is a preference —
these tests make it executable, without a database and without a socket.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from typer.core import TyperGroup
from typer.main import get_command
from typer.testing import CliRunner

from hifa.cli.main import app

#: A verb list that opens the text and ends with a full stop: `Issues: create, list, move.`
#: Anchoring both ends is what keeps `Manage teams (visibility: public, private, restricted).`
#: from being read as a promise of three verbs (it is a parenthesised enumeration of values).
VERB_LIST_RE = re.compile(r"^[A-Z][A-Za-z ]+:\s*([a-z][a-z0-9-]*(?:\s*,\s*[a-z][a-z0-9-]*)+)\.\s*$")

#: CSI sequences (colour, bold, reset). Rich emits them when the environment looks like a CI.
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

#: One line of the `Commands` panel: the name, padded, then the description (or nothing).
PANEL_ROW_RE = re.compile(r"^│\s+([a-z][a-z0-9-]*)\s{2,}")

#: Enough columns that every row of the panel keeps its two-space gutter (Rich wraps below it).
HELP_COLUMNS = "200"


def walk(group: TyperGroup, prefix: str = "") -> Iterator[tuple[str, TyperGroup]]:
    """Yield `(path, group)` for every sub-application of `group`, recursively (root excluded)."""
    for name, command in group.commands.items():
        if isinstance(command, TyperGroup):
            path = f"{prefix} {name}".strip()
            yield path, command
            yield from walk(command, prefix=path)


def root_group() -> TyperGroup:
    """The tree Typer actually renders for `app` (the root group included)."""
    root = get_command(app)
    assert isinstance(root, TyperGroup), "the root app no longer renders as a TyperGroup"
    return root


def groups() -> Iterator[tuple[str, TyperGroup]]:
    """Yield `(path, group)` for the root of the rendered tree and every group under it."""
    yield "", root_group()
    yield from walk(root_group())


def verb_violations() -> list[str]:
    """Every `help` that enumerates a verb which is not a registered subcommand."""
    violations: list[str] = []
    for path, group in groups():
        help_text = (group.help or "").strip()
        registered = set(group.commands)
        match = VERB_LIST_RE.search(help_text)
        if match:
            promised = {verb.strip() for verb in match.group(1).split(",")}
            missing = promised - registered
            if missing:
                violations.append(
                    f"{path or 'hifa'}: help promises {sorted(missing)}, "
                    f"registered subcommands are {sorted(registered)}"
                )
    return violations


def commands_panel(output: str) -> set[str]:
    """Names rendered in the `Commands` panel of a Typer help screen.

    The output is ANSI-stripped first: CI runners make Rich emit colour, and a line that starts
    with `\\x1b[36m│` is not a line that starts with `│` (that is exactly how this test failed on
    GitHub Actions while passing locally).
    """
    lines = ANSI_RE.sub("", output).splitlines()
    start = next((index for index, line in enumerate(lines) if "─ Commands ─" in line), None)
    if start is None:
        pytest.fail("no Commands panel in the help output")
    names: set[str] = set()
    for line in lines[start + 1 :]:
        if line.startswith("╰"):
            break
        match = PANEL_ROW_RE.match(line)
        if match:
            names.add(match.group(1))
    return names


def test_verb_list_regex_only_matches_a_leading_verb_enumeration() -> None:
    """The pattern is anchored: a parenthesised value list is not a promise of verbs."""
    assert VERB_LIST_RE.search("Manage teams (visibility: public, private, restricted).") is None
    assert VERB_LIST_RE.search("Issues: create, list, move.") is not None


def test_group_help_never_promises_an_unregistered_verb() -> None:
    """No group `help` — the root included — may list a verb that is not a subcommand."""
    violations = verb_violations()
    assert not violations, "\n".join(violations)


def test_root_help_that_promises_a_missing_verb_is_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walker reaches the root: a lying `hifa --help` is caught like any other group."""
    monkeypatch.setattr(app.info, "help", "Issues: create, list, move.")
    violations = verb_violations()
    assert violations, "a root help that promises 'list' and 'move' went unnoticed"
    assert violations[0].startswith("hifa: help promises")


def test_issue_help_lists_exactly_the_registered_subcommands() -> None:
    """`hifa issue --help` renders the panel from the registry: every verb, and nothing else."""
    tree = dict(groups())
    assert "issue" in tree, "the issue group is no longer registered on the root app"
    group = tree["issue"]
    registered = set(group.commands)

    result = CliRunner().invoke(app, ["issue", "--help"], env={"COLUMNS": HELP_COLUMNS})

    assert result.exit_code == 0, result.output
    assert commands_panel(result.output) == registered, result.output
    # Nothing else is promised: the Commands panel is the list, the help text does not enumerate.
    assert not VERB_LIST_RE.search((group.help or "").strip()), (
        f"the issue group help enumerates verbs ({group.help!r}); "
        "name the area and let the Commands panel list the subcommands"
    )


def test_walk_covers_nested_groups() -> None:
    """The walker yields every group of the rendered tree, not only the first level."""
    paths = [path for path, _ in walk(root_group())]
    assert "issue" in paths
    assert "team" in paths
