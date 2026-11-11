"""Smoke: the package imports, the entrypoint exists and the import contracts hold."""

from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

MODULES = [
    "hifa",
    "hifa.config",
    "hifa.ports",
    "hifa.domain",
    "hifa.domain.enums",
    "hifa.domain.entities",
    "hifa.domain.ranking",
    "hifa.domain.identifier",
    "hifa.domain.rules",
    "hifa.domain.errors",
    "hifa.app",
    "hifa.app.commands",
    "hifa.app.retry",
    "hifa.adapters",
    "hifa.adapters.sqlite",
    "hifa.adapters.sqlite.connection",
    "hifa.adapters.sqlite.uow",
    "hifa.adapters.sqlite.migrate",
    "hifa.cli",
    "hifa.cli.main",
    "hifa.cli.output",
    "hifa.cli.errors",
    "hifa.api",
    "hifa.web",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module: str) -> None:
    assert importlib.import_module(module) is not None


def test_version() -> None:
    import hifa

    assert hifa.__version__


def test_entrypoint_is_typer_app() -> None:
    import typer

    from hifa.cli.main import app

    assert isinstance(app, typer.Typer)


def test_core_does_not_import_web_stack() -> None:
    """A5: importing the core and the CLI does not pull in fastapi/uvicorn/jinja2."""
    code = (
        "import sys, hifa.cli.main, hifa.adapters.sqlite.migrate;"
        "malos=[m for m in ('fastapi','uvicorn','jinja2') if m in sys.modules];"
        "print(','.join(malos))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout.strip()
    assert out == "", f"the core imported {out}"


def test_import_contracts_pass() -> None:
    """SPEC-000 A1/A2: the `.importlinter` contracts have no violations."""
    if shutil.which("lint-imports") is None:
        pytest.skip("import-linter is not installed")
    result = subprocess.run(["lint-imports"], cwd=REPO_ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_spec_contract_check_runs() -> None:
    """The drift checker runs with no database (T-01: no migrations applied yet)."""
    result = subprocess.run(
        [sys.executable, "scripts/spec_contract_check.py", "--all"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
