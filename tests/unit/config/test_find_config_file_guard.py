"""P-11: a stray `.hifa.toml` above the project root must not poison resolution.

The walk-up stops at the first `.git` (or the `.hifa.toml` it returns) and never climbs
above the project root.
"""

from __future__ import annotations

from pathlib import Path

from hifa.config import CONFIG_FILENAME, find_config_file


def _write_config(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CONFIG_FILENAME
    path.write_text('[workspace]\nname = "decoy"\n', encoding="utf-8")
    return path


def test_ancestor_config_is_ignored_below_a_git_root(tmp_path: Path) -> None:
    _write_config(tmp_path)  # decoy in an ancestor
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    nested = project / "pkg" / "mod"
    nested.mkdir(parents=True)

    assert find_config_file(nested) is None


def test_project_config_is_found_and_walk_stops_there(tmp_path: Path) -> None:
    _write_config(tmp_path)  # decoy above
    project = tmp_path / "project"
    (project / ".git").mkdir(parents=True)
    config = _write_config(project)
    nested = project / "pkg"
    nested.mkdir()

    assert find_config_file(nested) == config


def test_git_file_also_marks_a_project_root(tmp_path: Path) -> None:
    _write_config(tmp_path)
    project = tmp_path / "worktree"
    project.mkdir()
    (project / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")

    assert find_config_file(project) is None


def test_walk_without_git_keeps_finding_ancestor_config(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    assert find_config_file(nested) == config
