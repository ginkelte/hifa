"""Version contract (SPEC-110 §2.1/§2.2): one literal, one metadata read, one printed line.

The design reverted by D-039 is what these tests now forbid: no `dynamic = ["version"]`, no
git-derived version backend in `[build-system].requires`, no `python-semantic-release` dev row
and no `[tool.semantic_release]`. What remains is a single literal in `pyproject.toml` that only
the release pipeline writes, and `__version__` read from the installed metadata.
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
PACKAGE_JSON = REPO_ROOT / "package.json"

# §2.1: the one product version literal, and its exact spelling (column 0, double quotes, one
# space on each side of `=`) — the `sed` of §2.5 matches that shape literally.
VERSION_LINE = re.compile(r'^version = "([0-9]+\.[0-9]+\.[0-9]+)"$', re.MULTILINE)
# §2.1.1: the *uniqueness* check counts every `version = "..."` line, not only the ones whose
# value already has the release shape — a line with a typo must not hide behind the regex above.
ANY_VERSION_LINE = re.compile(r'^version = "([^"]*)"$', re.MULTILINE)
# §2.2: a release version is three numeric components; the only other allowed value is the
# documented fallback. No `v` prefix, no dev/local suffix: the value is a literal, not a describe.
RELEASE_SHAPE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
FALLBACK = "0.0.0+unknown"
QUOTED_RELEASE_LITERAL = re.compile(r'"\d+\.\d+\.\d+"')
# §2.1: both git-derived version backends left `requires`, and so did their switch.
FORBIDDEN_BUILD_REQUIRES = ("hatch-vcs", "setuptools-scm", "setuptools_scm")


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


def test_version_matches_the_shape_contract() -> None:
    """§2.2: `X.Y.Z` (no `v`, no space, no second line) or exactly the documented fallback."""
    import hifa

    value = hifa.__version__
    assert RELEASE_SHAPE.fullmatch(value) or value == FALLBACK, value
    assert not value.startswith("v")
    assert value == value.strip()
    assert "\n" not in value


def test_version_is_read_from_the_installed_metadata() -> None:
    """§2.2: the value is fixed at install time and read, never recomputed from git."""
    import hifa

    assert hifa.__version__ == importlib.metadata.version("hifa")


def test_installed_metadata_is_a_release_version_not_a_git_describe() -> None:
    """§2.1/§2.2: the revert in one assertion — no `0.1.devN+g<sha>` shape survives.

    The metadata is not compared with the literal: §2.2 accepts a version that lags HEAD (a
    checkout installed before the last release commit still reports the previous one). What the
    revert forbids is a *computed* version, and that is what this checks.
    """
    reported = importlib.metadata.version("hifa")
    literal = VERSION_LINE.search(PYPROJECT.read_text(encoding="utf-8"))

    assert literal is not None, 'pyproject.toml carries no `version = "X.Y.Z"` literal'
    assert RELEASE_SHAPE.fullmatch(reported) or reported == FALLBACK, reported
    assert "+" not in reported or reported == FALLBACK, f"a local/dev suffix is back: {reported}"
    assert ".dev" not in reported, f"a git-derived dev version is back: {reported}"


def test_fallback_when_the_metadata_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _init_without_metadata(monkeypatch)

    assert module.__version__ == FALLBACK
    assert RELEASE_SHAPE.fullmatch(module.__version__) or module.__version__ == FALLBACK


def test_package_init_keeps_the_metadata_read_and_the_documented_fallback() -> None:
    """§2.1: `src/hifa/__init__.py` is unchanged — no literal ever lives in Python."""
    source = INIT.read_text(encoding="utf-8")

    assert "importlib.metadata" in source
    assert source.count(f'"{FALLBACK}"') == 1, "the documented fallback must appear exactly once"
    assert QUOTED_RELEASE_LITERAL.search(source) is None, "a release literal is back in the package"


def test_pyproject_carries_exactly_one_version_literal() -> None:
    """§2.1.1: exactly one `version = "..."` line, and it is the value `tomllib` parses.

    The value itself is never asserted. The release rewrites this literal inside a commit that
    enters `main` with `[skip ci]`, so no CI run ever sees the change: a test pinned to the number
    turns `main` red on the first successful release, and the required `test` check then blocks
    every later pull request. Shape and uniqueness survive every release; a number does not.
    """
    text = PYPROJECT.read_text(encoding="utf-8")
    data = _pyproject()
    parsed = data["project"]["version"]

    matches = ANY_VERSION_LINE.findall(text)
    assert len(matches) == 1, f'exactly one `version = "..."` line is expected, got {matches}'
    assert matches[0] == parsed, "the single line and `[project].version` disagree"
    assert RELEASE_SHAPE.fullmatch(parsed) or parsed == FALLBACK, parsed
    assert "dynamic" not in data["project"], '`dynamic = ["version"]` is back in [project]'


def test_the_version_machinery_is_hatchling_and_nothing_else() -> None:
    """§2.1: build-system, hatch hook and the release tool block, in one assertion.

    Merged from three tests that asserted the same object: what matters is that no git-derived
    backend is reachable and that no release tool writes a second version source.
    """
    data = _pyproject()

    assert data["build-system"] == {
        "requires": ["hatchling"],
        "build-backend": "hatchling.build",
    }, data["build-system"]
    requires = " ".join(data["build-system"]["requires"])
    assert not any(name in requires for name in FORBIDDEN_BUILD_REQUIRES), requires

    assert "dynamic" not in data["project"], '`dynamic = ["version"]` is back'
    tool = data.get("tool", {})
    assert "version" not in tool.get("hatch", {}), "[tool.hatch.version] is back"
    hooks = tool.get("hatch", {}).get("build", {}).get("hooks", {})
    assert "version-file" not in hooks.get("vcs", {}), "the tree must not carry generated files"
    assert "semantic_release" not in tool, "[tool.semantic_release] is back"


def test_release_tooling_never_reaches_the_runtime_or_the_python_build() -> None:
    """§2.8 / REQ-N110-03: the engine is npm-only; no PSR row, no runtime dependency."""
    data = _pyproject()

    dev = data.get("dependency-groups", {}).get("dev", [])
    assert not any(req.startswith("python-semantic-release") for req in dev), dev
    runtime = " ".join(
        list(data["project"]["dependencies"])
        + [
            req
            for extra in data["project"].get("optional-dependencies", {}).values()
            for req in extra
        ]
    )
    assert "semantic" not in runtime
    assert "hatch-vcs" not in runtime


def test_cli_prints_the_version_line() -> None:
    """§2.2: one `hifa <version>` line on stdout, exit 0, nothing recomputed."""
    from typer.testing import CliRunner

    from hifa import __version__
    from hifa.cli.main import app

    result = CliRunner().invoke(app, ["--version"])

    assert result.exit_code == 0, result.output
    assert result.output.strip().splitlines() == [f"hifa {__version__}"]
    assert result.output.strip().splitlines()[0].split(" ")[0] == "hifa"
