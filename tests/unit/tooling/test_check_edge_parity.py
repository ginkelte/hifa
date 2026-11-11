"""Tests of `scripts/check_edge_parity.py` (T-18, HIFA-37, AGENTS.md §5 P2).

The script is not a package module: it is loaded from its path, the way it runs in CI. The
synthetic surfaces keep the rule tests independent of the tree; the last block runs the real
map against the real Typer tree and the real route table, which is what guards against drift.

`check()` is called with the same split `main()` uses — `web` for the capability routes and
`all_routes` for the full table — so a test never exercises a mode the script does not have.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "check_edge_parity.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_edge_parity", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # `dataclasses` resolves annotations through `sys.modules[cls.__module__]`, so the module
    # must be registered before it is executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


parity = _load()

CREATE_WEB = ("POST", "/{team}/issue")
LIST_WEB = ("GET", "/{team}/list")
FRAGMENT = ("GET", "/_/issues")


class _FakeRoute:
    """The two attributes `_walk_routes`/`web_surface` read from a real `APIRoute`."""

    def __init__(self, path: str, methods: set[str]) -> None:
        self.path = path
        self.methods = methods


class _FakeApp:
    def __init__(self, *routes: _FakeRoute) -> None:
        self.routes = list(routes)


# --- the rule: both edges, debt, exemptions --------------------------------------------


def test_a_capability_missing_its_cli_edge_is_a_violation() -> None:
    """AC1: one edge only and no debt ticket is a failure, never a silent pass."""
    caps = (parity.Capability(name="issue.create", cli="issue create", web=CREATE_WEB),)

    report = parity.check(caps, cli=set(), web={CREATE_WEB}, all_routes={CREATE_WEB})

    assert not report.ok
    assert [finding.capability for finding in report.violations] == ["issue.create"]
    assert "issue create" in report.violations[0].detail


def test_a_single_edge_row_with_debt_is_reported_as_debt_not_a_violation() -> None:
    """AC2: a web-only read with an open ticket is printed as DEBT and exits clean."""
    caps = (parity.Capability(name="issue.list", web=LIST_WEB, debt="HIFA-2"),)

    report = parity.check(caps, cli=set(), web={LIST_WEB}, all_routes={LIST_WEB})

    assert report.ok
    assert report.violations == []
    assert [finding.ticket for finding in report.debt] == ["HIFA-2"]
    text = parity.render(report)
    assert "DEBT" in text
    assert "HIFA-2" in text


def test_a_single_edge_row_without_debt_or_reason_is_a_violation() -> None:
    caps = (parity.Capability(name="issue.list", web=LIST_WEB),)

    report = parity.check(caps, cli=set(), web={LIST_WEB}, all_routes={LIST_WEB})

    assert not report.ok
    assert "debt=" in report.violations[0].detail


def test_debt_and_reason_together_are_rejected() -> None:
    caps = (parity.Capability(name="db.init", cli="init", debt="HIFA-37", reason="operational"),)

    report = parity.check(caps, cli={"init"}, web=set(), all_routes=set())

    assert not report.ok
    assert "mutually exclusive" in report.violations[0].detail


def test_a_reason_is_an_exemption_without_a_ticket() -> None:
    caps = (parity.Capability(name="db.init", cli="init", reason="operational"),)

    report = parity.check(caps, cli={"init"}, web=set(), all_routes=set())

    assert report.ok
    assert report.debt == []
    assert [finding.capability for finding in report.exempt] == ["db.init"]
    assert "EXEMPT" in parity.render(report)


def test_a_malformed_debt_ticket_is_a_violation() -> None:
    """`debt=` is a ticket id, not prose: one word used to open the escape hatch."""
    caps = (parity.Capability(name="db.init", cli="init", debt="soon"),)

    report = parity.check(caps, cli={"init"}, web=set(), all_routes=set())

    assert not report.ok
    assert "KEY-NNN" in report.violations[0].detail
    assert "soon" in report.violations[0].detail


def test_an_empty_web_tuple_fails_validation_instead_of_raising() -> None:
    """`web=()` used to raise `IndexError` from `routes()` while the map was validated."""
    caps = (parity.Capability(name="issue.create", web=()),)

    report = parity.check(caps, cli={"issue create"}, web=set(), all_routes=set())

    assert not report.ok
    assert "at least one edge" in report.violations[0].detail


def test_a_stale_debt_on_a_both_edges_row_is_a_violation() -> None:
    """A `debt=` on a row that already has both edges would be silently dropped."""
    caps = (
        parity.Capability(name="issue.create", cli="issue create", web=CREATE_WEB, debt="HIFA-2"),
    )

    report = parity.check(caps, cli={"issue create"}, web={CREATE_WEB}, all_routes={CREATE_WEB})

    assert not report.ok
    assert report.violations[0].detail == (
        "stale debt ticket: the capability is already on both edges"
    )
    assert report.parity == 0


def test_parity_counts_only_the_rows_whose_pointers_resolved() -> None:
    """`parity` used to be `capabilities - debt - exempt`, so a broken row counted as parity."""
    caps = (
        parity.Capability(name="issue.create", cli="issue create", web=CREATE_WEB),
        parity.Capability(name="issue.list", cli="issue list", web=("GET", "/{team}/nope")),
    )

    report = parity.check(
        caps, cli={"issue create", "issue list"}, web={CREATE_WEB}, all_routes={CREATE_WEB}
    )

    assert report.capabilities == 2
    assert report.parity == 1
    assert "1 on both edges" in parity.render(report, quiet=True)


# --- the map cannot drift --------------------------------------------------------------


def test_a_pointer_to_a_missing_command_is_a_violation() -> None:
    caps = (parity.Capability(name="issue.create", cli="issue create", web=CREATE_WEB),)

    report = parity.check(caps, cli={"issue new"}, web={CREATE_WEB}, all_routes={CREATE_WEB})

    details = [finding.detail for finding in report.violations]
    assert details == ["cli `issue create` is not a command of the tree"]


def test_a_pointer_to_a_missing_route_is_a_violation() -> None:
    caps = (parity.Capability(name="issue.create", cli="issue create", web=CREATE_WEB),)

    report = parity.check(caps, cli={"issue create"}, web=set(), all_routes=set())

    assert not report.ok
    assert "is not a route of the app" in report.violations[0].detail


def test_an_undeclared_route_is_a_violation() -> None:
    caps = (parity.Capability(name="issue.create", cli="issue create", web=CREATE_WEB),)

    report = parity.check(
        caps, cli={"issue create"}, web={CREATE_WEB, LIST_WEB}, all_routes={CREATE_WEB, LIST_WEB}
    )

    assert not report.ok
    assert [finding.capability for finding in report.violations] == ["GET /{team}/list"]


def test_an_undeclared_command_is_a_violation() -> None:
    caps = (parity.Capability(name="issue.create", cli="issue create", web=CREATE_WEB),)

    report = parity.check(
        caps, cli={"issue create", "team list"}, web={CREATE_WEB}, all_routes={CREATE_WEB}
    )

    assert not report.ok
    assert [finding.capability for finding in report.violations] == ["team list"]


def test_an_unresolved_pointer_suppresses_the_not_in_the_map_block() -> None:
    """A single typo used to bury the real failure under one line per forgotten route."""
    caps = (parity.Capability(name="issue.create", cli="issue create", web=CREATE_WEB),)

    report = parity.check(
        caps, cli={"issue new"}, web={CREATE_WEB, LIST_WEB}, all_routes={CREATE_WEB, LIST_WEB}
    )

    details = [finding.detail for finding in report.violations]
    assert details == ["cli `issue create` is not a command of the tree"]
    assert report.hint is not None
    text = parity.render(report)
    assert "HINT" in text
    assert "not-in-the-map block is suppressed" in text


# --- the web surface reads every method -----------------------------------------------


def test_web_surface_reads_every_method_but_head_and_options() -> None:
    """An allowlist of GET/POST used to hide a PATCH/DELETE route; here it cannot."""
    app = _FakeApp(
        _FakeRoute("/{team}/issue/{ref}", {"PATCH"}),
        _FakeRoute("/{team}/issue/{ref}", {"DELETE"}),
        _FakeRoute("/{team}/issue/{ref}/move", {"POST"}),
        _FakeRoute("/{team}/issue/{ref}/read", {"GET", "HEAD", "OPTIONS"}),
        _FakeRoute("/static/app.css", {"GET"}),
        _FakeRoute("/staticx", {"GET"}),
    )

    capabilities, all_routes = parity.web_surface(app)

    assert ("PATCH", "/{team}/issue/{ref}") in capabilities
    assert ("DELETE", "/{team}/issue/{ref}") in capabilities
    assert ("POST", "/{team}/issue/{ref}/move") in capabilities  # no false positive either
    assert ("GET", "/{team}/issue/{ref}/read") in capabilities
    assert not any(method in {"HEAD", "OPTIONS"} for method, _ in all_routes)
    assert ("GET", "/static/app.css") not in all_routes
    # `/static` is the mount; `/staticx` is somebody else's route and must not be swallowed.
    assert ("GET", "/staticx") in all_routes


# --- fragments are declared, not ignored ----------------------------------------------


def test_a_declared_fragment_is_not_a_violation_and_not_a_capability() -> None:
    caps = (parity.Capability(name="issue.list", web=LIST_WEB, debt="HIFA-2"),)

    report = parity.check(
        caps,
        cli=set(),
        web={LIST_WEB},
        all_routes={LIST_WEB, FRAGMENT},
        fragment_allowlist={FRAGMENT: "htmx partial of the list rows"},
    )

    assert report.violations == []
    assert report.ok


def test_render_says_so_when_every_row_has_both_edges() -> None:
    caps = (parity.Capability(name="issue.create", cli="issue create", web=CREATE_WEB),)

    report = parity.check(caps, cli={"issue create"}, web={CREATE_WEB}, all_routes={CREATE_WEB})

    assert "no debt, no exemption: every capability is on both edges" in parity.render(report)


def test_an_undeclared_fragment_is_a_violation() -> None:
    """A `/_/*` route used to be a free zone: only the map rows were checked there."""
    caps = (parity.Capability(name="issue.list", web=LIST_WEB, debt="HIFA-2"),)

    report = parity.check(
        caps,
        cli=set(),
        web={LIST_WEB},
        all_routes={LIST_WEB, FRAGMENT},
        fragment_allowlist={},
    )

    assert not report.ok
    assert [finding.capability for finding in report.violations] == ["GET /_/issues"]
    assert report.violations[0].detail == ("fragment route is not declared in FRAGMENT_ALLOWLIST")


def test_a_fragment_named_in_the_map_must_exist_in_the_app() -> None:
    caps = (
        parity.Capability(
            name="issue.create",
            cli="issue create",
            web=(CREATE_WEB, ("POST", "/_/issues")),
        ),
    )

    report = parity.check(
        caps,
        cli={"issue create"},
        web={CREATE_WEB},
        all_routes={CREATE_WEB},
        fragment_allowlist={("POST", "/_/issues"): "htmx quick-add"},
    )

    assert not report.ok
    assert "web `POST /_/issues` is not a route of the app" in report.violations[0].detail


# --- exit codes and infrastructure failures -------------------------------------------


def test_a_violating_map_exits_one(capsys) -> None:
    caps = (parity.Capability(name="nope", cli="no-such-verb", reason="broken on purpose"),)

    assert parity.main(["--quiet"], capabilities=caps) == 1

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert out[0].startswith("edge parity FAILED")


def test_duplicate_capability_names_exit_two(capsys) -> None:
    caps = (
        parity.Capability(name="same", cli="init", reason="a"),
        parity.Capability(name="same", cli="serve", reason="b"),
    )

    assert parity.main(["--quiet"], capabilities=caps) == 2

    assert "duplicate capability name(s): same" in capsys.readouterr().err


def test_a_missing_web_extra_exits_two(monkeypatch, capsys) -> None:
    def boom() -> None:
        raise ImportError("No module named 'fastapi'")

    monkeypatch.setattr(parity, "build_web_app", boom)

    assert parity.main(["--quiet"]) == 2

    err = capsys.readouterr().err
    assert "install the [web] extra" in err
    assert "Traceback" not in err


def test_a_broken_app_is_an_infrastructure_error_not_a_parity_diff(monkeypatch, capsys) -> None:
    """A locked database or a broken template used to die with exit 1 and a traceback."""

    def boom() -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(parity, "build_web_app", boom)

    assert parity.main(["--quiet"]) == 2

    err = capsys.readouterr().err
    assert "RuntimeError: database is locked" in err
    assert "Traceback" not in err


def test_render_prints_the_fail_line_and_the_summary() -> None:
    """The only output a blocked developer sees: the `FAIL` line and the last line."""
    caps = (
        parity.Capability(name="issue.create", cli="issue create", web=CREATE_WEB),
        parity.Capability(name="team.create", cli="team create"),
    )

    report = parity.check(
        caps, cli={"issue create", "team create"}, web={CREATE_WEB}, all_routes={CREATE_WEB}
    )
    lines = parity.render(report).splitlines()

    assert any(line.startswith("FAIL    team.create") for line in lines)
    assert any("needs debt=<ticket>" in line for line in lines)
    assert lines[-1] == (
        "edge parity FAILED: 2 capabilities, 1 on both edges, 0 debt, 0 exempt, 1 violations"
    )


# --- the real surfaces -----------------------------------------------------------------


def test_cli_surface_has_root_and_nested_commands() -> None:
    surface = parity.cli_surface()

    assert {"init", "serve"} <= surface
    assert "issue create" in surface
    assert "team show" in surface
    assert "issue" not in surface  # a group is not a verb


def test_web_surface_finds_the_create_route_and_skips_the_probe() -> None:
    capabilities, all_routes = parity.web_surface(parity.build_web_app())

    assert CREATE_WEB in capabilities
    assert CREATE_WEB in all_routes
    assert ("GET", "/{team}/list") in capabilities
    assert ("GET", "/healthz") not in all_routes
    # A fragment is reachable but is not a capability route of its own.
    assert FRAGMENT in all_routes
    assert FRAGMENT not in capabilities


def test_every_real_fragment_is_declared() -> None:
    _, all_routes = parity.web_surface(parity.build_web_app())

    fragments = {pair for pair in all_routes if pair[1].startswith(parity.FRAGMENT_PREFIX)}
    assert fragments
    assert fragments == set(parity.FRAGMENT_ALLOWLIST)


def test_the_real_map_matches_the_real_tree() -> None:
    cli = parity.cli_surface()
    capabilities, all_routes = parity.web_surface(parity.build_web_app())

    report = parity.check(parity.CAPABILITIES, cli, capabilities, all_routes=all_routes)

    assert report.violations == [], [finding.detail for finding in report.violations]
    assert report.hint is None


def test_the_script_exits_zero_on_the_real_tree(capsys) -> None:
    assert parity.main(["--quiet"]) == 0

    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1
    assert out[0].startswith("edge parity OK")


def test_the_json_envelope_carries_the_five_p7_keys(capsys) -> None:
    assert parity.main(["--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert set(payload) == {"ok", "violations", "debt", "cli", "web"}
    assert payload["ok"] is True
    assert "POST /{team}/issue" in payload["web"]
