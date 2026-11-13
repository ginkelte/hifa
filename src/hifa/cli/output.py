"""Single CLI output formatter: table / `--json` / `--quiet`.

No command prints on its own: it builds the object and calls `emit(obj)`. The formatting
rules live here and in no other module of the edge.

- `stdout`: data only. `stderr`: diagnosis only (used by `errors.py`).
- `--json`: one JSON document, `indent=2`, `ensure_ascii=False`, stable key order.
- `--quiet`: one line per record (ids), no header.
- Default: simple table with an UPPERCASE header and two spaces between columns.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import typer
from pydantic import BaseModel

from hifa.app.teams import DEFAULT_LABELS, DEFAULT_STATES, BootstrapResult

MAX_TABLE_WIDTH = 120


@dataclass(frozen=True)
class OutputOptions:
    """Effective format of the invocation (global + leaf flags)."""

    json: bool = False
    quiet: bool = False
    color: bool = False


_DEFAULT = OutputOptions()
_CURRENT: ContextVar[OutputOptions] = ContextVar("hifa_output_options", default=_DEFAULT)


def configure(*, json_mode: bool = False, quiet: bool = False, color: bool = False) -> None:
    """Set the format of the current invocation (called by the command, not the formatter)."""
    _CURRENT.set(OutputOptions(json=json_mode, quiet=quiet, color=color))


def options() -> OutputOptions:
    """Current format; read by `emit` and `errors.emit_error`."""
    return _CURRENT.get()


# --- serialization ---------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def write_json(payload: Any) -> None:
    """One JSON document to `stdout` with `indent=2` and a trailing newline."""
    typer.echo(json.dumps(_jsonable(payload), indent=2, ensure_ascii=False))


def write_line(text: str = "") -> None:
    """Data line to `stdout`."""
    typer.echo(text)


def write_note(text: str) -> None:
    """Diagnostic line to `stderr`: it never shares the stream that carries data."""
    typer.echo(text, err=True)


# --- table -----------------------------------------------------------------------------

#: Columns whose content is truncated.
_TRUNCATING_COLUMNS = frozenset({"TITLE"})


def _cell(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return ", ".join(_cell(item) for item in value) if value else "–"
    text = str(value)
    return text if text else "–"


def _terminal_width() -> int:
    try:
        width = sys.stdout.isatty() and typer.get_terminal_size()[0] or 120
    except (OSError, ValueError):
        width = 120
    return min(width, MAX_TABLE_WIDTH)


def render_table(rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> str:
    """Text table: UPPERCASE header, two spaces, numerics right-aligned."""
    headers = [column.upper() for column in columns]
    body = [[_cell(row.get(column)) for column in columns] for row in rows]

    widths = [len(header) for header in headers]
    for line in body:
        for index, value in enumerate(line):
            widths[index] = max(widths[index], len(value))

    # Maximum width: only TITLE is truncated with `…` (U+2026).
    while sum(widths) + 2 * (len(widths) - 1) > _terminal_width():
        candidates = [
            index for index, column in enumerate(columns) if column.upper() in _TRUNCATING_COLUMNS
        ]
        if not candidates:
            break
        index = max(candidates, key=lambda candidate: widths[candidate])
        if widths[index] <= len(headers[index]):
            break
        widths[index] -= 1
        for line in body:
            if len(line[index]) > widths[index]:
                line[index] = line[index][: widths[index] - 1] + "…"

    def format_row(values: Sequence[str]) -> str:
        cells: list[str] = []
        for index, value in enumerate(values):
            if index < len(values) - 1 and _is_numeric_row_value(value):
                cells.append(value.rjust(widths[index]))
            elif index < len(values) - 1:
                cells.append(value.ljust(widths[index]))
            else:
                cells.append(value)
        return "  ".join(cells).rstrip()

    lines = [format_row(headers)]
    lines.extend(format_row(line) for line in body)
    return "\n".join(lines)


def _is_numeric_row_value(value: str) -> bool:
    return value.replace(",", "").replace(".", "", 1).isdigit()


# --- init ------------------------------------------------------------------------------


def _bootstrap_lines(result: BootstrapResult, meta: Mapping[str, Any]) -> list[str]:
    team_key = meta.get("team_key", "")
    if result.already_initialized:
        lines = [
            f"hifa is already initialized at {result.db_path}",
            f"  workspace {meta.get('workspace_name', '')} · team {team_key}"
            f" · {len(result.created)} changes",
        ]
        return lines

    labels = meta.get("labels")
    states = meta.get("states")
    member = meta.get("member")
    lines = [
        f"initialized hifa at {result.db_path}",
        f"  workspace  {meta.get('workspace_name', '')} ({meta.get('url_key', '')})",
        f"  team       {team_key} — {meta.get('team_name', '')} {meta.get('icon', '')}".rstrip(),
        f"  states     {', '.join(states) if states else '—'}",
        f"  labels     {', '.join(labels) if labels else '—'}",
        f"  member     {member if member else '—'}",
        f"  config     {meta.get('config', '—')}",
        f'next: hifa issue create "My first issue" --team {team_key}',
    ]
    return lines


def _emit_bootstrap(result: BootstrapResult, meta: Mapping[str, Any] | None) -> None:
    current = options()
    if current.json:
        write_json(result.to_dict())
        return
    if current.quiet:
        write_line(result.workspace_id)
        write_line(result.team_id)
        if result.member_id:
            write_line(result.member_id)
        return
    default_meta: dict[str, Any] = {
        "workspace_name": "",
        "url_key": "",
        "team_key": "",
        "team_name": "",
        "icon": "",
        "states": [state[0] for state in DEFAULT_STATES] if not result.already_initialized else [],
        "labels": [label[0] for label in DEFAULT_LABELS] if not result.already_initialized else [],
        "member": None,
        "config": "—",
    }
    default_meta.update(dict(meta or {}))
    for line in _bootstrap_lines(result, default_meta):
        write_line(line)


# --- dispatch --------------------------------------------------------------------------


def emit(
    obj: Any,
    *,
    table_cols: Sequence[str] | None = None,
    meta: Mapping[str, Any] | None = None,
    json_obj: Any = None,
    quiet_obj: Any = None,
) -> None:
    """Format `obj` for the active output mode. The only data-writing entry point.

    `json_obj` overrides the payload for `--json` (e.g. an issue row is rendered as a
    table but emitted as the full canonical `Issue` object). `quiet_obj` overrides what
    `--quiet` prints (a str, or a sequence of one value per row): `team --quiet` promises the
    team **key**, while the canonical object would hand `_quiet_id` a ULID first.
    """
    if isinstance(obj, BootstrapResult):
        _emit_bootstrap(obj, meta)
        return

    current = options()
    if isinstance(obj, (list, tuple)):
        items = list(obj)
        if current.json:
            # A list may still declare an envelope (`{items: [...]}`); ignoring it here silently
            # emitted a bare array and broke the envelope shape for every list command.
            write_json(json_obj if json_obj is not None else [_jsonable(item) for item in items])
            return
        if current.quiet:
            values = list(quiet_obj) if quiet_obj is not None else items
            for value in values:
                write_line(_quiet_text(value))
            return
        if not items:
            return
        columns = list(table_cols or _columns_for(items[0]))
        rows = [_row_dict(item) for item in items]
        write_line(render_table(rows, columns))
        return

    payload = _row_dict(obj)
    if current.json:
        write_json(_jsonable(obj if json_obj is None else json_obj))
        return
    if current.quiet:
        write_line(_quiet_text(obj if quiet_obj is None else quiet_obj))
        return
    columns = list(table_cols or list(payload))
    if table_cols is None and len(columns) > 4:
        # Key-value block for wide entities (`issue show`). The column is
        # sized from the keys so a 10-character key (`visibility`) is not glued to its value.
        width = max(len(str(key)) for key in columns) + 2
        for key in columns:
            write_line(f"{key:<{width}}{_cell(payload.get(key))}")
        return
    write_line(render_table([payload], columns))


def _row_dict(obj: Any) -> dict[str, Any]:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="python")
    if isinstance(obj, Mapping):
        return dict(obj)
    raise TypeError(f"emit() does not know how to format {type(obj).__name__}")


def _columns_for(obj: Any) -> list[str]:
    if isinstance(obj, BaseModel):
        return list(type(obj).model_fields)
    if isinstance(obj, Mapping):
        return [str(key) for key in obj]
    raise TypeError(f"emit() does not know how to infer columns from {type(obj).__name__}")


def _quiet_text(value: Any) -> str:
    """One `--quiet` value: a bare string/identifier prints itself, anything else goes
    through `_quiet_id`."""
    if isinstance(value, str):
        return value
    return str(_quiet_id(value))


def _quiet_id(obj: Any) -> str:
    if isinstance(obj, BaseModel):
        for attribute in ("identifier", "id", "key", "name"):
            value = getattr(obj, attribute, None)
            if value:
                return str(value)
        return ""
    if isinstance(obj, Mapping):
        for key in ("identifier", "id", "key", "name"):
            if obj.get(key):
                return str(obj[key])
    return str(obj)


__all__ = [
    "MAX_TABLE_WIDTH",
    "OutputOptions",
    "configure",
    "emit",
    "options",
    "render_table",
    "write_json",
    "write_line",
    "write_note",
]
