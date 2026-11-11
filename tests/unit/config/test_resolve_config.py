"""Configuration resolution tests (SPEC-100 §2.2-§2.4, R1-R8)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hifa.config import (
    DEFAULT_DB_FILENAME,
    ConfigError,
    find_config_file,
    load_config_file,
    resolve_config,
    resolve_db_path,
    slugify,
    validate_timezone,
    write_config_file,
)


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_flag_wins_over_env_and_file(tmp_path: Path) -> None:
    _write(tmp_path / ".hifa.toml", '[workspace]\ndb = ".hifa/hifa.db"\n')
    path, source, config_file = resolve_db_path(
        db_flag="/tmp/flag.db", env={"HIFA_DB": "/tmp/env.db"}, cwd=tmp_path
    )
    assert (path, source, config_file) == (Path("/tmp/flag.db"), "flag", None)


def test_flag_relative_to_cwd(tmp_path: Path) -> None:
    path, source, _ = resolve_db_path(db_flag="sub/x.db", env={}, cwd=tmp_path)
    assert path == (tmp_path / "sub/x.db").resolve()
    assert source == "flag"


def test_empty_flag_is_ignored(tmp_path: Path) -> None:
    path, source, _ = resolve_db_path(db_flag="   ", env={"HIFA_DB": "/tmp/env.db"}, cwd=tmp_path)
    assert (path, source) == (Path("/tmp/env.db"), "env")


def test_env_wins_over_config_file(tmp_path: Path) -> None:
    _write(tmp_path / ".hifa.toml", '[workspace]\ndb = ".hifa/hifa.db"\n')
    path, source, _ = resolve_db_path(env={"HIFA_DB": "rel.db"}, cwd=tmp_path)
    assert path == (tmp_path / "rel.db").resolve()
    assert source == "env"


def test_empty_env_falls_through_to_file(tmp_path: Path) -> None:
    _write(tmp_path / ".hifa.toml", '[workspace]\ndb = ".hifa/hifa.db"\n')
    path, source, config_file = resolve_db_path(env={"HIFA_DB": "  "}, cwd=tmp_path)
    assert source == "file"
    assert path == (tmp_path / ".hifa/hifa.db").resolve()
    assert config_file == tmp_path / ".hifa.toml"


def test_file_db_is_relative_to_the_config_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _write(repo / ".hifa.toml", '[workspace]\ndb = ".hifa/hifa.db"\n')
    deep = repo / "src" / "deep"
    deep.mkdir(parents=True)

    path, source, config_file = resolve_db_path(env={}, cwd=deep)

    assert path == (repo / ".hifa/hifa.db").resolve()
    assert source == "file"
    assert config_file == repo / ".hifa.toml"


def test_file_without_db_uses_project_local_default(tmp_path: Path) -> None:
    _write(tmp_path / ".hifa.toml", "[team]\nkey = 'ENG'\n")
    path, source, _ = resolve_db_path(env={}, cwd=tmp_path)
    assert path == (tmp_path / ".hifa" / DEFAULT_DB_FILENAME).resolve()
    assert source == "file"


def test_xdg_when_no_config_file(tmp_path: Path) -> None:
    path, source, _ = resolve_db_path(env={"XDG_DATA_HOME": str(tmp_path / "xdg")}, cwd=tmp_path)
    assert path == (tmp_path / "xdg/hifa/hifa.db").resolve()
    assert source == "xdg"


def test_relative_xdg_is_ignored_and_falls_back_to_home(tmp_path: Path) -> None:
    path, source, _ = resolve_db_path(
        env={"XDG_DATA_HOME": "relative/xdg", "HOME": str(tmp_path / "home")}, cwd=tmp_path
    )
    assert source == "home"
    assert path == (tmp_path / "home/.local/share/hifa/hifa.db").resolve()


def test_home_fallback_without_xdg(tmp_path: Path) -> None:
    path, source, _ = resolve_db_path(env={"HOME": str(tmp_path)}, cwd=tmp_path)
    assert path == (tmp_path / ".local/share/hifa/hifa.db").resolve()
    assert source == "home"


def test_memory_is_preserved_literally(tmp_path: Path) -> None:
    path, source, _ = resolve_db_path(db_flag=":memory:", env={}, cwd=tmp_path)
    assert path == Path(":memory:")
    assert source == "flag"


def test_db_pointing_to_directory_is_invalid(tmp_path: Path) -> None:
    target = tmp_path / "adir"
    target.mkdir()
    with pytest.raises(ConfigError) as info:
        resolve_db_path(db_flag=str(target), env={}, cwd=tmp_path)
    assert info.value.code == "config_invalid_value"
    assert info.value.field == "db"


def test_resolve_config_has_no_side_effects(tmp_path: Path) -> None:
    before = set(tmp_path.iterdir())
    # Explicit HOME inside tmp_path: if the real HOME is inherited, the test fails because of a
    # database created by an earlier `hifa init` (the default lives at ~/.local/share/hifa/hifa.db).
    cfg = resolve_config(env={"HOME": str(tmp_path / "home")}, cwd=tmp_path)
    assert set(tmp_path.iterdir()) == before
    assert not cfg.db_path.exists()
    assert cfg.source == "home"
    assert cfg.team.key == "ENG"
    assert cfg.team.timezone == "America/Santiago"
    assert cfg.cli.default_team == "ENG"
    assert cfg.web.port == 7777


def test_find_config_file_walks_up(tmp_path: Path) -> None:
    config = _write(tmp_path / ".hifa.toml", "")
    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    assert find_config_file(nested) == config


def test_find_config_file_returns_none_outside_tree(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    # No `.hifa.toml` up to `/` inside the tmp_path sandbox.
    assert find_config_file(empty) is None


def test_unknown_keys_become_warnings(tmp_path: Path) -> None:
    path = _write(tmp_path / ".hifa.toml", "[defaults]\nmember = 'x'\n\n[team]\nkey = 'ENG'\n")
    cfg = load_config_file(path)
    assert "unknown_key: defaults.member" in cfg.warnings


def test_port_as_string_is_invalid(tmp_path: Path) -> None:
    path = _write(tmp_path / ".hifa.toml", '[web]\nport = "7777"\n')
    with pytest.raises(ConfigError) as info:
        load_config_file(path)
    assert info.value.code == "config_invalid_value"
    assert info.value.field == "web.port"


def test_invalid_color_and_key_in_file(tmp_path: Path) -> None:
    bad_color = _write(tmp_path / "a" / ".hifa.toml", '[team]\ncolor = "red"\n')
    with pytest.raises(ConfigError) as info:
        load_config_file(bad_color)
    assert info.value.field == "team.color"

    bad_key = _write(tmp_path / "b" / ".hifa.toml", '[team]\nkey = "e1"\n')
    with pytest.raises(ConfigError) as info:
        load_config_file(bad_key)
    assert info.value.field == "team.key"


def test_broken_toml_reports_line(tmp_path: Path) -> None:
    path = _write(tmp_path / ".hifa.toml", "[web]\nport = = 7777\n")
    with pytest.raises(ConfigError) as info:
        load_config_file(path)
    assert info.value.code == "config_invalid_toml"
    assert "line" in info.value.message.lower()


def test_unreadable_config_file(tmp_path: Path) -> None:
    path = _write(tmp_path / ".hifa.toml", "")
    os.chmod(path, 0o000)
    try:
        with pytest.raises(ConfigError) as info:
            load_config_file(path)
        assert info.value.code == "config_unreadable"
    finally:
        os.chmod(path, 0o600)


def test_slugify_cases() -> None:
    assert slugify("Hifa Dev") == "hifa-dev"
    assert slugify("Ñandú") == "nandu"
    assert slugify("  --  ") == "workspace"
    assert len(slugify("a" * 90)) == 63


def test_validate_timezone_accepts_utc_and_rejects_unknown() -> None:
    assert validate_timezone("UTC") == "UTC"
    with pytest.raises(ConfigError) as info:
        validate_timezone("Mars/Olympus")
    assert info.value.code == "config_invalid_value"
    assert info.value.field == "team.timezone"


def test_write_config_file_is_relative_and_0600(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    db_path = project / ".hifa" / "hifa.db"
    cfg = resolve_config(db_flag=str(db_path), env={}, cwd=project)
    target = project / ".hifa.toml"

    write_config_file(target, cfg)

    assert target.stat().st_mode & 0o777 == 0o600
    text = target.read_text(encoding="utf-8")
    assert 'db = ".hifa/hifa.db"' in text
    assert "[team]" in text and "[cli]" in text and "[web]" in text
    reloaded = load_config_file(target)
    assert reloaded.db == ".hifa/hifa.db"
    assert reloaded.team.key == "ENG"
