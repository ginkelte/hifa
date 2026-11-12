"""`hifa serve` — run the dashboard.

`uvicorn` and `hifa.web.app` are imported **inside** the command: the core never imports
FastAPI, and a missing `[web]` extra is a clean exit 2 with the install hint.

Flags: `--host`, `--port`, `--read-only`, `--open/--no-open` (default
`--no-open`), `--reload` (dev only) and `--json` (prints `{url, host, port, read_only}` before
blocking).
"""

from __future__ import annotations

import json
import os
import webbrowser
from pathlib import Path

import typer

from hifa.cli.context import CliContext
from hifa.config import resolve_config

EXIT_MISSING_WEB = 2
EXIT_DATABASE_NOT_FOUND = 3
INSTALL_HINT = "install it with: uv sync --extra web"
#: Environment hand-off to the `--reload` import string (uvicorn re-imports the factory).
ENV_DB = "HIFA_SERVE_DB"
ENV_TEAM = "HIFA_SERVE_TEAM"
ENV_READ_ONLY = "HIFA_SERVE_READ_ONLY"


def serve_command(
    ctx: typer.Context,
    host: str | None = typer.Option(None, "--host", help="Bind address (default `[web].host`)."),
    port: int | None = typer.Option(None, "--port", help="Port (default `[web].port`)."),
    read_only: bool = typer.Option(False, "--read-only", help="Reject every mutation (403)."),
    open_browser: bool = typer.Option(
        False, "--open/--no-open", help="Open a browser (default: do not open one)."
    ),
    reload: bool = typer.Option(False, "--reload", help="Reload on change (development only)."),
    json_output: bool = typer.Option(
        False, "--json", help="Print {url, host, port, read_only} before blocking."
    ),
    db: str | None = typer.Option(None, "--db", help="SQLite file (wins over HIFA_DB)."),
) -> None:
    """Serve the local dashboard (read-only views in this stage)."""
    root = ctx.obj if isinstance(ctx.obj, CliContext) else CliContext()
    cfg = resolve_config(db_flag=db or root.db)

    try:
        import uvicorn

        from hifa.web.app import create_app
    except ImportError as exc:
        typer.echo(f"hifa serve needs the web extra (missing module: {exc.name})", err=True)
        typer.echo(INSTALL_HINT, err=True)
        raise typer.Exit(code=EXIT_MISSING_WEB) from exc

    path = Path(cfg.db_path)
    if not path.is_file():
        typer.echo(f"database not found: {path} (run `hifa init` first)", err=True)
        raise typer.Exit(code=EXIT_DATABASE_NOT_FOUND)

    bind_host = host or cfg.web.host
    bind_port = port or cfg.web.port
    application = create_app(path, default_team=cfg.default_team, read_only=read_only)
    url = f"http://{bind_host}:{bind_port}/"
    if json_output:
        payload = {"url": url, "host": bind_host, "port": bind_port, "read_only": read_only}
        typer.echo(json.dumps(payload))
    else:
        suffix = " (read-only)" if read_only else ""
        typer.echo(f"serving hifa on {url}{suffix} (Ctrl+C to stop)")
    if open_browser:
        webbrowser.open(url)
    if reload:
        # uvicorn only watches the sources when it gets an import string, so the settings
        # travel through the environment to `create_app_from_env`.
        os.environ[ENV_DB] = str(path)
        os.environ[ENV_READ_ONLY] = "1" if read_only else "0"
        if cfg.default_team:
            os.environ[ENV_TEAM] = str(cfg.default_team)
        uvicorn.run(
            "hifa.web.app:create_app_from_env",
            host=bind_host,
            port=bind_port,
            reload=True,
            log_level="info",
        )
        return
    uvicorn.run(application, host=bind_host, port=bind_port, log_level="info")


__all__ = ["EXIT_MISSING_WEB", "serve_command"]
