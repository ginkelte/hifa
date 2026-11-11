"""Resolution of the process configuration (SPEC-100 §2.1-§2.4).

Public contract (SPEC-100 §2.1): the `DEFAULT_*` constants, the `TeamConfig`,
`CliConfig`, `WebConfig`, `Config`, `ConfigFile`, `ConfigError` dataclasses, and the
`find_config_file`, `load_config_file`, `resolve_config`, `resolve_db_path`,
`write_config_file`, `slugify`, `validate_timezone` functions.

Rules:
- `--db` (flag) > `HIFA_DB` (env) > `.hifa.toml` (walk-up) > `$XDG_DATA_HOME` > `~`.
- Everything is pure except `write_config_file`: no directory is created and the database is
  never opened (R7). Creation is done by `hifa init`.
- Stdlib only: `tomllib` (REQ-N100-04). This module does **not** import `sqlite3`, `typer` or
  `fastapi`.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import available_timezones

DEFAULT_DB_FILENAME: str = "hifa.db"
CONFIG_FILENAME: str = ".hifa.toml"
APP_DIRNAME: str = "hifa"
MAX_WALK_UP: int = 32
DEFAULT_TEAM_KEY: str = "ENG"
DEFAULT_TEAM_NAME: str = "Engineering"
DEFAULT_ICON: str = "🚀"
DEFAULT_COLOR: str = "#5E6AD2"
DEFAULT_TIMEZONE: str = "America/Santiago"
DEFAULT_HOST: str = "127.0.0.1"
DEFAULT_PORT: int = 7777

Source = Literal["flag", "env", "file", "xdg", "home"]

KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
URL_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

#: `ConfigError` codes fixed by SPEC-100 §6.
CONFIG_ERROR_CODES: frozenset[str] = frozenset(
    {
        "config_invalid_toml",
        "config_invalid_value",
        "config_unreadable",
        "db_dir_not_writable",
        "in_memory_not_supported",
    }
)

_KNOWN_KEYS: dict[str, frozenset[str]] = {
    "workspace": frozenset({"name", "db", "url_key"}),
    "team": frozenset({"key", "name", "icon", "color", "timezone"}),
    "cli": frozenset({"default_team"}),
    "web": frozenset({"host", "port"}),
}


class ConfigError(Exception):
    """Configuration error with a stable `code` and a populated `field` (SPEC-100 §6).

    `code` ∈ `CONFIG_ERROR_CODES`. `field` is `"<section>.<key>"` or the flag name
    (`"db"`, `"team.key"`, `"web.port"`, …). The CLI maps it to exit 2.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "config_invalid_value",
        field: str | None = None,
        hint: str | None = None,
    ) -> None:
        if code not in CONFIG_ERROR_CODES:
            raise ValueError(f"unknown configuration code: {code}")
        self.code = code
        self.message = message
        self.field = field
        self.hint = hint
        super().__init__(message)

    def __str__(self) -> str:
        return self.message

    def __repr__(self) -> str:
        return f"ConfigError(code={self.code!r}, field={self.field!r}, message={self.message!r})"


@dataclass(frozen=True)
class TeamConfig:
    """`[team]` section of `.hifa.toml` (all fields optional, §2.3)."""

    key: str | None = None
    name: str | None = None
    icon: str | None = None
    color: str | None = None
    timezone: str | None = None


@dataclass(frozen=True)
class CliConfig:
    """`[cli]` section of `.hifa.toml`."""

    default_team: str | None = None


@dataclass(frozen=True)
class WebConfig:
    """`[web]` section of `.hifa.toml`."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT


@dataclass(frozen=True)
class Config:
    """Effective configuration: database path + `[team]`/`[cli]`/`[web]` values."""

    db_path: Path
    source: Source
    config_file: Path | None
    workspace_name: str | None
    url_key: str | None
    team: TeamConfig = TeamConfig()
    cli: CliConfig = CliConfig()
    web: WebConfig = WebConfig()
    warnings: tuple[str, ...] = ()

    @property
    def default_team(self) -> str | None:
        """Default team (`[cli].default_team`, SPEC-050 §2.2.2)."""
        return self.cli.default_team


@dataclass(frozen=True)
class ConfigFile:
    """Already validated contents of a `.hifa.toml`."""

    path: Path
    workspace_name: str | None
    db: str | None
    url_key: str | None
    team: TeamConfig = TeamConfig()
    cli: CliConfig = CliConfig()
    web: WebConfig = WebConfig()
    warnings: tuple[str, ...] = ()


# --- pure utilities -------------------------------------------------------------------


def slugify(text: str) -> str:
    """`"Hifa Dev"` -> `"hifa-dev"`; NFKD folding drops accents and non-ASCII letters (R8)."""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
    replaced = re.sub(r"[^a-z0-9]", "-", ascii_text.lower())
    collapsed = re.sub(r"-{2,}", "-", replaced).strip("-")
    return collapsed[:63] or "workspace"


@lru_cache(maxsize=1)
def _timezones() -> frozenset[str]:
    return frozenset(available_timezones())


def validate_timezone(name: str) -> str:
    """Return `name` if it is in `zoneinfo.available_timezones()`; otherwise `ConfigError`."""
    text = name.strip() if isinstance(name, str) else ""
    if not text or text not in _timezones():
        raise ConfigError(
            f"unknown timezone: {name!r}",
            code="config_invalid_value",
            field="team.timezone",
            hint="use an IANA name, for example America/Santiago or UTC",
        )
    return text


def _invalid(field: str, value: object, expected: str) -> ConfigError:
    return ConfigError(
        f"{field}: invalid value {value!r} (expected: {expected})",
        code="config_invalid_value",
        field=field,
    )


def _expect_str(
    section: str,
    key: str,
    value: object,
    *,
    min_len: int = 1,
    max_len: int | None = None,
) -> str:
    field = f"{section}.{key}"
    if not isinstance(value, str):
        raise _invalid(field, value, "string")
    text = value.strip()
    if len(text) < min_len:
        raise _invalid(field, value, f"at least {min_len} characters")
    if max_len is not None and len(text) > max_len:
        raise _invalid(field, value, f"at most {max_len} characters")
    if "\n" in text:
        raise _invalid(field, value, "a single line")
    return text


def _expect_no_spaces(section: str, key: str, value: object) -> str:
    text = _expect_str(section, key, value, min_len=1, max_len=8)
    if any(ch.isspace() for ch in text):
        raise _invalid(f"{section}.{key}", value, "no whitespace")
    return text


# --- file lookup (R3) ----------------------------------------------------------


def _stat_file(path: Path) -> bool:
    """True if `path` is a regular file; `PermissionError` ⇒ `config_unreadable`."""
    try:
        return path.is_file()
    except PermissionError as exc:  # pragma: no cover - is_file() swallows OSError on 3.12
        raise ConfigError(
            f"cannot read {path}: {exc.strerror or 'permission denied'}",
            code="config_unreadable",
            field=str(path),
        ) from exc


def find_config_file(start: Path) -> Path | None:
    """Walk up from `start` looking for `.hifa.toml`, never past the project root (P-11).

    Rule: the walk-up stops at the first directory that is a project root — one that
    contains `.git` or the `.hifa.toml` being returned — and never climbs above it. A
    stray config in an ancestor (e.g. `/tmp/.hifa.toml`) therefore cannot poison the
    resolution of every project below it. Without `.git` in the path the historical
    cap of 32 levels and the filesystem root still apply.
    """
    current = Path(start).resolve()
    for _ in range(MAX_WALK_UP):
        candidate = current / CONFIG_FILENAME
        if _stat_file(candidate):
            try:
                with candidate.open("rb"):
                    pass
            except PermissionError as exc:
                raise ConfigError(
                    f"cannot read {candidate}: {exc.strerror or 'permission denied'}",
                    code="config_unreadable",
                    field=str(candidate),
                ) from exc
            return candidate
        if (current / ".git").exists():
            break
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


# --- file parsing (R6) ------------------------------------------------------------


def _unknown_keys(data: Mapping[str, object]) -> list[str]:
    warnings: list[str] = []
    for section, value in data.items():
        known = _KNOWN_KEYS.get(section)
        if known is None:
            if isinstance(value, dict):
                warnings.extend(f"unknown_key: {section}.{key}" for key in sorted(value))
            else:
                warnings.append(f"unknown_key: {section}")
            continue
        if not isinstance(value, dict):
            warnings.append(f"unknown_key: {section}")
            continue
        warnings.extend(
            f"unknown_key: {section}.{key}" for key in sorted(value) if key not in known
        )
    return warnings


def load_config_file(path: Path) -> ConfigFile:
    """Read and validate a `.hifa.toml`; section defaults come from `resolve_config`."""
    source_path = Path(path)
    try:
        raw = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(
            f"cannot read {source_path}: {exc}",
            code="config_unreadable",
            field=str(source_path),
        ) from exc

    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"{source_path.name} is invalid: {exc}",
            code="config_invalid_toml",
            field=str(source_path),
            hint="check the line pointed to by tomllib",
        ) from exc

    if not isinstance(data, dict):  # pragma: no cover - tomllib always returns a dict
        raise ConfigError(
            f"{source_path.name} is invalid: expected a TOML table",
            code="config_invalid_toml",
            field=str(source_path),
        )

    warnings = _unknown_keys(data)

    workspace = data.get("workspace", {}) if isinstance(data.get("workspace", {}), dict) else {}
    team = data.get("team", {}) if isinstance(data.get("team", {}), dict) else {}
    cli = data.get("cli", {}) if isinstance(data.get("cli", {}), dict) else {}
    web = data.get("web", {}) if isinstance(data.get("web", {}), dict) else {}

    workspace_name: str | None = None
    if "name" in workspace:
        workspace_name = _expect_str("workspace", "name", workspace["name"], max_len=80)

    db: str | None = None
    if "db" in workspace:
        db = _expect_str("workspace", "db", workspace["db"])
        if Path(db) == Path(":memory:"):
            pass
        elif Path(db).name in {"", ".", ".."}:  # pragma: no cover - defensive
            raise _invalid("workspace.db", db, "a file path")

    url_key: str | None = None
    if "url_key" in workspace:
        url_key = _expect_str("workspace", "url_key", workspace["url_key"])
        if URL_KEY_PATTERN.match(url_key) is None:
            raise _invalid("workspace.url_key", url_key, URL_KEY_PATTERN.pattern)

    team_key: str | None = None
    if "key" in team:
        team_key = _expect_str("team", "key", team["key"])
        if KEY_PATTERN.match(team_key) is None:
            raise _invalid("team.key", team_key, KEY_PATTERN.pattern)
    team_name: str | None = None
    if "name" in team:
        team_name = _expect_str("team", "name", team["name"], max_len=80)
    icon: str | None = None
    if "icon" in team:
        icon = _expect_no_spaces("team", "icon", team["icon"])
    color: str | None = None
    if "color" in team:
        color = _expect_str("team", "color", team["color"])
        if COLOR_PATTERN.match(color) is None:
            raise _invalid("team.color", color, COLOR_PATTERN.pattern)
    timezone: str | None = None
    if "timezone" in team:
        timezone = validate_timezone(_expect_str("team", "timezone", team["timezone"]))

    default_team: str | None = None
    if "default_team" in cli:
        default_team = _expect_str("cli", "default_team", cli["default_team"])
        if KEY_PATTERN.match(default_team) is None:
            raise _invalid("cli.default_team", default_team, KEY_PATTERN.pattern)

    host = DEFAULT_HOST
    if "host" in web:
        host = _expect_str("web", "host", web["host"], max_len=255)
        if any(ch.isspace() for ch in host):
            raise _invalid("web.host", host, "a host without whitespace")
    port = DEFAULT_PORT
    if "port" in web:
        value = web["port"]
        if isinstance(value, bool) or not isinstance(value, int):
            raise _invalid("web.port", value, "an integer between 1 and 65535")
        if not 1 <= value <= 65535:
            raise _invalid("web.port", value, "an integer between 1 and 65535")
        port = value

    return ConfigFile(
        path=source_path,
        workspace_name=workspace_name,
        db=db,
        url_key=url_key,
        team=TeamConfig(key=team_key, name=team_name, icon=icon, color=color, timezone=timezone),
        cli=CliConfig(default_team=default_team),
        web=WebConfig(host=host, port=port),
        warnings=tuple(warnings),
    )


# --- path resolution (R1/R2/R4/R5) ----------------------------------------------


def _has_value(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def _normalize_db_value(value: str, cwd: Path, *, field: str) -> Path:
    """`strip` → `:memory:` literal → `expanduser` → relative to cwd → `resolve()`."""
    text = value.strip()
    if text == ":memory:":
        return Path(":memory:")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = cwd / path
    resolved = path.resolve()
    if resolved.is_dir():
        raise ConfigError(
            f"{field}: the path points to a directory: {resolved}",
            code="config_invalid_value",
            field=field,
        )
    return resolved


def _resolve_file_db(raw: str, base_dir: Path) -> Path:
    """`[workspace] db` is relative to the directory of `.hifa.toml` (R4)."""
    text = raw.strip()
    if text == ":memory:":
        return Path(":memory:")
    path = Path(text).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    resolved = path.resolve()
    if resolved.is_dir():
        raise ConfigError(
            f"workspace.db: the path points to a directory: {resolved}",
            code="config_invalid_value",
            field="workspace.db",
        )
    return resolved


def _home(env: Mapping[str, str]) -> Path:
    configured = env.get("HOME")
    return Path(configured).expanduser() if configured else Path.home()


def resolve_db_path(
    *,
    db_flag: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> tuple[Path, Source, Path | None]:
    """Apply the 5 sources in order and return `(path, source, config_file)` (R1)."""
    environment = dict(os.environ) if env is None else dict(env)
    base = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()

    if _has_value(db_flag):
        return _normalize_db_value(db_flag or "", base, field="db"), "flag", None

    if _has_value(environment.get("HIFA_DB")):
        return _normalize_db_value(environment["HIFA_DB"], base, field="db"), "env", None

    config_file = find_config_file(base)
    if config_file is not None:
        cfg = load_config_file(config_file)
        raw = cfg.db or f".hifa/{DEFAULT_DB_FILENAME}"
        return _resolve_file_db(raw, config_file.parent), "file", config_file

    xdg = (environment.get("XDG_DATA_HOME") or "").strip()
    if xdg:
        xdg_path = Path(xdg).expanduser()
        if xdg_path.is_absolute():
            db_path = (xdg_path / APP_DIRNAME / DEFAULT_DB_FILENAME).resolve()
            return db_path, "xdg", None
        # XDG requires an absolute path: it is ignored and resolution falls back to home (R5).

    home_db = _home(environment) / ".local" / "share" / APP_DIRNAME / DEFAULT_DB_FILENAME
    return home_db.resolve(), "home", None


def resolve_config(
    *,
    db_flag: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
) -> Config:
    """Resolve path + `[team]`/`[cli]`/`[web]` without touching the database or the
    filesystem (R7).
    """
    environment = dict(os.environ) if env is None else dict(env)
    base = Path(cwd).resolve() if cwd is not None else Path.cwd().resolve()
    warnings: list[str] = []

    db_path, source, config_file = resolve_db_path(db_flag=db_flag, env=environment, cwd=base)

    file_cfg: ConfigFile | None = None
    if config_file is not None:
        file_cfg = load_config_file(config_file)
        warnings.extend(file_cfg.warnings)
    elif source in {"flag", "env"}:
        # R1: `--db`/`HIFA_DB` only win the PATH; team/cli/web still come from the TOML.
        found = find_config_file(base)
        if found is not None:
            file_cfg = load_config_file(found)
            config_file = found
            warnings.extend(file_cfg.warnings)
    else:
        xdg = (environment.get("XDG_DATA_HOME") or "").strip()
        if xdg and not Path(xdg).expanduser().is_absolute():
            warnings.append(f"xdg_invalid: relative XDG_DATA_HOME ignored ({xdg!r})")

    workspace_name = (
        file_cfg.workspace_name
        if file_cfg is not None and file_cfg.workspace_name
        else _default_workspace_name(base)
    )
    url_key = (
        file_cfg.url_key if file_cfg is not None and file_cfg.url_key else slugify(workspace_name)
    )

    file_team = file_cfg.team if file_cfg is not None else TeamConfig()
    team = TeamConfig(
        key=file_team.key or DEFAULT_TEAM_KEY,
        name=file_team.name or DEFAULT_TEAM_NAME,
        icon=file_team.icon or DEFAULT_ICON,
        color=file_team.color or DEFAULT_COLOR,
        timezone=file_team.timezone or DEFAULT_TIMEZONE,
    )
    cli = CliConfig(
        default_team=(
            file_cfg.cli.default_team
            if file_cfg is not None and file_cfg.cli.default_team
            else team.key
        )
    )
    web = file_cfg.web if file_cfg is not None else WebConfig()

    return Config(
        db_path=db_path,
        source=source,
        config_file=config_file,
        workspace_name=workspace_name,
        url_key=url_key,
        team=team,
        cli=cli,
        web=web,
        warnings=tuple(warnings),
    )


def _default_workspace_name(base: Path) -> str:
    """`basename(cwd)` with the first letter uppercased; `"hifa"` at the root."""
    name = base.name or "hifa"
    return f"{name[:1].upper()}{name[1:]}"


# --- file writing (R13/R16) ----------------------------------------------------


def _toml_string(value: str) -> str:
    """Valid TOML quotes (compatible with basic JSON strings)."""
    return json.dumps(value, ensure_ascii=False)


def _db_value_for_config(db_path: Path, base_dir: Path) -> str:
    """Path relative to the directory of `.hifa.toml` when it is inside the tree."""
    if str(db_path) == ":memory:":
        return ":memory:"
    try:
        return db_path.resolve().relative_to(base_dir.resolve()).as_posix()
    except ValueError:
        return str(db_path)


def write_config_file(path: Path, config: Config) -> None:
    """Write the `.hifa.toml` of §2.3 with the effective values and mode `0600` (R13/R16)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    team = config.team
    name = config.workspace_name or _default_workspace_name(target.parent)
    url_key = config.url_key or slugify(name)
    db_value = _db_value_for_config(config.db_path, target.parent)

    lines = [
        "[workspace]",
        f"name = {_toml_string(name)}",
        f"db = {_toml_string(db_value)}",
        f"url_key = {_toml_string(url_key)}",
        "",
        "[team]",
        f"key = {_toml_string(team.key or DEFAULT_TEAM_KEY)}",
        f"name = {_toml_string(team.name or DEFAULT_TEAM_NAME)}",
        f"icon = {_toml_string(team.icon or DEFAULT_ICON)}",
        f"color = {_toml_string(team.color or DEFAULT_COLOR)}",
        f"timezone = {_toml_string(team.timezone or DEFAULT_TIMEZONE)}",
        "",
        "[cli]",
        f"default_team = {_toml_string(config.cli.default_team or team.key or DEFAULT_TEAM_KEY)}",
        "",
        "[web]",
        f"host = {_toml_string(config.web.host)}",
        f"port = {config.web.port}",
        "",
    ]
    text = "\n".join(lines)

    temp = target.with_name(f"{target.name}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.chmod(temp, 0o600)
    os.replace(temp, target)


__all__ = [
    "APP_DIRNAME",
    "CONFIG_FILENAME",
    "CONFIG_ERROR_CODES",
    "DEFAULT_COLOR",
    "DEFAULT_DB_FILENAME",
    "DEFAULT_HOST",
    "DEFAULT_ICON",
    "DEFAULT_PORT",
    "DEFAULT_TEAM_KEY",
    "DEFAULT_TEAM_NAME",
    "DEFAULT_TIMEZONE",
    "MAX_WALK_UP",
    "CliConfig",
    "Config",
    "ConfigError",
    "ConfigFile",
    "TeamConfig",
    "WebConfig",
    "find_config_file",
    "load_config_file",
    "resolve_config",
    "resolve_db_path",
    "slugify",
    "validate_timezone",
    "write_config_file",
]
