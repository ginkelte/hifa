"""Label use cases: the catalogue and the two issue links.

The catalogue owns four writes and two reads — `create_label`, `update_label`, `retire_label`,
`restore_label`, `list_labels` and `label_detail` — and the `label` table has no other writer
(an issue verb creates a label only under `create_if_missing`). The two issue verbs, `add_label`
and `remove_label`, own every `issue_label` row written after creation: attaching is idempotent,
a `singleSelect` group replaces the sibling the issue carried, and detaching one link never
touches a sibling.

Three reference scopes, and no fourth: an **issue-scoped** reference resolves a ULID directly and
a name in the issue's team and then in the workspace; a **catalogue-scoped** reference resolves a
ULID directly and a name over the whole workspace, retired rows included, and refuses a name that
names two rows; a **parent** is resolved at the level of the label being created.

A name resolves case-insensitively in both scopes, and the catalogue refuses a second spelling of
a name it already carries, so a reference never has to pick between two rows that read alike.

Everything here opens exactly one `uow.write()` (or one read-only connection) and reads the clock
at most once.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass

from hifa.adapters.clock import SystemClock
from hifa.adapters.idgen import UlidGen
from hifa.adapters.sqlite import reads
from hifa.adapters.sqlite.connection import Database
from hifa.adapters.sqlite.label_repo import LabelRepo
from hifa.adapters.sqlite.team_repo import TeamRepo
from hifa.adapters.sqlite.uow import Repos, UnitOfWork
from hifa.app.commands import (
    UNSET,
    Actor,
    AddLabel,
    CreateLabel,
    RemoveLabel,
    RestoreLabel,
    RetireLabel,
    UpdateLabel,
)
from hifa.app.issues import _reject_archived, _reject_trashed, _text_rows
from hifa.app.retry import with_write_retry
from hifa.domain import rules
from hifa.domain.entities import Issue, Label, Team
from hifa.domain.enums import LabelGroupType
from hifa.domain.errors import Conflict, HifaError, NotFound, Validation

#: Color of a label created without one: the same default the edge publishes for `--color`.
DEFAULT_LABEL_COLOR = "#6B6F76"

#: Crockford base32, as a ULID spells it: 26 characters, no `I`, `L`, `O` or `U`.
_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")

#: The one history field a label link writes (`label` is the logical key of `issue_label`).
LABEL_HISTORY_FIELD = "label"


@dataclass(frozen=True, slots=True)
class AddLabelResult:
    """What `add_label` answers: the issue after the write, and the sibling it displaced.

    `replaced` is the label a `singleSelect` group took off the issue, or `None` in every other
    case — including the idempotent re-add, which writes nothing at all.
    """

    issue: Issue
    replaced: Label | None


# --- the two issue links ----------------------------------------------------------------


def add_label(uow: UnitOfWork, cmd: AddLabel, actor: Actor) -> AddLabelResult:
    """Link one label to one issue, replacing a `singleSelect` sibling when there is one.

    The layer refusals come before any read of the label, so a parked issue answers for itself
    even when the label does not exist. A label of a `singleSelect` group detaches every sibling
    the issue carries (never the incoming label itself) before the insert; re-adding a label the
    issue already carries writes nothing when no sibling is present.

    raises: `NotFound(issue|label)`,
    `Validation(ISSUE_TRASHED|LABEL_TEAM_MISMATCH|LABEL_NOT_FOUND|COLOR_FORMAT)`,
    `Conflict(LABEL_RETIRED|UNIQUE_LABEL_NAME)`, `InvalidTransition(ISSUE_ARCHIVED)`
    """
    return with_write_retry(lambda: _add_label_once(uow, cmd, actor))


def _add_label_once(uow: UnitOfWork, cmd: AddLabel, actor: Actor) -> AddLabelResult:
    with uow.write() as tx:
        issue = _require_issue(tx, cmd.issue)
        workspace_id = _workspace_id(tx)

        label = _resolve_issue_label(tx.labels, workspace_id, issue.team_id, cmd.label)
        if label is None and cmd.create_if_missing:
            label = _insert_label(
                tx.labels,
                Label(
                    id=uow.idgen.new(),
                    workspace_id=workspace_id,
                    team_id=issue.team_id,
                    name=cmd.label,
                    color=_validate_label_color(cmd.color),
                    description=None,
                    parent_id=None,
                    is_group=False,
                    group_type=None,
                    retired_at=None,
                ),
            )
        if label is None:
            # The two shapes of "it is not there": a ULID names a row, a name is an argument.
            if _looks_like_ulid(cmd.label):
                raise NotFound(resource="label", key=cmd.label)
            raise _label_not_found(cmd.label)

        rules.validate_label_scope(issue.team_id, label)

        current = tx.issues.label_ids(issue.id)
        replaced = _single_select_siblings(tx, label, current)
        if label.id in current and not replaced:
            return AddLabelResult(issue, None)

        now = uow.clock.now()
        changes: list[tuple[str, object, object]] = [
            (LABEL_HISTORY_FIELD, sibling.name, None) for sibling in replaced
        ]
        if label.id not in current:
            changes.append((LABEL_HISTORY_FIELD, None, label.name))

        for sibling in replaced:
            tx.issues.remove_label(issue.id, sibling.id)
        tx.issues.add_label(issue.id, label.id)
        tx.history.record(issue.id, _text_rows(changes), actor_id=actor.member_id, now=now)
        return AddLabelResult(tx.issues.require(issue.id), replaced[0] if replaced else None)


def remove_label(uow: UnitOfWork, cmd: RemoveLabel, actor: Actor) -> Issue:
    """Detach one link from one issue and answer the issue as read back.

    A name resolves exactly as it does in `add_label`, and the one input the two verbs answer
    differently is a name that resolves nowhere: this one is a `NotFound(label)`. Nothing here
    validates the scope of the label: a retired label has to stay detachable, or retiring one
    would strand the chips it left behind. Detaching is one link, so no sibling is touched.

    raises: `NotFound(issue|label|label_link)`, `Validation(ISSUE_TRASHED)`,
    `InvalidTransition(ISSUE_ARCHIVED)`
    """
    return with_write_retry(lambda: _remove_label_once(uow, cmd, actor))


def _remove_label_once(uow: UnitOfWork, cmd: RemoveLabel, actor: Actor) -> Issue:
    with uow.write() as tx:
        issue = _require_issue(tx, cmd.issue)
        workspace_id = _workspace_id(tx)
        label = _resolve_issue_label(tx.labels, workspace_id, issue.team_id, cmd.label)
        if label is None:
            raise NotFound(resource="label", key=cmd.label)
        if label.id not in tx.issues.label_ids(issue.id):
            raise NotFound(resource="label_link", key=cmd.label)

        now = uow.clock.now()
        tx.issues.remove_label(issue.id, label.id)
        tx.history.record(
            issue.id,
            _text_rows([(LABEL_HISTORY_FIELD, label.name, None)]),
            actor_id=actor.member_id,
            now=now,
        )
        return tx.issues.require(issue.id)


# --- the catalogue writes ---------------------------------------------------------------


def create_label(uow: UnitOfWork, cmd: CreateLabel, actor: Actor) -> Label:
    """Create one catalogue entry: a workspace label, a team label, a group or a child.

    A blank name, a color outside `#RRGGBB` and a parent that is not a group of the label's own
    level are refused before the insert. A name the level already carries is refused whether it is
    spelled the same or only the same case: the first is the uniqueness of the table, translated to
    `UNIQUE_LABEL_NAME`, and the second is judged here because the index is case-sensitive.

    raises: `NotFound(team|label)`,
    `Validation(TEAM_ARCHIVED|LABEL_NAME_EMPTY|LABEL_GROUP_INVALID|COLOR_FORMAT)`,
    `Conflict(UNIQUE_LABEL_NAME)`
    """
    return with_write_retry(lambda: _create_label_once(uow, cmd, actor))


def _create_label_once(uow: UnitOfWork, cmd: CreateLabel, actor: Actor) -> Label:
    del actor  # the `label` table carries no author
    name = _validate_label_name(cmd.name)
    color = _validate_label_color(cmd.color) if cmd.color is not None else DEFAULT_LABEL_COLOR
    group_type = _validate_group_type(cmd.group_type) if cmd.is_group else None

    with uow.write() as tx:
        workspace_id = _workspace_id(tx)
        team_id = _resolve_team_id(tx, workspace_id, cmd.team)
        parent = _resolve_parent(tx, workspace_id, team_id, cmd.parent)
        _refuse_case_variant(tx.labels, workspace_id, team_id, name)
        label = Label(
            id=uow.idgen.new(),
            workspace_id=workspace_id,
            team_id=team_id,
            parent_id=None if parent is None else parent.id,
            name=name,
            color=color,
            description=cmd.description,
            is_group=cmd.is_group,
            group_type=group_type,
            retired_at=None,
        )
        return _insert_label(tx.labels, label)


def update_label(uow: UnitOfWork, cmd: UpdateLabel, actor: Actor) -> Label:
    """Rename, recolor or re-describe one label; a command that changes nothing writes nothing.

    The level of a label (`team_id`, `parent_id`, `is_group`, `group_type`) and its `retired_at`
    are not editable here. A retired label may be renamed: the row is its record. The new name is
    judged against its own level and against the level's other spellings of the same case.

    raises: `NotFound(label)`,
    `Validation(LABEL_NAME_EMPTY|LABEL_AMBIGUOUS|COLOR_FORMAT)`, `Conflict(UNIQUE_LABEL_NAME)`
    """
    return with_write_retry(lambda: _update_label_once(uow, cmd, actor))


def _update_label_once(uow: UnitOfWork, cmd: UpdateLabel, actor: Actor) -> Label:
    del actor  # the `label` table carries no author
    with uow.write() as tx:
        workspace_id = _workspace_id(tx)
        label = _resolve_catalogue_label(tx.labels, tx.teams, workspace_id, cmd.label)
        updates: dict[str, object] = {}
        if isinstance(cmd.name, str):
            updates["name"] = _validate_label_name(cmd.name)
        if isinstance(cmd.color, str):
            updates["color"] = _validate_label_color(cmd.color)
        if cmd.description is not UNSET:
            updates["description"] = cmd.description
        candidate = label.model_copy(update=updates)
        if candidate == label:
            return label
        if "name" in updates:
            _refuse_case_variant(
                tx.labels,
                workspace_id,
                label.team_id,
                str(updates["name"]),
                exclude_id=label.id,
            )
        try:
            return tx.labels.update(candidate)
        except Conflict as exc:
            raise _unique_label_name(str(updates.get("name", label.name)), exc) from exc


def retire_label(uow: UnitOfWork, cmd: RetireLabel, actor: Actor) -> Label:
    """Stamp `retired_at` on one label; in use and already retired are both conflicts.

    In use means at least one `issue_label` row, whatever layer the issue it hangs off sits in:
    a restored issue comes back with its chips, so the stricter reading is the true one.

    raises: `NotFound(label)`, `Validation(LABEL_AMBIGUOUS)`,
    `Conflict(LABEL_RETIRED|LABEL_IN_USE)`
    """
    return with_write_retry(lambda: _retire_label_once(uow, cmd, actor))


def _retire_label_once(uow: UnitOfWork, cmd: RetireLabel, actor: Actor) -> Label:
    del actor  # the `label` table carries no author
    with uow.write() as tx:
        workspace_id = _workspace_id(tx)
        label = _resolve_catalogue_label(tx.labels, tx.teams, workspace_id, cmd.label)
        if label.retired_at is not None:
            raise Conflict(
                f"label {label.name} is already retired",
                rule="LABEL_RETIRED",
                field="label",
                value=label.name,
                details={"label_id": label.id},
                code="LABEL_RETIRED",
            )
        uses = tx.labels.count_uses(label.id)
        if uses > 0:
            raise Conflict(
                f"label {label.name} is in use by {uses} issue(s); detach it first",
                rule="LABEL_IN_USE",
                field="label",
                value=label.name,
                details={"label_id": label.id, "uses": uses},
                code="LABEL_IN_USE",
            )
        tx.labels.retire(label.id, uow.clock.now())
        return tx.labels.require(label.id)


def restore_label(uow: UnitOfWork, cmd: RestoreLabel, actor: Actor) -> Label:
    """Clear `retired_at` of one label; a label that is not retired is a conflict.

    raises: `NotFound(label)`, `Validation(LABEL_AMBIGUOUS)`, `Conflict(LABEL_NOT_RETIRED)`
    """
    return with_write_retry(lambda: _restore_label_once(uow, cmd, actor))


def _restore_label_once(uow: UnitOfWork, cmd: RestoreLabel, actor: Actor) -> Label:
    del actor  # the `label` table carries no author
    with uow.write() as tx:
        workspace_id = _workspace_id(tx)
        label = _resolve_catalogue_label(tx.labels, tx.teams, workspace_id, cmd.label)
        if label.retired_at is None:
            raise Conflict(
                f"label {label.name} is not retired",
                rule="LABEL_NOT_RETIRED",
                field="label",
                value=label.name,
                details={"label_id": label.id},
                code="LABEL_NOT_RETIRED",
            )
        tx.labels.retire(label.id, None)
        return tx.labels.require(label.id)


# --- the catalogue reads ----------------------------------------------------------------


def list_labels(
    db: Database, *, team: str | None = None, include_retired: bool = False
) -> tuple[Label, ...]:
    """The catalogue, ordered by `name` and then `id`, so two runs print the same table.

    `team=None` is every label of the workspace, both levels. `team=KEY` is the set an issue of
    that team can wear: the team's labels plus the workspace ones. `include_retired=False` hides
    every row with `retired_at` set — a listing renders a catalogue, and a reference that acts on
    one row is the caller that asks for the retired ones.

    raises: `NotFound(team)`
    """
    conn = db.ro_connection()
    try:
        teams, labels = _read_repos(conn)
        workspace = teams.workspace()
        if workspace is None:
            return ()
        if team is None:
            rows = labels.list(workspace.id, include_retired=include_retired)
        else:
            entity = _read_team(conn, team)
            rows = labels.list(workspace.id, team_id=entity.id, include_retired=include_retired)
            rows += labels.list(workspace.id, team_id=None, include_retired=include_retired)
        return tuple(sorted(rows, key=lambda label: (label.name, label.id)))
    finally:
        conn.close()


def label_detail(db: Database, ref: str) -> Label:
    """One label by ULID or by name, retired rows included.

    raises: `NotFound(label)`, `Validation(LABEL_AMBIGUOUS)`
    """
    conn = db.ro_connection()
    try:
        teams, labels = _read_repos(conn)
        workspace = teams.workspace()
        if workspace is None:
            raise NotFound(resource="label", key=ref)
        return _resolve_catalogue_label(labels, teams, workspace.id, ref)
    finally:
        conn.close()


# --- resolution ---------------------------------------------------------------------------


def _read_repos(conn: sqlite3.Connection) -> tuple[TeamRepo, LabelRepo]:
    """Repositories over a read-only connection: a read needs neither a clock nor an id."""
    clock, idgen = SystemClock(), UlidGen()
    return (
        TeamRepo(conn, clock=clock, idgen=idgen, readonly=True),
        LabelRepo(conn, clock=clock, idgen=idgen, readonly=True),
    )


def _read_team(conn: sqlite3.Connection, ref: str) -> Team:
    """Team by key or ULID over the same read-only connection (`NotFound(team)` otherwise)."""
    entity = reads.team_by_ref(conn, ref)
    if entity is None:
        raise NotFound(resource="team", key=ref)
    return entity


def _resolve_issue_label(
    labels: LabelRepo, workspace_id: str, team_id: str, ref: str
) -> Label | None:
    """ULID, else the name in the issue's team, else the name in the workspace.

    Retired rows are visible here: a retired match has to reach the scope rule so that attaching
    one answers `LABEL_RETIRED` instead of "not found", and detaching one has to stay possible.

    A name matches case-insensitively, and one scope step that matches two rows is refused instead
    of answered: the uniqueness of the table is case-sensitive, so a `Bug`/`bug` pair of one level
    would otherwise resolve to whichever of the two the listing happened to return first. Each
    step is decided on its own, so a unique match in the issue's team still wins over one in the
    workspace, and only a step that leaves two candidates is ambiguous.
    """
    label = labels.get(ref)
    if label is not None:
        return label
    wanted = ref.casefold()
    for scope in (team_id, None):
        matches = [
            row
            for row in labels.list(workspace_id, team_id=scope, include_retired=True)
            if row.name.casefold() == wanted
        ]
        if len(matches) > 1:
            raise _ambiguous_spellings(ref, matches)
        if matches:
            return matches[0]
    return None


def _resolve_catalogue_label(
    labels: LabelRepo, teams: TeamRepo, workspace_id: str, ref: str
) -> Label:
    """ULID, else the name anywhere in the workspace — retired rows included.

    One match is the label. None is `NotFound`. More than one is refused, in the two shapes the
    name can collide in: rows of different teams are named by team key, because a key is what
    narrows a reference, and two rows of one level are named by spelling, because that is the only
    thing that tells them apart.
    """
    label = labels.get(ref)
    if label is not None:
        return label
    wanted = ref.casefold()
    matches = [
        row
        for row in labels.list(workspace_id, include_retired=True)
        if row.name.casefold() == wanted
    ]
    if not matches:
        raise NotFound(resource="label", key=ref)
    if len(matches) > 1:
        if any(count > 1 for count in Counter(row.team_id for row in matches).values()):
            raise _ambiguous_spellings(ref, matches)
        raise _ambiguous_teams(ref, matches, teams)
    return matches[0]


def _resolve_parent(
    tx: Repos, workspace_id: str, team_id: str | None, ref: str | None
) -> Label | None:
    """The group a new child hangs from, resolved at the new label's own level."""
    if ref is None:
        return None
    wanted = ref.casefold()
    candidates = tx.labels.list(workspace_id, team_id=team_id, include_retired=True)
    parent = next((row for row in candidates if row.id == ref), None) or next(
        (row for row in candidates if row.name.casefold() == wanted), None
    )
    if parent is None or not parent.is_group:
        raise _group_invalid(f"parent {ref} is not a group at this level", "parent", ref)
    return parent


# --- rules of this layer ------------------------------------------------------------------


def _single_select_siblings(tx: Repos, label: Label, current: tuple[str, ...]) -> list[Label]:
    """The labels the issue carries from the incoming label's group, itself excluded.

    `current` is ordered by `label.name`, so the first element is the replacement the caller
    reports: an order that depends on how the rows came back is not an order.
    """
    if label.parent_id is None:
        return []
    group = tx.labels.get(label.parent_id)
    if group is None or group.group_type != LabelGroupType.SINGLE_SELECT:
        return []
    siblings: list[Label] = []
    for label_id in current:
        if label_id == label.id:
            continue
        row = tx.labels.get(label_id)
        if row is not None and row.parent_id == label.parent_id:
            siblings.append(row)
    return siblings


def _validate_label_name(name: str) -> str:
    """Trimmed name; blank content is `LABEL_NAME_EMPTY`."""
    cleaned = name.strip()
    if not cleaned:
        raise Validation(
            "a label needs a name",
            rule="LABEL_NAME_EMPTY",
            field="name",
            value=name,
            code="LABEL_NAME_EMPTY",
        )
    return cleaned


def _validate_label_color(color: str | None) -> str:
    """`#RRGGBB`, case-insensitive; anything else is `COLOR_FORMAT`."""
    if not isinstance(color, str) or _COLOR_PATTERN.match(color.strip()) is None:
        raise Validation(
            "color must be #RRGGBB",
            rule="COLOR_FORMAT",
            field="color",
            value=color,
            code="COLOR_FORMAT",
        )
    return color.strip()


def _validate_group_type(group_type: LabelGroupType | str | None) -> LabelGroupType:
    """The group vocabulary; a token outside it is `LABEL_GROUP_INVALID`."""
    try:
        return LabelGroupType(group_type)
    except ValueError as exc:
        raise _group_invalid(
            "group_type must be multiSelect or singleSelect", "group_type", group_type
        ) from exc


def _group_invalid(message: str, field: str, value: object) -> Validation:
    return Validation(
        message,
        rule="LABEL_GROUP_INVALID",
        field=field,
        value=value,
        code="LABEL_GROUP_INVALID",
    )


def _label_not_found(ref: str) -> Validation:
    return Validation(
        f"label not found: {ref}",
        rule="LABEL_NOT_FOUND",
        field="label",
        value=ref,
        code="LABEL_NOT_FOUND",
    )


def _ambiguous_spellings(ref: str, matches: list[Label]) -> Validation:
    """The refusal of a name that one level carries in more than one spelling.

    The uniqueness of the table is case-sensitive, so `Bug` and `bug` are two rows it accepts and
    one name to a reader. The message spells both out: a team key would name the level twice and
    explain nothing.
    """
    names = sorted({row.name for row in matches})
    return Validation(
        f"label {ref} is ambiguous: {', '.join(repr(name) for name in names)}",
        rule="LABEL_AMBIGUOUS",
        field="ref",
        value=ref,
        details={"labels": names},
        code="LABEL_AMBIGUOUS",
    )


def _ambiguous_teams(ref: str, matches: list[Label], teams: TeamRepo) -> Validation:
    """The refusal of a name more than one level carries, spelled by the keys that narrow it."""
    keys = sorted(
        {
            team.key
            for team in (teams.get(row.team_id) for row in matches if row.team_id)
            if team is not None
        }
    )
    if any(row.team_id is None for row in matches):
        keys.append("the workspace")
    return Validation(
        f"label {ref} is ambiguous: it exists in {', '.join(keys)}",
        rule="LABEL_AMBIGUOUS",
        field="ref",
        value=ref,
        details={"teams": keys},
        code="LABEL_AMBIGUOUS",
    )


def case_variant_at_level(
    labels: LabelRepo,
    workspace_id: str,
    team_id: str | None,
    name: str,
    *,
    exclude_id: str | None = None,
) -> Label | None:
    """The row a level already carries under another spelling of this name, if there is one.

    `bug` and `Bug` are two rows to a case-sensitive unique index and one name to a reader, and a
    reference to that name would have to pick one of them. Creation and rename refuse the second
    one (`_refuse_case_variant`) and the seed skips it (`bootstrap`), so both read the same
    predicate instead of restating it. Retired rows count, exactly as the partial unique indexes
    count them.
    """
    wanted = name.casefold()
    for row in labels.list(workspace_id, team_id=team_id, include_retired=True):
        if row.id != exclude_id and row.name.casefold() == wanted:
            return row
    return None


def _refuse_case_variant(
    labels: LabelRepo,
    workspace_id: str,
    team_id: str | None,
    name: str,
    *,
    exclude_id: str | None = None,
) -> None:
    """Refuse a name the same level already carries in another spelling of the same case."""
    existing = case_variant_at_level(labels, workspace_id, team_id, name, exclude_id=exclude_id)
    if existing is not None:
        raise _label_name_conflict(name, existing.name)


def _unique_label_name(name: str, cause: Conflict) -> Conflict:
    """The uniqueness the table enforces, in the vocabulary the edges publish."""
    if cause.rule != "DUPLICATE_LABEL":
        return cause
    return _label_name_conflict(name)


def _label_name_conflict(name: str, existing: str | None = None) -> Conflict:
    """`UNIQUE_LABEL_NAME`: the level already carries this name, in this spelling or another."""
    message = f"label {name} already exists at this level"
    if existing is not None and existing != name:
        message = f"{message} as {existing}"
    return Conflict(
        message,
        rule="UNIQUE_LABEL_NAME",
        field="name",
        value=name,
        code="UNIQUE_LABEL_NAME",
    )


def _insert_label(labels: LabelRepo, label: Label) -> Label:
    """Insert one label, translating the table's duplicate to the code the edges publish.

    Both callers resolve the name over the level just before this insert, so reaching the unique
    index means another writer took the name in between: the caller gets `UNIQUE_LABEL_NAME` and
    not the adapter's `DUPLICATE_LABEL`.
    """
    try:
        return labels.insert(label)
    except Conflict as exc:
        raise _unique_label_name(label.name, exc) from exc


def _require_issue(tx: Repos, ref: str) -> Issue:
    """The issue, refusing a parked one before anything else is read."""
    issue = tx.issues.get(ref) or tx.issues.get_by_identifier(ref)
    if issue is None:
        raise NotFound(resource="issue", key=ref)
    _reject_trashed(issue)
    _reject_archived(issue)
    return issue


def _workspace_id(tx: Repos) -> str:
    workspace = tx.teams.workspace()
    if workspace is None:
        raise HifaError("workspace is not initialized", code="NO_WORKSPACE")
    return workspace.id


def _resolve_team_id(tx: Repos, workspace_id: str, ref: str | None) -> str | None:
    """`None` is a workspace label; a key or ULID resolves and must not be archived."""
    if ref is None:
        return None
    team = tx.teams.get_by_key(workspace_id, ref) or tx.teams.get(ref)
    if team is None:
        raise NotFound(resource="team", key=ref)
    if team.archived_at is not None:
        raise Validation(
            f"team {team.key} is archived",
            rule="TEAM_ARCHIVED",
            field="team",
            value=team.key,
            code="TEAM_ARCHIVED",
        )
    return team.id


def _looks_like_ulid(ref: str) -> bool:
    """True when the reference is spelled as a ULID, which names a row and not a name."""
    return _ULID_PATTERN.match(ref.upper()) is not None


__all__ = [
    "DEFAULT_LABEL_COLOR",
    "LABEL_HISTORY_FIELD",
    "AddLabelResult",
    "add_label",
    "create_label",
    "label_detail",
    "list_labels",
    "remove_label",
    "restore_label",
    "retire_label",
    "update_label",
]
