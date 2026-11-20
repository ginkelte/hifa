---
id: SPEC-090
title: FTS5 full-text search
component: application
status: review
version: 4
spec_revision: 12
depends_on: [SPEC-000, SPEC-010, SPEC-040]
implements:
  - src/hifa/app/search.py
  - src/hifa/adapters/sqlite/reads.py
  - src/hifa/adapters/sqlite/migrations/001_init.sql
  - tests/**/search*
acceptance: specs/acceptance/001-mvp-v1/10-search.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-20
---

# SPEC-090 — FTS5 full-text search

## 1. Responsibility and boundaries

**Does:** defines the FTS5 index over `issue` (external content), the triggers that keep it
synchronized, the query parser (`free text`, `"phrase"`, `prefix*`, `field:value` filters), the
sanitization that prevents an input from breaking the `MATCH` syntax, the bm25 ranking with per-column
weights, the pagination and the fallback to `LIKE`. It exposes a single read operation: search issues.

**Does NOT do (explicit):**

- It does not write to `issue` or `issue_fts` (except explicit `'rebuild'`/`'integrity-check'`): the
  index is maintained by triggers, not by this component.
- It does not define the base schema: `issue`, `issue_fts` and their columns belong to SPEC-010 §3.4. This spec
  defines the **exact body of the triggers** and the query policy, not new tables.
- It does not search `comment.body` or `project.description`: the index covers `title`, `description` and
  `identifier` of `issue`. Searching comments is out of scope.
- It does not do semantic or fuzzy ranking: there are no embeddings, no synonyms, no stemming (the tokenizer is
  `unicode61`, which does not derive roots).
- It does not implement saved searches, search history or suggestions (autocomplete belongs to the UI).
- It does not do `COUNT(*)` by default: the total is optional (`count=True`) because it doubles the cost.
- It does not write SQL outside `adapters/sqlite/reads.py` (SPEC-000 A3): `app/search.py` does not import
  `sqlite3`.
- It declares no HTTP endpoint and no page. `GET /api/v1/search` is SPEC-060 §2.5.4 and
  `GET /search` is SPEC-070 §2.1.8; both consume §2.6 of this document and neither defines a flag,
  a filter or an envelope of its own (D-016).
- It does not escape the snippet. The `<mark>` pair reaches every edge as literal text and the
  escaping happens exactly once, in the read model of the page (REQ-S090-03, SPEC-070 R17).

## 2. Public contract

### 2.1 Constants

```python
# src/hifa/app/search.py
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
```

### 2.2 Types

```python
@dataclass(frozen=True)
class SearchFilters:
    team: str | None = None  # team.key
    state: str | None = None  # workflow_state.name (or type if no name matches)
    assignee: str | None = None  # email | display_name | "me" | "none"
    label: str | None = None  # label.name
    priority: str | None = None  # "none"|"urgent"|"high"|"medium"|"low"|"0".."4"
    due: str | None = None  # none|overdue|today|week|YYYY-MM-DD|>YYYY-MM-DD|<YYYY-MM-DD
    is_: str | None = None  # open|closed|archived|trashed|all
    negate: tuple[str, ...] = ()  # fields with "-field:value": team,state,assignee,label,priority


@dataclass(frozen=True)
class SearchQuery:
    text: str = ""
    filters: SearchFilters = SearchFilters()
    limit: int = DEFAULT_LIMIT
    offset: int = 0
    sort: Literal["relevance", "updated", "created", "priority", "due"] = "relevance"
    count: bool = False


@dataclass(frozen=True)
class ParsedQuery:  # what `parse_query(raw)` returns: the TEXT, never a SearchQuery (§2.3)
    text: str  # the free text, with the embedded field:value filters of R6 removed
    filters: SearchFilters  # only the filters the text carried; a flag overrides them (R6)
    terms: tuple[str, ...]  # sanitize_terms(text) (R2)
    match: str | None  # build_match_expression(terms), None when no positive term survives (R4)
    match_title: str | None  # build_title_match_expression(terms) (R4)
    like_terms: tuple[str, ...]  # the positive terms the `like` path would use (R14)
    is_empty: bool  # the text left no term and no filter; it describes the TEXT, not the query


@dataclass(frozen=True)
class TextPredicate:  # the text half of a search, as `reads.search` receives it (§2.4)
    match: str | None  # FTS5 MATCH expression, None when no positive term survives (R4)
    match_title: str | None  # the title-restricted expression, for the ranking band (R9)
    like_terms: tuple[str, ...]  # the POSITIVE terms, in order of appearance, `-` stripped (R14)
    not_terms: tuple[str, ...]  # the NEGATED terms, in order of appearance, `-` stripped (R14)


@dataclass(frozen=True)
class SearchHit:
    issue_id: str
    identifier: str
    title: str
    state_name: str
    state_type: str  # triage|backlog|unstarted|started|completed|canceled
    priority: int
    due_date: str | None
    assignee_display_name: str | None
    updated_at: str
    snippet: str  # contains MARK_START/MARK_END around the terms
    score: float  # higher = better; 0.0 when backend="like"


@dataclass(frozen=True)
class SearchPage:
    hits: list[SearchHit]
    total: int | None  # None if count=False
    has_more: bool
    backend: Literal["fts", "like", "none"]  # "none" = filters only, no text predicate
    took_ms: float


class SearchError(Validation):
    code: str  # query_empty | query_too_long | invalid_limit | invalid_filter (§6)
    message: str  # the one sentence of §6's table for that code
    field: str | None  # the INPUT that carried it: limit | offset | priority | due | q
```

### 2.3 Functions and service

```python
def sanitize_terms(text: str) -> tuple[str, ...]
def build_match_expression(text: str) -> str | None
def build_title_match_expression(text: str) -> str | None
def build_text_predicate(text: str) -> TextPredicate
def escape_like(value: str) -> str
def parse_query(raw: str) -> ParsedQuery
def identifier_candidate(text: str) -> str | None
def search_issues(db: Database, query: SearchQuery, *, now: datetime) -> SearchPage


class SearchService:
    def __init__(self, reads: SearchReads)
    def search(self, query: SearchQuery, *, now: datetime) -> SearchPage
```

**`parse_query` parses text and nothing else; the edge composes the `SearchQuery`.** `ParsedQuery` is
what a raw string is worth: the free text, the filters the text carried (R6) and, for the edge's own
decisions, the terms-derived values that `search_issues` recomputes from `SearchQuery.text` (§5.1). The
`SearchQuery` is composed by **each edge from three sources and no fourth**: the `ParsedQuery`'s `text`
and `filters`, the edge's own flags — which override the embedded value of the same field (`--state Done`
wins over `state:Todo`, R6) — and the options only the edge owns (`limit`, `offset`, `sort`, `count`).
The composition is the same three lines in all three edges (§2.5, §2.6.1; SPEC-050 §2.5.1, SPEC-060
§2.5.4, SPEC-070 §2.1.8):

```python
parsed = parse_query(raw)
overrides = {name: value for name, value in flags.items() if value is not None}  # only flags given
query = SearchQuery(
    text=parsed.text,
    filters=replace(parsed.filters, **overrides),
    limit=limit,
    offset=offset,
    sort=sort,
    count=count,
)
page = search_issues(db, query, now=clock.now())
```

A flag that was not passed contributes **no** override, so a `state:Todo` written in the text survives a
command that does not pass `--state`. No edge builds a `SearchFilters` from scratch, no edge passes a
flag into `parse_query`, and no edge reads the terms: they arrive in `ParsedQuery` for the two decisions
an edge makes locally (whether to call at all, and the `sort` default) and for nothing else.

**What `default_team` was, and why it is gone.** Revision 4 declared
`parse_query(raw, *, default_team=None)`: a parser that also resolved the caller's team — the
`[cli].default_team` of SPEC-100 §2.3 — and stamped it into `SearchFilters.team` when the text carried no
`team:` filter. It was wrong three times over. It made the parser read configuration, which `app/` may
not do (A1/A3: the config file is an edge concern, and `hifa search` was its only caller that ever passed
it); it made one string parse into two different queries depending on the environment the process ran
in, so `parse_query` was not a function of its argument; and it silently narrowed a workspace-wide search
to one team, which no flag asked for and `--team` already does explicitly. **A team that arrives by
default arrives from the edge that resolved it**, exactly as `--team`'s value does: `hifa search` leaves
`filters.team` as the text wrote it, and `GET /search` sets it from its one query parameter. The testable
consequence: `parse_query("deploy")` returns the same `ParsedQuery` for every working directory, every
`.hifa.toml` and every database.

**`build_text_predicate(text)` is the only constructor of the text half, and it is why the port can
tell "no text" from "text that is all negated".** It calls `sanitize_terms(text)`, splits the result on
R2's `-` marker into the positive and the negated terms (the marker stripped), and returns the
`TextPredicate` of §2.2: the two FTS5 expressions of R4 and the two tuples. `SearchService` builds it
once per call, from `query.text` alone, and hands it to `reads.search` (§5.1 steps 2-5 and 7). The
negated terms therefore reach the adapter as an **argument**, not as something it has to rediscover by
reading the text: `like_terms == ()` and `not_terms == ()` means no text, `like_terms == ()` with
`not_terms != ()` means the no-positive form of §5.3, and only the predicate can tell those two apart.
The terms are **not** carried on `SearchQuery` and are not read by any edge: `SearchQuery.text` already
carries the `-` and the service is the one that parses it (D-067).

**`search_issues` is the use case and the only entry point**, and its signature is the one SPEC-040
§2.2 declares — `search_issues(db, query, *, now)` with `now` aware and in UTC: the edges call it with the
`Database` they already hold, as they do with `list_issues` and `issue_detail`, and with the instant their
own `Clock` read, which is the one argument those two do not need. It builds the `SearchService` of this
module over `reads.search` (SPEC-030), calls `search` once and returns the page; it opens no transaction
and adds no rule of its own. `SearchService`
is the **implementation object**, not a port: no edge constructs it, because that would make `web/` or
`cli/` import `adapters/` to obtain the `Reads` it wants (SPEC-000 A3, D-064).

**Where the time comes from, and why there is no clock in this module.** `search_issues(db, query, *,
now)` takes the instant as a parameter, and `SearchService(reads).search(query, now=now)` passes it down:
`app/search.py` imports neither `time` nor `datetime.now` (A2, SPEC-000 §4.1: time enters through the
`Clock` port), and the adapter never reads the system date — it receives the `today` string of §2.4. The
edge is the only owner of "now": `hifa search` passes `SystemClock().now()`, the API passes the clock it
built with the app, and the page passes the same `now` its read model uses for `updated_label`
(SPEC-070 §2.3.5). `now` is aware and in UTC, the `Clock.now()` contract of SPEC-030 §2.7 — the
`AwareDatetime` of SPEC-020 §2.2 is the model-field spelling of the same constraint — and a naive
`datetime` is a bug of the caller, not a refusal: the unit test passes an aware one.

`SearchReads` is the `Reads` object from `adapters/sqlite/reads.py` (SPEC-030). The service never opens
connections: it uses the one `Reads` already has and does not manage transactions (all queries are
`SELECT`).

### 2.4 Read port (implemented in the adapter)

```python
# src/hifa/adapters/sqlite/reads.py
@dataclass(frozen=True)
class SearchRow:
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

def search(
    conn: sqlite3.Connection,
    *,
    text: TextPredicate,
    filters: SearchFilters,
    order: str,
    limit: int,
    offset: int,
    count: bool,
    today: str,
) -> tuple[list[SearchRow], int | None, Literal["fts", "like", "none"]]


def team_timezone(conn: sqlite3.Connection, *, team_key: str) -> str | None
```

Port rules:

- `text` is the `TextPredicate` of §2.2 — **already sanitized** FTS5 expressions and the two term tuples,
  built by `SearchService` with `build_text_predicate(query.text)` (§2.3). The port distinguishes exactly
  three text states — `fts`, `like`, `none`, and no fourth; `like` has the two forms of §5.3 — and
  **decides them from its arguments alone**: `text.match`, `text.like_terms` and `text.not_terms`, with
  nothing read out of the text again. The four argument combinations are these:

  | `text.match` | `text.like_terms` | `text.not_terms` | Path |
  |---|---|---|---|
  | not `None` | any | any | **`fts`**: `MATCH :match` (§5.2); `like_terms` is kept for R14b's retry and for the snippet (R15) |
  | `None` | non-empty | any | **`like`**, the first form of §5.3: the positives, minus the negated ones |
  | `None` | `()` | non-empty | **`like`**, the no-positive form of §5.3: nothing but the negations |
  | `None` | `()` | `()` | **no text predicate**, `backend="none"`, the filters alone (R14's last sentence) |

  The second row is unreachable from `SearchService` — R4 returns a `match` whenever a positive term
  survives — and it is declared so the adapter's decision is total over its arguments rather than
  dependent on a caller's promise. The third and fourth rows are the pair this revision separates: both
  have `like_terms == ()`, and only `not_terms` tells "a query that excludes something" from "a query
  with no text at all". An empty `match` never means "fall back to `like`": a `like` path over zero
  terms is an all-rows scan wearing the wrong label, and an implementation that refused to build a
  predicate from no term would answer **zero** rows. Both are wrong; `hifa search --state Done` is the
  case that tells them apart (§2.5).
- `today` is `YYYY-MM-DD` **in the query's timezone**, computed by `SearchService` from `now` and
  `reads.team_timezone` (R13): the adapter never calls `datetime.now` and never calls a date function in
  SQL.
- The received `limit` is already clamped to `1..MAX_LIMIT`; the adapter requests `limit + 1` rows and
  returns **up to `limit + 1`** of them — the extra row is the probe, not a hit. `SearchService` sets
  `has_more = len(rows) > limit` and **trims** the list to `limit` before building the page (§5.1 steps 8
  and 9). The tuple stays `(rows, total, backend)`: `has_more` is a derivation of the service, never a
  fourth return value of the port.
- It returns `(rows, total, backend)`. On a `sqlite3.OperationalError` whose message matches one of the
  triggers listed in **R14** — that list is the only one in this document, and this bullet names no
  trigger of its own — it retries once through the `like` path and returns `backend="like"`.
- `team_timezone` is the one extra read of this component: `SELECT timezone FROM team WHERE key = ?`,
  `None` when no team carries that key. `SearchService` calls it only when `query.filters.due` is a
  relative window (R13) and the query names a team; every other search reads no timezone at all.

### 2.5 CLI surface

| Command | Flags | Output (`--json`) | Exit codes |
|---|---|---|---|
| `hifa search <query>` | `--team`, `--state`, `--assignee`, `--label`, `--priority`, `--due`, `--is`, `--limit` (1-200), `--offset`, `--all`, `--count`, `--sort {relevance,updated,created,priority,due}`, `--json` | `{"hits": [...], "total": null, "has_more": true, "backend": "fts", "took_ms": 4.2}` | 0, 2, 6 |

- The flags override the filters embedded in the text (`--state Done` wins over `state:Todo`).
- `--all` is equivalent to `is:all` (includes archived and trashed).
- `hifa search` with no arguments and no filters ⇒ `query_empty` (exit 2). With filters and no text, the
  search is valid and does not use FTS (`match=None`), but it **does** apply visibility and filters.
- `--limit` outside `1..200`, or `--offset < 0`, ⇒ `invalid_limit` (exit 2, `error.field` = `"limit"` or
  `"offset"`). An `--offset` past the end of the result is **not** an error: `hits` is empty,
  `has_more` is `false`, exit 0 (R11).
- `--count` is the only way `total` stops being `null`. Without it the key is present and `null`, because
  the count doubles the query's cost (R11); `--json` alone never counts. With `--count`, `total` is the
  size of the whole filtered set under the same visibility predicate as the hits (R10) — never the size
  of the page and never `0` because the page came out empty.
- Without `--json`, the output is the table of SPEC-050 §2.4.1 — `IDENTIFIER  P  STATE  UPDATED  TITLE
  MATCH` — formatted by `cli/output.py` (SPEC-000 A6). `MATCH` renders the snippet with `MARK_START` and
  `MARK_END` removed (reverse video when ANSI is on, §2.4 of SPEC-050), and is the empty cell `–` when
  the query carried no text (`backend="none"`). The six fields are the ones the page of §2.6 renders.
- The command composes the `SearchQuery` of §2.3 — `parse_query(raw)` plus its twelve flags — and calls
  the use case `search_issues(db, query, now=clock.now())` (SPEC-040 §2.2, §2.3 of this document); it
  holds no query of its own and builds no envelope (D-016, D-064).
- **The walk of a filters-only search, which is where the two readings of §2.4 diverge.** `hifa search
  --state Done` carries no text at all: `parse_query("")` is empty (`is_empty` is `True`, and the command
  does not refuse, because the composed `SearchQuery` carries a filter), and `build_text_predicate`
  returns a predicate whose `match` is `None`, whose `like_terms` is `()` **and** whose `not_terms` is
  `()`; the adapter runs **no text predicate** with `backend="none"` (§2.4). The result is **every
  issue whose state is `Done`**, under the default visibility of R10. The wrong reading of "`match is
  None` ⇒ the `like` path" answers the same rows but labels them `backend="like"` — an all-rows scan
  reported as a text search — and an implementation that built `AND ` over an empty term list would
  answer **zero** rows, which is the failure this walk exists to pin.
- `GET /api/v1/issues?q=…` is **not** this contract: the `q` of the issue listing is the title filter of
  D-025 (SPEC-040 §2.2, §4; SPEC-030 §2.6), it declares no `MATCH`, no `backend` and no ranking, and it
  does not call `parse_query` or `search_issues`. This spec exposes no endpoint.

### 2.6 What the edges consume: `hifa search`, `/api/v1/search` and `GET /search`

One contract, three transports, no fourth definition:

| Edge | Owner | What it adds |
|---|---|---|
| `hifa search` | SPEC-050 §2.5.1 | the flags of §2.5 and the table of SPEC-050 §2.4.1 |
| `GET /api/v1/search` | SPEC-060 §2.5.4 | HTTP query parameters and the `422` of an invalid query |
| `GET /search` | SPEC-070 §2.1.8 | one HTML page: the four states of §2.6.1 and nothing else |

#### 2.6.1 `GET /search?q=` (SPEC-070 §2.1.8, D-063)

The page is a **consumer**, so it accepts exactly this document's inputs and renders exactly this
document's outputs:

- it composes the `SearchQuery` of §2.3 — `parse_query(q)` plus the one filter it exposes as a query
  parameter (`team`) — and it calls `search_issues(db, query, now=…)` with `count=False`, so it pays no
  second query, and it shows `len(hits)` under its result list instead of a total (SPEC-070 §2.3.5); it
  declares no grammar, no flag and no envelope of its own (D-016);
- `now` is the page's own: the same instant its read model uses for `updated_label` (§2.3.5), passed
  explicitly because this module never reads a clock (A2, §2.3). A relative `due:` window therefore
  resolves in the timezone of the `team` the query names, and in UTC when it names none (R13);
- it renders `identifier`, `title`, `snippet`, `state_name`, `priority` and `updated_at` of each hit —
  the six fields `hifa search` prints in §2.5;
- `hits=[]` from a valid query is the page's **no-results** state, `200`; it is not an error and it does
  not become a `404` (the search does not produce `NotFound`, §6);
- a `Validation` of §6 (`query_too_long`, `invalid_filter`, `invalid_limit`) is the page's **error**
  state: `422` with the sentence of SPEC-070 §6, the query kept in the box;
- a request with no `q` never reaches this component: the page answers `200` in its **empty** state and
  `query_empty` is a CLI/API refusal only (D-063). `<a href="/search">` is a link the sidebar renders on
  every page, and a link that answers `400` is the dead end the page exists to remove;
- a `team` that names no team is **not** an error either: §2.5's `team:KEY` filter has no existence
  check (R7), so the page renders the no-results state with `hits=[]`.

## 3. Data it touches

### 3.1 Index and triggers (normative)

`issue_fts` is declared in `migrations/001_init.sql` exactly as in SPEC-010 §3.4:

```sql
CREATE VIRTUAL TABLE issue_fts USING fts5(
  title, description, identifier UNINDEXED,
  content='issue', content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2'
);
```

The triggers are **part of the same migration** and their body is the following, exact:

```sql
CREATE TRIGGER issue_fts_ai AFTER INSERT ON issue BEGIN
  INSERT INTO issue_fts(rowid, title, description, identifier)
  VALUES (new.rowid, new.title, new.description, new.identifier);
END;

CREATE TRIGGER issue_fts_ad AFTER DELETE ON issue BEGIN
  INSERT INTO issue_fts(issue_fts, rowid, title, description, identifier)
  VALUES ('delete', old.rowid, old.title, old.description, old.identifier);
END;

CREATE TRIGGER issue_fts_au AFTER UPDATE OF title, description, identifier ON issue BEGIN
  INSERT INTO issue_fts(issue_fts, rowid, title, description, identifier)
  VALUES ('delete', old.rowid, old.title, old.description, old.identifier);
  INSERT INTO issue_fts(rowid, title, description, identifier)
  VALUES (new.rowid, new.title, new.description, new.identifier);
END;
```

Normative consequences of `content='issue'`:

- The index does **not** duplicate the text: `issue_fts` stores only the tokens. A column `SELECT` on
  `issue_fts` returns the current value of `issue` (external content).
- `identifier` is `UNINDEXED`: it does not participate in `MATCH` or in `bm25`. That is why the identifier
  is resolved with a SQL predicate over `issue.identifier` (§4.6) and **not** with FTS.
- The `issue` table has a `TEXT` PK, so its `rowid` is implicit and **can be renumbered by
  `VACUUM`**. Any `VACUUM` on the database must be followed by
  `INSERT INTO issue_fts(issue_fts) VALUES('rebuild')` in the same operation (R19).
- The triggers do not cover `comment` or any other table.

### 3.2 Tables read

| Table | Use | Invariant |
|---|---|---|
| `issue_fts` | `MATCH`, `bm25`, `snippet` | synchronized by triggers; `integrity-check` must pass |
| `issue` | output columns, filters, visibility | `trashed = 0 AND archived_at IS NULL` by default |
| `workflow_state` | `state_name`, `state_type` in the hit and the `state` filter | `issue.state_id` belongs to the issue's team |
| `team` | resolution of `team:KEY` | `UNIQUE(workspace_id, key)` |
| `member` | resolution of `assignee` and `assignee_display_name` | unique `email`; `display_name` may repeat |
| `label`, `issue_label` | `label` filter | PK `(issue_id, label_id)`; the label may be workspace-wide |

Nothing is ever written to any of these tables from this component.

## 4. Business rules

### 4.1 Tokenization and input sanitization

- **R1 — Normalization (REQ-F090-01).** The input text goes through `unicodedata.normalize("NFKC", text)`
  and control characters (`Cc`) are removed except `\t` and `\n`, which become spaces. Typographic
  quotes `“ ” ‘ ’` are normalized to `"` and `'`. No `casefold` is applied: the tokenizer
  `unicode61` already folds case and removes diacritics (`remove_diacritics 2`), so that
  `diseño`, `DISENO` and `Diseño` produce the same tokens.
- **R2 — Terms (REQ-F090-02).** `sanitize_terms` walks the text and produces a tuple of
  terms in order of appearance:
  - `"..."` (double quotes closed or not) ⇒ **phrase**: the content is kept with spaces
    collapsed to one; an unclosed quote consumes up to the end of the text.
  - `\S+` without quotes ⇒ **word**. If it ends in `*`, it is a **prefix** (the `*` is stripped from the term).
  - A term starting with `-` and longer than 1 ⇒ **negated** with the rest as a word.
  - Terms whose content, after removing everything non-alphanumeric, is empty are discarded
    (`-`, `()`, `&&`, `...`).
- **R3 — Operators (REQ-F090-03).** v1 does **not** expose the FTS5 boolean syntax. The tokens
  `AND`, `OR`, `NOT` and `NEAR` are treated as literal words of the language (they are searched inside
  `"..."`), not as operators; negation is expressed only with `-term` (word) or
  `-field:value` (filter). This removes precedence errors and malformed `NEAR(a b, 5)` at the root.
  Parentheses, `^`, `{`, `}` and `:` outside a filter are discarded as punctuation.
- **R4 — Building the MATCH expression (REQ-F090-04).** With the terms from R2:
  - Each term is **always** emitted quoted with `"` and with internal quotes doubled
    (`"` → `""`). A prefix is emitted `"term"*` (asterisk **outside** the quotes).
  - Positive terms are joined with spaces (implicit AND of FTS5).
  - If there are negated terms, the expression is `(<positives>) NOT (<neg1> OR <neg2> …)`.
  - If there are **no** positives (all negated), `build_match_expression` returns `None` and the query is
    resolved through the `like` path with negation (`NOT (title LIKE … OR description LIKE …)`), because
    FTS5 does not accept `NOT` without a left operand.
  - If no term remains, it returns `None`.
  - `build_title_match_expression` emits the same with a column filter: `title : (<positives>)`.
    It is used only to compute the ranking band (R9), never to filter.
  - `build_text_predicate(text)` is the four values above, together, in one `TextPredicate` (§2.2): the
    two expressions and the two term tuples, positives and negated, split on R2's `-` marker. It is what
    the service hands the adapter, so the negation travels with the query instead of being re-derived
    from the text inside `reads.py` (§2.3, §2.4, D-067).
- **R5 — Query limits (REQ-F090-05).** `len(raw) > 512` ⇒ `query_too_long`. More than 16
  terms ⇒ `query_too_long`. A term (post-normalization) longer than 128 characters ⇒
  `query_too_long`. No text and no filters ⇒ `query_empty`.
- **R6 — Embedded filters (REQ-F090-06).** They are extracted with the shape `(-)?(field):(value)` where
  `value` may be quoted (`state:"In Progress"`). Fields from `FILTER_FIELDS`; an unknown
  `field` (`foo:bar`) is **not** a filter: the whole token is folded back into the free text. The CLI
  flags override the embedded value of the same field. An invalid value for a known field
  (`priority:urgentísimo`, `due:ayer`) ⇒ `invalid_filter` with `field` populated.
- **R7 — Semantics of each filter (REQ-F090-07).**
  - `team:KEY` → `i.team_id IN (SELECT id FROM team WHERE key = :team_key)`. With no existence
    check: a nonexistent key returns 0 results, not an error.
  - `state:X` → `EXISTS (SELECT 1 FROM workflow_state ws WHERE ws.id = i.state_id AND
    (lower(ws.name) = lower(:state) OR ws.type = lower(:state)))`. The `OR` with `type` allows
    `state:started` with no practical ambiguity; a team with a state named `started` matches both ways
    and the result is the same set.
  - `assignee:X` → `me` resolves to the local member (`SELECT id FROM member ORDER BY created_at, id
    LIMIT 1`, the one seeded by `hifa init`); `none` → `i.assignee_id IS NULL`; any other value
    matches `lower(email) = lower(:a) OR lower(display_name) = lower(:a)` (union, not ambiguity).
  - `label:X` → `EXISTS (SELECT 1 FROM issue_label il JOIN label l ON l.id = il.label_id
    WHERE il.issue_id = i.id AND lower(l.name) = lower(:label))`. It matches team and
    workspace labels alike.
  - `priority:X` → `i.priority = :priority`, with `PRIORITY_NAMES` or the digit `0`..`4`.
  - `due:X` → see R13.
  - `is:X` → `open` (`s.type NOT IN ('completed','canceled')`), `closed` (the opposite), `archived`
    (`i.archived_at IS NOT NULL`, disables the default filter), `trashed` (`i.trashed = 1`),
    `all` (no visibility filter).
- **R8 — Negation (REQ-F090-08).** `-field:value` adds `NOT EXISTS (…)` or `<>` depending on the field;
  the negatable fields are `team`, `state`, `assignee`, `label`, `priority`. `-due:` and `-is:` ⇒
  `invalid_filter` (negation of date ranges is not defined in v1).
- **R9 — Ranking (REQ-F090-09).** The default order is by band, then by text score:

  | Band | Condition | Effective weight |
  |---|---|---|
  | 0 | `i.identifier = :ident_exact` (the input *is* a complete identifier) | absolute maximum |
  | 1 | the issue matches the expression restricted to `title` | `bm25` with title weight **10.0** |
  | 2 | `i.identifier LIKE :ident_like` (the input is an identifier prefix) | between bands 1 and 3 |
  | 3 | match only through `description` (or through a non-prefix partial identifier) | `bm25` with description weight **1.0** |

  Final order: `band ASC, (-bm25) DESC, CASE priority WHEN 0 THEN 5 ELSE priority END ASC,
  updated_at DESC, id ASC`. `bm25(issue_fts, 10.0, 1.0, 0.0)` is used with the weights in the index's
  column order (`title`, `description`, `identifier`); the weight of `identifier` is `0.0`
  because the column is `UNINDEXED` and does not participate in the calculation (its place in the ranking
  comes from the band). `bm25` returns values ≤ 0 (more negative = better), which is why the exposed score is its
  negation.
- **R10 — Default visibility (REQ-F090-10).** Every search adds `i.trashed = 0 AND
  i.archived_at IS NULL` unless `is:archived`, `is:trashed` or `is:all` is requested. The counts
  (`total`) use the same predicate: what is not returned is never counted.
- **R11 — Pagination (REQ-F090-11).** `limit` outside `1..200` ⇒ `invalid_limit`. `offset < 0` ⇒
  `invalid_limit`. The adapter requests `limit + 1` rows; `SearchService` sets
  `has_more = len(rows) > limit` and then **trims** `rows` to `limit`, so `hits` never carries the probe
  and the port never returns a fourth value (§2.4, §5.1 steps 8 and 9). `total` is computed only with
  `count=True`; without it, it is `None`. An `offset` greater than the total returns `hits=[]` with
  `has_more=False` and exit 0, not an error.
  `total`, when computed, is the size of the **filtered set** and never the size of the page: with 20
  matches and `offset=100`, `hits` is empty and `total` is `20`, because a consumer that read
  `total = len(hits)` would conclude that the query matched nothing.
- **R12 — Alternative order (REQ-F090-12).** `sort="updated"|"created"` → `updated_at`/`created_at`
  DESC; `"priority"` → priority band (urgent first, `0` last) and then `updated_at` DESC;
  `"due"` → `due_date IS NULL ASC, due_date ASC`. With `sort != "relevance"` the band and the `bm25`
  do not take part in the order, but the same filters and the same visibility apply.
- **R13 — `due` filter, and the timezone of the query (REQ-F090-13).** `today` is **derived, never
  read**: `today = now.astimezone(zone).date().isoformat()`, where `now` is the aware-UTC instant
  `search_issues` received (§2.3) and `zone` is **the query's timezone**:
  - `team.timezone` of the team `query.filters.team` names, read with `reads.team_timezone` (§2.4);
  - **UTC** when the query names no team. The schema declares no workspace timezone — `workspace` is
    `id, name, url_key, created_at` (SPEC-010 §3.1) and only `team` and `member` carry one — and the
    default team is an edge's configuration (`[cli].default_team`, SPEC-100 §2.3), which `app/` may not
    read (A1/A3). Inventing a workspace zone would need a migration this ticket does not own, so the
    fallback is UTC and it is declared rather than guessed.

  The consequence is stated plainly: a workspace-wide `due:today` is the **UTC day**, so for the hours in
  which a team's zone is already on another date, `hifa search due:today` and `hifa search due:today
  --team ENG` name different days. The way to ask about a team's day is to name the team; the contract
  does not silently borrow one. `today` is computed only when `due` is `overdue`, `today` or `week`: `none`,
  `YYYY-MM-DD`, `>YYYY-MM-DD` and `<YYYY-MM-DD` read no timezone and issue no query for it (§5.1 step 6).
  The SQL never calls a system date function. **Testable:** with `now = 2026-11-20T02:00:00Z`, a query with
  `team:ENG` (`America/Santiago`, UTC−3) resolves `today = "2026-11-19"` and the same instant with no team
  resolves `today = "2026-11-20"`; both cases are pinned by the two scenarios of §7. Values: `none` →
  `due_date IS NULL`; `overdue` → `due_date < :today AND s.type NOT IN ('completed','canceled')`;
  `today` → `= :today`; `week` → `BETWEEN :today AND date(:today,'+7 days')`; `YYYY-MM-DD` → `=`; `>YYYY-MM-DD`
  / `<YYYY-MM-DD` → strict comparison. Any other format ⇒ `invalid_filter`.
- **R14 — Fallback to LIKE (REQ-F090-14), and the only list of retry triggers.** The `like` path is used
  when (a) `text.match is None` and `text.not_terms` is non-empty — a purely negative query, R4 — or
  (b) the `MATCH` raises `sqlite3.OperationalError` whose message **contains one of exactly four
  strings**. This is the only place in the corpus where those four are listed: §2.4 cites this rule and
  names no trigger of its own.

  | Trigger | What it means |
  |---|---|
  | `fts5:` | an FTS5 error of the engine itself (a syntax or shadow-table complaint) |
  | `malformed` | `database disk image is malformed`: the index cannot be read |
  | `no such module: fts5` | this interpreter's SQLite was built without FTS5, so the virtual table does not resolve |
  | `no such table` | `issue_fts` is gone (dropped, or a database that never ran the migration) |

  The last two are one situation with two messages — the index exists for the reader but not for this
  process — and they are matched the same way as the first two, by `in` over the driver's message, never
  by an exact comparison and never by a regex. The verification is a unit test that raises each of the
  four as `sqlite3.OperationalError` and asserts that `reads.search` answers `backend="like"`.

  **The bindings of the `like` path, positive and negated, are declared here and nowhere else.** For
  each **positive** term of `text.like_terms`, one binding `:p0`, `:p1`, … =
  `'%' + escape_like(term) + '%'`, and the predicate
  `(title LIKE :p ESCAPE '\' OR description LIKE :p ESCAPE '\' OR identifier LIKE :p ESCAPE '\')`
  with `AND` between terms. For each **negated** term of `text.not_terms`, one binding `:n0`, `:n1`, … of
  the **same** shape — `'%' + escape_like(term) + '%'`, `ESCAPE '\'` — used by
  `NOT (title LIKE :n ESCAPE '\' OR COALESCE(description, '') LIKE :n ESCAPE '\')`, one such `NOT` block
  per negated term, `AND` between blocks. `escape_like` escapes `\`, `%` and `_`, so a negated `-100%` is
  a literal. Both tuples are numbered independently and in order of appearance: `deploy -bug -infra`
  binds `:p0`, `:n0`, `:n1`. The match is **case-insensitive for ASCII only** — `LIKE` folds `A`–`Z`
  and nothing else in SQLite, so `-bug` also excludes `BUG` and `Bug`, and `-ñandú` excludes neither
  `Ñandú` nor `ÑANDÚ`. That is the same asymmetry the positive path already has, stated here because the
  no-positive form is nothing but negations.

  **A predicate under `NOT` treats a NULL column as empty.** `NULL LIKE '%x%'` is `NULL`, and `NOT NULL`
  is `NULL`, which the `WHERE` discards: an issue with `description IS NULL` would silently disappear
  from a negated query instead of surviving it. `description` is the only column of these predicates the
  schema leaves nullable (SPEC-010 §3.4: `title` and `identifier` are `NOT NULL`), so **every nullable
  column inside a `NOT (…)` is read as `COALESCE(column, '')`**, in both forms of §5.3. The `fts` path
  needs no such rule: the FTS5 index stores a NULL column as an empty one and `NOT` there is evaluated
  over the index, not over SQL NULL.

  The ranking of the `like` path is fixed: exact identifier (0), `title LIKE 'term%'` (1),
  `title LIKE '%term%'` (2), the rest (3). `SearchPage.backend` reports `"like"` and `score` is
  `0.0`. The fallback **never** changes the set of filters or the visibility. If there is no text
  (filters only), no text predicate is added and `backend` is `"none"`.
- **R15 — Snippet (REQ-F090-15).** On the `fts` path it uses
  `snippet(issue_fts, -1, '<mark>', '</mark>', '…', 12)` (automatic column, 12-token window).
  On the `like` path the snippet is built in Python: first case-insensitive occurrence of the first
  positive term in `title`; if it is not there, in `description`; a window of 60 characters on
  each side is taken and the occurrence is wrapped with `MARK_START`/`MARK_END`. If there is no occurrence, the
  snippet is the description truncated to 120 characters or the title. The snippet is **not** escaped
  here, and it is not escaped in `reads.py` either (REQ-S090-03): `MARK_START`/`MARK_END` are the only
  markup it carries, they arrive as the literal five- and six-character strings, and the text between
  two markers is raw. The escaping happens exactly once, in the read model of the page (SPEC-070 §2.3.5,
  R17), which splits the snippet on the marker pair, escapes each piece and rejoins the pieces around
  the literal `<mark>`/`</mark>`. Both backends produce the same shape, so one rule covers them: an
  edge that escaped it a second time would show `&lt;mark&gt;` to the reader, and an edge that inserted
  it whole (`|safe` over the raw snippet) would turn an issue title into executable markup.
- **R16 — Performance budget (REQ-N090-01).** With 10 000 issues and 40 000 description
  tokens, a search of 1 to 3 terms with `limit=50` and `count=False` must take
  **< 50 ms p50** and **< 120 ms p95** (warm connection, no `--count`). `count=True` has its own
  budget: **< 120 ms p50**. It is measured with `time.perf_counter` around
  `SearchService.search`, 30 iterations, discarding the first 5.
- **R17 — Query plan (REQ-N090-02).** The `EXPLAIN QUERY PLAN` of §5.2's query has two halves and they
  do not have the same reach. **First half, `SCAN issue`** (full table access): the plan must not contain
  it **under a `team` filter or a `team`+`state` filter**, it must resolve `issue` by rowid or by index
  there, and **under `state` alone it does contain it** — the query this spec publishes prints `SCAN i`
  in that case, so the requirement is scoped to the filters where it is true instead of asserted where it
  is false. **Second half, `MATERIALIZE`**: the plan must carry one `MATERIALIZE` row per CTE of §5.2 —
  `MATERIALIZE fts` and `MATERIALIZE title_hit` — **under every filter**, and it does. The two are one
  requirement because the `SCAN` clause alone is passed by a flat query with a `team` filter, and that
  query still costs 68-89 ms (§5.2's note). Measured on SQLite 3.53.1 with §5.2's text, both keywords,
  R16's 10 000-issue fixture and **no `ANALYZE`** — the `issue` access line of each plan; the join lines
  under it are left out because they move with row count:

  | filter | the plan of §5.2 as published |
  |---|---|
  | none | `MATERIALIZE fts` / `MATERIALIZE title_hit` / **`SCAN i`** |
  | `state` | `MATERIALIZE fts` / `MATERIALIZE title_hit` / **`SCAN i`** |
  | `team` | `MATERIALIZE fts` / `MATERIALIZE title_hit` / `SEARCH i USING INDEX` (`idx_issue_open`) |
  | `team`+`state` | `MATERIALIZE fts` / `MATERIALIZE title_hit` / `SEARCH i USING INDEX` (`idx_issue_team_state`) |

  So the first half is asserted **for `team` and `team`+`state` and for no other filter**: under `state`
  alone a test that asserted it would fail against the normative text, and with no filter the full scan is
  the right access — the unfiltered materialized form is 8.8 ms p50, inside R16's 50 ms half. The
  requirement **names no index**: `SEARCH i USING INDEX` is the assertion, because which of the `team_id`
  indexes SQLite picks moves with row count (`idx_issue_sort` at 200 rows, `idx_issue_open` at 10 000).
  Re-running the four statements on a 200-row schema-only fixture printed the same four `issue` access
  lines, so the split belongs to the query text and not to the fixture.
  **The `state` residual is coverage, not query form, and it is HIFA-82.** No index on `issue` leads with
  `state_id` (SPEC-010 §3.2: `idx_issue_team_state` is `(team_id, state_id)`, so a `state`-only predicate
  cannot use it), and a `SCAN` with no predicate of the query's own text behind it is the case SPEC-030 §10
  names — `030-sqlite-adapter.md:2009-2010`: *"if `EXPLAIN QUERY PLAN` shows a `SCAN`, the solution is a
  new index in a migration, not a change in the adapter"*. Its remedy is an index on `issue(state_id)` in a
  migration under SPEC-010, **not** anything this delta does: no schema, no migration and no index moves
  here. It is the **other half of the same rule** from the one D-073 used — there the `SCAN` was the
  flattened CTE, so the text was both cause and fix; here the text is right and the coverage is missing.
  **The `state` plan is the one the statistics decide, so this requirement is stated without them.** With
  `ANALYZE` the review's fixture resolved `idx_issue_team_state` for the `state` case and the `SCAN`
  disappeared; with `ANALYZE` on a skewed 200-row fixture all four cases print `SCAN i`; and nothing in the
  tree runs it (`grep -rn ANALYZE src/ scripts/ tests/` answers nothing). A `state`-only assertion would
  read whatever statistics the fixture happened to gather, so R17 is asserted on a database that has never
  run `ANALYZE` — every database this tree creates — and no `ANALYZE` is added by this revision. The test
  compares the plan text, not the timings (D-073).
  **None of the `MATERIALIZE` half is executable today**:
  `tests/integration/db/test_search_reads.py` inlines a copy
  of revision 9's CTE text and asserts only that `SCAN (i|issue)` is absent and `SEARCH i USING INDEX`
  present — nothing about `MATERIALIZE` — and `grep -rn MATERIALIZE tests/ scripts/` answers nothing, so
  removing the keywords from `_FTS_CTE` leaves the suite green: the rewrite is HIFA-81 and the test for
  §7's scenario is HIFA-80, both registered debt under AGENTS.md P8.
- **R18 — Read-only (REQ-F090-16).** `SearchService.search` does not open write transactions,
  does not execute write `PRAGMA` or `optimize`. The only write statements allowed in the whole
  component are the two FTS5 commands — `INSERT INTO issue_fts(issue_fts) VALUES('rebuild')`, executed
  **only** by `hifa db reindex`, and `VALUES('integrity-check')`, executed **only** by `hifa doctor`
  inside a transaction that always rolls back — and never by a search (SPEC-100 R20, R21).
- **R19 — `VACUUM` and rowid (REQ-F090-17).** Since `issue` has a `TEXT` PK, its `rowid` can be
  renumbered by `VACUUM`. Any path that executes `VACUUM` must immediately execute
  `INSERT INTO issue_fts(issue_fts) VALUES('rebuild')` in the same operation; `hifa doctor` detects
  the deviation with `'integrity-check'` (§SPEC-100 C8). This spec **does not** implement the rebuild: it only
  fixes the obligation.
- **R20 — Parameterized SQL (REQ-S090-01, SPEC-000 REQ-S002).** The dynamic construction of filters
  assembles **only constant** SQL fragments; every value travels as a bound parameter. The
  `IN (...)` clauses are generated with one `?` per element. No `f-string`/`%`/`.format()` touches a value:
  the REQ-S090-01 test injects `label:"x' OR 1=1 --"` and verifies that it returns 0 rows without a syntax
  error.
- **R21 — No network (REQ-S090-02).** The search opens no sockets and resolves no DNS; everything is local.

### 4.2 Non-functional and security requirements

| ID | Requirement | Verification |
|---|---|---|
| **REQ-N090-01** | < 50 ms p50 / < 120 ms p95 with 10 000 issues, 1-3 terms, `limit=50` (R16). | `tests/perf/test_search_speed.py`, 30 iterations, `time.perf_counter` |
| **REQ-N090-02** | No `SCAN issue` in the plan with a `team` or `team`+`state` filter — **not** with `state` alone, where §5.2 itself prints `SCAN i` (HIFA-82) — and one `MATERIALIZE` row per CTE of §5.2 under every filter (R17). | assert on `EXPLAIN QUERY PLAN`, on a database that has never run `ANALYZE` |
| **REQ-N090-03** | `count=True` < 120 ms p50 with 10 000 issues. | same harness, variant with `count=True` |
| **REQ-N090-04** | Index and triggers add no more than **15 %** to the INSERT time of an issue (vs. a table without triggers). | benchmark of 1 000 inserts with and without `DROP TRIGGER` on a test DB |
| **REQ-N090-05** | `app/search.py` imports neither `sqlite3` nor `fastapi`/`typer`/`jinja2`; the SQL lives in `reads.py` (SPEC-000 A1/A3). | `import-linter` + `grep -rn "import sqlite3" src/hifa/app/search.py` |
| **REQ-S090-01** | 100 % of values as bound parameters, including the dynamic filters (R20). | injection test with `label:"x' OR 1=1 --"`, `team:"' UNION SELECT 1 --"` |
| **REQ-S090-02** | No network, no writes, no file access outside the DB (R18/R21). | `socket.socket` monkeypatched to raise; assert that there is no `INSERT`/`UPDATE` in the connection trace |
| **REQ-S090-03** | The snippet is not escaped in `app/` or in `reads.py`: the `<mark>` must survive to the presentation layer and HTML escaping happens exactly once (SPEC-000 REQ-S003, SPEC-070). | snippet unit test + web view test |

## 5. Flow

### 5.1 Sequence of `SearchService.search`

```
 1. validate limit/offset (R11) and query limits (R5)        → invalid_limit / query_too_long
 2. terms ← sanitize_terms(query.text)
 3. if terms is empty and there are no filters               → query_empty
 4. pred         ← build_text_predicate(query.text)           (R4: match, match_title, like_terms, not_terms)
    ident_exact  ← complete identifier_candidate(query.text) or ""
    ident_like   ← prefix "KEY-" or "KEY-12" + "%" or ""
 5. three text states, no fourth (§2.4), decided on the predicate's own fields:
    pred.match is not None                              → fts path
    pred.match is None, pred.like_terms non-empty       → like path, first form (positives + negations)
    pred.match is None, pred.like_terms == (),
    pred.not_terms non-empty                            → like path, second form (negations only)
    pred.match is None and both tuples == ()            → no text predicate, backend = "none"
 6. today ← ""   unless filters.due ∈ {overdue, today, week}
    zone  ← reads.team_timezone(conn, team_key=filters.team) if filters.team else "UTC"   (R13)
    today ← now.astimezone(zone).date().isoformat()
 7. rows, total, backend ← reads.search(conn, text=pred, filters=query.filters,
                                         order=query.sort, limit=…, offset=…,
                                         count=query.count, today=today)
 8. has_more ← len(rows) > limit;  rows ← rows[:limit]
 9. build SearchPage(hits, total, has_more, backend, took_ms)
```

Steps 8 and 9 are the whole of §2.4's last port rule: the adapter may answer `limit + 1` rows, and this
is where the probe becomes a boolean and leaves the page. Step 4 is the only place the negated terms are
produced: `reads.search` receives them in `text.not_terms` and never looks at `query.text`, which it does
not have. `now` is the parameter of `search`, so this sequence contains no clock read at all; it contains
no team read either unless step 6 needs a timezone.

`took_ms` is measured with `time.perf_counter()` between step 1 and step 9 and rounded to 1 decimal; it is the
only non-deterministic field of the response and it **does not** enter the equality comparisons of the
tests (`pytest.approx` is used or it is ignored).

### 5.2 Reference query (normative, `fts` path)

The block below is the query revision 9 published, **byte for byte**, with one change in it: the two
`AS MATERIALIZED` keywords on the two CTE names. Nothing else in the query moved — no column, no
predicate, no binding, no order — and §5.2's notes say why the keywords are part of the contract and not
an optimisation an implementation may skip (D-073).

```sql
-- Parameters (all bound, none interpolated):
--   :match, :match_title, :ident_exact, :ident_like, :today, :limit_plus_one, :offset
--   plus the ones of each filter present: :team_key, :state, :assignee, :label, :priority, :due_date
WITH fts AS MATERIALIZED (
  SELECT rowid AS rid,
         bm25(issue_fts, 10.0, 1.0, 0.0)                                AS bm,
         snippet(issue_fts, -1, '<mark>', '</mark>', '…', 12)            AS snip
  FROM issue_fts
  WHERE issue_fts MATCH :match
),
title_hit AS MATERIALIZED (
  SELECT rowid AS rid FROM issue_fts WHERE issue_fts MATCH :match_title
)
SELECT i.id           AS issue_id,
       i.identifier   AS identifier,
       i.title        AS title,
       s.name         AS state_name,
       s.type         AS state_type,
       i.priority     AS priority,
       i.due_date     AS due_date,
       m.display_name AS assignee_display_name,
       i.updated_at   AS updated_at,
       COALESCE(f.snip, '')                                              AS snippet,
       CASE
         WHEN i.identifier = :ident_exact                  THEN 0
         WHEN t.rid IS NOT NULL                            THEN 1
         WHEN i.identifier LIKE :ident_like ESCAPE '\'     THEN 2
         ELSE 3
       END                                                               AS band,
       -COALESCE(f.bm, 0.0)                                              AS score
FROM issue i
JOIN workflow_state s ON s.id = i.state_id
LEFT JOIN member m    ON m.id = i.assignee_id
LEFT JOIN fts f       ON f.rid = i.rowid
LEFT JOIN title_hit t ON t.rid = i.rowid
WHERE (f.rid IS NOT NULL OR i.identifier LIKE :ident_like ESCAPE '\')
  AND i.trashed = 0
  AND i.archived_at IS NULL
  -- optional filters, each one appended as "AND (<constant fragment>)"
  -- if the filter is not present, the fragment is not appended (no NULL is passed)
ORDER BY band ASC,
         score DESC,
         CASE i.priority WHEN 0 THEN 5 ELSE i.priority END ASC,
         i.updated_at DESC,
         i.id ASC
LIMIT :limit_plus_one OFFSET :offset
```

Normative notes:

- `:ident_exact` and `:ident_like` are `''` when the input is not an identifier; `identifier = ''` and
  `identifier LIKE ''` are always false, so no conditional logic is needed in SQL.
- **The `title_hit` CTE is what lets a band say "matches the title" without bringing text into Python; the
  two `MATERIALIZED` keywords are what let it do so inside R16's budget, and they are part of the
  contract.** Without them SQLite flattens both CTEs into the join and makes `issue_fts` the inner loop:
  the plan is `SCAN i` plus two `SCAN issue_fts VIRTUAL TABLE INDEX 0:=M3 LEFT-JOIN` rows — correlated,
  so the virtual table is probed **once per row of `issue`**, 10 000 times on R16's fixture, twice, once
  per CTE. With them the plan is `MATERIALIZE fts` / `SCAN issue_fts VIRTUAL TABLE INDEX 0:M3` (once,
  uncorrelated) / `MATERIALIZE title_hit` / `SCAN issue_fts VIRTUAL TABLE INDEX 0:M3`, and the two joins
  probe materialized row sets. **The three rows are not of one provenance, and this note says which is
  which**, because a cost printed without the statement of what printed it is the defect this block
  exists to correct. The flat row is **the figure the defect was reported with**, not one a harness in
  this tree can print: R16's 10 000-issue fixture, three terms, `limit=50`, `count=False` — reported,
  and re-runnable only by retyping revision 9's text, because `_FTS_CTE` in the adapter always carries
  the keywords. The committed harness, `tests/perf/test_search_speed.py`, calls `search_issues` and so
  runs **the materialized form only**, with **two** terms and **always** a `team=ENG` filter: it can
  re-run the shape of the third row and no other, and it asserts R16's budget rather than printing these
  medians (D-073). The rows, as reported: the query **as revision 9 printed it** is
  **88.7 ms p50 / 94.5 ms p95**; the same query with the two keywords is **8.8 ms p50 / 9.1 ms p95**;
  the same form with a `team=ENG` filter added is **9.5 ms p50 / 10.0 ms p95**. Read against R16's two
  halves, exactly: the flat **p50 is 1.77× the 50 ms half** (88.7 / 50 = 1.774) while its **p95, 94.5 ms,
  stays inside the 120 ms half** (0.79 of it) — one half can be missed while the other passes, and
  **R16's first half is the one that catches this query**; the **~10× belongs to flat against
  materialized alone** (88.7 / 8.8 = 10.1), and that ratio is what the two keywords are worth. The cost
  is the **plan**, not the row count, the snippet or a second query: on that fixture `MATCH` alone is
  0.03 ms, `MATCH + bm25` 0.06 ms, `+ snippet` over the 25 matches 0.28 ms, and joining the matches to
  `issue` 0.35 ms — against the 68-89 ms the flat plan costs, three orders of magnitude above the
  0.03 ms a bare `MATCH` takes. **`0.06 ms` is not the second `MATCH`** — that step of the series is
  `MATCH` → `MATCH + bm25`. The second `MATCH` was measured on its own: a two-`MATCH` materialized query
  costs **6.70 ms p50 against 6.58 ms** for the same query with one, on a 10 000-issue fixture whose terms
  select 115 rows (1.02×) — the pair names its fixture, its term selectivity and its statistic, which is
  the minimum a ratio owes a reader. A second pair that stood here, 5.09 against 4.90 ms (1.04×), is
  withdrawn rather than completed: the record states neither its fixture nor its term selectivity nor its
  iteration count, and *a cost printed without the statement of what printed it* is the defect this block
  exists to correct. Repeating the second `MATCH` once per row of `issue` is what costs the budget.
  **Which plan a flat form gets depends on the filter, and the filter decides which half of R17 the flat
  form fails.** Under a `team` filter the flat plan is `SEARCH i USING INDEX idx_issue_open`: it passes
  the `SCAN issue` half while costing the same 68-89 ms, so only the missing `MATERIALIZE` rows expose it.
  Unfiltered, and under a `state` filter, the same text is `SCAN i` — and so is **the materialized form**
  under `state` alone, which is why R17's first half is asserted for `team` and `team`+`state` and the
  `state` case is filed as coverage, not as a query-form defect (HIFA-82; R17, §4.1). The two
  `MATERIALIZE` rows are absent from every flat form and present in every materialized one, which is why
  that half carries no filter and §7 pins it with a scenario (REQ-N090-02, D-073).
- `count=True` reuses the same `WHERE` in a `SELECT COUNT(*)` with the CTEs included
  (`COUNT(DISTINCT i.rowid)`); `COUNT(*) OVER ()` is **not** used because it forces materializing the whole
  result.
- The filter fragments are constant by construction: `p.team` is
  `"i.team_id IN (SELECT id FROM team WHERE key = ?)"` and the value goes separately (R20). An `IN` with N
  values generates `IN (?,?,?)` with `N` parameters.

### 5.3 `like` path (fallback)

Two forms, one per `like`-path state of §2.4's table. This is the first — a predicate with at least one
**positive** term, which is R14b's retry and §2.4's second row — and its bands are decided by the
query's first positive term:

```sql
SELECT i.id, i.identifier, i.title, s.name, s.type, i.priority, i.due_date,
       m.display_name, i.updated_at, '' AS snippet,
       CASE WHEN i.identifier = :ident_exact THEN 0
            WHEN i.title LIKE :first_prefix ESCAPE '\' THEN 1
            WHEN i.title LIKE :first_contains ESCAPE '\' THEN 2
            ELSE 3 END AS band
FROM issue i
JOIN workflow_state s ON s.id = i.state_id
LEFT JOIN member m    ON m.id = i.assignee_id
WHERE ( i.title LIKE :p0 ESCAPE '\' OR i.description LIKE :p0 ESCAPE '\'
        OR i.identifier LIKE :p0 ESCAPE '\' )
  [ AND ( … ) for each additional positive term ]
  [ AND NOT ( i.title LIKE :n0 ESCAPE '\' OR COALESCE(i.description, '') LIKE :n0 ESCAPE '\' ) for each negated term ]
  AND i.trashed = 0 AND i.archived_at IS NULL
ORDER BY band ASC, i.updated_at DESC, i.id ASC
LIMIT :limit_plus_one OFFSET :offset
```

The `COALESCE` inside `NOT (…)` is R14's NULL rule and not a decoration: with `description IS NULL` the
bare `NOT (title LIKE :n0 OR description LIKE :n0)` evaluates to `NULL` for a row whose title does not
carry the term, and the row is dropped from a query it should survive (§12 has the measurement). The
positive disjunction above needs no `COALESCE` — a `NULL` there only makes one disjunct unknown, and the
row is returned as soon as any other disjunct is true.

The snippet of this path is computed after the fetch, in `SearchService`, following R15.

**The form with no positive term.** `hifa search -bug` carries text and has no positive term, so R4
returns `match=None` and §2.4's third row sends the query here with a predicate made only of negations.
The form above cannot express it: `:p0`, `:first_prefix` and `:first_contains` are derived from a *first
positive* term, and there is none. The same query is emitted as this one:

```sql
SELECT i.id, i.identifier, i.title, s.name, s.type, i.priority, i.due_date,
       m.display_name, i.updated_at, '' AS snippet,
       0 AS band
FROM issue i
JOIN workflow_state s ON s.id = i.state_id
LEFT JOIN member m    ON m.id = i.assignee_id
WHERE NOT ( i.title LIKE :n0 ESCAPE '\' OR COALESCE(i.description, '') LIKE :n0 ESCAPE '\' )
  [ AND NOT ( i.title LIKE :n1 ESCAPE '\' OR COALESCE(i.description, '') LIKE :n1 ESCAPE '\' ) for each further negated term ]
  AND i.trashed = 0 AND i.archived_at IS NULL
ORDER BY i.updated_at DESC, i.id ASC
LIMIT :limit_plus_one OFFSET :offset
```

Its bindings are exactly the negated terms (`:n0`, `:n1`, …, each `'%' + escape_like(term) + '%'` with
`ESCAPE '\'`, the shape R14 declares), the parameters of the filters present, `:limit_plus_one` and
`:offset`: it binds **no** `:p0`, no `:first_prefix`, no `:first_contains` and no `:ident_exact` — an
identifier candidate is derived from a positive term and there is none. `band` is the constant `0`,
because there is nothing to rank above anything else, and the order is the tail of the first form without
the band: `i.updated_at DESC, i.id ASC`. The rows are the issues whose title and description carry none
of the negated terms, under the same visibility and filter rules as the first form; the snippet follows
R15's last case (no positive term ⇒ the description truncated to 120 characters, or the title). Its
`NOT (…)` blocks carry the same `COALESCE(i.description, '')` for the same reason, and here it is the
whole predicate: an issue with no description at all is exactly the row a bare `NOT` would lose. A query
with neither a positive nor a negated term is **not** this form: it is §2.4's `backend="none"`, with no
text predicate at all.

## 6. Errors

| Error | `code` | `message` | When | CLI | HTTP |
|---|---|---|---|---|---|
| `SearchError` | `query_empty` | `nothing to search for` | no text and no filters | exit 2, stderr | 422 |
| `SearchError` | `query_too_long` | `the query is longer than 512 characters or has more than 16 terms or a term longer than 128 characters` | > 512 characters, > 16 terms or term > 128 | exit 2 | 422 |
| `SearchError` | `invalid_limit` | `limit must be between 1 and 200` (`limit`) · `offset cannot be negative` (`offset`) | `limit` outside 1..200 or `offset < 0` | exit 2 | 422 |
| `SearchError` | `invalid_filter` | `'<value>' is not a value of <field>` | invalid value of a known field (`priority:urgentísimo`, `due:ayer`, `-due:…`) | exit 2 | 422 |
| `InternalError` | `internal_error` | — (the driver's message, on `stderr`) | a `MATCH` that R14's four triggers did not cover, and any other unmapped exception | exit 6 | 500 |

- **`code` is `error.code` and `field` names the input, on every edge.** `limit`, `offset`, `priority`,
  `due`, `q` — the input that carried the refusal — is what `error.field` publishes; a code never
  travels there. The four codes are the contract of this section, and SPEC-050 §6 and SPEC-060 §6.1
  publish them as they are: one refusal, one envelope, no edge renaming a code or a field.
- The search does **not** produce `NotFound`, `Conflict` or `InvalidTransition`: a filter that matches
  nothing is a valid response with `hits=[]`, `total=0` (if requested) and exit 0.
- **One class, four codes.** `SearchError` is the `Validation` subclass of §2.2 and the only exception
  this component raises for input it refuses: it carries `code` (the four values of the table), `message`
  (its column) and `field`. No edge special-cases it — the CLI maps it as every other `Validation` (exit
  2) and the API as `422` (SPEC-050 §6, SPEC-060 §6.1) — and the class exists so the four codes have one
  owner. The four are **lowercase envelope codes**, and that is what makes them survive the CLI's mapping:
  a code that is already lowercase is passed through as the envelope's `error.code`, the mechanism
  `invalid_config` uses, instead of collapsing into `validation_error` (declared in SPEC-050 §6, in the
  `Validation` row's `error.code` cell, which is where the pass-through lives; this contract names no
  module and no private function of `cli/`).
- `query_empty` is a refusal of the **edges that are asked to run a query**: `hifa search` with nothing
  to search and `GET /api/v1/search` without `q` and without filters (SPEC-060 §2.5.4). `GET /search`
  is a page, and a page with no `q` is its empty state, `200` (SPEC-070 §2.1.8, D-063).
- **A degraded index is not a failed search.** A database whose `issue_fts` is missing, and a Python
  whose SQLite was built without FTS5, are answered through the `like` path with `backend="like"` and
  exit 0 (R14b): the rows are still there and a search that can read `issue` is worth more than an
  error. `hifa db reindex` is the command that refuses the build loudly instead (SPEC-100 R21, exit 6
  `fts5_unavailable`) and the one that **recreates** a missing `issue_fts` before it rebuilds it, so the
  advice a degraded search carries is the one command that repairs it.
- An FTS `sqlite3.OperationalError` that matches none of R14's four triggers is an **infrastructure**
  failure, not a validation one: it leaves the command as exit 6, `internal_error`, with the driver's
  message on `stderr` and no traceback in the human output (`--verbose` shows it). Exit 2 is reserved
  for what the user typed.
- `took_ms` is never a reason for error, not even if it exceeds the R16 budget: the budget is
  verified in performance tests, not at runtime.

## 7. Acceptance Criteria

```gherkin
@SPEC-090
Feature: Full-text search over issues

  Background:
    Given a database with team "ENG" and the 7 default workflow_states
    And 3 issues: "Deploy roto en producción", "Arreglar deploy de staging" and "Diseño del dashboard"

  Scenario: free text matches by title
    When "hifa search deploy" is executed
    Then the exit code is 0
    And the result contains 2 hits
    And the first hit has "deploy" between <mark> and </mark>
    And backend is "fts"

  Scenario: diacritics do not affect the search
    Given an issue with title "Diseño del dashboard"
    When "hifa search diseno" is executed
    Then the result contains the issue "Diseño del dashboard"
    And the score is greater than 0

  Scenario: a quoted phrase requires exact order
    Given issues with "deploy roto" and "roto deploy" in the title
    When "hifa search '\"deploy roto\"'" is executed
    Then the result contains only the issue "deploy roto"

  Scenario: prefix with asterisk
    Given an issue with title "Autenticación OAuth"
    When "hifa search autent*" is executed
    Then the result contains the issue "Autenticación OAuth"

  Scenario: FTS operators are treated as text and do not break the syntax
    When "hifa search 'foo OR bar NOT baz NEAR'" is executed
    Then the exit code is 0
    And backend is "fts"
    And no MATCH syntax error is raised

  Scenario: unbalanced quotes do not break the MATCH
    When "hifa search 'deploy \"roto'" is executed
    Then the exit code is 0
    And the result contains the issue "Deploy roto en producción"

  Scenario: filters combined with text
    Given an issue "Deploy roto" in state "In Progress" with label "bug" and priority 1
    When "hifa search deploy --state 'In Progress' --label bug --priority urgent" is executed
    Then the result contains exactly that issue
    And an issue "Deploy roto" in state "Done" does not appear

  Scenario: filters embedded in the text
    When "hifa search 'deploy state:\"In Progress\"'" is executed
    Then the state filter is applied and the searched text is "deploy"

  Scenario: trashed and archived issues are excluded by default
    Given an issue with title "Deploy archivado" with a non-null archived_at
    And an issue with title "Deploy borrado" with trashed 1
    When "hifa search deploy" is executed
    Then neither of the two appears in the result
    When "hifa search deploy --is all" is executed
    Then both appear in the result

  Scenario: an exact identifier wins first place
    Given an issue "ENG-42" with title "Cualquier cosa"
    And 5 issues whose title contains "eng"
    When "hifa search ENG-42" is executed
    Then the first hit is "ENG-42"
    And its score corresponds to band 0

  Scenario: the title weighs more than the description
    Given an issue with title "login" and a long description
    And an issue with title "otra cosa" and a description that mentions "login" once
    When "hifa search login" is executed
    Then the issue with "login" in the title appears before the one with it in the description
    And the two hits have a different band (1 and 3)

  Scenario: fallback to LIKE when FTS cannot parse
    Given a database where "issue_fts" was dropped
    When "hifa search deploy" is executed
    Then the exit code is 0
    And backend is "like"
    And the result contains the issue "Deploy roto en producción"

  Scenario: pagination with has_more
    Given 120 issues whose title contains "pago"
    When "hifa search pago --limit 50" is executed
    Then the result has 50 hits
    And has_more is true
    When "hifa search pago --limit 50 --offset 100" is executed
    Then the result has 20 hits
    And has_more is false

  Scenario: the triggers keep the index up to date on update and delete
    Given an indexed issue with title "alpha"
    When its title is updated to "beta" through the use case of SPEC-040
    Then "hifa search alpha" does not return it
    And "hifa search beta" does return it
    When the issue row is deleted
    Then "hifa search beta" does not return it
    And "INSERT INTO issue_fts(issue_fts) VALUES('integrity-check')" does not fail

  Scenario: an injection in a filter does not alter the SQL
    When "hifa search 'deploy label:\"x' OR 1=1 --\"'" is executed
    Then the exit code is 0
    And the result has 0 hits
    And the issue table still has the same rows as before

  Scenario: search within budget with 10 000 issues
    Given a database with 10 000 issues and their descriptions
    When 30 two-term searches with limit 50 are executed
    Then the p50 is less than 50 ms
    And the p95 is less than 120 ms
    And the backend is "fts" in all 30

  Scenario: the query plan does not scan issue
    Given a search with team filter "ENG"
    When the EXPLAIN QUERY PLAN of the query of §5.2 is executed
    Then the plan does not contain "SCAN issue"

  Scenario: the two CTEs of the reference query are materialized
    Given a search with team filter "ENG"
    When the EXPLAIN QUERY PLAN of the query of §5.2 is executed
    Then the plan contains "MATERIALIZE fts"
    And the plan contains "MATERIALIZE title_hit"

  Scenario: only --count produces a total
    When "hifa search deploy --json" is executed
    Then the "total" field is null
    When "hifa search deploy --json --count" is executed
    Then the "total" field is 2

  Scenario: a limit outside the range is a validation error
    When "hifa search deploy --limit 201 --json" is executed
    Then the exit code is 2
    And error.code is "invalid_limit"
    And error.field is "limit"

  Scenario: an offset past the end is an empty page and not an error
    When "hifa search deploy --offset 100 --count --json" is executed
    Then the exit code is 0
    And the "hits" field is empty
    And the "has_more" field is false
    And the "total" field is 2

  Scenario: an empty invocation is a refusal of the command, not an empty page
    When "hifa search" is executed
    Then the exit code is 2
    And error.code is "query_empty"

  Scenario: a database this Python cannot read through FTS5 still answers
    Given a database whose "issue_fts" is not readable by the running Python
    When "hifa search deploy --json" is executed
    Then the exit code is 0
    And the "backend" field is "like"
    And the result contains 2 hits

  Scenario: the snippet leaves this component unescaped
    Given an issue with title "Deploy the <form> tag"
    When "hifa search deploy --json" is executed
    Then a snippet contains the literal "<form>"
    And a snippet contains the literal "<mark>"

  Scenario: a purely negative query goes through the like path
    Given the 3 issues of the Background, none of whose titles carries "bug"
    And an issue "Bug del deploy" whose title carries "bug"
    When "hifa search -bug" is executed
    Then the exit code is 0
    And backend is "like"
    And the result holds the 3 issues of the Background and not "Bug del deploy"
    And every hit has score 0.0 and a snippet of R15's last case

  Scenario: a filters-only search adds no text predicate
    Given 3 issues, one of them in state "Done"
    When "hifa search --state Done --json" is executed
    Then the exit code is 0
    And the "backend" field is "none"
    And the result contains every issue in state "Done"
    And the result contains no issue in any other state

  Scenario: a relative due window resolves in the team's timezone
    Given team "ENG" with timezone "America/Santiago"
    And an issue due "2026-11-19" and an issue due "2026-11-20"
    When "hifa search due:today --team ENG --json" is executed at "2026-11-20T02:00:00Z"
    Then the result contains the issue due "2026-11-19"
    And the result does not contain the issue due "2026-11-20"

  Scenario: a relative due window with no team is the UTC day
    Given an issue due "2026-11-19" and an issue due "2026-11-20"
    When "hifa search due:today --json" is executed at "2026-11-20T02:00:00Z"
    Then the result contains the issue due "2026-11-20"
    And the result does not contain the issue due "2026-11-19"

  Scenario: the LIKE triggers are the four of R14
    Given a connection whose next MATCH raises OperationalError "no such module: fts5"
    When "hifa search deploy --json" is executed
    Then the exit code is 0
    And the "backend" field is "like"
```

## 8. Regeneration checklist

1. Read `SPEC-000` + `SPEC-010` + this spec in full.
2. In `migrations/001_init.sql`, verify that `issue_fts` matches SPEC-010 §3.4 **character by character**
   (tokenizer `unicode61 remove_diacritics 2`, `content='issue'`,
   `identifier UNINDEXED`) and add the 3 triggers of §3.1 with those exact names
   (`issue_fts_ai`, `issue_fts_ad`, `issue_fts_au`).
3. Create `src/hifa/app/search.py` with the constants, dataclasses and functions of §2.1-2.3. No SQL.
4. Implement `sanitize_terms` → `build_match_expression` → `build_title_match_expression` →
   `build_text_predicate` with unit tests covering: unbalanced quotes, `OR`/`NOT`/`NEAR`, parentheses,
   `%`, `_`, empty terms, prefix, phrase and negation — the last one asserting that `-bug deploy` and
   `deploy -bug -infra` split into the two tuples of §2.2 with the `-` stripped. No input may produce an
   invalid `MATCH`: the parameterized test runs
   `conn.execute("SELECT rowid FROM issue_fts WHERE issue_fts MATCH ?", …)`
   over 100 adversarial inputs and requires 0 `OperationalError`.
5. Implement `parse_query` returning `ParsedQuery` (§2.3) respecting R6/R7/R8 and `PRIORITY_NAMES`; it
   takes one argument and reads no configuration.
6. Implement `reads.search` with the query of §5.2, assembling the filter fragments as
   constants and the values as parameters, and `reads.team_timezone` for R13. `limit + 1` rows out of the
   adapter; the trim belongs to `SearchService`.
7. Implement the `like` path of §5.3 and the `try/except sqlite3.OperationalError` that falls back to it
   on R14's four triggers — the list lives there and nowhere else.
8. Implement the snippet of the `like` path in `SearchService` (R15), and the `backend="none"` state of
   §2.4 (no text ⇒ no text predicate).
9. **The two commands of this delivery are created from their own specs' contracts, in this ticket**:
   `hifa search` from SPEC-050 §2.5.1 (its flags, its table of §2.4.1.1, its exit codes) and
   `hifa db reindex` from SPEC-100 §2.7/R21 (its transaction, its `{"reindexed": N}`, its exit `0/3/4/6`)
   — they are what the acceptance of §7 (`10-search.feature`) needs. This spec claims **no `cli/**`
   file**: `hifa search` lives in `cli/commands/search.py` under SPEC-050's `implements:` and `db reindex`
   in `cli/commands/db.py` under SPEC-100's, so neither module appears in this document's frontmatter and
   §8 asks for no code below `app/` and `adapters/`.
10. Cover the 29 scenarios of §7 and the performance test of R16 with the 10 000-issue fixture
    (bulk insert + `INSERT INTO issue_fts(issue_fts) VALUES('rebuild')`). The one that names the plan
    asserts both the absence of `SCAN issue` and the two `MATERIALIZE` rows of R17 — the `team` filter it
    runs under being the one R17 asserts the first half for, since §7 carries no `state`-only plan
    scenario (HIFA-82) — and **no test runs
    it today**: `tests/integration/db/test_search_reads.py` inlines a copy of revision 9's CTE text and
    asserts that `SCAN (i|issue)` is absent and `SEARCH i USING INDEX` present, nothing about
    `MATERIALIZE`, so it certifies a query the tree does not run and stays green with the keywords removed
    (`grep -rn MATERIALIZE tests/ scripts/` answers nothing). The step is a contract with two tickets
    against it rather than a covered case: **HIFA-81** (the only query-plan test inlines a copy of the
    CTE text instead of the adapter query) and **HIFA-80** (a test for §7's materialization scenario).
    Until they land, AGENTS.md P8 is satisfied by the registered debt, not by a green test.
11. `pytest tests -k search` green; `python scripts/spec_contract_check.py --spec
    specs/090-search-fts.md` with no drift in `FTS_WEIGHTS`, `DEFAULT_LIMIT`, `MAX_LIMIT` and the
    signatures of `search_issues`/`parse_query`/`SearchService`.
12. `grep -rn "import sqlite3" src/hifa/app/search.py` must return 0 lines.
13. Render `GET /search` from the read model of SPEC-070 §2.3.5: four states, `count=False`,
    `len(hits)` instead of a total, and the snippet escaped once (SPEC-070 R17).

## 9. Provenance

- `tokenize='unicode61 remove_diacritics 2'`, `content='issue'`, `content_rowid='rowid'`: SQLite's
  FTS5 (official documentation: external content tables and `bm25`), fixed in SPEC-010 §3.4.
- bm25 weights `10.0 / 1.0`: product choice (a match in the title is worth ~10 times one in the
  description), which is the ranking that behaves best when people search for an issue by name;
  `identifier` is `0.0` because the column is `UNINDEXED`.
- Ranking bands (0 exact identifier, 1 title, 2 identifier prefix, 3 description): a consequence
  of `identifier` being `UNINDEXED` in SPEC-010 — the "identifier between title and
  description" weight is implemented as a band and not as a bm25 weight.
- Absence of boolean syntax in v1: author's decision (2026-11-08) to avoid exposing the
  FTS5 grammar to the user; `OR`/`NOT`/`NEAR` as text avoids 100 % of the parse errors
  reported with `MATCH`.
- Fallback to `LIKE`: standard pattern for optional FTS; the 50 ms budget holds because
  the `like` path is only activated on degenerate queries or with the table missing.
- `AFTER INSERT/DELETE/UPDATE OF` triggers: canonical form documented by SQLite for external
  content tables (the `'delete'` branch with the `old.*` values is mandatory or the index gets corrupted).
- `VACUUM` + rowid: explicit warning in the SQLite documentation about rowids of tables with a
  non-integer PK; hence R19 and check C8 of SPEC-100.

## 10. Out of scope

- Searching comments, attachments, projects or labels (only `issue.title`/`description`).
- Stemming, synonyms, typo tolerance, transliteration or semantic/vector search.
- Boolean syntax (`AND`/`OR`/`NOT`/`NEAR` as operators) and filters by creation date
  ranges.
- Saved searches, history, suggestions and autocomplete.
- Automatic reindexing: the rebuild is explicit (`hifa db reindex`, SPEC-100).
- Multi-workspace search: v1 assumes one workspace per database.

## 11. Interface behaviour (P3)

This component owns one edge surface of its own — `hifa search` — plus an operational command,
`hifa db reindex`. The page that consumes the same use case (`GET /search`) declares its four states,
its 320 px layout and its keyboard path in SPEC-070 §2.1.8; nothing of that is repeated here.

| Surface | Empty | No results | Error |
|---|---|---|---|
| `hifa search` (table) | not reachable: no query and no filter is `query_empty`, exit 2, `stdout` untouched | empty `stdout`, `0 issues` on `stderr` — the empty-collection line of SPEC-050 §2.4.1, exit 0; `--quiet` prints nothing | §6's sentence on `stderr` and its exit code; `stdout` stays empty |
| `hifa search --json` | the same `query_empty` | `{"hits": [], "total": null, "has_more": false, "backend": "fts"\|"like"\|"none", "took_ms": …}` — `total` is `0` only with `--count` | the error envelope of SPEC-050 §2.4.5, exit 2 or 6 |
| `hifa db reindex` | an empty database rebuilds `0` issues: `{"reindexed": 0}`, exit 0 — "there was nothing to rebuild" is a success, not a no-op error | — | exit 3 `database_not_found` (no such file) or `not_a_hifa_database` (a file that opens but is not a hifa database); exit 4 `DB_READONLY` (an intact WAL database in a directory the process cannot write — the read-only probe cannot create its `-shm`); exit 6 `fts5_unavailable` on a build without FTS5 or `corrupt_database` on an image SQLite refuses — the set is `0, 3, 4, 6` (SPEC-100 R21, D-066, D-069) |

- **No loading state.** The command is one synchronous query against a local file: there is no progress
  to report and no partial output to flush. A long search prints nothing until it has its rows.
- **No focus, no keys, no breakpoints.** `hifa search` reads its arguments from `argv` and never reads
  `stdin`: it has no interactive mode, so P3's keyboard path and mobile layout do not apply to it. The
  one terminal-dependent decision is the table width, which is SPEC-050 §2.4's rule
  (`min(terminal_width, 120)`, `120` without a TTY) and not a rule of this spec.
- **`--quiet` is the machine-readable identifier list**: one `identifier` per hit, no header, no color
  (SPEC-050 §2.4.4), and no snippet — a consumer that wants the snippet asks for `--json`.
- **`hifa db reindex` is the only repair**, and it is explicit: nothing in the search path ever rebuilds
  the index (R18). A user who sees `backend="like"` in a result has one corrective action, and the
  command states it in its own `--verbose` output rather than repairing anything by itself.

## 12. Notes

- spec_revision 12 (2026-11-20): **round three: R17's `SCAN issue` half is not a property a `state`-only
  query has, and two numbers in revisions 10-11 were not what the record produced (HIFA-7, third review of
  D-073).** `version` stays 4 — no signature, no dataclass, no flag, no envelope. §5.2's query block, §7's
  29 scenarios and the 240 `.feature` scenarios do not move; §4.1's R17, REQ-N090-02's row, §5.2's note and
  §8 step 10 do. (1) **R17's first half was falsified by the query it is asserted over.** With §5.2's text
  and both CTEs materialized, on SQLite 3.53.1 and without `ANALYZE`: a `team` filter gives
  `SEARCH i USING INDEX idx_issue_open`, `team`+`state` gives `SEARCH i USING INDEX idx_issue_team_state`,
  and **`state` alone gives `SCAN i`** — the normative query's own plan, not a deviation from it. R17 now
  prints the four-row table, asserts the half for `team` and `team`+`state` and for no other filter, and
  files the `state` case for what it is: **coverage**, whose remedy under SPEC-030 §10 is an index on
  `issue(state_id)` in a migration under SPEC-010 — **HIFA-82**, not something this revision adds. That is
  the other half of the SPEC-030 rule from the one D-073 used. (2) **The `state` plan is the statistic's,
  so the requirement is stated without statistics.** With `ANALYZE` the review's fixture lost the `SCAN`
  (it resolved `idx_issue_team_state`); with `ANALYZE` on a skewed 200-row fixture all four cases print
  `SCAN i`; and nothing in the tree runs it (`grep -rn ANALYZE src/ scripts/ tests/` answers nothing). R17
  is therefore asserted on a database that has never run `ANALYZE`, which is every database this tree
  creates, and no `ANALYZE` is added. (3) **The correction's own factor was wrong.** D-073 said the phrase
  "by an order of magnitude" overstated the miss "by 4×", a quotient none of its operands produce: the
  claim was ~10× against R16's p50 half, the ratio is **1.774** (88.7 / 50), and **10 / 1.774 = 5.6**, so
  the phrase multiplied the miss by five and a half — and the p95 half was never missed at all (94.5 ms,
  0.79 of the 120 ms). D-073 now shows the division. (4) **The half-specified `MATCH` pair is withdrawn.**
  5.09 against 4.90 ms (1.04×) carried no fixture, no term selectivity and no iteration count, so it is
  removed from §5.2 and D-073 rather than completed — by the standard that same block states. What stays
  is the pair that names its fixture and its selectivity (10 000 issues, terms selecting 115 rows) with its
  result, 6.70 vs 6.58 ms p50 (1.02×).

- spec_revision 11 (2026-11-20): **five sentences of revision 10 disagreed with revision 10's own
  numbers, and the coverage it claimed does not exist (HIFA-7, review of D-073).** `version` stays 4 and
  no normative content moves: §5.2's query, R17's two halves, §7's 29 scenarios, §8's count and the 240
  `.feature` scenarios are what revision 10 published. What moves is what the text says about them.
  (1) **The miss is 1.77×, not an order of magnitude.** 88.7 / 50 = 1.77 on R16's p50 half; 94.5 ms
  stays **inside** the 120 ms p95 half (0.79 of it); the ~10× belongs to flat against materialized
  alone (88.7 / 8.8 = 10.1), and §5.2 now prints it as that ratio and no other. A query can miss one
  half and pass the other, so **R16's first half is the one that catches this defect** — the p95 half
  passes it, and a text that claims an order of magnitude over the budget for a 1.77× miss overstates the
  failure in the same breath as it corrects one. (2) **The scenario has no executable home, and revision
  10's "its executable home is the search test" said it had.** `grep -rn MATERIALIZE tests/ scripts/`
  answers nothing; the suite's only plan test
  (`tests/integration/db/test_search_reads.py::test_the_query_plan_of_the_ranked_query_does_not_scan_the_issue_table`)
  inlines a copy of revision 9's CTE text and asserts that `SCAN (i|issue)` is absent and
  `SEARCH i USING INDEX` present — nothing about `MATERIALIZE` — so it certifies a query the tree does not
  run and stays green with the keywords removed. §4.1, §8 step 10 and this note now name the gap where
  they used to claim coverage, with the two tickets — **HIFA-81**
  (the plan test reads the adapter's query) and **HIFA-80** (a test for §7's scenario) — as the
  registered debt AGENTS.md P8 asks for. (3) **`0.06 ms` was not the second `MATCH`.** That step of the
  elimination series is `MATCH` → `MATCH + bm25`; the second `MATCH` over a materialized CTE was
  measured separately at **6.70 vs 6.58 ms p50 (1.02×)** on a 10 000-issue fixture whose terms select 115
  rows — a fraction of a millisecond, not a multiplier, and §5.2 now says so instead of annexing another
  series' increment. A second pair printed here (5.09 vs 4.90 ms, 1.04×) is withdrawn in revision 12:
  nothing in the record says what produced it. (4) **R17's
  premise and its example disagreed about the same filter.** Unfiltered and under a `state` filter the
  flat plan is `SCAN i`; under a `team` filter it is `SEARCH i USING INDEX idx_issue_open`, which
  **passes** the first half — as the next clause of the same sentence said. R17 and §5.2 now assign each
  filter its own plan, and **revision 12 splits the requirement itself to match**: the `SCAN issue` half is
  asserted for `team` and `team`+`state` and no other filter, the `state` case is filed as coverage
  (HIFA-82), and the `MATERIALIZE` half carries no filter at all. (5) **The three rows of §5.2 carry their provenance.** Only the team-filtered row
  has a committed harness behind it, and only in shape (`search_issues`, two terms); the other two are
  the figures the defect was reported with, and no harness in the tree can re-run the flat one because
  the adapter always materializes. D-073's ninth paragraph records these five corrections and reconciles
  SPEC-030's "a new index in a migration, not a change in the adapter" with this fix.

- spec_revision 10 (2026-11-20): **the reference query's two CTEs are `MATERIALIZED`, and the number that
  says why is now on the page (HIFA-7, D-073).** `version` stays 4: §2 does not move — no signature, no
  dataclass, no flag, no envelope — and what changes is §5.2's query body (a §5 block), R17's second half
  (§4.1) and the §7 scenario that pins them. (1) **§5.2, implemented verbatim, misses the p50 half of its
  own budget by 1.77× and passes the p95 half (revision 11).** On R16's 10 000-issue fixture, three terms,
  `limit=50`, `count=False`: revision 9's block measures **88.7 ms p50 / 94.5 ms p95**, the same query with
  `AS MATERIALIZED` on `fts` and on `title_hit` measures **8.8 ms p50 / 9.1 ms p95**, and the same form
  with a `team=ENG` filter added **9.5 / 10.0** — against R16's 50 ms p50. The flattening is the whole of
  the difference and it is visible in the plan: revision 9's form is `SCAN i` plus two
  `SCAN issue_fts VIRTUAL TABLE INDEX 0:=M3 LEFT-JOIN` rows, the virtual table probed once per row of
  `issue`; the keywords give `MATERIALIZE fts` / `SCAN issue_fts VIRTUAL TABLE INDEX 0:M3` /
  `MATERIALIZE title_hit` / `SCAN issue_fts VIRTUAL TABLE INDEX 0:M3`, each `MATCH` run once. The note
  that stood in §5.2 said the second `MATCH` cost "~1.6× that of a single `MATCH`, within the R16 budget":
  true of neither form — `0.03 ms → 0.06 ms` is `MATCH` → `MATCH + bm25`, and a second `MATCH` over a
  materialized CTE costs 6.70 vs 6.58 ms p50 (revision 11) — false of the published one, and it is
  replaced by both measured rows and the plan lines, so the keyword cannot be read as optional. (2)
  **R17 names the materialization.** Its `SCAN issue` half is passable by a query that misses R16's p50
  half: with a **team** filter the flat plan is `SEARCH i USING INDEX idx_issue_open` and still costs
  68-89 ms, while unfiltered and under a **state** filter the same text is `SCAN i` (revision 11; revision
  12 scopes the half to `team` and `team`+`state` — §5.2's own materialized plan under `state` alone is
  `SCAN i` too, and that residual is HIFA-82). R17
  now also requires `MATERIALIZE fts` and `MATERIALIZE title_hit`, and REQ-N090-02's row says so — a plan
  assertion that only forbids a scan is not a budget assertion. (3) **§7 gains one scenario** — 28 → **29**
  inline scenarios — that runs the `EXPLAIN QUERY PLAN` of §5.2 and asserts those two rows; both strings
  come from a plan captured on SQLite 3.53.1 over a 10 000-issue fixture, the flat plan carries neither, so
  the scenario fails without the keywords **once something runs it — nothing does yet (revision 11;
  HIFA-80, HIFA-81)**. §8 step 10 counts the 29. §5.2's block is otherwise **byte for
  byte** the query revision 9 published. The `.feature` corpus does not move — 17 files, **240** scenarios
  (228 `Scenario` + 12 `Scenario Outline`): `10-search.feature` carries no plan and no performance
  scenario, so this one has no neighbour there to mirror; revision 11 states that it has no executable
  home yet.

- spec_revision 9 (2026-11-20): **`db reindex`'s published exit set gains the `4` the probe was measured
  to answer (HIFA-7, fifth review round, D-069).** `version` stays 4: no signature, query, column or
  envelope of this spec changes, and §11 is its interface table, not its public contract.
  §11 said the set was "`0, 3, 6` and does not move"; on SQLite 3.53.1 a database **already in WAL** in a
  directory the process cannot write answers `attempt to write a readonly database` on the read-only
  probe's `PROBE_SQL` read (`-shm` cannot be created), which SPEC-030 §6 maps to
  `Conflict(code='DB_READONLY')` — exit `4`. The row now names it with the input that produces it, §8's
  step 9 follows, and §7 — the search contract this round was told not to reopen — does not move: **28**
  inline scenarios, 17 `.feature` files, 240 scenarios.
- spec_revision 8 (2026-11-20): **the negated terms reach the port, the `NOT (…)` survives a NULL
  column, and the numbers in this note are the ones a probe printed (HIFA-7, third review round,
  D-067).** `version` 3 → 4: §2.2 gains a type and §2.4's signature changes, so the public contract
  moves. Three corrections, two of them executable text that was run and answered the wrong rows:
  (1) **The no-positive form could not be called.** `reads.search` carried `like_terms` — "the positive
  terms" — and nothing else, while §5.3 printed a form whose whole predicate is a negation, and the
  adapter's third state ("no text at all") was declared as "`like_terms == ()` and no negated term":
  inside the adapter that is indistinguishable from `like_terms == ()`, period. §2.2 now declares
  `TextPredicate` (`match`, `match_title`, `like_terms`, `not_terms`), §2.3 declares
  `build_text_predicate(text)` as its only constructor, §2.4's `text` argument carries it, and §2.4
  publishes a four-row table that decides the path from those four fields alone — `(None, (), ())` is
  `backend="none"`, `(None, (), (n0…))` is the no-positive form. §5.1 steps 4-5 and 7, R4, R14 and §2.5's
  filters-only walk now name the same two tuples. The field is **not** on `SearchQuery`: the text already
  carries its `-` and an edge that had to fill a second copy would be parsing the query it is forbidden
  to parse (D-067).
  (2) **The printed `NOT (…)` dropped rows with a NULL column.** Measured on a four-row fixture where
  `i3` has `description IS NULL` and carries no `bug`: the form of revision 7 —
  `NOT (i.title LIKE :n0 OR i.description LIKE :n0)` — returns **1** row (`i1`), because for `i3` the
  predicate is `NULL` and the `WHERE` discards it; with `COALESCE(i.description, '')` inside both `NOT`
  blocks it returns the **2** rows it must (`i1`, `i3`), and the first form's negated clause does the
  same. R14 states the rule once — "a predicate under `NOT` treats a NULL column as empty" — with the
  SPEC-010 §3.4 citation that makes `description` the only nullable column of these predicates.
  (3) **The negated bindings are declared.** R14 said what the positive bindings are and said nothing
  about `:n0`, which the no-positive form binds as its *entire* predicate: one binding per negated term
  of `text.not_terms`, same shape (`'%' + escape_like(term) + '%'`, `ESCAPE '\'`), independently
  numbered, and the ASCII-only case folding of `LIKE` stated with the terms it does not fold.
  §8 step 4 asks for the split test. The `.feature` corpus does not move — 17 files, **240** scenarios
  (228 `Scenario` + 12 `Scenario Outline`).
- spec_revision 7 (2026-11-20): **the `like` path can now express a query with no positive term, and the
  two failures of the only write this component authorises are named by the file each one refuses
  (HIFA-7, second review round).** `version` stays 3: §2.2, §2.3 and §2.4 do not move — the port already
  distinguished the three text states, and what was missing was the SQL that compiles the second one.
  (Revision 8 corrects this sentence: the port did **not** distinguish the third state from "no text at
  all", because no argument carried a negated term, so §2.2 and §2.4 do move after all — `version` 3 → 4.)
  (1) **§5.3 had one form and R14a names two states.** The block bound `:p0`, `:first_prefix` and
  `:first_contains` from a *first positive* term, so `hifa search -bug` — a predicate that is only
  `AND NOT (…)` — had no normative SQL to compile to, and an implementation had to invent one. §5.3 now
  carries both forms, says which bindings the no-positive form needs (the negated terms, the filters of
  the query, `:limit_plus_one` and `:offset`, and no positive-term parameter at all, `:ident_exact`
  included), and fixes its constant band, its order and the R15 case its snippet comes from. §7 gains one
  inline scenario for it: 27 → **28** inline scenarios.
  (2) **§11 publishes the same set SPEC-100 R21 does, with the code that reaches each exit.**
  `not_a_hifa_database` at `3` for a file that opens but is not a hifa database, `corrupt_database` at
  `6` for an image SQLite itself refuses, next to `database_not_found` (`3`) and `fts5_unavailable` (`6`):
  the set is `0, 3, 6` and it does not move (D-066; revision 9 below adds the `4`). The `.feature` corpus does not move either — 17
  files, **240** scenarios (228 `Scenario` + 12 `Scenario Outline`).
- spec_revision 6 (2026-11-20): **the review round of HIFA-7 settled the three signatures this document
  published without saying how they are called, and closed eight contradictions it carried with its own
  edges.** `version` 2 → 3, because §2.3 and §2.4 both change shape:
  (1) **`parse_query` parses text.** It is `parse_query(raw: str) -> ParsedQuery` — the free text, the
  filters the text carried, the four terms-derived values and `is_empty` — and the `SearchQuery` is
  composed by **each edge** from that `ParsedQuery`, its own flags (which override the embedded value of
  the same field) and its own `limit`/`offset`/`sort`/`count` (§2.3 has the three lines all three edges
  run). `default_team` is gone and §2.3 says what it was and why it could not stay: it made the parser
  read configuration, and one string parsed into two queries.
  (2) **`search_issues` takes the instant.** `search_issues(db, query, *, now)` and
  `SearchService(reads).search(query, now=…)`: `app/search.py` has no clock and imports no time module
  (A2), the edge supplies `now` from its own `Clock`, and R13 derives `today = now.astimezone(zone).date()`
  where `zone` is the **query's** timezone — the team's when the query names one, **UTC** otherwise,
  because no workspace timezone exists in the schema and the default team is an edge's configuration. The
  consequence (a workspace-wide `due:today` is the UTC day) and the two test instants are declared.
  (3) **`has_more` had two owners and now has one.** §2.4 said the adapter "requests `limit + 1` rows and
  returns at most `limit`" while R11 and §5.1 derived `has_more` from `len(rows) > limit`: the adapter
  returns up to `limit + 1`, the port keeps `(rows, total, backend)`, and `SearchService` derives the
  boolean and trims. (4) **The three text states are named once.** §2.4 said "if `match is None`, the
  adapter runs the `like` path", which made a filters-only search a like-scan with no terms; it now says:
  no text ⇒ no text predicate and `backend="none"`, text with no `MATCH` ⇒ `like`. §2.5 walks
  `hifa search --state Done` under both readings and states that the answer is every `Done` issue.
  (5) **The retry triggers are one list.** R14's four strings (`fts5:`, `malformed`, `no such module:
  fts5`, `no such table`) live in R14 and §2.4 names none of its own; §7 pins the fallback with one of
  them. (6) **§6 publishes `message` and says where the code goes.** The four refusals have one sentence
  each, `error.code` carries the code and `error.field` carries the **input** (`limit`, `offset`,
  `priority`, `due`, `q`) — never the code — which is what SPEC-050 §6 and SPEC-060 §6.1 now publish; the
  pass-through of a lowercase code is cited where SPEC-050 declares it, and no private symbol is named.
  (7) **`GET /api/v1/issues?q=` is not this contract.** The bullet that claimed it reused `parse_query` +
  `search_issues` is gone: that `q` is D-025's title filter.
  (8) **§8 step 9 stops claiming files this spec does not own:** both commands are built from SPEC-050
  §2.5.1 and SPEC-100 R21 in this ticket, and `implements:` gains no `cli/**` path.
  §7 gains four inline scenarios (filters-only, the two timezone cases, one R14 trigger); the `.feature`
  corpus does not move — 17 files, **240** scenarios (228 `Scenario` + 12 `Scenario Outline`).
- spec_revision 5 (2026-11-20): **promoted from `draft` to `review`, with the two things that made it a
  draft closed and the four contradictions it carried with its edges (SPEC-040, SPEC-050, SPEC-070)
  removed.** `version` 1 → 2:
  §2.3 gains the entry point, §2.5 changes one column set and one exit code, and §2.6 is new.
  **Why it was still a draft:** (1) `acceptance:` named `specs/acceptance/090-search-fts.feature`, which
  the tree does not have — the file is `specs/acceptance/001-mvp-v1/10-search.feature`, and a pointer to
  a file that does not exist is the one thing a regeneration cannot survive; (2) the mandatory section 11
  of `specs/README.md` — interface behaviour, P3/D-032 — did not exist: the document ran from Out of scope
  straight to Notes, so its only edge (a CLI command) had no declared empty, no-results or error
  behaviour and no statement of what does *not* apply to it. §11 now exists and Notes is §12.
  **What the contract gained:** (1) `search_issues(db, query)` is the use case and the only entry point in
  §2.3 — the signature SPEC-040 §2.2 has carried since its revision 30 — and `SearchService` is demoted to
  the implementation object it already was, with `SearchService.parse` replaced by the module-level
  `parse_query`; the edges construct nothing (D-064, and §2.5, SPEC-050 §2.3, SPEC-070 §2.1.4/R21 now all
  name the function). (2) §2.6 is new: the three transports of one contract and, in §2.6.1, the seven
  rules `GET /search` follows as a consumer, including that a page with no `q` is its empty state and
  never `query_empty` (D-063). (3) §2.5's table is the one of SPEC-050 §2.4.1 — `IDENTIFIER  P  STATE
  UPDATED  TITLE  MATCH` — replacing a column list (`TITLE (snippet) | STATE | PRIORITY | DUE`) that
  disagreed with the output contract of the edge that renders it. (4) The exit codes are `0, 2, 6`: an FTS
  `OperationalError` outside R14's four cases is an infrastructure failure and leaves as exit 6
  `internal_error`, where rev 4 said exit 2 — exit 2 is what the user typed, and SPEC-100's `db reindex`
  row already used 6 for the same class of failure. (5) R14 gains its fourth case (an interpreter whose
  SQLite has no FTS5, and a virtual table this process cannot resolve) and §6 states the other half:
  a degraded index answers through `like` with exit 0, because losing the rows to an error is the worse
  trade. (6) R11 and §2.5 say what `total` is (the filtered set, never the page, and `null` unless
  `--count`) and that an `--offset` past the end is an empty page with exit 0. (7) R15 pins REQ-S090-03:
  neither `app/` nor `reads.py` escapes the snippet, the marker pair is the only markup it carries, and
  the escaping happens once, in SPEC-070's read model. (8) §7 gains six inline scenarios for exactly those
  cases. The `.feature` corpus does not move — 17 files, **240** scenarios (228 `Scenario` + 12
  `Scenario Outline`): the four `hifa search` commands of `10-search.feature` that asserted a `total` now
  pass `--count`, which is the only way that key stops being `null`, and no scenario was added or removed.
- spec_revision 4 (2026-11-16): **the fixture's team carries seven `workflow_state`s, not six (D-058).**
  §7's `Background` counted the fixture team's states as six, a count that stopped being the seed when
  SPEC-100 §2.5 (rev 7) gained `In Review` at `4.5`; the scenarios would have measured a team
  `create_team` no longer produces. It reads seven, the count of SPEC-040 REQ-F040-14. The search
  contract is untouched — filters, envelope, ranking and `backend` do not see the board's width, and a
  state is matched as text, not counted as a row — so `version` stays at 1.
- **spec_revision 2 (2026-11-08, orchestrator):** this document is the **authority for the search
  contract** (D-002 of `specs/DECISIONS.md`). The `hifa search` command of SPEC-050 uses
  exactly these flags and this envelope: `{hits: [{issue, snippet, score}], total, has_more,
  backend, took_ms}`, `--limit` range 1-200 with default 50. SPEC-050 cannot redefine it.
  The index rebuild runs from `hifa db reindex` / `hifa doctor` (SPEC-100), never
  from a search. (The element in that sentence is D-002's: since rev 5 the hit is the flat `SearchHit`
  of §2.2 and the index rebuild is `hifa db reindex` alone, because `hifa doctor` only reports —
  D-064, SPEC-100 R20/R21.)
- spec_revision 3 (2026-11-11): translated to English (repo-wide language convention).
