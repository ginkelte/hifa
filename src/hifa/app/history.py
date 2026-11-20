"""The one formatter of `issue_history` rows: labels, sentences and the page they arrive in.

Every edge reads a history row through `history_rows`, so the wording of a sentence exists once,
here, and no route, template or CLI command assembles one of its own. The function resolves
nothing: the maps it receives are the labels the read already looked up, and the word maps it
owns — the priority labels, the due-date shape, the month names and the "how long ago" grammar —
are constants. `relative_time` is public for the same reason: a comment's stamp and the detail's
`updated_label` are the same sentence about the same instant, and the edge that printed its own
would be the way two surfaces started disagreeing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from hifa.domain.entities import IssueHistory

#: The five priority labels a `priority` row renders: a closed 0-4, so this map is total.
PRIORITY_LABELS: Final[Mapping[int, str]] = {
    0: "No priority",
    1: "Urgent",
    2: "High",
    3: "Medium",
    4: "Low",
}

#: The one date shape a `due_date` row renders.
DUE_DATE_FORMAT: Final[str] = "%d %b %Y"

#: The read's window: a page with this many rows may have older ones the read never fetched.
WINDOW_SIZE: Final[int] = 100

#: The word an actor whose member is gone (or absent) renders as.
SOMEONE: Final[str] = "someone"

#: The twelve month names the timeline prints. A constant, not `strftime`'s locale: a row's stamp
#: reads the same on a machine with no locale set (SPEC-070 §2.5.7.2's day separators print it too).
MONTHS: Final[tuple[str, ...]] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

#: `relation` values are stored as "<type as this issue reads it> <identifier>"; the two columns are
#: the wording the sentence builds from the type. A type outside the map has no sentence and the
#: row falls back to its field name.
_RELATION_PARTICIPLE: Final[Mapping[str, str]] = {
    "blocks": "blocking",
    "blocked_by": "blocked by",
    "duplicate": "a duplicate of",
    "related": "related to",
    "similar": "similar to",
}
_RELATION_ADJECTIVE: Final[Mapping[str, str]] = {
    "blocks": "blocking",
    "blocked_by": "blocked-by",
    "duplicate": "duplicate",
    "related": "related",
    "similar": "similar",
}


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """One event of an issue, resolved for a reader: the stored pair plus the rendered strings."""

    id: int
    field: str
    old_value: str | None
    new_value: str | None
    old_label: str | None
    new_label: str | None
    actor_id: str | None
    actor_label: str
    sentence: str
    dot_color: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class LastActivity:
    """The one-line summary under the title: who last touched the issue, what and when."""

    text: str
    row_id: int


@dataclass(frozen=True, slots=True)
class HistoryPage:
    """The formatter's answer: the window oldest-first, its size, and whether it is full."""

    rows: list[HistoryRow]
    window_size: int
    truncated: bool
    last_activity: LastActivity | None


def wire_instant(moment: datetime) -> str:
    """`2026-11-11T18:35:35Z` — seconds, UTC, `Z`; the one rendering of an instant on the wire."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def history_rows(
    rows: Sequence[IssueHistory],
    *,
    states: Mapping[str, str],
    members: Mapping[str, str],
    parents: Mapping[str, str] | None = None,
    priorities: Mapping[int, str] = PRIORITY_LABELS,
    due_dates: Mapping[str, str] | None = None,
    state_colors: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> HistoryPage:
    """Sort the window oldest-first and answer it with every rendering a reader needs.

    `states`, `members` and `parents` are the read's maps; `priorities` and `due_dates` are word
    maps this module owns and cannot miss. `state_colors` and `now` are optional and change
    nothing about the rows: a dot colour needs the state's colour, which the name map does not
    carry, and the summary's relative stamp needs the edge's clock. An unknown field, an
    unresolvable value and a missing actor are answers, never refusals.
    """
    ordered = sorted(rows, key=lambda row: (row.created_at, row.id))
    dates = due_dates if due_dates is not None else _due_dates(ordered)
    colours = state_colors or {}
    parent_map = parents or {}

    page: list[HistoryRow] = []
    for entry in ordered:
        old_label = _label(
            entry.field, entry.old_value, states, members, parent_map, priorities, dates
        )
        new_label = _label(
            entry.field, entry.new_value, states, members, parent_map, priorities, dates
        )
        sentence = _sentence(entry, old_label, new_label) or f"changed {entry.field}"
        page.append(
            HistoryRow(
                id=entry.id,
                field=entry.field,
                old_value=entry.old_value,
                new_value=entry.new_value,
                old_label=old_label,
                new_label=new_label,
                actor_id=entry.actor_id,
                actor_label=(members.get(entry.actor_id) if entry.actor_id else None) or SOMEONE,
                sentence=sentence,
                dot_color=_dot(entry, colours),
                created_at=entry.created_at,
            )
        )

    return HistoryPage(
        rows=page,
        window_size=len(page),
        truncated=len(page) == WINDOW_SIZE,
        last_activity=_last_activity(page, now),
    )


# --- labels ------------------------------------------------------------------------------


def _label(
    field: str,
    value: str | None,
    states: Mapping[str, str],
    members: Mapping[str, str],
    parents: Mapping[str, str],
    priorities: Mapping[int, str],
    due_dates: Mapping[str, str],
) -> str | None:
    """The printable side of a row, or `None` when the field names no target."""
    if value is None:
        return None
    if field == "state_id":
        return states.get(value) or "state (deleted)"
    if field == "assignee_id":
        return members.get(value) or "member (deleted)"
    if field == "parent_id":
        return parents.get(value) or "issue (deleted)"
    if field == "priority":
        try:
            return priorities.get(int(value))
        except ValueError:
            return None
    if field == "due_date":
        return due_dates.get(value)
    if field == "estimate":
        return _trim(value)
    if field in {"title", "branch_name", "relation"}:
        return value
    return None


def _dot(entry: IssueHistory, colours: Mapping[str, str]) -> str | None:
    """The state's colour on a `state_id` row; `None` for every other field."""
    if entry.field != "state_id":
        return None
    return colours.get(entry.new_value or "") or colours.get(entry.old_value or "")


# --- sentences ---------------------------------------------------------------------------


def _sentence(entry: IssueHistory, old: str | None, new: str | None) -> str | None:
    """The wording of one row, or `None` when the field has no sentence for this direction."""
    field = entry.field
    if field == "created":
        return "created the issue"
    if field == "description":
        return "edited the description"
    if field == "branch_name":
        return "changed the branch name"
    if field == "board_order":
        return "reordered it in the column"
    if field == "state_id":
        if old is not None and new is not None:
            return f"moved from {old} to {new}"
        return None
    if field == "title":
        return None if new is None else f"changed the title to \u201c{new}\u201d"
    if field == "priority":
        if new is not None and old is None:
            return f"set priority to {new}"
        if old is not None and new is None:
            return "cleared the priority"
        if old is not None and new is not None:
            return f"changed the priority from {old} to {new}"
        return None
    if field == "assignee_id":
        if new is not None and old is None:
            return f"assigned to {new}"
        if old is not None and new is None:
            return "unassigned"
        if old is not None and new is not None:
            return f"reassigned from {old} to {new}"
        return None
    if field == "parent_id":
        if new is not None and old is None:
            return f"moved under {new}"
        if old is not None and new is None:
            return f"removed it from {old}"
        if old is not None and new is not None:
            return f"moved it from {old} to {new}"
        return None
    if field == "label":
        if entry.new_value is not None and entry.old_value is None:
            return f"added the label {entry.new_value}"
        if entry.old_value is not None and entry.new_value is None:
            return f"removed the label {entry.old_value}"
        return None
    if field == "relation":
        return _relation_sentence(old, new)
    if field == "estimate":
        if new is not None and old is None:
            return f"set the estimate to {new}"
        if old is not None and new is None:
            return "cleared the estimate"
        return None
    if field == "due_date":
        if new is not None and old is None:
            return f"set the due date to {new}"
        if old is not None and new is None:
            return "removed the due date"
        if old is not None and new is not None:
            return f"moved the due date from {old} to {new}"
        return None
    if field == "started_at":
        return "started work" if entry.new_value and not entry.old_value else None
    if field == "completed_at":
        if entry.new_value and not entry.old_value:
            return "completed it"
        if entry.old_value and not entry.new_value:
            return "cleared the completion"
        return None
    if field == "canceled_at":
        if entry.new_value and not entry.old_value:
            return "canceled it"
        if entry.old_value and not entry.new_value:
            return "cleared the cancellation"
        return None
    if field == "archived_at":
        if entry.new_value and not entry.old_value:
            return "archived the issue"
        if entry.old_value and not entry.new_value:
            return "restored the issue"
        return None
    if field == "trashed":
        if entry.old_value == "0" and entry.new_value == "1":
            return "deleted the issue"
        if entry.old_value == "1" and entry.new_value == "0":
            return "restored it from the trash"
        return None
    if field == "project_id":
        if new is not None and not entry.old_value:
            return f"moved to project {new}"
        if old is not None and not entry.new_value:
            return f"removed it from project {old}"
        return None
    if field == "milestone_id":
        if new is not None and not entry.old_value:
            return f"set milestone {new}"
        if old is not None and not entry.new_value:
            return f"removed it from milestone {old}"
        return None
    if field == "cycle_id":
        if new is not None and not entry.old_value:
            return f"added to cycle {new}"
        if old is not None and not entry.new_value:
            return f"removed it from cycle {old}"
        return None
    return None


def _relation_sentence(old: str | None, new: str | None) -> str | None:
    """`marked it as blocking HIFA-33` / `removed the blocking relation to HIFA-33`."""
    if new is not None and old is None:
        kind, _, identifier = new.partition(" ")
        participle = _RELATION_PARTICIPLE.get(kind)
        return None if participle is None else f"marked it as {participle} {identifier}"
    if old is not None and new is None:
        kind, _, identifier = old.partition(" ")
        adjective = _RELATION_ADJECTIVE.get(kind)
        return None if adjective is None else f"removed the {adjective} relation to {identifier}"
    return None


# --- summary -----------------------------------------------------------------------------


def _last_activity(page: list[HistoryRow], now: datetime | None) -> LastActivity | None:
    """The newest row as the line under the title; `now` adds how long ago it happened."""
    if not page:
        return None
    newest = page[-1]
    text = f"{newest.actor_label} {newest.sentence}"
    if now is not None:
        text = f"{text} \u00b7 {relative_time(newest.created_at, now)}"
    return LastActivity(text=text, row_id=newest.id)


def relative_time(moment: datetime, now: datetime) -> str:
    """`just now`, `2 m ago`, `2 h ago`, `3 d ago`, and an absolute stamp past a month.

    The one grammar of "how long ago" in the tree: the summary under the title, a comment's stamp
    and the detail's `updated_label` all read it, so an edge that printed its own would be the way
    two surfaces started disagreeing about the same instant.
    """
    seconds = int((now - moment).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} m ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    if seconds < 86400 * 30:
        return f"{seconds // 86400} d ago"
    return f"{MONTHS[moment.month - 1]} {moment.day} {moment.hour:02d}:{moment.minute:02d}"


# --- word maps ---------------------------------------------------------------------------


def _due_dates(rows: Sequence[IssueHistory]) -> dict[str, str]:
    """`2026-11-20` → `20 Nov 2026` for exactly the due dates the window carries."""
    wanted = {row.old_value for row in rows if row.field == "due_date"} | {
        row.new_value for row in rows if row.field == "due_date"
    }
    rendered: dict[str, str] = {}
    for value in wanted:
        if not value:
            continue
        try:
            moment = datetime.strptime(value, "%Y-%m-%d")
        except ValueError:
            continue
        rendered[value] = moment.strftime(DUE_DATE_FORMAT)
    return rendered


def _trim(value: str) -> str:
    """`3.0` → `3`: an estimate is a whole scale label, so a trailing `.0` is the only thing cut."""
    return value[:-2] if value.endswith(".0") else value


__all__ = [
    "DUE_DATE_FORMAT",
    "MONTHS",
    "PRIORITY_LABELS",
    "HistoryPage",
    "HistoryRow",
    "LastActivity",
    "history_rows",
    "relative_time",
    "wire_instant",
]
