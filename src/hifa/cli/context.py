"""CLI invocation context (SPEC-050 §2.2).

Transport only: the global options already parsed. It resolves nothing — that is
`hifa.config` — and it formats nothing — that is `hifa.cli.output`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CliContext:
    """Opciones globales (`--db`, `--json`, `--quiet`, `--no-color`)."""

    db: str | None = None
    json: bool = False
    quiet: bool = False
    color: bool = True


__all__ = ["CliContext"]
