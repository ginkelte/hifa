"""Shared fixtures for the suite."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Path to a fresh SQLite database (the file does not exist yet)."""
    return tmp_path / "hifa.db"


@pytest.fixture
def migrations_dir() -> Path:
    """Migrations directory of the installed package (or of the repo in editable mode)."""
    path = SRC / "hifa" / "adapters" / "sqlite" / "migrations"
    assert path.is_dir(), f"migrations are missing at {path}"
    return path
