"""Version contract (SPEC-110 §2.1/§2.2): derived from metadata, shape-checked, no literal.

The tag is the single source of version, so nothing here asserts a release number: the tests
assert the *shape* of whatever the installation reports, the documented fallback when the
distribution metadata is absent, and the absence of a release literal in the source tree.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
import re
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT = REPO_ROOT / "src" / "hifa" / "__init__.py"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# §2.2: the patch component is optional, because a tag-free history yields `0.1.devN+g<sha>`.
VERSION_SHAPE = re.compile(r"^[0-9]+\.[0-9]+(\.[0-9]+)?([.+-][0-9A-Za-z.+-]+)?$")
# §2.1: a quoted `X.Y.Z` is a release literal; the fallback `0.0.0+unknown` is not one.
QUOTED_RELEASE_LITERAL = re.compile(r'"\d+\.\d+\.\d+"')
FALLBACK = "0.0.0+unknown"


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _init_without_metadata(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Execute `src/hifa/__init__.py` as if the distribution were not installed."""

    def _not_installed(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _not_installed)
    spec = importlib.util.spec_from_file_location("hifa_version_fallback_probe", INIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_symbol_exists() -> None:
    import hifa

    assert isinstance(hifa.__version__, str)
    assert hifa.__version__ != ""
    assert "__version__" in hifa.__all__


def test_version_matches_the_shape_contract() -> None:
    import hifa

    assert VERSION_SHAPE.fullmatch(hifa.__version__), hifa.__version__
    assert not hifa.__version__.startswith("v")
    assert hifa.__version__ == hifa.__version__.strip()
    assert "\n" not in hifa.__version__


def test_version_is_read_from_the_installed_metadata() -> None:
    """§2.2: the value is fixed at install time and read, never recomputed from git."""
    import hifa

    assert hifa.__version__ == importlib.metadata.version("hifa")


def test_fallback_when_the_metadata_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _init_without_metadata(monkeypatch)

    assert module.__version__ == FALLBACK
    assert VERSION_SHAPE.fullmatch(module.__version__), module.__version__


def test_source_tree_carries_no_release_literal() -> None:
    source = INIT.read_text(encoding="utf-8")

    assert QUOTED_RELEASE_LITERAL.search(source) is None, "a release literal is back in the package"
    assert source.count(f'"{FALLBACK}"') == 1, "the documented fallback must appear exactly once"
    assert "importlib.metadata" in source


def test_pyproject_declares_a_vcs_dynamic_version() -> None:
    data = _pyproject()

    assert "version" not in data["project"], "a literal version is back in [project]"
    assert data["project"]["dynamic"] == ["version"]
    assert data["tool"]["hatch"]["version"]["source"] == "vcs"
    hooks = data["tool"].get("hatch", {}).get("build", {}).get("hooks", {})
    assert "version-file" not in hooks.get("vcs", {}), "the tree must not carry generated files"


def test_release_tooling_is_build_and_dev_only() -> None:
    """§2.8 / REQ-N110-03: PSR is exact-pinned in the dev group and absent at runtime."""
    data = _pyproject()

    assert any("hatch-vcs" in req for req in data["build-system"]["requires"])
    dev = data["dependency-groups"]["dev"]
    pins = [req for req in dev if req.startswith("python-semantic-release")]
    assert len(pins) == 1, pins
    assert "==" in pins[0], f"the parser pin must be exact, not a range: {pins[0]}"
    assert not any(marker in pins[0] for marker in ("<", ">", "~")), pins[0]
    runtime = " ".join(data["project"]["dependencies"])
    assert "semantic" not in runtime


def test_cli_prints_the_version_line() -> None:
    """§2.2: one `hifa <version>` line on stdout, exit 0, nothing recomputed."""
    from typer.testing import CliRunner

    from hifa import __version__
    from hifa.cli.main import app

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.output.strip().splitlines() == [f"hifa {__version__}"]
