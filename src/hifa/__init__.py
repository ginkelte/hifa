"""hifa — local issue tracker (CLI + SQLite + dashboard)."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__: str = version("hifa")
except PackageNotFoundError:  # source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
