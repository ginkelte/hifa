"""Purity of `domain/`: no infrastructure imports and no clock reads (REQ-F020-15, A2)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

DOMAIN_DIR = Path(__file__).resolve().parents[3] / "src" / "hifa" / "domain"
MODULES = sorted(DOMAIN_DIR.glob("*.py"))

FORBIDDEN_MODULES = {
    "sqlite3",
    "fastapi",
    "typer",
    "click",
    "jinja2",
    "httpx",
    "uvicorn",
    "os",
    "time",
    "random",
    "uuid",
    "ulid",
    "secrets",
}

FORBIDDEN_HIFA_MODULES = {
    "hifa.ports",
    "hifa.adapters",
    "hifa.app",
    "hifa.cli",
    "hifa.api",
    "hifa.web",
}

FORBIDDEN_CALLS = {"now", "utcnow", "today", "time", "uuid4", "urandom"}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_the_six_modules_and_init_are_present() -> None:
    names = {path.name for path in MODULES}
    assert {
        "__init__.py",
        "enums.py",
        "entities.py",
        "errors.py",
        "identifier.py",
        "ranking.py",
        "rules.py",
    } <= names


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_does_not_import_infrastructure(path: Path) -> None:
    imported: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    roots = {name.split(".")[0] for name in imported}
    assert not (roots & FORBIDDEN_MODULES), f"{path.name} importa {roots & FORBIDDEN_MODULES}"
    for name in imported:
        assert not any(
            name == forbidden or name.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN_HIFA_MODULES
        ), f"{path.name} importa {name}"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_does_not_read_the_clock_or_generate_ids(path: Path) -> None:
    offenders: list[str] = []
    for node in ast.walk(_tree(path)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name in FORBIDDEN_CALLS:
            offenders.append(name)
    assert not offenders, f"{path.name} llama a {sorted(set(offenders))}"


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_does_not_print_or_write(path: Path) -> None:
    offenders: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in {"print", "open", "write_text", "write_bytes"}:
                offenders.append(str(name))
    assert not offenders, f"{path.name} usa {sorted(set(offenders))}"


def test_domain_does_not_import_sqlite3_on_import() -> None:
    import subprocess
    import sys

    code = (
        "import sys, hifa.domain.entities, hifa.domain.rules;"
        "bad=[m for m in ('sqlite3','fastapi','typer','jinja2') if m in sys.modules];"
        "print(','.join(bad))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == ""
