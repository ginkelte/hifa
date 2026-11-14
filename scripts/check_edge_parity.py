#!/usr/bin/env python3
"""CLI-vs-web capability parity checker — T-18, HIFA-37.

The rule is that the CLI and the dashboard expose the same capabilities: `hifa issue create` and
`POST /{team}/issue` are two transports of one capability, and neither edge may own a
feature the other cannot reach. This script is the executable form of that rule.

How the two surfaces are read
-----------------------------
* CLI — `typer.main.get_command(hifa.cli.main.app)` and a recursive walk of `.commands`, so the
  surface is the real Typer tree (no source parsing, no grep). Only leaf commands are verbs:
  `issue` is a group, `issue create` is a command.
* web — `create_app(...)` is instantiated against a throwaway path and `app.routes` is walked.
  Recent FastAPI wraps an included router in `_IncludedRouter`, so the walk descends into
  `original_router.routes` (and into `Mount.routes`) instead of trusting the flat list.
  Every HTTP method counts except `HEAD`/`OPTIONS`, which FastAPI adds for free: a `PATCH` or a
  `DELETE` route is a capability route exactly like a `GET` and must be declared.

The database is never touched: `create_app` opens it lazily and `boards.startup_state` degrades
to an empty workspace on a missing file, so no migration is applied and no file is created.

How the verdict is made
-----------------------
`CAPABILITIES` is the contract, one row per capability. `cli` and `web` both required means
parity. A single-edge row is only legal with one of:

* `debt="HIFA-N"` — a known gap with an open ticket; printed as a DEBT line, not a failure.
  The ticket must be the one that closes the gap: a ticket that is already done, or this
  checker's own ticket, is not an honest pointer.
* `reason="..."`  — single-edge by design (an operational command with no dashboard meaning);
                     printed as an EXEMPT line, so the exclusion is visible instead of silent.

Everything else fails: a pointer to a command or route that does not exist, a CLI command or a
capability route that no row declares (the map cannot drift silently), a single-edge row with
neither `debt` nor `reason`, a malformed ticket id, and a `debt`/`reason` left on a row that
already has both edges (stale).

`.htmx` fragments (`/_/*`) are not capabilities of their own — the pages are — but they are not
a free zone either: every one of them must be declared in `FRAGMENT_ALLOWLIST` with the reason
it exists, so a fragment cannot become the only web transport of a capability unnoticed. A row
may still name one (e.g. the quick-add `POST /_/issues` behind `issue create`), and then it
must exist like any other pointer. `/healthz` is a liveness probe and `/static` is an asset
mount: neither is a capability.

Usage
-----
    python scripts/check_edge_parity.py            # human-readable diff
    python scripts/check_edge_parity.py --quiet    # only the final line
    python scripts/check_edge_parity.py --json     # {ok, violations, debt, cli, web}

Exit codes: 0 parity (debt and exemptions are allowed and printed), 1 violations, 2 usage error
(a malformed map, a duplicate capability name) or an infrastructure failure (an edge that cannot
be imported, or an app that cannot be built: a locked database, a broken template). An
infrastructure failure is not a parity diff, so it never exits 1.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

#: `(METHOD, path)` of a route, exactly as FastAPI reports it.
Route = tuple[str, str]

#: Paths that carry no capability: the liveness probe and the static asset mount.
NON_CAPABILITY_PATHS = frozenset({"/healthz", "/static"})

#: Prefix of the htmx fragments: part of a page, not a capability of their own.
FRAGMENT_PREFIX = "/_/"

#: Methods FastAPI adds to every route for free; they are not capabilities.
IGNORED_METHODS = frozenset({"HEAD", "OPTIONS"})

#: Ticket ids are `KEY-NNN` (`HIFA-42`): the `debt=` escape hatch is not free text.
DEBT_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")


def is_non_capability_path(path: str) -> bool:
    """`/healthz` and the `/static` mount (with its children) are not capabilities.

    `path.startswith("/static")` alone would also swallow a `/staticx` route.
    """
    return path in NON_CAPABILITY_PATHS or path.startswith("/static/")


@dataclass(frozen=True)
class Capability:
    """One capability and the edges that expose it.

    `cli` is a command path of the Typer tree (`"issue create"`); `web` is one `(METHOD, path)`
    pair or a tuple of them when two routes serve the same capability (the no-JS form and the
    htmx one). A single-edge row must carry `debt=` or `reason=`.
    """

    name: str
    cli: str | None = None
    web: Route | tuple[Route, ...] | None = None
    debt: str | None = None
    reason: str | None = None

    def routes(self) -> tuple[Route, ...]:
        """The web pointers of the row, normalised to a tuple (`()` for none)."""
        if not self.web:
            return ()
        if isinstance(self.web[0], str):
            return (cast(Route, self.web),)
        return tuple(cast(Iterable[Route], self.web))

    def is_single_edge(self) -> bool:
        return self.cli is None or not self.routes()

    def validate(self) -> str | None:
        """`None` when the row is legal, else the reason it is not."""
        if self.debt is not None and self.reason is not None:
            return "debt= and reason= are mutually exclusive"
        if self.debt is not None and not DEBT_PATTERN.fullmatch(self.debt):
            return f"debt= must be a ticket id like KEY-NNN, got {self.debt!r}"
        if self.cli is None and not self.routes():
            return "a capability needs at least one edge"
        if self.is_single_edge() and self.debt is None and self.reason is None:
            return "single-edge capability needs debt=<ticket> or reason=<why>"
        return None


# --- the map ---------------------------------------------------------------------------
# One row per capability of the product. Both edges = parity; one edge = debt (open ticket)
# or an exemption (single-edge by design). Add a row in the same PR that adds a verb or a
# capability route: an undeclared command/route fails the check.
#
# Ticket ids: the rule is that `debt=` names the ticket that CLOSES the gap. A ticket that is
# already done, or this checker's own ticket (HIFA-37), is not honest: it would keep the debt
# line green while nothing is planned to remove it.
# * HIFA-49 ships `hifa issue show REF --with history` — it closes `issue.show`.
# * HIFA-2  (T-07) ships `issue list --sort manual`, which closes `issue.list`; the board model
#           it shares the order with is `issue.board` (T-08).
# * HIFA-7  (T-11) brings full-text search and replaces the `/search` stub: `search.issues`.
# * HIFA-42 is the dashboard form for a team and a workflow state from the UI: it closes
#           `team.create` and `state.create`.
# * HIFA-3  (dashboard stage 2) brings the web edge of the five issue writes — inline edit,
#           move from the panel — so it closes `issue.edit`, `issue.move`, `issue.archive`,
#           `issue.delete` and `issue.restore`.

CAPABILITIES: tuple[Capability, ...] = (
    # --- operational: they build or run an edge, they are not a capability of one ---------
    Capability(
        name="db.init",
        cli="init",
        reason="bootstraps the database before any edge can exist",
    ),
    Capability(
        name="web.serve",
        cli="serve",
        reason="it is the web edge itself, not a capability of it",
    ),
    # --- issues ---------------------------------------------------------------------------
    Capability(
        name="issue.create",
        cli="issue create",
        # The no-JS form post and the htmx quick-add are the same capability.
        web=(("POST", "/{team}/issue"), ("POST", "/_/issues")),
    ),
    Capability(name="issue.list", cli="issue list", web=("GET", "/{team}/list")),
    # The no-JS form post and the htmx field write are the same capability.
    Capability(
        name="issue.edit",
        cli="issue edit",
        web=(("POST", "/{team}/issue/{identifier}"), ("PATCH", "/_/issues/{identifier}")),
    ),
    # The four writes below are CLI-only until the dashboard stage 2 lands their web edge.
    Capability(name="issue.move", cli="issue move", web=None, debt="HIFA-3"),
    Capability(name="issue.archive", cli="issue archive", web=None, debt="HIFA-3"),
    Capability(name="issue.delete", cli="issue delete", web=None, debt="HIFA-3"),
    Capability(name="issue.restore", cli="issue restore", web=None, debt="HIFA-3"),
    # Relations are read and written from the CLI; the dashboard shows their blockers once
    # HIFA-34 lands the panel and the badge, which is the later of the two edges.
    Capability(name="issue.relate", cli="issue relate", web=None, debt="HIFA-34"),
    Capability(name="issue.relations", cli="issue relations", web=None, debt="HIFA-34"),
    Capability(name="issue.blocked", cli="issue blocked", web=None, debt="HIFA-34"),
    # The CLI transport of this page is `hifa issue show`, which no verb carries yet.
    Capability(name="issue.show", cli=None, web=("GET", "/{team}/issue/{ref}"), debt="HIFA-49"),
    # HIFA-2 (T-07) ships the verb that exposes both reads; the board read MODEL
    # (`app/boards.columns`, counts and wip) is T-08 / HIFA-4, so this line closes
    # only when both are done -- the ticket named below is the later of the two.
    Capability(name="issue.board", cli=None, web=("GET", "/{team}/board"), debt="HIFA-2"),
    # --- teams ----------------------------------------------------------------------------
    Capability(name="team.create", cli="team create", web=None, debt="HIFA-42"),
    Capability(name="team.list", cli="team list", web=("GET", "/")),
    # The board is the team's page: key, emoji, workflow columns and counts of one team.
    Capability(name="team.show", cli="team show", web=("GET", "/{team}/board")),
    # --- workflow states ------------------------------------------------------------------
    Capability(name="state.create", cli="state create", web=None, debt="HIFA-42"),
    Capability(name="state.list", cli="state list", web=("GET", "/{team}/board")),
    # The board drag & drop is the web transport of `state update` (position only today;
    # rename/color wait for the inline editing of T-19).
    Capability(
        name="state.update",
        cli="state update",
        web=("POST", "/_/states/{state_id}/position"),
    ),
    # --- search ---------------------------------------------------------------------------
    # `GET /search` is a 501 placeholder until T-11, which also lands `hifa search`.
    Capability(name="search.issues", cli=None, web=("GET", "/search"), debt="HIFA-7"),
)


# --- fragments -------------------------------------------------------------------------
# Every `/_/*` route of the app, with the one line that says why it exists. A fragment is a
# piece of a page, never a capability on its own: if a capability could only be reached
# through one of these, the rule would have a hole, so an UNDECLARED fragment is a violation.
FRAGMENT_ALLOWLIST: Mapping[Route, str] = {
    ("GET", "/_/issues"): "htmx partial: the rows (view=list) or the board (view=board)",
    ("GET", "/_/issues/{identifier}/row"): "htmx partial of one list row",
    ("GET", "/_/issues/{identifier}/card"): "htmx partial of one board card",
    ("GET", "/_/issues/{identifier}/panel"): "htmx partial of the issue panel",
    ("GET", "/_/counts"): "htmx partial of the dashboard counters",
    ("GET", "/_/revision"): "htmx read of the change signal: one number, no HTML to swap",
    ("POST", "/_/issues"): "htmx quick-add: the web transport of `issue create`",
    ("PATCH", "/_/issues/{identifier}"): "htmx field write: the web transport of `issue edit`",
    ("POST", "/_/states/{state_id}/position"): "htmx write of the board drag & drop",
}


# --- surfaces --------------------------------------------------------------------------


def cli_surface(app: Any | None = None) -> set[str]:
    """Every leaf command of the Typer tree, as `"issue create"`-style paths.

    The root group is not a verb and neither is an intermediate group: only commands without
    subcommands are. Root-level commands (`init`, `serve`) come out as bare words.
    """
    if app is None:
        from hifa.cli.main import app as root

        app = root
    import typer.main

    command = typer.main.get_command(app)
    surface: set[str] = set()

    def walk(current: Any, prefix: str) -> None:
        for name, sub in getattr(current, "commands", {}).items():
            path = f"{prefix} {name}".strip()
            if getattr(sub, "commands", None):
                walk(sub, path)
            else:
                surface.add(path)

    walk(command, "")
    return surface


def _walk_routes(routes: Any, prefix: str = "") -> Iterator[tuple[Any, str]]:
    """`(route, full_path)` for every route, descending into included routers and mounts."""
    for route in routes:
        original = getattr(route, "original_router", None)
        if original is not None:  # FastAPI >= 0.121: an included router is one opaque entry
            context = getattr(route, "include_context", None)
            own = getattr(context, "prefix", "") or ""
            yield from _walk_routes(original.routes, prefix + own)
            continue
        mounted = getattr(route, "routes", None)
        if mounted is not None and not getattr(route, "methods", None):
            own = getattr(route, "path", "") or ""
            yield from _walk_routes(mounted, prefix + own)
            continue
        yield route, prefix + (getattr(route, "path", "") or "")


def web_surface(app: Any) -> tuple[set[Route], set[Route]]:
    """`(capability routes, every route)` of the dashboard, whatever the HTTP method.

    The first set is what the map must cover; the second one is what a row may point at (it
    also carries the `/_/*` fragments and excludes `/healthz` and the static mount). `HEAD`
    and `OPTIONS` are the only methods dropped: reading `GET`/`POST` only would hide a
    `PATCH`/`DELETE`/`PUT` capability and reject a map row that declares one.
    """
    capabilities: set[Route] = set()
    everything: set[Route] = set()
    for route, path in _walk_routes(app.routes):
        if not path or is_non_capability_path(path):
            continue
        methods = getattr(route, "methods", None) or set()
        for method in sorted(set(methods) - IGNORED_METHODS):
            pair = (method, path)
            everything.add(pair)
            if not path.startswith(FRAGMENT_PREFIX):
                capabilities.add(pair)
    return capabilities, everything


def build_web_app() -> Any:
    """Instantiate the dashboard without a database: a throwaway path, read-only, no server.

    May raise `ImportError` (the `[web]` extra is not installed) or anything `create_app`
    raises (a locked database, a broken template): `main()` turns both into exit 2, because
    an app that cannot start is not a parity diff.
    """
    from hifa.web.app import create_app

    with tempfile.TemporaryDirectory(prefix="hifa-parity-") as tmp:
        return create_app(Path(tmp) / "parity.db", read_only=True)


# --- verdict ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Finding:
    """One line of the report: a failure, a debt or an exemption."""

    kind: str  # "violation" | "debt" | "exempt"
    capability: str
    detail: str
    ticket: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "capability": self.capability,
            "detail": self.detail,
            "ticket": self.ticket,
        }


@dataclass
class Report:
    """The verdict of one run."""

    cli: set[str] = field(default_factory=set)
    web: set[Route] = field(default_factory=set)
    capabilities: int = 0
    #: Rows whose two pointers both resolved: the "on both edges" of the summary.
    parity: int = 0
    #: Every route of the app, fragments included: the "of N routes" of the summary.
    routes: int = 0
    violations: list[Finding] = field(default_factory=list)
    debt: list[Finding] = field(default_factory=list)
    exempt: list[Finding] = field(default_factory=list)
    #: One line that replaces the "not in the map" block when a pointer did not resolve.
    hint: str | None = None

    @property
    def ok(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict[str, Any]:
        """JSON envelope: `{ok, violations, debt, cli, web}` and nothing else.

        Exemptions travel inside `debt` with `kind: "exempt"` and no ticket, so a consumer can
        read one list without losing the rows that the map deliberately keeps single-edge.
        """
        return {
            "ok": self.ok,
            "violations": [finding.as_dict() for finding in self.violations],
            "debt": [finding.as_dict() for finding in (*self.debt, *self.exempt)],
            "cli": sorted(self.cli),
            "web": [f"{method} {path}" for method, path in sorted(self.web)],
        }


def _single_edge_detail(capability: Capability) -> str:
    if capability.cli is not None:
        return f"CLI-only: `hifa {capability.cli}` has no dashboard route"
    routes = ", ".join(f"{method} {path}" for method, path in capability.routes())
    return f"web-only: {routes} has no CLI verb"


def check(
    capabilities: Iterable[Capability],
    cli: set[str],
    web: set[Route],
    *,
    all_routes: set[Route],
    fragment_allowlist: Mapping[Route, str] = FRAGMENT_ALLOWLIST,
) -> Report:
    """Compare the map against the two surfaces and return the verdict.

    `web` is the capability routes (fragments excluded) — what the map must cover;
    `all_routes` is the full route table, fragments included — what a row may point at. The
    split is an argument, not a default, so the tests exercise the mode `main()` uses.
    """
    rows = tuple(capabilities)
    known = set(all_routes)
    report = Report(cli=set(cli), web=set(web), capabilities=len(rows), routes=len(known))
    declared_cli: set[str] = set()
    declared_web: set[Route] = set()
    unresolved = False

    for row in rows:
        # Declare the pointers before validating: a row that fails validation must not also
        # report its own command/route as "not in the capability map" (same cascade as below).
        if row.cli is not None:
            declared_cli.add(row.cli)
        declared_web.update(row.routes())
        problem = row.validate()
        if problem is not None:
            report.violations.append(Finding("violation", row.name, problem))
            continue
        resolved = True
        if row.cli is not None and row.cli not in cli:
            resolved = False
            report.violations.append(
                Finding("violation", row.name, f"cli `{row.cli}` is not a command of the tree")
            )
        for route in row.routes():
            if route not in known:
                resolved = False
                report.violations.append(
                    Finding(
                        "violation",
                        row.name,
                        f"web `{route[0]} {route[1]}` is not a route of the app",
                    )
                )
        unresolved = unresolved or not resolved
        if row.is_single_edge():
            if row.debt is not None:
                report.debt.append(Finding("debt", row.name, _single_edge_detail(row), row.debt))
            else:
                report.exempt.append(
                    Finding("exempt", row.name, f"single-edge by design: {row.reason}")
                )
        elif row.debt is not None or row.reason is not None:
            stale = "debt ticket" if row.debt is not None else "reason"
            report.violations.append(
                Finding(
                    "violation",
                    row.name,
                    f"stale {stale}: the capability is already on both edges",
                )
            )
        elif resolved:
            report.parity += 1

    for pair in sorted(pair for pair in known if pair[1].startswith(FRAGMENT_PREFIX)):
        if pair not in fragment_allowlist:
            report.violations.append(
                Finding(
                    "violation",
                    f"{pair[0]} {pair[1]}",
                    "fragment route is not declared in FRAGMENT_ALLOWLIST",
                )
            )

    if unresolved:
        # One typo in a route parameter used to bury the real failure under a line per route
        # the map "forgot"; say it once and let the pointer failure be the only failure.
        report.hint = "a pointer did not resolve; the not-in-the-map block is suppressed"
    else:
        for command in sorted(cli - declared_cli):
            report.violations.append(
                Finding("violation", command, "CLI command is not in the capability map")
            )
        for method, path in sorted(web - declared_web):
            report.violations.append(
                Finding("violation", f"{method} {path}", "web route is not in the capability map")
            )
    return report


# --- output ----------------------------------------------------------------------------


def _summary(report: Report) -> str:
    verdict = "OK" if report.ok else "FAILED"
    return (
        f"edge parity {verdict}: {report.capabilities} capabilities, "
        f"{report.parity} on both edges, {len(report.debt)} debt, "
        f"{len(report.exempt)} exempt, {len(report.violations)} violations"
    )


def render(report: Report, *, quiet: bool = False) -> str:
    """Human-readable diff; `quiet` keeps only the final line."""
    if quiet:
        return _summary(report)
    lines = [
        "CLI vs web capability parity",
        f"CLI surface: {len(report.cli)} commands",
        f"web surface: {len(report.web)} capability routes of {report.routes}",
        f"map: {report.capabilities} capabilities",
        "",
    ]
    for finding in report.violations:
        lines.append(f"FAIL    {finding.capability:<28} {finding.detail}")
    if report.hint is not None:
        lines.append(f"HINT    {report.hint}")
    for finding in report.debt:
        lines.append(f"DEBT    {finding.capability:<28} {finding.detail} -> {finding.ticket}")
    for finding in report.exempt:
        lines.append(f"EXEMPT  {finding.capability:<28} {finding.detail}")
    if not (report.violations or report.debt or report.exempt):
        lines.append("no debt, no exemption: every capability is on both edges")
    lines.append("")
    lines.append(_summary(report))
    return "\n".join(lines)


def _duplicate_names(capabilities: Iterable[Capability]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for row in capabilities:
        if row.name in seen:
            duplicates.add(row.name)
        seen.add(row.name)
    return sorted(duplicates)


def main(
    argv: list[str] | None = None,
    *,
    capabilities: tuple[Capability, ...] = CAPABILITIES,
) -> int:
    parser = argparse.ArgumentParser(
        prog="check_edge_parity.py",
        description=(
            "Check that every capability of the product is exposed by both the CLI and the "
            "dashboard (AGENTS.md §5 P2)."
        ),
    )
    parser.add_argument("--quiet", action="store_true", help="print only the final line")
    parser.add_argument("--json", action="store_true", help="machine-readable report (P7)")
    args = parser.parse_args(argv)

    duplicates = _duplicate_names(capabilities)
    if duplicates:
        names = ", ".join(duplicates)
        print(f"edge parity: duplicate capability name(s): {names}", file=sys.stderr)
        return 2

    try:
        cli = cli_surface()
        app = build_web_app()
    except ImportError as exc:
        print(
            f"edge parity: cannot import an edge, install the [web] extra: {exc}",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001 — deliberate: a broken app is not a parity diff
        detail = f"{type(exc).__name__}: {exc}"
        print(f"edge parity: cannot build the web app: {detail}", file=sys.stderr)
        return 2

    web, all_routes = web_surface(app)
    report = check(capabilities, cli, web, all_routes=all_routes)

    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
    else:
        print(render(report, quiet=args.quiet))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
