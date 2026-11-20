"""Search use case: the query grammar, the predicate it compiles to and the page it answers.

The module owns three things and nothing else: the parsing of a raw string into free text plus
filters, the `TextPredicate` that carries the text half to the adapter, and the `SearchService`
that decides the three text states, derives `today` from the instant it is handed and turns the
adapter's rows into a page with `has_more`, `total` and a `backend` label.

No SQL lives here and no clock is read: the adapter holds the SQL and the edge holds the clock,
which is why `search_issues` takes `now` and this module imports neither `datetime.now` nor a
system date function.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from hifa.adapters.sqlite import reads as sqlite_reads
from hifa.adapters.sqlite.connection import Database
from hifa.domain.errors import Validation

FTS_WEIGHTS: tuple[float, float, float] = (10.0, 1.0, 0.0)  # title, description, identifier
DEFAULT_LIMIT: int = 50
MAX_LIMIT: int = 200
MAX_TERMS: int = 16
MAX_TERM_LEN: int = 128
MAX_QUERY_LEN: int = 512
SNIPPET_TOKENS: int = 12
MARK_START: str = "<mark>"
MARK_END: str = "</mark>"
ELLIPSIS: str = "…"
FILTER_FIELDS: tuple[str, ...] = (
    "team",
    "state",
    "assignee",
    "label",
    "priority",
    "due",
    "is",
)
PRIORITY_NAMES: dict[str, int] = {
    "none": 0,
    "no priority": 0,
    "urgent": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
}
IDENTIFIER_RE: str = r"^[A-Za-z][A-Za-z0-9]{1,9}-[0-9]{0,9}$"

#: The four sentences of the refusal catalog, written once.
QUERY_EMPTY: str = "nothing to search for"
QUERY_TOO_LONG: str = (
    "the query is longer than 512 characters or has more than 16 terms "
    "or a term longer than 128 characters"
)
INVALID_LIMIT: str = "limit must be between 1 and 200"
INVALID_OFFSET: str = "offset cannot be negative"

#: The values `is:` accepts, and the ones a relative `due:` window accepts.
IS_VALUES: frozenset[str] = frozenset({"open", "closed", "archived", "trashed", "all"})
RELATIVE_DUE: frozenset[str] = frozenset({"overdue", "today", "week"})

_QUOTES = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'"})
_PUNCTUATION = str.maketrans({character: None for character in "()^~{}:"})
_FILTER_TOKEN = re.compile(
    r"(?<!\S)(?P<neg>-)?(?P<field>team|state|assignee|label|priority|due|is):"
    r'(?:"(?P<quoted>[^"]*)"|(?P<bare>[^\s"]+))'
)
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_IDENTIFIER_EXACT = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,9}-[0-9]{1,9}$")


# --- types ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchFilters:
    """The filters of a search: each one is a value the grammar accepts, or `None`."""

    team: str | None = None  # team.key
    state: str | None = None  # workflow_state.name (or type if no name matches)
    assignee: str | None = None  # email | display_name | "me" | "none"
    label: str | None = None  # label.name
    priority: str | None = None  # "none"|"urgent"|"high"|"medium"|"low"|"0".."4"
    due: str | None = None  # none|overdue|today|week|YYYY-MM-DD|>YYYY-MM-DD|<YYYY-MM-DD
    is_: str | None = None  # open|closed|archived|trashed|all
    negate: tuple[str, ...] = ()  # fields written as "-field:value"

    def is_empty(self) -> bool:
        """True when no filter narrows anything: the text is then the whole query."""
        return self.negate == () and all(
            value is None
            for value in (
                self.team,
                self.state,
                self.assignee,
                self.label,
                self.priority,
                self.due,
                self.is_,
            )
        )


@dataclass(frozen=True)
class SearchQuery:
    """The query an edge composes: the free text plus the filters and the page options."""

    text: str = ""
    filters: SearchFilters = SearchFilters()
    limit: int = DEFAULT_LIMIT
    offset: int = 0
    sort: Literal["relevance", "updated", "created", "priority", "due"] = "relevance"
    count: bool = False


@dataclass(frozen=True)
class ParsedQuery:
    """What one raw string is worth: its text, the filters it carried and the terms it leaves."""

    text: str
    filters: SearchFilters
    terms: tuple[str, ...]
    match: str | None
    match_title: str | None
    like_terms: tuple[str, ...]
    is_empty: bool


@dataclass(frozen=True)
class TextPredicate:
    """The text half of a search, as the read port receives it."""

    match: str | None
    match_title: str | None
    like_terms: tuple[str, ...]
    not_terms: tuple[str, ...]


@dataclass(frozen=True)
class SearchHit:
    """One row of a result page, flat: the eleven fields every edge prints."""

    issue_id: str
    identifier: str
    title: str
    state_name: str
    state_type: str
    priority: int
    due_date: str | None
    assignee_display_name: str | None
    updated_at: str
    snippet: str
    score: float


@dataclass(frozen=True)
class SearchPage:
    """One page of results and how it was answered."""

    hits: list[SearchHit]
    total: int | None
    has_more: bool
    backend: Literal["fts", "like", "none"]
    took_ms: float


class SearchError(Validation):
    """A query the component refuses: one class, four codes, and the input that carried it."""

    def __init__(self, message: str, *, code: str, field: str | None = None) -> None:
        super().__init__(message, rule=code.upper(), field=field, value=None, code=code)


class SearchReads(Protocol):
    """The two reads the service needs from the adapter."""

    def search(
        self,
        *,
        text: TextPredicate,
        filters: SearchFilters,
        order: str,
        limit: int,
        offset: int,
        count: bool,
        today: str,
    ) -> tuple[list[Any], int | None, Literal["fts", "like", "none"]]:
        """Rows (up to `limit + 1`), the total when `count` is set, and the backend."""

    def team_timezone(self, *, team_key: str) -> str | None:
        """The IANA zone of one team, or `None` when the workspace holds no such team."""


# --- normalization and terms ----------------------------------------------------------


def _search_error(message: str, code: str, field: str | None) -> SearchError:
    return SearchError(message, code=code, field=field)


def _invalid_filter(field: str, value: str) -> SearchError:
    return _search_error(f"'{value}' is not a value of {field}", "invalid_filter", field)


def normalize(text: str) -> str:
    """NFKC, control characters removed (tab and line feed become spaces), curly quotes folded."""
    normalized = unicodedata.normalize("NFKC", text).translate(_QUOTES)
    characters = [
        " " if character in "\t\n\r" else character
        for character in normalized
        if character in "\t\n\r" or not unicodedata.category(character).startswith("C")
    ]
    return "".join(characters)


def _clean(token: str) -> str | None:
    """One unquoted token as a term: punctuation dropped, a trailing `*` kept as the marker."""
    body = token.translate(_PUNCTUATION)
    prefix = body.endswith("*")
    core = body[:-1] if prefix else body
    core = core.replace("*", "")
    if not any(character.isalnum() for character in core):
        return None
    return f"{core}*" if prefix else core


def sanitize_terms(text: str) -> tuple[str, ...]:
    """The terms of `text`, in order: phrases, words, prefixes and `-`-marked negations.

    A quoted phrase keeps the words it holds separated by one space; an unclosed quote consumes
    the rest of the text. A term that carries no alphanumeric character at all is discarded, so
    `-`, `()`, `&&` and `"*"` are not terms.
    """
    if len(text) > MAX_QUERY_LEN:
        raise _search_error(QUERY_TOO_LONG, "query_too_long", "q")
    source = normalize(text)
    terms: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        character = source[index]
        if character.isspace():
            index += 1
            continue
        if character == '"':
            closing = source.find('"', index + 1)
            if closing == -1:
                content = source[index + 1 :]
                index = length
            else:
                content = source[index + 1 : closing]
                index = closing + 1
            phrase = " ".join(content.split())
            if any(item.isalnum() for item in phrase):
                terms.append(phrase)
            continue
        end = index
        while end < length and not source[end].isspace():
            end += 1
        raw = source[index:end]
        index = end
        negated = raw.startswith("-") and len(raw) > 1
        term = _clean(raw[1:] if negated else raw)
        if term is None:
            continue
        terms.append(f"-{term}" if negated else term)
        if len(terms) > MAX_TERMS:
            raise _search_error(QUERY_TOO_LONG, "query_too_long", "q")
    for term in terms:
        if len(term.lstrip("-")) > MAX_TERM_LEN:
            raise _search_error(QUERY_TOO_LONG, "query_too_long", "q")
    return tuple(terms)


def _fts_term(term: str) -> str:
    """One term as an FTS5 atom: quoted, internal quotes doubled, a prefix star outside."""
    prefix = term.endswith("*")
    core = term[:-1] if prefix else term
    return '"' + core.replace('"', '""') + '"' + ("*" if prefix else "")


def _split(terms: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    positive = tuple(term for term in terms if not term.startswith("-"))
    negative = tuple(term[1:] for term in terms if term.startswith("-"))
    return positive, negative


def build_match_expression(text: str) -> str | None:
    """The FTS5 `MATCH` expression of `text`, or `None` when no positive term survives."""
    positive, negative = _split(sanitize_terms(text))
    if not positive:
        return None
    expression = " ".join(_fts_term(term) for term in positive)
    if negative:
        excluded = " OR ".join(_fts_term(term) for term in negative)
        return f"({expression}) NOT ({excluded})"
    return expression


def build_title_match_expression(text: str) -> str | None:
    """The same expression restricted to the `title` column, used only to rank."""
    positive, _ = _split(sanitize_terms(text))
    if not positive:
        return None
    return "title : (" + " ".join(_fts_term(term) for term in positive) + ")"


def build_text_predicate(text: str) -> TextPredicate:
    """The one constructor of the text half: two expressions and the two term tuples."""
    terms = sanitize_terms(text)
    positive, negative = _split(terms)
    return TextPredicate(
        match=build_match_expression(text),
        match_title=build_title_match_expression(text),
        like_terms=positive,
        not_terms=negative,
    )


def escape_like(value: str) -> str:
    """Escapes `\\`, `%` and `_` so a term is a literal inside a `LIKE` with `ESCAPE '\\'`."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def identifier_candidate(text: str) -> str | None:
    """The complete identifier `text` spells, uppercased, or `None`."""
    candidate = text.strip()
    if _IDENTIFIER_EXACT.fullmatch(candidate):
        return candidate.upper()
    return None


# --- the raw string -------------------------------------------------------------------


def _validate(field: str, value: str, *, negated: bool) -> str:
    """The canonical stored value of one `field:value`, or `invalid_filter`."""
    if negated and field in {"due", "is"}:
        raise _invalid_filter(field, value)
    if field == "priority":
        lowered = value.strip().lower()
        if lowered in PRIORITY_NAMES:
            return str(PRIORITY_NAMES[lowered])
        if lowered in {"0", "1", "2", "3", "4"}:
            return lowered
        raise _invalid_filter(field, value)
    if field == "due":
        lowered = value.strip().lower()
        if lowered in {"none", "overdue", "today", "week"}:
            return lowered
        operator = lowered[0] if lowered[:1] in {">", "<"} else ""
        candidate = lowered[1:] if operator else lowered
        if not _DATE_ONLY.match(candidate) or not _is_date(candidate):
            raise _invalid_filter(field, value)
        return f"{operator}{candidate}"
    if field == "is":
        lowered = value.strip().lower()
        if lowered not in IS_VALUES:
            raise _invalid_filter(field, value)
        return lowered
    if field == "team":
        return value.strip().upper()
    return value.strip()


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def parse_query(raw: str) -> ParsedQuery:
    """Parses text and nothing else: the free text, the filters it carried and its terms.

    The filters are the `field:value` tokens of the known fields (negated with a leading `-`);
    the token is removed from the text and the flag that names the same field overrides it. An
    unknown field is not a filter: it stays in the text as words.
    """
    if len(raw) > MAX_QUERY_LEN:
        raise _search_error(QUERY_TOO_LONG, "query_too_long", "q")

    pieces: list[str] = []
    found: dict[str, str] = {}
    negate: list[str] = []
    position = 0
    for match in _FILTER_TOKEN.finditer(raw):
        if match.start() < position:
            continue
        field = match.group("field")
        quoted = match.group("quoted")
        value = quoted if quoted is not None else (match.group("bare") or "")
        negated = bool(match.group("neg"))
        pieces.append(raw[position : match.start()])
        position = match.end()
        found[field] = _validate(field, value, negated=negated)
        if negated and field not in negate:
            negate.append(field)
    pieces.append(raw[position:])

    text = " ".join(" ".join(pieces).split())
    terms = sanitize_terms(text)
    positive, negative = _split(terms)
    filters = SearchFilters(
        team=found.get("team"),
        state=found.get("state"),
        assignee=found.get("assignee"),
        label=found.get("label"),
        priority=found.get("priority"),
        due=found.get("due"),
        is_=found.get("is"),
        negate=tuple(negate),
    )
    return ParsedQuery(
        text=text,
        filters=filters,
        terms=terms,
        match=build_match_expression(text),
        match_title=build_title_match_expression(text),
        like_terms=positive,
        is_empty=not terms and filters.is_empty(),
    )


# --- the use case ---------------------------------------------------------------------


def _today_for(filters: SearchFilters, *, now: datetime, reads: SearchReads) -> str:
    """`today` in the query's timezone, or `""` when the filter needs no date at all."""
    if filters.due not in RELATIVE_DUE:
        return ""
    zone = "UTC"
    if filters.team:
        candidate = reads.team_timezone(team_key=filters.team)
        if candidate:
            zone = candidate
    try:
        local = now.astimezone(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        local = now.astimezone(ZoneInfo("UTC"))
    return local.date().isoformat()


def _hit(row: Any) -> SearchHit:
    return SearchHit(
        issue_id=str(row.issue_id),
        identifier=str(row.identifier),
        title=str(row.title),
        state_name=str(row.state_name),
        state_type=str(row.state_type),
        priority=int(row.priority),
        due_date=None if row.due_date is None else str(row.due_date),
        assignee_display_name=(
            None if row.assignee_display_name is None else str(row.assignee_display_name)
        ),
        updated_at=str(row.updated_at),
        snippet=str(row.snippet),
        score=float(row.score),
    )


class SearchService:
    """The implementation of the use case over one read port."""

    def __init__(self, reads: SearchReads) -> None:
        """Wraps the read port the adapter hands over; no connection is opened here."""
        self._reads = reads

    def search(self, query: SearchQuery, *, now: datetime) -> SearchPage:
        """Runs one search and returns its page.

        raises: `SearchError` for `invalid_limit`, `query_too_long`, `query_empty`
        """
        started = time.perf_counter()
        if query.limit < 1 or query.limit > MAX_LIMIT:
            raise _search_error(INVALID_LIMIT, "invalid_limit", "limit")
        if query.offset < 0:
            raise _search_error(INVALID_OFFSET, "invalid_limit", "offset")

        predicate = build_text_predicate(query.text)
        filters = normalize_filters(query.filters)
        has_text = bool(predicate.like_terms or predicate.not_terms)
        if not has_text and filters.is_empty():
            raise _search_error(QUERY_EMPTY, "query_empty", "q")

        today = _today_for(filters, now=now, reads=self._reads)
        rows, total, backend = self._reads.search(
            text=predicate,
            filters=filters,
            order=query.sort,
            limit=query.limit,
            offset=query.offset,
            count=query.count,
            today=today,
        )

        has_more = len(rows) > query.limit
        page_rows = rows[: query.limit]
        hits = [_hit(row) for row in page_rows]
        took_ms = round((time.perf_counter() - started) * 1000, 1)
        return SearchPage(
            hits=hits, total=total, has_more=has_more, backend=backend, took_ms=took_ms
        )


class _ConnectionReads:
    """The read port of one open connection: the adapter's functions, bound to it."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def search(
        self,
        *,
        text: TextPredicate,
        filters: SearchFilters,
        order: str,
        limit: int,
        offset: int,
        count: bool,
        today: str,
    ) -> tuple[list[Any], int | None, Literal["fts", "like", "none"]]:
        return sqlite_reads.search(
            self._conn,
            text=text,
            filters=filters,
            order=order,
            limit=limit,
            offset=offset,
            count=count,
            today=today,
        )

    def team_timezone(self, *, team_key: str) -> str | None:
        return sqlite_reads.team_timezone(self._conn, team_key=team_key)


def search_issues(db: Database, query: SearchQuery, *, now: datetime) -> SearchPage:
    """The one entry point of the component: builds the service, searches once, closes.

    `now` is aware and in UTC, read by the edge from its own clock; a relative `due:` window is
    resolved against the timezone of the team the query names, and against UTC when it names
    none.
    """
    conn = db.ro_connection()
    try:
        return SearchService(_ConnectionReads(conn)).search(query, now=now)
    finally:
        conn.close()


def normalize_filters(filters: SearchFilters) -> SearchFilters:
    """The canonical spelling of every filter, or `invalid_filter` for a value outside the set.

    The flags reach the component as raw strings, so the same gate the text goes through has to
    run on what the flags composed; otherwise `--due ayer` would travel to the query and match
    nothing instead of naming the field it refuses.
    """
    canonical: dict[str, Any] = {}
    for field in FILTER_FIELDS:
        name = "is_" if field == "is" else field
        value = getattr(filters, name)
        if value is None:
            canonical[name] = None
            continue
        canonical[name] = _validate(field, str(value), negated=field in filters.negate)
    return replace(filters, **canonical)


def with_overrides(
    parsed: ParsedQuery, overrides: dict[str, Any], *, all_flag: bool = False
) -> SearchFilters:
    """The query's filters with the flags that were actually passed applied on top."""
    changes = {name: value for name, value in overrides.items() if value is not None}
    if all_flag and "is_" not in changes:
        changes["is_"] = "all"
    return normalize_filters(replace(parsed.filters, **changes))


__all__ = [
    "DEFAULT_LIMIT",
    "ELLIPSIS",
    "FILTER_FIELDS",
    "FTS_WEIGHTS",
    "IDENTIFIER_RE",
    "MARK_END",
    "MARK_START",
    "MAX_LIMIT",
    "MAX_QUERY_LEN",
    "MAX_TERMS",
    "MAX_TERM_LEN",
    "PRIORITY_NAMES",
    "SNIPPET_TOKENS",
    "ParsedQuery",
    "SearchError",
    "SearchFilters",
    "SearchHit",
    "SearchPage",
    "SearchQuery",
    "SearchReads",
    "SearchService",
    "TextPredicate",
    "build_match_expression",
    "build_text_predicate",
    "build_title_match_expression",
    "escape_like",
    "identifier_candidate",
    "normalize",
    "normalize_filters",
    "parse_query",
    "sanitize_terms",
    "search_issues",
    "with_overrides",
]
