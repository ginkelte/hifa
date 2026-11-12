"""`hifa init` — workspace seeding (SPEC-050 §2.5.1, SPEC-100 §4.2 R9-R17, D-013).

The command is edge orchestration only: it resolves the config (`hifa.config`), validates
the flags by building `BootstrapOptions`, prepares the file, calls `bootstrap` and formats
(`hifa.cli.output`). No business rule lives here.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import typer

from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import MAX_VERSION, apply_migrations
from hifa.adapters.sqlite.uow import UnitOfWork
from hifa.app.teams import (
    DEFAULT_LABELS,
    DEFAULT_STATES,
    BootstrapOptions,
    BootstrapResult,
    bootstrap,
)
from hifa.cli import output
from hifa.cli.context import CliContext
from hifa.cli.errors import CliError, fail
from hifa.config import (
    CONFIG_FILENAME,
    DEFAULT_COLOR,
    DEFAULT_ICON,
    DEFAULT_TEAM_KEY,
    DEFAULT_TEAM_NAME,
    DEFAULT_TIMEZONE,
    Config,
    ConfigError,
    TeamConfig,
    find_config_file,
    find_project_root,
    resolve_config,
    slugify,
    write_config_file,
)
from hifa.domain.errors import HifaError, SchemaTooNew, Validation


def init_command(
    ctx: typer.Context,
    name: str | None = typer.Option(
        None, "--name", help="Workspace name (default: basename of the cwd)."
    ),
    team_key: str = typer.Option(DEFAULT_TEAM_KEY, "--team-key", help="Team key (2-10)."),
    team_name: str = typer.Option(DEFAULT_TEAM_NAME, "--team-name", help="Team name."),
    icon: str = typer.Option(DEFAULT_ICON, "--icon", help="Team emoji."),
    color: str = typer.Option(DEFAULT_COLOR, "--color", help="Team color (#RRGGBB)."),
    timezone: str = typer.Option(DEFAULT_TIMEZONE, "--timezone", help="IANA timezone."),
    no_labels: bool = typer.Option(False, "--no-labels", help="Do not seed the 4 labels."),
    no_member: bool = typer.Option(False, "--no-member", help="Do not seed the local member."),
    force: bool = typer.Option(
        False, "--force", help="Re-seed whatever is missing and rewrite the config."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over HIFA_DB)."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="IDs only."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Initialize a workspace: workspace, team, 6 states, labels and the local member."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(
        json_mode=json_mode,
        quiet=quiet_mode,
        color=not no_color,
    )

    if json_mode and quiet_mode:
        raise fail(
            CliError(
                "--json and --quiet are mutually exclusive",
                code="invalid_option",
                field="output",
            )
        )

    try:
        result, meta = _run_init(
            db_flag=db if db is not None else root.db,
            name=name,
            team_key=team_key,
            team_name=team_name,
            icon=icon,
            color=color,
            timezone=timezone,
            no_labels=no_labels,
            no_member=no_member,
            force=force,
        )
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactivo
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, nunca 1
        raise fail(exc) from exc

    output.emit(result, meta=meta)


# --- orchestration ----------------------------------------------------------------------


def _run_init(
    *,
    db_flag: str | None,
    name: str | None,
    team_key: str,
    team_name: str,
    icon: str,
    color: str,
    timezone: str,
    no_labels: bool,
    no_member: bool,
    force: bool,
) -> tuple[BootstrapResult, dict[str, Any]]:
    cfg = resolve_config(db_flag=db_flag)

    options = BootstrapOptions(
        team_key=team_key or cfg.team.key or DEFAULT_TEAM_KEY,
        team_name=team_name or cfg.team.name or DEFAULT_TEAM_NAME,
        icon=icon or cfg.team.icon or DEFAULT_ICON,
        color=color or cfg.team.color or DEFAULT_COLOR,
        timezone=timezone or cfg.team.timezone or DEFAULT_TIMEZONE,
        workspace_name=name or cfg.workspace_name,
        seed_labels=not no_labels,
        seed_member=not no_member,
        force=force,
    )

    db_path = cfg.db_path
    if str(db_path) == ":memory:":
        raise ConfigError(
            "hifa init does not support an ephemeral database (:memory:)",
            code="in_memory_not_supported",
            field="db",
        )

    created_dir = _ensure_db_dir(db_path.parent)
    is_new = _inspect_existing(db_path)
    work_path = _work_path(db_path, is_new)

    _migrate(work_path, backup=not is_new)

    uow = UnitOfWork(work_path, clock=SystemClock(), idgen=UlidGen())
    try:
        result = bootstrap(uow, options, clock=uow.clock, ids=uow.idgen)
        with uow.read() as repos:
            member = repos.members.get(result.member_id) if result.member_id else None
    finally:
        uow.close()

    if is_new:
        os.replace(work_path, db_path)
        _remove_sidecars(work_path)
        result = replace(result, db_path=db_path)

    _harden_file(db_path)
    if created_dir:
        with contextlib.suppress(OSError):  # read-only directory
            os.chmod(db_path.parent, 0o700)

    config_path, config_status = _write_config(cfg, options, db_path, force=force)

    meta: dict[str, Any] = {
        "workspace_name": options.workspace_name or "",
        "url_key": slugify(options.workspace_name or ""),
        "team_key": options.team_key,
        "team_name": options.team_name,
        "icon": options.icon,
        "states": [state[0] for state in DEFAULT_STATES],
        "labels": [label[0] for label in DEFAULT_LABELS] if options.seed_labels else [],
        "member": _member_label(member),
        "config": _config_report(config_path, config_status),
        "already_initialized": result.already_initialized,
    }
    return result, meta


def _ensure_db_dir(directory: Path) -> bool:
    """Create the database directory with `0700`; return whether it was created (R16/R17)."""
    if directory.exists():
        if not directory.is_dir():
            raise ConfigError(
                f"the database path is not a valid directory: {directory}",
                code="db_dir_not_writable",
                field="db",
            )
        if not os.access(directory, os.W_OK | os.X_OK):
            raise ConfigError(
                f"the database directory is not writable: {directory} (EACCES)",
                code="db_dir_not_writable",
                field="db",
            )
        return False
    try:
        directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"cannot create the database directory: {directory} ({exc.strerror or exc})",
            code="db_dir_not_writable",
            field="db",
        ) from exc
    return True


def _inspect_existing(db_path: Path) -> bool:
    """`True` when the database is new; validates an existing one (R14) before writing."""
    if not db_path.exists():
        return True
    if db_path.is_dir():
        raise ConfigError(
            f"the database path is a directory: {db_path}",
            code="config_invalid_value",
            field="db",
        )
    if db_path.stat().st_size == 0:
        return True

    conn = open_db(db_path, readonly=True)
    try:
        has_workspace = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'workspace'"
            ).fetchone()
            is not None
        )
        (version,) = conn.execute("PRAGMA user_version").fetchone()
    except sqlite3.DatabaseError as exc:
        raise Validation(
            f"cannot read the database {db_path}: {exc}",
            rule="DATABASE_UNREADABLE",
            code="corrupt_database",
            field="db",
        ) from exc
    finally:
        conn.close()

    if not has_workspace:
        raise Validation(
            f"the file is not a hifa database (the workspace table is missing): {db_path}",
            rule="NOT_A_HIFA_DATABASE",
            code="not_a_hifa_database",
            field="db",
        )
    if int(version) > MAX_VERSION:
        raise SchemaTooNew(
            f"the database is at version {version} while this binary reaches {MAX_VERSION}",
            hint="upgrade hifa",
            details={"user_version": int(version), "max_version": MAX_VERSION},
        )
    return False


def _work_path(db_path: Path, is_new: bool) -> Path:
    """New database: written to `.hifa.db.tmp` and renamed at the end (R17)."""
    if not is_new:
        return db_path
    temp = db_path.parent / ".hifa.db.tmp"
    for suffix in ("", "-wal", "-shm"):
        stale = temp.with_name(temp.name + suffix)
        if stale.exists():
            stale.unlink()
    return temp


def _migrate(path: Path, *, backup: bool) -> None:
    conn = open_db(path)
    try:
        apply_migrations(conn, backup=backup)
    finally:
        conn.close()


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        if sidecar.exists():  # pragma: no cover - SQLite removes them on a clean close
            sidecar.unlink()


def _harden_file(db_path: Path) -> None:
    try:
        os.chmod(db_path, 0o600)
    except OSError as exc:  # pragma: no cover - filesystem without chmod
        raise ConfigError(
            f"cannot adjust the permissions of {db_path}: {exc.strerror or exc}",
            code="db_dir_not_writable",
            field="db",
        ) from exc


def _member_label(member: Any) -> str | None:
    if member is None:
        return None
    if getattr(member, "email", None):
        return f"{member.display_name} <{member.email}>"
    return str(member.display_name)


def _config_report(path: Path | None, status: str) -> str:
    """`<path> (created|existing|rewritten)` or `none (outside a git work tree)` (R13, §2.7)."""
    if path is None:
        return f"none ({status})"
    return f"{path} ({status})"


def _write_config(
    cfg: Config,
    options: BootstrapOptions,
    db_path: Path,
    *,
    force: bool,
) -> tuple[Path | None, str]:
    """`created` | `existing` | `rewritten` inside a work tree, `outside a git work tree` outside.

    The guard of R13 (D-041) is about **writing** the file and never about resolving the database:
    when the cwd has no `.git` in itself or in an ancestor, nothing is written, no error is raised
    and the base resolved by R1 stands. `--force` does not lift it — there is no file to rewrite.
    """
    cwd = Path.cwd()
    if find_project_root(cwd) is None:
        return None, "outside a git work tree"

    existing = find_config_file(cwd)
    target = existing if existing is not None else cwd / CONFIG_FILENAME

    if existing is None:
        write_config_file(target, _effective_config(cfg, options, db_path, target))
        return target, "created"

    if not force:
        return target, "existing"

    backup = target.with_name(f"{target.name}.bak")
    try:
        shutil.copy2(target, backup)
    except OSError as exc:
        raise ConfigError(
            f"cannot back up {target}: {exc.strerror or exc}",
            code="config_unreadable",
            field=str(target),
        ) from exc
    write_config_file(target, _effective_config(cfg, options, db_path, target))
    return target, "rewritten"


def _effective_config(
    cfg: Config, options: BootstrapOptions, db_path: Path, target: Path
) -> Config:
    team = TeamConfig(
        key=options.team_key,
        name=options.team_name,
        icon=options.icon,
        color=options.color,
        timezone=options.timezone,
    )
    return replace(
        cfg,
        db_path=db_path,
        config_file=target,
        workspace_name=options.workspace_name,
        url_key=slugify(options.workspace_name or ""),
        team=team,
    )


__all__ = ["init_command"]
