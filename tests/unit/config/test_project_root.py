"""`find_project_root` — the project-root notion behind R3 and the write guard of R13 (D-041).

The helper answers one question: is this directory inside a git work tree, and if so, which
directory is the root? `.git` is a directory in a clone and a file in a linked work tree or a
submodule, so both shapes must be recognised.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hifa.config import find_project_root


def _repo(directory: Path, *, git_file: bool = False) -> Path:
    """Create a fake work tree at `directory`: `.git` as a directory or as a `gitdir:` file."""
    directory.mkdir(parents=True, exist_ok=True)
    if git_file:
        (directory / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n", encoding="utf-8")
    else:
        (directory / ".git").mkdir()
    return directory


def _plain(tmp_path: Path) -> Path:
    """A directory with no `.git` in itself or in any ancestor (skips when TMPDIR has one)."""
    if find_project_root(tmp_path) is not None:
        pytest.skip("the temporary root of this machine lives inside a git work tree")
    plain = tmp_path / "plain"
    plain.mkdir()
    return plain


def test_the_repository_root_itself_is_a_project_root(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")

    assert find_project_root(repo) == repo


def test_a_subdirectory_reports_the_repository_root(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "repo")
    nested = repo / "src" / "hifa" / "cli"
    nested.mkdir(parents=True)

    assert find_project_root(nested) == repo


def test_a_git_file_marks_a_work_tree_too(tmp_path: Path) -> None:
    worktree = _repo(tmp_path / "linked", git_file=True)
    nested = worktree / "pkg"
    nested.mkdir()

    assert find_project_root(nested) == worktree


def test_outside_every_work_tree_there_is_no_project_root(tmp_path: Path) -> None:
    plain = _plain(tmp_path)

    assert find_project_root(plain) is None


def test_the_nearest_work_tree_wins_over_an_outer_one(tmp_path: Path) -> None:
    outer = _repo(tmp_path / "outer")
    inner = _repo(outer / "vendor" / "inner")
    nested = inner / "pkg"
    nested.mkdir()

    assert find_project_root(nested) == inner


def test_the_walk_is_bounded_and_does_not_escape_the_temporary_tree(tmp_path: Path) -> None:
    """A deep nesting without `.git` is `None`, not an ancestor outside the sandbox."""
    plain = _plain(tmp_path)
    deep = plain.joinpath(*["level"] * 10)
    deep.mkdir(parents=True)

    assert find_project_root(deep) is None
