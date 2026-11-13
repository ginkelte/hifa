"""CLI invocation context.

Transport only: the global options already parsed, plus the one place where an invocation
decides which team it works on. The database path belongs to `hifa.config` and formatting
belongs to `hifa.cli.output`; the rules of the domain stay in `app/` and `domain/`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from hifa.cli.errors import EXIT_VALIDATION, CliError

if TYPE_CHECKING:
    from hifa.adapters.sqlite.uow import Repos
    from hifa.config import Config

#: Environment variable that names the team of the invocation.
TEAM_ENV = "HIFA_TEAM"

#: Message of the `missing_context` error: no team was named and the workspace does not hold
#: exactly one candidate, so the command has nothing to work on and must not guess.
TEAM_REQUIRED_MESSAGE = "team required: use --team, HIFA_TEAM or [cli].default_team in .hifa.toml"


@dataclass(frozen=True)
class CliContext:
    """Opciones globales (`--db`, `--json`, `--quiet`, `--no-color`)."""

    db: str | None = None
    json: bool = False
    quiet: bool = False
    color: bool = True


def resolve_team(repos: Repos, cfg: Config, *, flag: str | None = None) -> str:
    """Key of the team this invocation works on; every verb asks this function, no verb guesses.

    Order, first non-empty value wins:

    1. `flag` (`--team`, global or leaf);
    2. `HIFA_TEAM`;
    3. `[cli].default_team` declared in `.hifa.toml`;
    4. the only non-archived team of the workspace;
    5. otherwise nothing is chosen: `missing_context` (exit 2).

    A name given by `flag` or by `HIFA_TEAM` is returned as written, so an unknown one fails
    as `not_found` where the caller looks it up. A configured name only counts when the
    workspace holds that team: a stale `.hifa.toml` leaves the workspace as the next source
    instead of breaking every command. The `[team].key` fallback of `hifa.config` is the
    value `hifa init` writes into a fresh file, not a choice made by the user, so it never
    wins step 3 on its own.
    """
    explicit = _first_non_empty(flag, os.environ.get(TEAM_ENV))
    if explicit is not None:
        return explicit

    teams = [team for team in repos.teams.list(include_archived=True) if team.archived_at is None]
    configured = _first_non_empty(cfg.cli.default_team if cfg.cli.default_team_declared else None)
    if configured is not None:
        for team in teams:
            if team.id == configured or team.key.casefold() == configured.casefold():
                return team.key
    if len(teams) == 1:
        return teams[0].key
    raise CliError(
        TEAM_REQUIRED_MESSAGE,
        code="missing_context",
        field="team",
        exit_code=EXIT_VALIDATION,
    )


def _first_non_empty(*values: str | None) -> str | None:
    """First value that is neither `None`, `""` nor blanks; `None` when there is none."""
    for value in values:
        if value is not None and value.strip():
            return value.strip()
    return None


__all__ = ["CliContext", "TEAM_ENV", "TEAM_REQUIRED_MESSAGE", "resolve_team"]
