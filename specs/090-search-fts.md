---
id: SPEC-090
title: FTS5 full-text search
component: application
status: draft
version: 1
spec_revision: 3
depends_on: [SPEC-000, SPEC-010, SPEC-040]
implements:
  - src/hifa/app/search.py
  - src/hifa/adapters/sqlite/reads.py
  - src/hifa/adapters/sqlite/migrations/001_init.sql
  - tests/**/search*
acceptance: specs/acceptance/090-search-fts.feature
owner: Ginkelte
created: 2026-11-08
updated: 2026-11-11
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
    code: str  # query_too_long | query_empty | invalid_filter | invalid_limit
    field: str | None
```

### 2.3 Functions and service

```python
def sanitize_terms(text: str) -> tuple[str, ...]
def build_match_expression(text: str) -> str | None
def build_title_match_expression(text: str) -> str | None
def escape_like(value: str) -> str
def parse_query(raw: str, *, default_team: str | None = None) -> SearchQuery
def identifier_candidate(text: str) -> str | None

class SearchService:
    def __init__(self, reads: SearchReads, clock: Clock)
    def search(self, query: SearchQuery) -> SearchPage
    def parse(self, raw: str) -> SearchQuery
```

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
    match: str | None,
    match_title: str | None,
    like_terms: tuple[str, ...],
    filters: SearchFilters,
    order: str,
    limit: int,
    offset: int,
    count: bool,
    today: str,
) -> tuple[list[SearchRow], int | None, Literal["fts", "like", "none"]]
```

Port rules:

- `match`/`match_title` are **already sanitized** FTS5 expressions (§2.3). If `match is None`, the
  adapter runs the `like` path with `like_terms`.
- `today` is `YYYY-MM-DD` in the team's timezone, computed by `SearchService` with `Clock` (the
  adapter never calls `datetime.now`).
- The received `limit` is already clamped to `1..MAX_LIMIT`; the adapter requests `limit + 1` rows to
  compute `has_more` and returns at most `limit`.
- It returns `(rows, total, backend)`. On `sqlite3.OperationalError` with `fts5:` in the message, it
  retries once through the `like` path and returns `backend="like"` (R14).

### 2.5 CLI surface

| Command | Flags | Output (`--json`) | Exit codes |
|---|---|---|---|
| `hifa search <query>` | `--team`, `--state`, `--assignee`, `--label`, `--priority`, `--due`, `--is`, `--limit` (1-200), `--offset`, `--all`, `--count`, `--sort {relevance,updated,created,priority,due}`, `--json` | `{"hits": [...], "total": null, "has_more": true, "backend": "fts", "took_ms": 4.2}` | 0, 2 |

- The flags override the filters embedded in the text (`--state Done` wins over `state:Todo`).
- `--all` is equivalent to `is:all` (includes archived and trashed).
- `hifa search` with no arguments and no filters ⇒ `query_empty` (exit 2). With filters and no text, the
  search is valid and does not use FTS (`match=None`), but it **does** apply visibility and filters.
- Without `--json`, the output is a table with `IDENTIFIER | TITLE (snippet) | STATE | PRIORITY |
  DUE`, formatted by `cli/output.py` (SPEC-000 A6).
- `GET /issues?q=…` from SPEC-060 reuses `parse_query` + `SearchService.search`; it does not define its own
  new parameters. This spec exposes no endpoints.

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
  `invalid_limit`. The adapter requests `limit + 1` rows: if `limit + 1` arrive, `has_more = True` and
  the last one is discarded. `total` is computed only with `count=True`; without it, it is `None`. An
  `offset` greater than the total returns `hits=[]` with `has_more=False`, not an error.
- **R12 — Alternative order (REQ-F090-12).** `sort="updated"|"created"` → `updated_at`/`created_at`
  DESC; `"priority"` → priority band (urgent first, `0` last) and then `updated_at` DESC;
  `"due"` → `due_date IS NULL ASC, due_date ASC`. With `sort != "relevance"` the band and the `bm25`
  do not take part in the order, but the same filters and the same visibility apply.
- **R13 — `due` filter (REQ-F090-13).** `today` is computed in `SearchService` as
  `Clock.today(team.timezone).isoformat()` and reaches the adapter as a parameter; the SQL never calls
  system date functions. Values: `none` → `due_date IS NULL`; `overdue` →
  `due_date < :today AND s.type NOT IN ('completed','canceled')`; `today` → `= :today`; `week` →
  `BETWEEN :today AND date(:today,'+7 days')`; `YYYY-MM-DD` → `=`; `>YYYY-MM-DD` / `<YYYY-MM-DD` →
  strict comparison. Any other format ⇒ `invalid_filter`.
- **R14 — Fallback to LIKE (REQ-F090-14).** The `like` path is used when: (a) `match is None` and there
  are terms (purely negative query), (b) the `MATCH` raises `sqlite3.OperationalError` whose
  message contains `fts5:` or `malformed`, or (c) the `issue_fts` table does not exist (`no such table`).
  On that path the predicate is, for each positive term,
  `(title LIKE :p ESCAPE '\' OR description LIKE :p ESCAPE '\' OR identifier LIKE :p ESCAPE '\')`
  with `:p = '%' || escape_like(term) || '%'` and AND between terms; `escape_like` escapes `\`, `%`
  and `_`. The ranking of the `like` path is fixed: exact identifier (0), `title LIKE 'term%'` (1),
  `title LIKE '%term%'` (2), the rest (3). `SearchPage.backend` reports `"like"` and `score` is
  `0.0`. The fallback **never** changes the set of filters or the visibility. If there is no text
  (filters only), no text predicate is added and `backend` is `"none"`.
- **R15 — Snippet (REQ-F090-15).** On the `fts` path it uses
  `snippet(issue_fts, -1, '<mark>', '</mark>', '…', 12)` (automatic column, 12-token window).
  On the `like` path the snippet is built in Python: first case-insensitive occurrence of the first
  positive term in `title`; if it is not there, in `description`; a window of 60 characters on
  each side is taken and the occurrence is wrapped with `MARK_START`/`MARK_END`. If there is no occurrence, the
  snippet is the description truncated to 120 characters or the title. The snippet is **not** sanitized
  here: HTML escaping is the responsibility of the presentation layer (SPEC-070).
- **R16 — Performance budget (REQ-N090-01).** With 10 000 issues and 40 000 description
  tokens, a search of 1 to 3 terms with `limit=50` and `count=False` must take
  **< 50 ms p50** and **< 120 ms p95** (warm connection, no `--count`). `count=True` has its own
  budget: **< 120 ms p50**. It is measured with `time.perf_counter` around
  `SearchService.search`, 30 iterations, discarding the first 5.
- **R17 — Query plan (REQ-N090-02).** The `EXPLAIN QUERY PLAN` of the query with a team or state filter
  must **not** contain `SCAN issue` (full table access); it must resolve `issue`
  by rowid or by index. The test compares the plan text, not the timings.
- **R18 — Read-only (REQ-F090-16).** `SearchService.search` does not open write transactions,
  does not execute write `PRAGMA` or `optimize`. The only write allowed in the whole
  component is `INSERT INTO issue_fts(issue_fts) VALUES('rebuild'|'integrity-check')`, and only
  from `hifa db reindex` / `hifa doctor` (SPEC-100), never from a search.
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
| **REQ-N090-02** | No `SCAN issue` in the plan with a team/state filter (R17). | assert on `EXPLAIN QUERY PLAN` |
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
 4. match        ← build_match_expression(terms)             (None if there are no positives)
    match_title  ← build_title_match_expression(terms)
    ident_exact  ← complete identifier_candidate(text) or ""
    ident_like   ← prefix "KEY-" or "KEY-12" + "%" or ""
 5. if match is None and there are negative terms → like path (R14a)
 6. today ← clock.today(team.timezone).isoformat()   (only if filters.due needs it)
 7. rows, total, backend ← reads.search(conn, match=…, match_title=…, like_terms=…,
                                         filters=…, order=query.sort, limit=…, offset=…,
                                         count=query.count, today=today)
 8. has_more ← len(rows) > limit  → trim to limit
 9. build SearchPage(hits, total, has_more, backend, took_ms)
```

`took_ms` is measured with `time.perf_counter()` between step 1 and step 9 and rounded to 1 decimal; it is the
only non-deterministic field of the response and it **does not** enter the equality comparisons of the
tests (`pytest.approx` is used or it is ignored).

### 5.2 Reference query (normative, `fts` path)

```sql
-- Parameters (all bound, none interpolated):
--   :match, :match_title, :ident_exact, :ident_like, :today, :limit_plus_one, :offset
--   plus the ones of each filter present: :team_key, :state, :assignee, :label, :priority, :due_date
WITH fts AS (
  SELECT rowid AS rid,
         bm25(issue_fts, 10.0, 1.0, 0.0)                                AS bm,
         snippet(issue_fts, -1, '<mark>', '</mark>', '…', 12)            AS snip
  FROM issue_fts
  WHERE issue_fts MATCH :match
),
title_hit AS (
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
- The `title_hit` CTE doubles the cost of the `MATCH` but it is what allows distinguishing "matches the
  title" from "matches only the description" without bringing text into Python. Both `MATCH` clauses use the same
  index; the measured cost is ~1.6× that of a single `MATCH`, within the R16 budget.
- `count=True` reuses the same `WHERE` in a `SELECT COUNT(*)` with the CTEs included
  (`COUNT(DISTINCT i.rowid)`); `COUNT(*) OVER ()` is **not** used because it forces materializing the whole
  result.
- The filter fragments are constant by construction: `p.team` is
  `"i.team_id IN (SELECT id FROM team WHERE key = ?)"` and the value goes separately (R20). An `IN` with N
  values generates `IN (?,?,?)` with `N` parameters.

### 5.3 `like` path (fallback)

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
  [ AND NOT ( i.title LIKE :n0 ESCAPE '\' OR i.description LIKE :n0 ESCAPE '\' ) for each negated term ]
  AND i.trashed = 0 AND i.archived_at IS NULL
ORDER BY band ASC, i.updated_at DESC, i.id ASC
LIMIT :limit_plus_one OFFSET :offset
```

The snippet of this path is computed after the fetch, in `SearchService`, following R15.

## 6. Errors

| Error | `code` | When | CLI | HTTP |
|---|---|---|---|---|
| `Validation` | `query_empty` | no text and no filters | exit 2, stderr | 422 |
| `Validation` | `query_too_long` | > 512 characters, > 16 terms or term > 128 | exit 2 | 422 |
| `Validation` | `invalid_limit` | `limit` outside 1..200 or `offset < 0` | exit 2 | 422 |
| `Validation` | `invalid_filter` | invalid value of a known field (`priority:urgentísimo`, `due:ayer`, `-due:…`) | exit 2, message with `field` | 422 |

- The search does **not** produce `NotFound`, `Conflict` or `InvalidTransition`: a filter that matches
  nothing is a valid response with `hits=[]`, `total=0` (if requested) and exit 0.
- An FTS `sqlite3.OperationalError` that is not resolved by the fallback (R14) is propagated as an
  infrastructure error: exit 2 with `backend="like"` in the message, without a stacktrace in the human
  output (`--verbose` shows it).
- `took_ms` is never a reason for error, not even if it exceeds the R16 budget: the budget is
  verified in performance tests, not at runtime.

## 7. Acceptance Criteria

```gherkin
@SPEC-090
Feature: Full-text search over issues

  Background:
    Given a database with team "ENG" and the 6 default workflow_states
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
```

## 8. Regeneration checklist

1. Read `SPEC-000` + `SPEC-010` + this spec in full.
2. In `migrations/001_init.sql`, verify that `issue_fts` matches SPEC-010 §3.4 **character by character**
   (tokenizer `unicode61 remove_diacritics 2`, `content='issue'`,
   `identifier UNINDEXED`) and add the 3 triggers of §3.1 with those exact names
   (`issue_fts_ai`, `issue_fts_ad`, `issue_fts_au`).
3. Create `src/hifa/app/search.py` with the constants, dataclasses and functions of §2.1-2.3. No SQL.
4. Implement `sanitize_terms` → `build_match_expression` → `build_title_match_expression` with
   unit tests covering: unbalanced quotes, `OR`/`NOT`/`NEAR`, parentheses, `%`, `_`,
   empty terms, prefix, phrase and negation. No input may produce an invalid `MATCH`: the
   parameterized test runs `conn.execute("SELECT rowid FROM issue_fts WHERE issue_fts MATCH ?", …)`
   over 100 adversarial inputs and requires 0 `OperationalError`.
5. Implement `parse_query` respecting R6/R7/R8 and `PRIORITY_NAMES`.
6. Implement `reads.search` with the query of §5.2, assembling the filter fragments as
   constants and the values as parameters. `limit + 1` for `has_more`.
7. Implement the `like` path of §5.3 and the `try/except sqlite3.OperationalError` that falls back to it.
8. Implement the snippet of the `like` path in `SearchService` (R15).
9. Create the `hifa search` command of §2.5 delegating the formatting to `cli/output.py`.
10. Cover the scenarios of §7 and the performance test of R16 with the 10 000-issue fixture
    (bulk insert + `INSERT INTO issue_fts(issue_fts) VALUES('rebuild')`).
11. `pytest tests -k search` green; `python scripts/spec_contract_check.py --spec
    specs/090-search-fts.md` with no drift in `FTS_WEIGHTS`, `DEFAULT_LIMIT`, `MAX_LIMIT` and the signatures
    of `search`/`SearchService`.
12. `grep -rn "import sqlite3" src/hifa/app/search.py` must return 0 lines.

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

## 11. Notes

- **spec_revision 2 (2026-11-08, orchestrator):** this document is the **authority for the search
  contract** (D-002 of `specs/DECISIONS.md`). The `hifa search` command of SPEC-050 uses
  exactly these flags and this envelope: `{hits: [{issue, snippet, score}], total, has_more,
  backend, took_ms}`, `--limit` range 1-200 with default 50. SPEC-050 cannot redefine it.
  The index rebuild runs from `hifa db reindex` / `hifa doctor` (SPEC-100), never
  from a search.
- spec_revision 3 (2026-11-11): translated to English (repo-wide language convention).
