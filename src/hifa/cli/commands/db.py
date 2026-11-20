"""`hifa db` — maintenance verbs of the database.

The group ships one verb: `reindex`. It identifies the file on a read-only probe before any
writable connection exists, refuses what is not a hifa database and rebuilds the full-text
index in one transaction. It composes no SQL: the rebuild lives in the adapter, as every other
statement of the tree does.
"""

from __future__ import annotations

from pathlib import Path

import typer

from hifa.adapters.sqlite.connection import open_db, open_probe, require_fts5
from hifa.adapters.sqlite.reindex import reindex
from hifa.cli import output
from hifa.cli.context import CliContext
from hifa.cli.errors import CliError, fail
from hifa.config import ConfigError, resolve_config
from hifa.domain.errors import DatabaseNotFound, HifaError, NotAHifaDatabase

db_app = typer.Typer(
    name="db",
    help="Database maintenance: rebuild the full-text index.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@db_app.command("reindex")
def reindex_command(
    ctx: typer.Context,
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Nothing on stdout."),
    no_color: bool = typer.Option(False, "--no-color", help="No color."),
) -> None:
    """Rebuild the full-text index of the database: the one repair the index has."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    json_mode = bool(json_output or root.json)
    quiet_mode = bool(quiet or root.quiet)
    output.configure(json_mode=json_mode, quiet=quiet_mode, color=not no_color)

    if json_mode and quiet_mode:
        raise fail(
            CliError(
                "--json and --quiet are mutually exclusive", code="invalid_option", field="output"
            )
        )

    try:
        count = _run(db_flag=root.db)
    except (HifaError, ConfigError, CliError) as exc:
        raise fail(exc) from exc
    except KeyboardInterrupt as exc:  # pragma: no cover - interactive
        raise fail(exc) from exc
    except Exception as exc:  # noqa: BLE001 - bug: exit 6, never 1
        raise fail(exc) from exc

    if output.options().json:
        output.write_json({"reindexed": count})
        return
    if output.options().quiet:
        return
    output.write_line(f"reindexed {count} issues")


def _run(*, db_flag: str | None) -> int:
    """Three ordered steps: the path, the read-only verdict, and only then the writable rebuild."""
    cfg = resolve_config(db_flag=db_flag)
    path: Path = cfg.db_path

    if not path.exists():
        raise DatabaseNotFound(f"database not found at {path}; run 'hifa init'", key=str(path))

    probe = open_probe(path)
    try:
        if "workspace" not in probe.tables or "issue" not in probe.tables:
            raise NotAHifaDatabase(f"not a hifa database: {path}; run 'hifa init'", key=str(path))
        require_fts5(probe.conn)
    finally:
        probe.conn.close()

    conn = open_db(path)
    try:
        return reindex(conn)
    finally:
        conn.close()


__all__ = ["db_app", "reindex_command"]
