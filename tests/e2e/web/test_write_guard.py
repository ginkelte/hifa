"""E2E of the write guard: a state-changing request from another authority is refused.

The dashboard had two ways to be told no, and neither was about the caller: `--read-only` is a
property of the process and `HX-Request` a statement about the client's `Accept`. A page the
reader visits while the dashboard is open can therefore submit a plain form post to it — the two
page forms take urlencoded bodies and need no header at all — so the guard asks who sent the
request instead: it answers on the method alone, before the route is matched, and refuses a
state-changing one from another authority.

Every test here reads the database beside the HTTP answer, the way the neighbouring files do: a
`403` proves nothing if the row moved anyway, and a `200` proves nothing if it did not.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import BaseRoute
from typer.testing import CliRunner

from hifa.cli.main import app as cli_app
from hifa.web.app import READ_METHODS, create_app

runner = CliRunner()
HTMX = {"HX-Request": "true"}
#: What a page of another origin sends, and the two headers the guard decides on.
CROSS_SITE = {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"}
#: The refusal of the guard: neither the read-only body nor a page, one sentence of its own.
REFUSAL = "cross-site write refused"
#: The title the fixture's issue holds, i.e. the value a refused write must leave untouched.
STORED_TITLE = "Fix login"
#: The authority of the tunnel client, written once: the socket it addresses and the authority
#: the browser reports in `Origin` are the same host and port, only the scheme differs.
TUNNEL_AUTHORITY = "127.0.0.1:7777"
HISTORY = (
    "SELECT h.field FROM issue_history h JOIN issue i ON i.id = h.issue_id"
    " WHERE i.identifier = ? ORDER BY h.id"
)
PATH_PARAM = re.compile(r"\{([^}]+)\}")
#: Values for the path parameters of the enumerated routes; the guard fires before routing, so
#: only a plausible shape matters, never a resolvable one.
PATH_VALUES = {"team": "ENG", "identifier": "ENG-1", "state_id": "01J0000000000000000000000A"}


def _run(*args: str) -> None:
    result = runner.invoke(cli_app, list(args))
    assert result.exit_code == 0, result.output


@pytest.fixture
def seeded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Isolated cwd + HIFA_DB with one team (ENG) and one issue, served three ways.

    `client` addresses the app as every other test of this directory does — no `Origin` and no
    `Sec-Fetch-Site`, which is the declared non-browser client. `tunnel` addresses the same
    database on `TUNNEL_AUTHORITY`, the authority a browser behind a cloudflared quick tunnel
    reports. `read_only` serves the same database with the instance's own write refusal.
    """
    project = tmp_path / "proj"
    project.mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("HIFA_DB", str(tmp_path / "hifa.db"))
    monkeypatch.setenv("HIFA_ME_EMAIL", "ginkelte@example.com")
    _run("init", "--team-key", "ENG")
    _run("issue", "create", STORED_TITLE, "--state", "Todo")
    db = tmp_path / "hifa.db"
    with sqlite3.connect(db) as conn:
        (identifier,) = conn.execute("SELECT identifier FROM issue").fetchone()
    return SimpleNamespace(
        db=db,
        identifier=identifier,
        client=TestClient(create_app(db, default_team="ENG")),
        tunnel=TestClient(
            create_app(db, default_team="ENG"), base_url=f"http://{TUNNEL_AUTHORITY}"
        ),
        read_only=TestClient(create_app(db, default_team="ENG", read_only=True)),
    )


def _title(db: Path, identifier: str) -> str:
    """The `title` of the issue, read from the database rather than from a write's answer."""
    with sqlite3.connect(db) as conn:
        row = conn.execute("SELECT title FROM issue WHERE identifier = ?", (identifier,)).fetchone()
    assert row is not None, f"{identifier} is not in the database"
    assert isinstance(row[0], str), f"{identifier} has a title that is not text: {row[0]!r}"
    return row[0]


def _history(db: Path, identifier: str) -> list[str]:
    """`field` of every history row of the issue, oldest first — `created` included.

    Read as a whole list rather than as a count: a refused write must add no row at all, and a
    served one must add exactly the row of the field it changed, whatever else the use case
    records beside it.
    """
    with sqlite3.connect(db) as conn:
        return [field for (field,) in conn.execute(HISTORY, (identifier,))]


def _assert_refusal(response: httpx.Response) -> None:
    """Pin the whole shape of the guard's answer, headers included.

    The security headers are asserted here because they are what `_headers` wrapping the guard
    buys: a `text/plain` answer without `nosniff` would be the only answer of this edge a browser
    may sniff, and the guard's own `Cache-Control: no-store` must not be overwritten by the
    default `_headers` supplies to an answer that has none.
    """
    assert response.status_code == 403, response.text
    assert response.text == REFUSAL, "the refusal is one sentence, not a page"
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["cache-control"] == "no-store"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"


def _patch_title(client: TestClient, identifier: str, headers: dict[str, str]) -> httpx.Response:
    """Perform the inline edit of a row: the one write that answers `200` or the guard's `403`."""
    return client.patch(
        f"/_/issues/{identifier}?view=list",
        headers=headers,
        data={"title": "Fix SSO login"},
    )


def test_a_cross_site_write_is_refused_and_writes_nothing(seeded: SimpleNamespace) -> None:
    """Pin that the whole contract of the refusal holds, and that the row did not move.

    Both headers of a foreign page are sent: `Sec-Fetch-Site: cross-site` is the browser's own
    statement, and `Origin` agrees with it. Nothing may be written, because the refusal is
    produced before the handler, so no use case and no transaction runs.
    """
    before = _history(seeded.db, seeded.identifier)

    response = _patch_title(seeded.client, seeded.identifier, {**HTMX, **CROSS_SITE})

    _assert_refusal(response)
    assert _title(seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier) == before


def test_a_same_origin_write_is_accepted(seeded: SimpleNamespace) -> None:
    """Pin that `Sec-Fetch-Site: same-origin` alone is served, so fetch metadata is read first.

    No `Origin` is sent: if the guard compared `Origin` before the fetch metadata, or demanded
    it, a legitimate same-origin write would be refused. The write must also land — a `200` that
    re-renders without committing is the failure this edge had before.
    """
    response = _patch_title(
        seeded.client, seeded.identifier, {**HTMX, "Sec-Fetch-Site": "same-origin"}
    )

    assert response.status_code == 200, response.text
    assert _title(seeded.db, seeded.identifier) == "Fix SSO login"
    assert _history(seeded.db, seeded.identifier).count("title") == 1


def test_the_fetch_metadata_decides_before_the_origin(seeded: SimpleNamespace) -> None:
    """Pin that `Sec-Fetch-Site` decides alone and `Origin` is only the fallback.

    The rule's table makes `same-origin` allowed and `cross-site` refused without qualification,
    and its prose says the fetch metadata "is read first; `Origin` is the fallback for the client
    that does not send it". The two headers are therefore made to disagree in both directions: a
    `same-origin` request carrying a foreign `Origin` must be served, and a `cross-site` one whose
    `Origin` names the served authority must be refused. A guard that consulted `Origin` first
    whenever it is present would invert both answers and stay green, because no other test of this
    file sends the two headers at once — which is exactly the mutation this test forecloses.
    """
    served_origin = f"{seeded.client.base_url.scheme}://{seeded.client.base_url.netloc}"

    allowed = _patch_title(
        seeded.client,
        seeded.identifier,
        {**HTMX, "Sec-Fetch-Site": "same-origin", "Origin": "https://evil.example"},
    )

    assert allowed.status_code == 200, allowed.text
    assert _title(seeded.db, seeded.identifier) == "Fix SSO login"
    accepted = _history(seeded.db, seeded.identifier)
    assert accepted.count("title") == 1

    refused = _patch_title(
        seeded.client,
        seeded.identifier,
        {**HTMX, "Sec-Fetch-Site": "cross-site", "Origin": served_origin},
    )

    _assert_refusal(refused)
    assert _title(seeded.db, seeded.identifier) == "Fix SSO login"
    assert _history(seeded.db, seeded.identifier) == accepted


def test_a_write_without_origin_information_is_accepted(seeded: SimpleNamespace) -> None:
    """Pin absence as allowed: the declared non-browser client of this edge.

    Neither header is sent — what the test client, an agent driving fragments and `curl` in the
    no-JS path all are. Refusing this would break every client this application is driven by, and
    there is no cookie, session or token for the absence to expose.
    """
    response = _patch_title(seeded.client, seeded.identifier, dict(HTMX))

    assert response.status_code == 200, response.text
    assert _title(seeded.db, seeded.identifier) == "Fix SSO login"


def test_a_cross_site_read_is_served(seeded: SimpleNamespace) -> None:
    """Pin that reads are not covered: the guard is about writes, which are sent and not read.

    The same foreign headers that are refused on a write are served on a `GET`, because the
    browser's same-origin policy already stops the attacker reading the answer and this edge
    emits no CORS header on any route, so nothing of it can be read from another origin.
    """
    for title in ("Avatar cache", "Empty trash"):
        _run("issue", "create", title, "--state", "Todo")

    response = seeded.client.get("/ENG/board", headers=CROSS_SITE)

    assert response.status_code == 200, response.text
    for title in (STORED_TITLE, "Avatar cache", "Empty trash"):
        assert title in response.text, title
    assert REFUSAL not in response.text


def test_the_guard_answers_before_the_route_runs(seeded: SimpleNamespace) -> None:
    """Pin the `403`-not-`404` order: a refusal cannot depend on the target resolving.

    An identifier that does not exist is written to. If the answer were `404`, the guard would
    provably not be in the way, and the answer would have begun to depend on the database — which
    the guard must be able to answer with the file locked.
    """
    response = seeded.client.post("/ENG/issue/ENG-999", headers=CROSS_SITE, data={"title": "ghost"})

    _assert_refusal(response)
    with sqlite3.connect(seeded.db) as conn:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM issue WHERE identifier = 'ENG-999'"
        ).fetchone()
    assert count == 0


def test_a_same_site_write_is_refused(seeded: SimpleNamespace) -> None:
    """Pin `same-site` as refused: one host and one port serve this edge.

    No page of it can be `same-site` to another page of it, so the value is necessarily a sibling
    origin — a different port or a different subdomain — which is exactly the caller the guard
    exists to deny.
    """
    before = _history(seeded.db, seeded.identifier)

    response = _patch_title(
        seeded.client,
        seeded.identifier,
        {**HTMX, "Origin": "https://hifa.example", "Sec-Fetch-Site": "same-site"},
    )

    _assert_refusal(response)
    assert _title(seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier) == before


def test_an_unknown_fetch_site_value_is_refused(seeded: SimpleNamespace) -> None:
    """Pin that a `Sec-Fetch-Site` value this edge does not know is refused, not trusted.

    Only `same-origin` and `none` name this edge. A browser that ships a value this code has never
    heard of must therefore be refused rather than read as permission: the guard refuses what it
    cannot classify, so a helper rewritten to allow anything outside `cross-site`/`same-site`
    fails here.
    """
    before = _history(seeded.db, seeded.identifier)

    response = _patch_title(
        seeded.client, seeded.identifier, {**HTMX, "Sec-Fetch-Site": "cross-origin-ish"}
    )

    _assert_refusal(response)
    assert _title(seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier) == before


def test_the_fetch_site_value_is_normalised_before_it_is_compared(
    seeded: SimpleNamespace,
) -> None:
    """Pin the normalisation: `SAME-ORIGIN` in another case is still this edge.

    Header values are case-insensitive in HTTP, so the guard strips and lowercases before it
    compares. Dropping that would refuse a legitimate write, and no other test of this file would
    notice, because every other one sends the value exactly as the constant spells it.
    """
    response = _patch_title(
        seeded.client, seeded.identifier, {**HTMX, "Sec-Fetch-Site": "SAME-ORIGIN"}
    )

    assert response.status_code == 200, response.text
    assert _title(seeded.db, seeded.identifier) == "Fix SSO login"
    assert _history(seeded.db, seeded.identifier).count("title") == 1


def test_an_origin_of_the_served_authority_is_accepted(seeded: SimpleNamespace) -> None:
    """Pin the `Origin` fallback and the tunnel case: only the authority is compared.

    The browser reports the same authority over `https` while the socket this process accepts is
    plain `http` — cloudflared terminates TLS and forwards. Comparing the scheme would refuse a
    legitimate write; the name and the port are what a foreign page cannot forge.
    """
    response = _patch_title(
        seeded.tunnel, seeded.identifier, {**HTMX, "Origin": f"https://{TUNNEL_AUTHORITY}"}
    )

    assert response.status_code == 200, response.text
    assert _title(seeded.db, seeded.identifier) == "Fix SSO login"


def test_an_origin_of_another_authority_is_refused(seeded: SimpleNamespace) -> None:
    """Pin the `Origin` fallback as a refusal, without any fetch metadata to lean on.

    This is the shape the older browser sends: one header, naming another authority. Nothing it
    names can be this edge, so the write is refused before the route, and nothing is written.
    """
    before = _history(seeded.db, seeded.identifier)

    response = _patch_title(
        seeded.client, seeded.identifier, {**HTMX, "Origin": "https://evil.example"}
    )

    _assert_refusal(response)
    assert _title(seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier) == before


def test_an_opaque_origin_is_refused(seeded: SimpleNamespace) -> None:
    """Pin `Origin: null` as refused: it names no authority, so it is neither foreign nor absent.

    A sandboxed frame, a `file://` document and a redirect that stripped the header all send it,
    and no page of this application produces it. Treating it as absent would open the guard's one
    hole to exactly the caller most likely to be hostile.
    """
    before = _history(seeded.db, seeded.identifier)

    response = _patch_title(seeded.client, seeded.identifier, {**HTMX, "Origin": "null"})

    _assert_refusal(response)
    assert _title(seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier) == before


def test_a_cross_site_write_on_a_read_only_instance_is_the_guards_refusal(
    seeded: SimpleNamespace,
) -> None:
    """Pin the guard answering first on a read-only instance: the caller outranks the instance.

    The two 403s differ by their message. A cross-site write must get the statement about the
    caller, never `server in read-only mode`, or the precedence of the caller over the instance
    would be unobservable from outside.
    """
    before = _history(seeded.db, seeded.identifier)

    response = _patch_title(seeded.read_only, seeded.identifier, {**HTMX, **CROSS_SITE})

    _assert_refusal(response)
    assert "read-only" not in response.text
    assert _title(seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier) == before


def test_read_only_keeps_its_own_403(seeded: SimpleNamespace) -> None:
    """Pin the other side of the order: the guard lets a same-origin write through to the route.

    On a read-only instance the route's inline `return` answers with the instance's own sentence.
    If the guard's text appeared here the two 403s would have been unified, and they are kept as
    two sentences precisely so a human can tell which one they are reading.
    """
    before = _history(seeded.db, seeded.identifier)

    response = _patch_title(
        seeded.read_only, seeded.identifier, {**HTMX, "Sec-Fetch-Site": "same-origin"}
    )

    assert response.status_code == 403, response.text
    assert "read-only" in response.text
    assert REFUSAL not in response.text
    assert _title(seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier) == before


def test_a_method_no_route_serves_is_guarded_too(seeded: SimpleNamespace) -> None:
    """Pin the method as the rule, not the route table: `PUT` and `DELETE` are covered.

    The guard covers every request whose method is outside `GET`/`HEAD`/`OPTIONS`, and a method
    no route serves yet (`PUT`, `DELETE`) with it. The route-coverage test below walks the
    registered routes, so it cannot reach a method this tree does not serve: a guard rewritten as
    an allow-list of the methods that exist today (`POST`, `PATCH`) would keep it green while a
    cross-site `PUT` answered `405` — routing reached, the guard not in the way. The path is
    served for reading, so a `405` here can only mean "the guard was bypassed". Nothing may move
    either way.
    """
    before = _history(seeded.db, seeded.identifier)

    for method in ("PUT", "DELETE"):
        response = seeded.client.request(method, "/ENG/board", headers=CROSS_SITE, content=b"")
        _assert_refusal(response)

    assert _title(seeded.db, seeded.identifier) == STORED_TITLE
    assert _history(seeded.db, seeded.identifier) == before


def _state_changing_routes(app: FastAPI) -> list[APIRoute]:
    """Every route the app answers with a state-changing method, however the routers nest.

    It walks the registration tree instead of a literal list, so the claim holds for routes that
    do not exist yet. FastAPI keeps an included router as one entry that owns its routes, and a
    `Mount` owns its own tree, so each level is entered rather than assumed flat. A route that
    carries `methods` is an `APIRoute`, which is the only shape this function returns.
    """

    def walk(routes: Sequence[BaseRoute]) -> list[APIRoute]:
        found: list[APIRoute] = []
        for route in routes:
            methods = getattr(route, "methods", None)
            if methods:
                if set(methods) - READ_METHODS:
                    found.append(cast(APIRoute, route))
                continue
            nested = getattr(route, "routes", None)
            if nested:
                found.extend(walk(nested))
                continue
            included = getattr(route, "original_router", None)
            if included is not None:
                found.extend(walk(included.routes))
        return found

    return walk(app.routes)


def test_every_state_changing_route_is_covered(seeded: SimpleNamespace) -> None:
    """Pin that no write route can be added without passing through the guard, from the app itself.

    The routes are enumerated from `app.routes` rather than copied from a list, because the claim
    is about routes that do not exist yet: a later phase adds eight, and each must be covered the
    moment it is registered. **Every** non-read method of a route is probed, so a route serving
    both `POST` and `PATCH` is covered twice and neither method can slip through. A route
    answering anything but the refusal — a `404`, a `422`, a `405` — means the request reached
    routing or validation, i.e. the guard was not in the way.
    """
    routes = _state_changing_routes(seeded.client.app)
    assert routes, "no state-changing route is registered: nothing to cover"
    assert all(route.path != "/{team}/board" for route in routes), "a read route was enumerated"

    answered: dict[str, httpx.Response] = {}
    for route in routes:
        path = PATH_PARAM.sub(lambda match: PATH_VALUES.get(match.group(1), "x"), route.path)
        for method in sorted(set(route.methods) - READ_METHODS):
            answered[f"{method} {path}"] = seeded.client.request(
                method, path, headers=CROSS_SITE, content=b""
            )

    for label, response in answered.items():
        assert response.status_code == 403, f"{label} answered {response.status_code}"
        assert response.text == REFUSAL, label
