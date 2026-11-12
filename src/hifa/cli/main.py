"""`hifa` CLI entry point.

`app` is the root group: it declares the global options (`--db`, `--json`, `--quiet`,
`--no-color`, `--version`) and registers the subcommands. The package entrypoint is
`hifa = "hifa.cli.main:app"` (`pyproject.toml`); `python -m hifa.cli.main` also works.
"""

from __future__ import annotations

import typer

from hifa import __version__
from hifa.cli.commands.init import init_command
from hifa.cli.commands.issue import issue_app
from hifa.cli.commands.serve import serve_command
from hifa.cli.commands.state import state_app
from hifa.cli.commands.team import team_app
from hifa.cli.context import CliContext

app = typer.Typer(
    name="hifa",
    help="Local issue tracker: CLI + SQLite + dashboard.",
    no_args_is_help=True,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"hifa {__version__}")
        raise typer.Exit(code=0)


@app.callback()
def main(
    ctx: typer.Context,
    db: str | None = typer.Option(None, "--db", help="SQLite file to use."),
    json_output: bool = typer.Option(False, "--json", help="JSON output."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output (ids)."),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI."),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """CLI root: global options go before the subcommand."""
    ctx.obj = CliContext(db=db, json=json_output, quiet=quiet, color=not no_color)


app.command("init")(init_command)
app.add_typer(issue_app, name="issue")
app.add_typer(team_app, name="team")
app.add_typer(state_app, name="state")
app.command("serve")(serve_command)


if __name__ == "__main__":  # pragma: no cover - allows `python -m hifa.cli.main`
    app()
