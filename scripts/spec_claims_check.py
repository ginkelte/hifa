#!/usr/bin/env python3
"""Check that what a spec declares has a producer, a test or a provenance.

A spec is the source of truth, so a claim it makes has to be reachable from the tree: a name a
closed vocabulary declares must be written by some code, a scenario must be pinned by a test (or
say it is not), and a count published as a measurement must name the file, the command or the day
it came from. This script is that check. It is the mechanical form of the review that reads prose.

The three rules
---------------
producer
    A closed vocabulary table, a fenced JSON document or a dataclass declared in a fenced block
    enumerates names the code must be able to produce. For every name such a shape declares, a
    literal of that name must appear in a writer under `src/`: a target assigned inside a function
    body, a subscript store, a keyword argument of a call, a string passed positionally to a call
    that stores it, or a key of a dict literal built inside a function. A string a call only
    raises or prints is a message and names nothing: the callees that take one are listed in one
    place. Nothing outside a function body is a writer, because a declaration is not a write:
    `dot_color: str | None = None` in a class body, a module-level
    `DEAD = "actor_initials"`, and `getattr(row, "actor_initials")` all name a field and produce
    none, so a field a class declares and nothing ever assigns is a violation. Comments and
    docstrings do not count either — quoting a sentence is not producing it. A dataclass whose
    class does not exist in `src/` is not checked: the contract of a component that has not been
    built yet declares nothing the tree can contradict.

scenario
    Every `Scenario` a spec states as an acceptance case, and every one of the acceptance
    feature files, must be accounted for, in this order: `@test(<path>::<name>)` naming a test
    that exists; a test whose name is the scenario's title, compared exactly after both are
    normalised; or `@deferred(KEY-NN)` naming a ticket the accepted-gaps registry knows. Anything
    else is unaccounted, except a tag that claims an owner and names none — a bare `@deferred`, a
    ticket nobody issued — which is broken and fails as such. There is no third guess: a literal
    quoted in the steps, a shared word or a similar title does not pin a scenario, because a
    phrase that appears in a hundred tests identifies none of them.

provenance
    A sentence that publishes a count as a fact about a database must name where the number came
    from, in that same sentence: a backticked token that is a command (a path with a `/` and a
    `.py`, `.db` or `.sql`, or one that begins with `sqlite3 `, `python`, `.venv/bin/`, `pytest`
    or `hifa `), or a backticked path together with a date. A path on its own is not a source and
    neither is a date on its own: the file changes and the sentence keeps asserting. A count
    inside a fence is a published count and is read like any other, and no document is exempt for
    being a log — the decision log is where a count rotted.

Exemptions
----------
The tree carries gaps with tickets against them, and a ticket is only a ticket when the registry
carries it: a well-formed id somebody typed names nobody, so a row citing one fails the same way
`@deferred(HIFA-9999)` does. Each accepted gap is one row in the table of its rule: the subject as
the spec states it, the ticket that closes it, and the reason. A row whose subject no longer
appears in the specs is stale and fails, so the table can only shrink honestly. The number of rows
in force is printed in the summary.

The scenario rule carries its legacy tail in a file rather than in this one: the specs name
behaviours in prose and most of them were never joined to a test, so the unaccounted scenarios are
accepted by key in `spec_claims_debt.json`, next to this script and versioned with it. A key is
`<document>::<normalised title>`, and the file also holds the ticket that owns each cluster and the
ticket ids a deferral or an exemption may cite. The set is compared exactly: a scenario deleted
from the corpus leaves a key no claim carries and fails until `--accept` drops it, and a scenario
added without a test has no key and fails on the spot. `--accept` rewrites the file from the tree,
drops the keys the corpus no longer carries, and refuses to add one — a new claim needs a test or a
registered ticket, and growing the set is a decision somebody makes in a pull request. The only
way past the refusal is `--allow-growth`, which says so out loud.

Usage
-----
    python scripts/spec_claims_check.py                  # every check, human-readable
    python scripts/spec_claims_check.py --only producer  # one check
    python scripts/spec_claims_check.py --list-debt      # the accepted keys, by cluster
    python scripts/spec_claims_check.py --accept         # drop the stale accepted keys
    python scripts/spec_claims_check.py --accept --allow-growth  # and add new ones too
    python scripts/spec_claims_check.py --quiet          # only the summary line
    python scripts/spec_claims_check.py --json           # {ok, violations, exemptions, …}

Exit codes: 0 no violation (accepted gaps are printed, not failed), 1 violations, 2 usage error.
No external dependencies: stdlib only.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "specs"
SRC_DIR = REPO_ROOT / "src"
TESTS_DIR = REPO_ROOT / "tests"
ACCEPTANCE_DIR = SPECS_DIR / "acceptance"

#: The accepted unaccounted scenarios, read and written as a set of keys rather than counted.
DEBT_FILE = Path(__file__).resolve().parent / "spec_claims_debt.json"
DEBT_VERSION = 1

CHECKS = ("producer", "scenario", "provenance")

#: Files that are records rather than live claims: an index, the reading guide, the template.
#: The decision log is a record too, and it is read by the provenance rule all the same: it is
#: where a published count rotted, so excluding it by name was the defect.
NOT_A_CLAIM = frozenset({"INDEX.md", "README.md", "_template.md", "DECISIONS.md", "notes.md"})

#: Records that declare no vocabulary and state no scenario, but do publish counts.
PROSE_ONLY = frozenset({"DECISIONS.md"})

#: Ticket ids are `KEY-NNN`: a deferral or an exemption is not free text.
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")

FENCE_RE = re.compile(
    r"^```(?P<lang>[A-Za-z0-9_+-]*)[ \t]*\n(?P<body>.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL
)
SECTION_RE = re.compile(r"^##[ \t]+(?P<number>\d+)\.[ \t]*(?P<title>.*)$", re.MULTILINE)
SCENARIO_RE = re.compile(r"^\s*Scenario(?: Outline)?:[ \t]*(?P<title>.+?)[ \t]*$", re.MULTILINE)
TAG_LINE_RE = re.compile(r"^\s*(?P<tags>@\S+(?:[ \t]+@\S+)*)[ \t]*$")
DEFERRED_RE = re.compile(r"^@deferred(?:\((?P<ticket>[^)]*)\))?$")
TEST_POINTER_RE = re.compile(r"^@test\((?P<path>[^:)]+?)::(?P<name>[^)]+)\)$")


#: A measurement of a file is about rows; a budget, a latency or a fixture is not this rule.
ROW_NOUN = re.compile(
    r"\brows?\b|\brecords?\b|\bentries\b|\brank\b|\bhistory rows?\b", re.IGNORECASE
)
MEASUREMENT = (
    re.compile(r"\bmeasured database\b", re.IGNORECASE),
    re.compile(r"\b\d[\d,\s]{0,11} of (?:its|the)[ \t]+\d[\d,\s]{0,11}\b", re.IGNORECASE),
    re.compile(r"\bthe database (?:has|holds|carries)[ \t]+\d", re.IGNORECASE),
)
NOT_A_DATABASE_COUNT = re.compile(
    r"\bfixture\b|\bp50\b|\bp95\b|\bbudget\b|\bms\b|10[ ,]?000", re.IGNORECASE
)
#: A source is a command, and a command is written in a fence the reader can run: backticks are
#: the spelling, so a bare path in prose names nothing anybody can repeat.
BACKTICK = re.compile(r"`([^`\n]+)`")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2} [A-Z][a-z]{2}\.? \d{4}\b")
#: The words a command begins with; the rest of the token is its arguments.
COMMAND_PREFIXES = ("sqlite3 ", "python", ".venv/bin/", "pytest", "hifa ")
#: A path is a file a command reads: it has a directory or a data or script extension.
PATH_SUFFIX = re.compile(r"\.(?:py|db|sql)$")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z`*“(])")


@dataclass(frozen=True)
class Violation:
    """One failed claim: the rule it breaks, where the spec states it, and what is missing."""

    check: str
    location: str
    detail: str
    subject: str = ""

    def line(self) -> str:
        """The one-line report of the violation."""
        return f"{self.check}: {self.location}: {self.detail}"


@dataclass(frozen=True)
class Exemption:
    """An accepted gap: the subject, the ticket that closes it and why it is accepted today."""

    check: str
    subject: str
    ticket: str
    reason: str


@dataclass(frozen=True)
class ScenarioClaim:
    """One scenario a spec states, with the tags it carries and the steps it declares."""

    origin: str
    location: str
    title: str
    tags: tuple[str, ...]
    body: str
    document: str


@dataclass(frozen=True)
class ScenarioDebt:
    """One cluster of accepted unaccounted scenarios: the ticket that owns them and their keys."""

    ticket: str
    reason: str
    keys: frozenset[str] = frozenset()


class DebtRegistry:
    """The committed set of accepted unaccounted scenarios, and the tickets a deferral may cite.

    The set is the whole point: a scenario is accepted because its key is in here, never because
    its document has room left. Keys therefore cannot be repurposed — dropping one and adding
    another is a change to the file, visible in the diff, and `--accept` is what writes it.
    """

    def __init__(
        self,
        clusters: Sequence[ScenarioDebt] = (),
        tickets: frozenset[str] = frozenset(),
        path: Path = DEBT_FILE,
        default_ticket: str = "",
    ) -> None:
        """Hold the clusters, the ticket registry and where the file lives."""
        self.clusters = tuple(clusters)
        self.tickets = frozenset(tickets) | {cluster.ticket for cluster in clusters}
        self.path = path
        self.default_ticket = default_ticket or next(iter(sorted(self.tickets)), "")

    @property
    def keys(self) -> frozenset[str]:
        """Every accepted key, whichever cluster owns it."""
        return frozenset(key for cluster in self.clusters for key in cluster.keys)

    def owner(self, key: str) -> str:
        """The ticket that owns an accepted key, or an empty string when nothing accepts it."""
        for cluster in self.clusters:
            if key in cluster.keys:
                return cluster.ticket
        return ""

    @classmethod
    def read(cls, path: Path = DEBT_FILE) -> DebtRegistry:
        """Read the registry; a file that is absent or malformed accepts nothing."""
        if not path.is_file():
            return cls(path=path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            clusters = tuple(
                ScenarioDebt(
                    ticket=str(cluster["ticket"]),
                    reason=str(cluster.get("reason", "")),
                    keys=frozenset(str(key) for key in cluster.get("keys", ())),
                )
                for cluster in data.get("clusters", ())
            )
            tickets = frozenset(str(ticket) for ticket in data.get("tickets", ()))
            default = str(data.get("default_ticket", ""))
        except (ValueError, KeyError, TypeError):
            return cls(path=path)
        return cls(clusters, tickets, path, default)

    def document(self) -> dict[str, object]:
        """The registry as the JSON it is stored as: versioned, sorted, one cluster per ticket."""
        return {
            "version": DEBT_VERSION,
            "default_ticket": self.default_ticket,
            "tickets": sorted(self.tickets),
            "clusters": [
                {
                    "ticket": cluster.ticket,
                    "reason": cluster.reason,
                    "keys": sorted(cluster.keys),
                }
                for cluster in sorted(self.clusters, key=lambda cluster: cluster.ticket)
            ],
        }

    def write(self, path: Path | None = None) -> Path:
        """Write the registry, one key per line so a diff reads as a set of rows."""
        target = path or self.path
        lines = json.dumps(self.document(), indent=2, ensure_ascii=False)
        target.write_text(f"{lines}\n", encoding="utf-8")
        return target

    def accepted(self, keys: Sequence[str], reason: str = "") -> DebtRegistry:
        """The registry with `keys` accepted: the cluster that owns each keeps it, the rest arrive.

        This is what `--accept` builds. A key already in a cluster stays in it, so the ownership a
        human assigned survives a regeneration; a new key goes to the default cluster.
        """
        kept: dict[str, list[str]] = {cluster.ticket: [] for cluster in self.clusters}
        reasons = {cluster.ticket: cluster.reason for cluster in self.clusters}
        for key in keys:
            owner = self.owner(key) or self.default_ticket
            kept.setdefault(owner, []).append(key)
            reasons.setdefault(owner, reason)
        clusters = tuple(
            ScenarioDebt(ticket=ticket, reason=reasons[ticket], keys=frozenset(sorted(found)))
            for ticket, found in sorted(kept.items())
            if ticket
        )
        return DebtRegistry(clusters, self.tickets, self.path, self.default_ticket)


@dataclass(frozen=True)
class Vocabulary:
    """One closed vocabulary a spec enumerates: what it names and where it lives."""

    location: str
    kind: str
    names: tuple[str, ...]


@dataclass
class Report:
    """The verdict: violations by rule, the exemptions in force, and what was read."""

    violations: list[Violation] = field(default_factory=list)
    exemptions: list[Exemption] = field(default_factory=list)
    debts: DebtRegistry = field(default_factory=DebtRegistry)
    readings: list[Accounting] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)

    def of(self, check: str) -> list[Exemption]:
        """The exemptions in force for one rule."""
        return [e for e in self.exemptions if e.check == check]

    @property
    def ok(self) -> bool:
        """True when no claim fails."""
        return not self.violations


# --- reading the tree ---------------------------------------------------------------------


def spec_files(specs_dir: Path = SPECS_DIR) -> list[Path]:
    """The specs that make claims: every numbered document, not the index nor the decision log."""
    return sorted(p for p in specs_dir.glob("*.md") if p.name not in NOT_A_CLAIM)


def feature_files(acceptance_dir: Path = ACCEPTANCE_DIR) -> list[Path]:
    """The acceptance feature files, which carry scenarios of their own."""
    return sorted(acceptance_dir.rglob("*.feature")) if acceptance_dir.is_dir() else []


@dataclass
class Producers:
    """What the functions of `src/` write: the names they produce, the classes they define, and
    the strings they build, with each interpolated string kept as one template.

    `names` and `values` are writes and nothing else — a target assigned inside a function body, a
    subscript store, a keyword argument, a dict key, or a string a call receives positionally, less
    the strings a call only prints or raises.
    """

    names: set[str] = field(default_factory=set)
    values: set[str] = field(default_factory=set)
    classes: set[str] = field(default_factory=set)

    def writes(self, name: str) -> bool:
        """True when a function body writes `name` as a field: a target or a keyword argument.

        A declaration is not a write: a field a class annotates — with a default or without — and
        nothing ever assigns has no producer, which is the defect this rule exists to find. A
        module-level constant is not a write either, and neither is a name reached by `getattr`.
        """
        return name in self.names

    def stores(self, name: str) -> bool:
        """True when a function body stores `name` as a value: a literal or a key it writes.

        A vocabulary table enumerates the values a writer stores, and a stored value reaches the
        code as a string — the column's name is the payload, so a literal a function passes is its
        producer. A literal outside a function is not: it is a constant nothing reads.
        """
        return name in self.values or name in self.names


#: Calls that name an attribute by a string. The string is a selector, not a write: `getattr` and
#: `hasattr` read a name, and reading one is exactly what the rule must not accept. `setattr` does
#: write, so it is a target with a keyword's standing, not a selector.
REFLECTION = frozenset({"getattr", "hasattr", "attrgetter"})

#: Calls that take a message: an exception (a name ending in `Error` or `Exception`, which is how
#: the standard library and this repository spell one), `print`, a logger, `warnings.warn` and
#: `click.echo`. One denylist, one reason: these calls show a string or raise it, so the string is
#: prose about a failure and never a value a writer stores. Without this, `raise
#: ValueError("zzz_probe_key")` and `print("zzz_probe_key")` made every typo a producer.
MESSAGE_VERBS = frozenset(
    {"print", "echo", "warn", "warning", "error", "critical", "info", "debug", "exception"}
)
EXCEPTION_SUFFIXES = ("Error", "Exception")


def _is_message_sink(callee: str) -> bool:
    """True when the callee raises or prints, so its positional strings are messages, not fields."""
    return callee.endswith(EXCEPTION_SUFFIXES) or callee in MESSAGE_VERBS


def producers(sources: dict[Path, str]) -> Producers:
    """Read the producers of every module once: one parse, one walk, no regex over the text."""
    found = Producers()
    for text in sources.values():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        _Reader(found).visit(tree)
    return found


class _Reader(ast.NodeVisitor):
    """Collect what each node writes, and count nothing outside a function body as a write."""

    def __init__(self, found: Producers) -> None:
        """Read into the producers being built, at module scope."""
        self.found = found
        self.depth = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """A class defines a name; its body declares fields, and a declaration is not a write."""
        self.found.classes.add(node.name)
        outer = self.depth
        self.depth = 0
        self.generic_visit(node)
        self.depth = outer

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """A function body is where the code writes."""
        self._enter(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """An async function body is where the code writes, the same as a plain one."""
        self._enter(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        """A lambda has no assignments, but its calls and dicts are writes all the same."""
        self._enter(node)

    def _enter(self, node: ast.AST) -> None:
        """Walk a function body with the depth raised, so only its statements write."""
        self.depth += 1
        self.generic_visit(node)
        self.depth -= 1

    def visit_Assign(self, node: ast.Assign) -> None:
        """An assignment inside a function writes its targets."""
        if self.depth:
            for target in node.targets:
                _target_name(target, self.found)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """An annotated assignment writes its target inside a function and only when it has a value.

        A bare annotation declares a name without producing one; a class body's annotation, default
        or not, is a field the code still has to fill somewhere.
        """
        if self.depth and node.value is not None:
            _target_name(node.target, self.found)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """An augmented assignment inside a function writes its target."""
        if self.depth:
            _target_name(node.target, self.found)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """A keyword names a field its callee receives; a literal argument is a value it stores.

        A positional string is a value only when the callee takes values: a call that raises or
        prints takes a message, and a message names no field. `record("created", …)` still writes
        the vocabulary it names; `raise ValueError("zzz_probe_key")` writes nothing.
        """
        callee = _callee(node)
        if self.depth and callee not in REFLECTION:
            for keyword in node.keywords:
                if keyword.arg:
                    self.found.names.add(keyword.arg)
                if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
                    self.found.values.add(keyword.value.value)
            if not _is_message_sink(callee):
                for argument in node.args:
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
                        self.found.values.add(argument.value)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        """A string key of a dict literal a function builds is a name the code produces."""
        if self.depth:
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    self.found.names.add(key.value)
                    self.found.values.add(key.value)
        self.generic_visit(node)


def _target_name(target: ast.AST, found: Producers) -> None:
    """The name an assignment writes, whether to a local, an attribute or a subscript key."""
    if isinstance(target, ast.Name):
        found.names.add(target.id)
    elif isinstance(target, ast.Attribute):
        found.names.add(target.attr)
    elif isinstance(target, ast.Subscript):
        _subscript_key(target, found)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            _target_name(element, found)


def _subscript_key(node: ast.Subscript, found: Producers) -> None:
    """The string key of a subscript store: `row["field"] = …` writes the field's value.

    A subscript read is not a write, so the key counts only when the store is the assignment's
    target — the node's context says which it is.
    """
    if not isinstance(node.ctx, ast.Store):
        return
    key = node.slice
    if isinstance(key, ast.Constant) and isinstance(key.value, str):
        found.names.add(key.value)
        found.values.add(key.value)


def _callee(node: ast.Call) -> str:
    """The name of the function a call applies, whether it is bare or reached through a module."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def normalised_title(title: str) -> str:
    """A title as one comparable string: lowercase words, punctuation and spacing flattened.

    The same function reads a scenario's title and a test's name, which is what makes the
    comparison exact: nothing here is a prefix, a stem or a similarity score.
    """
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


def test_key(name: str) -> str:
    """The normalised form of a test's name, with pytest's `test` prefix taken off."""
    return normalised_title(re.sub(r"^test_?", "", name))


def claim_key(claim: ScenarioClaim) -> str:
    """The key an accepted scenario is stored under: its document and its normalised title."""
    return f"{claim.document}::{normalised_title(claim.title)}"


# --- the tree under test ------------------------------------------------------------------


@dataclass
class Tree:
    """What the checks read: the specs' text, the producers and the tests."""

    specs: list[Path]
    features: list[Path]
    producers: Producers
    prose: dict[str, list[tuple[str, str]]]
    root: Path
    #: Every test of the suite by its normalised name, so a title is one dict lookup, not a scan.
    tests: dict[str, str] = field(default_factory=dict)

    @classmethod
    def read(cls, specs_dir: Path, src_dir: Path, tests_dir: Path, acceptance_dir: Path) -> Tree:
        """Read the three trees once, so a check is a walk over memory."""
        sources = {p: p.read_text(encoding="utf-8") for p in src_dir.rglob("*.py")}
        written = producers(sources)
        test_files = sorted(tests_dir.rglob("*.py")) if tests_dir.is_dir() else []
        tests: dict[str, str] = {}
        for path in test_files:
            for where, name in test_names(path, path.read_text(encoding="utf-8")):
                tests.setdefault(test_key(name), where)
        spec_texts = {p.name: p.read_text(encoding="utf-8") for p in spec_files(specs_dir)}
        records = {
            name: (specs_dir / name).read_text(encoding="utf-8")
            for name in sorted(PROSE_ONLY)
            if (specs_dir / name).is_file()
        }
        return cls(
            prose={name: prose_units(text) for name, text in {**spec_texts, **records}.items()},
            root=tests_dir.parent,
            specs=spec_files(specs_dir),
            features=feature_files(acceptance_dir),
            producers=written,
            tests=tests,
        )


def test_names(path: Path, text: str) -> Iterator[tuple[str, str]]:
    """Every test of a module as `(where, name)`, the way a pointer or a title can reach it.

    The module is parsed, not scanned: a `def test_…(` inside a string literal — a fixture that
    writes a test file, a spec quoted in a test — is not a test, and treating it as one would let
    a document be accounted for by the fixture that quotes it.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:  # pragma: no cover - the suite is parsed by the tests themselves
        return
    label = str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            yield f"{label}::{node.name}", node.name


# --- rule 1: every declared name has a producer -------------------------------------------


def vocabulary_tables(text: str, location: str) -> list[Vocabulary]:
    """The closed vocabularies a spec enumerates in a table whose first column is the name.

    A table whose header names a `field` (or a `key`, or a `code`) in its first cell enumerates a
    discriminator: every value in that column is a name some writer must be able to store.
    """
    vocabularies: list[Vocabulary] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = _cells(line)
        if not cells:
            continue
        head = cells[0].strip().strip("`")
        if head not in {"field", "key", "code"}:
            continue
        if cells[-1].strip().strip("`").lower() == "sentence":
            # A grammar table lists the wordings of a sentence, not the values a writer may store:
            # its first column says which field a row is about and enumerates no vocabulary.
            continue
        names: list[str] = []
        for row in lines[index + 1 :]:
            if not row.lstrip().startswith("|"):
                break
            row_cells = _cells(row)
            if not row_cells or set(row_cells[0]) <= set("-: "):
                continue
            name = row_cells[0].strip().strip("`")
            if re.fullmatch(r"[a-z_][a-z0-9_]*", name):
                names.append(name)
        if names:
            vocabularies.append(
                Vocabulary(location=f"{location}:{index + 1}", kind="field", names=tuple(names))
            )
    return vocabularies


def json_fences(text: str, location: str) -> list[Vocabulary]:
    """The keys a fenced JSON document declares: the first object's, which is the shape it shows.

    A read model or a wire document is published as a JSON literal, and its keys are the names the
    code has to write — the same claim a `field` table makes, in a shape no table describes. A
    block that is not a JSON document (a fragment, a config with an ellipsis) declares nothing.
    """
    declared: list[Vocabulary] = []
    for block in FENCE_RE.finditer(text):
        if block.group("lang") != "json":
            continue
        first = _first_object(_json_document(block.group("body")))
        if not isinstance(first, dict):
            continue
        names = tuple(str(key) for key in first if _KEY.fullmatch(str(key)))
        if names:
            line = text.count("\n", 0, block.start()) + 1
            declared.append(Vocabulary(location=f"{location}:{line}", kind="json", names=names))
    return declared


#: A key a document declares: any name a writer can receive or store, camel case and all, but not
#: a phrase or a number, which no keyword argument spells.
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _first_object(document: object) -> object | None:
    """The first object a fenced document shows: the document itself, or the head of its list."""
    if isinstance(document, dict):
        return document
    if isinstance(document, list) and document:
        return document[0]
    return None


def _json_document(body: str) -> object | None:
    """A fenced block as the JSON it is, with the comments a spec writes in it removed.

    The fences annotate their keys (`# the id of the row`), and a comment is not JSON. The block is
    read character by character so a `#` inside a string stays part of it, and a block that does
    not parse declares nothing rather than guessing at its keys.
    """
    out: list[str] = []
    index, length = 0, len(body)
    in_string = escaped = in_line_comment = in_block_comment = False
    while index < length:
        char = body[index]
        pair = body[index : index + 2]
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                out.append(char)
            index += 1
            continue
        if in_block_comment:
            if pair == "*/":
                in_block_comment = False
                index += 2
                continue
            index += 1
            continue
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == "#" or pair == "//":
            in_line_comment = True
            index += 2 if pair == "//" else 1
            continue
        if pair == "/*":
            in_block_comment = True
            index += 2
            continue
        out.append(char)
        index += 1
    try:
        return json.loads("".join(out))
    except ValueError:
        return None


def _cells(row: str) -> list[str]:
    """The cells of a markdown table row."""
    stripped = row.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def dataclass_fields(text: str, location: str) -> list[Vocabulary]:
    """The dataclasses a spec declares in a fenced block, with the fields a writer must fill.

    Every annotated field is a declaration, default or not: a default is the class's own value and
    not a producer, so `state: str | None = None` still names a field some writer has to fill. A
    private field is internal, so the contract does not state it.
    """
    declared: list[Vocabulary] = []
    for block in FENCE_RE.finditer(text):
        if block.group("lang") not in {"python", "py"}:
            continue
        try:
            fragment = ast.parse(_dedent(block.group("body")))
        except SyntaxError:
            continue
        for node in ast.walk(fragment):
            if not isinstance(node, ast.ClassDef) or not _is_dataclass(node):
                continue
            names = [
                statement.target.id
                for statement in node.body
                if isinstance(statement, ast.AnnAssign)
                and isinstance(statement.target, ast.Name)
                and not statement.target.id.startswith("_")
            ]
            if names:
                declared.append(
                    Vocabulary(
                        location=f"{location}:{node.name}", kind="dataclass", names=tuple(names)
                    )
                )
    return declared


def _dedent(body: str) -> str:
    """A fenced block's text with its common indentation removed, so it can be parsed."""
    lines = [line for line in body.splitlines() if line.strip()]
    if not lines:
        return ""
    margin = min(len(line) - len(line.lstrip()) for line in lines)
    return "\n".join(line[margin:] for line in body.splitlines())


def _is_dataclass(node: ast.ClassDef) -> bool:
    """True when a class carries the dataclass decorator, called or bare."""
    for decorator in node.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", "")
        if name == "dataclass":
            return True
    return False


def check_producer(
    tree: Tree, exemptions: Sequence[Exemption], needed: set[str] | None = None
) -> list[Violation]:
    """Every name a closed vocabulary declares is written by some code under `src/`.

    `needed` collects the subject of every claim that fails, exempted or not, so the exemption
    table can be checked for rows the tree no longer needs without a second pass over the corpus.
    """
    accepted = {e.subject for e in exemptions if e.check == "producer"}
    violations: list[Violation] = []
    for path in tree.specs:
        text = path.read_text(encoding="utf-8")
        location = path.name
        declared = vocabulary_tables(text, location) + json_fences(text, location)
        for vocabulary in declared:
            for name in vocabulary.names:
                subject = _producer_subject(vocabulary, name)
                if tree.producers.stores(name):
                    continue
                if needed is not None:
                    needed.add(subject)
                if subject in accepted:
                    continue
                violations.append(
                    Violation(
                        "producer",
                        vocabulary.location,
                        f"the name {name!r} has no producer",
                        subject=subject,
                    )
                )
        for declared in dataclass_fields(text, location):
            class_name = declared.location.rsplit(":", 1)[-1]
            if class_name not in tree.producers.classes:
                continue
            for name in declared.names:
                subject = f"dataclass:{class_name}.{name}"
                if tree.producers.writes(name):
                    continue
                if needed is not None:
                    needed.add(subject)
                if subject in accepted:
                    continue
                violations.append(
                    Violation(
                        "producer",
                        declared.location,
                        f"the field {class_name}.{name} is declared and nothing assigns it",
                        subject=subject,
                    )
                )
    return violations


def _producer_subject(vocabulary: Vocabulary, name: str) -> str:
    """The stable key of one declared name, as the exemption table spells it."""
    if vocabulary.kind == "dataclass":
        class_name = vocabulary.location.rsplit(":", 1)[-1]
        return f"dataclass:{class_name}.{name}"
    return f"field:{name}"


# --- rule 2: every scenario has a test or a deferral --------------------------------------


def scenarios(tree: Tree) -> list[ScenarioClaim]:
    """Every scenario the specs state: the acceptance section of each, and the feature corpus."""
    claims: list[ScenarioClaim] = []
    for path in tree.specs:
        text = path.read_text(encoding="utf-8")
        for number, body in _sections(text):
            if number != "7":
                continue
            for block in FENCE_RE.finditer(body):
                claims.extend(_scenarios_in(block.group("body"), origin="spec", location=path.name))
    for path in tree.features:
        claims.extend(
            _scenarios_in(path.read_text(encoding="utf-8"), origin="feature", location=path.name)
        )
    return claims


def _sections(text: str) -> Iterator[tuple[str, str]]:
    """The numbered sections of a document as `(number, body)`."""
    matches = list(SECTION_RE.finditer(text))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield match.group("number"), text[match.end() : end]


def _scenarios_in(text: str, *, origin: str, location: str) -> list[ScenarioClaim]:
    """The scenarios of one gherkin block, with the tags written above each one."""
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if SCENARIO_RE.match(line)]
    claims: list[ScenarioClaim] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        tags: list[str] = []
        index = start - 1
        while index >= 0:
            stripped = lines[index].strip()
            tag_match = TAG_LINE_RE.match(lines[index])
            if tag_match:
                tags = tag_match.group("tags").split() + tags
                index -= 1
                continue
            if not stripped or stripped.startswith("#"):
                index -= 1
                continue
            break
        title = SCENARIO_RE.match(lines[start]).group("title")  # type: ignore[union-attr]
        claims.append(
            ScenarioClaim(
                origin=origin,
                location=f"{location}:{title}",
                title=title,
                tags=tuple(tags),
                body="\n".join(lines[start:end]),
                document=location,
            )
        )
    return claims


def _defines(path: Path, name: str) -> bool:
    """True when the module at `path` defines a function called `name`, parsed not grepped.

    A pointer is a claim that a test exists. Searching the text for `def name(` would accept one
    written inside a string literal — a fixture that builds a test, a test that quotes one — so
    the module is parsed and only a real definition counts.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return False
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
        for node in ast.walk(tree)
    )


def _first_tag(tags: Sequence[str], pattern: re.Pattern[str]) -> re.Match[str] | None:
    """The first tag of a scenario that a pattern matches, or nothing when no tag does."""
    for tag in tags:
        match = pattern.match(tag)
        if match:
            return match
    return None


def ticket_is_registered(ticket: str, registry: DebtRegistry) -> bool:
    """True when a ticket is one the registry carries, which is what makes it an owner.

    A deferral and an exemption ask the tree the same question — is somebody accountable for this
    gap — so they ask it here, once. A ticket id checked for its shape alone accepts a ticket
    nobody issued: `HIFA-9999` is a well-formed id and an owner of nothing.
    """
    return bool(ticket) and ticket in registry.tickets


def check_exemptions(
    exemptions: Sequence[Exemption],
    registry: DebtRegistry,
    checks: Sequence[str] = CHECKS,
) -> list[Violation]:
    """Every accepted gap names a ticket the registry carries, the rule a deferral already obeys."""
    return [
        Violation(
            exemption.check,
            exemption.ticket,
            f"the exemption for {exemption.subject} names a ticket the registry does not carry",
            subject=exemption.subject,
        )
        for exemption in exemptions
        if exemption.check in checks and not ticket_is_registered(exemption.ticket, registry)
    ]


def account_for(claim: ScenarioClaim, tree: Tree, registry: DebtRegistry) -> tuple[str, str]:
    """How a scenario is accounted for, as `(kind, evidence)`; `("", "")` when nothing does.

    Four answers, in this order: a `@test(<path>::<name>)` that resolves; a test whose name is the
    title, normalised on both sides and compared whole; a `@deferred(<ticket>)` whose ticket the
    registry knows; nothing. `broken` is a tag that claims to account for the scenario and does
    not — a pointer into thin air, a deferral with no ticket, a ticket nobody issued — and it fails
    as such, because a broken link and a missing one are different defects and only one of them is
    a lie. A bare `@deferred` is broken wherever it sits: a deferral that names nobody is nobody's,
    so the accepted-key test is the wrong question to ask of it.
    """
    pointer = _first_tag(claim.tags, TEST_POINTER_RE)
    if pointer:
        where, name = pointer.group("path"), pointer.group("name")
        target = tree.root / where
        if not target.is_file():
            return "broken", f"@test({where}::{name}) names a file that does not exist"
        if not _defines(target, name):
            return "broken", f"@test({where}::{name}) names a test that does not exist"
        return "test", f"{where}::{name}"
    matched = tree.tests.get(normalised_title(claim.title))
    if matched:
        return "test", f"{matched} carries the title"
    deferral = _first_tag(claim.tags, DEFERRED_RE)
    if deferral:
        ticket = (deferral.group("ticket") or "").strip()
        if not ticket:
            return "broken", "@deferred names no ticket: a deferral without a ticket is nobody's"
        if not TICKET_RE.match(ticket):
            return "broken", f"{deferral.group(0)} names no ticket id"
        if not ticket_is_registered(ticket, registry):
            return "broken", f"{deferral.group(0)} names a ticket the registry does not carry"
        return "deferred", ticket
    return "", ""


@dataclass(frozen=True)
class Accounting:
    """One scenario and how the tree accounts for it."""

    claim: ScenarioClaim
    kind: str
    evidence: str


def account(tree: Tree, registry: DebtRegistry | None = None) -> list[Accounting]:
    """Every scenario of the corpus with what accounts for it; nothing is guessed."""
    accepted = registry or DebtRegistry()
    return [Accounting(claim, *account_for(claim, tree, accepted)) for claim in scenarios(tree)]


def unaccounted(reading: Accounting) -> bool:
    """True when nothing accounts for the scenario, which is the only case a key may accept.

    One predicate, because four places ask the question — whether a claim passes, whether a key is
    still stale, what the summary counts, what `--accept` keeps — and a list of kinds spelled out
    in each of them is how a broken tag came to be counted as an accepted one.
    """
    return reading.kind == ""


def check_scenario(
    readings: Sequence[Accounting], registry: DebtRegistry | None = None
) -> list[Violation]:
    """Every scenario is pinned by a test, deferred by a known ticket, or accepted by its own key.

    Each unaccounted scenario is judged alone: its key is either in the committed set or it is a
    violation, so a defect inserted anywhere in a document is reported against itself and not
    against whichever claim happens to sit at the same ordinal. A key the corpus no longer needs
    fails too, with the command that drops it, which is what makes the set shrink and never grow.
    """
    accepted = (registry or DebtRegistry()).keys
    violations: list[Violation] = []
    for reading in readings:
        key = claim_key(reading.claim)
        if reading.kind == "broken":
            violations.append(Violation("scenario", reading.claim.location, reading.evidence))
            continue
        if not unaccounted(reading) or key in accepted:
            continue
        detail = reading.evidence or (
            "no test pins it and no ticket defers it: add @test(<path>::<name>) or name a test "
            f"after the title; {key!r} is a claim that needs one, not a key to accept"
        )
        violations.append(Violation("scenario", reading.claim.location, detail))
    return violations


def scenario_counts(
    readings: Sequence[Accounting], registry: DebtRegistry | None = None
) -> dict[str, int]:
    """The five numbers the summary publishes: pinned, deferred, accepted, new and broken.

    A broken tag is counted as broken and never as accepted: its key may well be in the set, and
    that is precisely the reading the set must not swallow — a tag that claims an owner and names
    none is a lie whether or not the scenario behind it was ever accepted.
    """
    accepted = (registry or DebtRegistry()).keys
    counts = {
        "scenarios": len(readings),
        "accounted": 0,
        "deferred": 0,
        "accepted": 0,
        "new": 0,
        "broken": 0,
    }
    for reading in readings:
        if reading.kind == "test":
            counts["accounted"] += 1
        elif reading.kind == "deferred":
            counts["deferred"] += 1
        elif reading.kind == "broken":
            counts["broken"] += 1
        elif not unaccounted(reading):
            continue
        elif claim_key(reading.claim) in accepted:
            counts["accepted"] += 1
        else:
            counts["new"] += 1
    return counts


def debt_report(readings: Sequence[Accounting], registry: DebtRegistry | None = None) -> list[str]:
    """One line per cluster and one per accepted key: the debt, named rather than counted."""
    live = {claim_key(reading.claim) for reading in readings if unaccounted(reading)}
    accepted = (registry or DebtRegistry()).keys
    lines: list[str] = []
    for cluster in sorted((registry or DebtRegistry()).clusters, key=lambda c: c.ticket):
        needed = sum(1 for key in cluster.keys if key in live)
        lines.append(
            f"  {cluster.ticket}: {needed} of {len(cluster.keys)} accepted keys still "
            f"unaccounted — {cluster.reason}"
        )
        lines.extend(f"    accepted: {key}" for key in sorted(cluster.keys))
    loose = sorted(live - accepted)
    if loose:
        lines.append(f"  not accepted: {len(loose)}")
        lines.extend(f"    unaccounted: {key}" for key in loose)
    return lines


# --- rule 3: every published measurement names its provenance -----------------------------


def prose_units(text: str) -> list[tuple[str, str]]:
    """A document's prose as `(unit, sentence)` pairs, a fence included and read on its own.

    A unit is a paragraph or a bullet: wrapped lines are one unit, and the sentences of a unit are
    what the rule reads, so a number and its provenance have to travel together. A fenced block is
    a unit too, one line at a time: a count inside a fence is a published count, and the file it
    came from has to be in that line, because the paragraph above it is not its source.
    """
    units: list[tuple[str, str]] = []
    cursor = 0
    for block in FENCE_RE.finditer(text):
        units.extend(_paragraphs(text[cursor : block.start()]))
        for line in block.group("body").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("|"):
                units.append(_unit([stripped]))
        cursor = block.end()
    units.extend(_paragraphs(text[cursor:]))
    return units


def _paragraphs(text: str) -> list[tuple[str, str]]:
    """The prose of a chunk of a document as units: a blank line, a heading or a table ends one."""
    units: list[tuple[str, str]] = []
    buffer: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("|") or stripped.startswith("#"):
            if buffer:
                units.append(_unit(buffer))
                buffer = []
            continue
        if re.match(r"^(?:[-*+]|\d+\.)[ \t]", stripped) and buffer:
            units.append(_unit(buffer))
            buffer = []
        buffer.append(stripped)
    if buffer:
        units.append(_unit(buffer))
    return units


def _unit(lines: list[str]) -> tuple[str, str]:
    """A unit's text and the first line of it, for the report."""
    text = " ".join(lines)
    for sentence in SENTENCE_SPLIT.split(text):
        if _publishes_a_count(sentence):
            return (lines[0][:80], sentence)
    return (lines[0][:80], SENTENCE_SPLIT.split(text)[0] if text else "")


def _publishes_a_count(sentence: str) -> bool:
    """True when a sentence states a row count as a fact about a database."""
    if NOT_A_DATABASE_COUNT.search(sentence):
        return False
    if not ROW_NOUN.search(sentence):
        return False
    return any(pattern.search(sentence) for pattern in MEASUREMENT)


def _backticked(sentence: str) -> list[str]:
    """The tokens a sentence writes in backticks, which is the only form the rule reads."""
    return [match.group(1).strip() for match in BACKTICK.finditer(sentence)]


def _is_command(token: str) -> bool:
    """True when a token is a command: something a reader could run and get the number from.

    A database file is data, not a command: `` `~/.local/share/hifa/hifa.db` `` names where the
    rows live and not what counted them, which is why a path on its own is the weaker clause.
    """
    if token.startswith(COMMAND_PREFIXES):
        return True
    return "/" in token and bool(re.search(r"\.(?:py|sql)\b", token))


def _is_path(token: str) -> bool:
    """True when a token names a file: it has a directory, or it ends in a script or data file."""
    return "/" in token or bool(PATH_SUFFIX.search(token))


def names_its_source(sentence: str) -> bool:
    """True when the sentence carries, in backticks, a command, or a path and a date.

    A path alone names where the number lives and not what read it, and a date alone names when
    somebody looked and not at what; neither can be repeated, so neither is a source. A command can
    be run again, and a path beside a date says which file on which day, which is the least a
    count needs to be checked.
    """
    tokens = _backticked(sentence)
    if any(_is_command(token) for token in tokens):
        return True
    return bool(DATE.search(sentence)) and any(_is_path(token) for token in tokens)


def check_provenance(
    tree: Tree, exemptions: Sequence[Exemption], needed: set[str] | None = None
) -> list[Violation]:
    """A published count names, in its sentence, the command or the file and day it came from."""
    accepted = [e for e in exemptions if e.check == "provenance"]
    violations: list[Violation] = []
    for name, units in tree.prose.items():
        for where, sentence in units:
            if not _publishes_a_count(sentence):
                continue
            if names_its_source(sentence):
                continue
            if needed is not None:
                needed.add(sentence)
            if any(e.subject in sentence for e in accepted if e.subject):
                continue
            violations.append(
                Violation(
                    "provenance",
                    f"{name}:{where[:60]}",
                    f"a measured count names no command or dated file: {sentence[:120]!r}",
                    subject=sentence,
                )
            )
    return violations


# --- the accepted gaps --------------------------------------------------------------------

#: Names a closed vocabulary, a fenced document or a dataclass declares that no function writes yet,
#: one row per name, each carrying the ticket the registry knows. The transfer catalogue is per code
#: rather than per table so that a code the component lands shrinks its own row and cannot ride on
#: the others.
PRODUCER_EXEMPTIONS: tuple[Exemption, ...] = (
    Exemption(
        check="producer",
        subject="dataclass:Repos.projects",
        ticket="HIFA-8",
        reason="the project repository lands with the projects feature; the rest are built",
    ),
    Exemption(
        check="producer",
        subject="dataclass:Repos.cycles",
        ticket="HIFA-8",
        reason="the cycle repository lands with the cycles feature; the rest are built",
    ),
    Exemption(
        check="producer",
        subject="dataclass:RetryPolicy.attempts",
        ticket="HIFA-106",
        reason="the retry parameters are read from the class default and no function assigns one",
    ),
    Exemption(
        check="producer",
        subject="dataclass:RetryPolicy.base_delay",
        ticket="HIFA-106",
        reason="the retry parameters are read from the class default and no function assigns one",
    ),
    Exemption(
        check="producer",
        subject="dataclass:MoveIssue.rebalance",
        ticket="HIFA-106",
        reason="the command validates the switch and nothing sets it: the internal switch is never "
        "thrown by a caller",
    ),
    Exemption(
        check="producer",
        subject="dataclass:CreateTeam.estimation_type",
        ticket="HIFA-106",
        reason="the command fills the field by name in one post-init and no function assigns it",
    ),
    Exemption(
        check="producer",
        subject="dataclass:CreateTeam.default_state",
        ticket="HIFA-106",
        reason="the use case refuses the option the command declares, so the field has no writer "
        "until the spec or the use case changes",
    ),
    *(
        Exemption(
            check="producer",
            subject=f"field:{code}",
            ticket="HIFA-9",
            reason="the row-error catalogue of the transfer component, which the tree lacks",
        )
        for code in (
            "missing_title",
            "invalid_priority",
            "invalid_due_date",
            "invalid_estimate",
            "unknown_state",
            "unknown_label",
            "ambiguous_label",
            "too_many_labels",
            "unknown_assignee",
            "ambiguous_assignee",
            "unknown_project",
            "ambiguous_project",
            "unknown_cycle",
            "ambiguous_cycle",
            "identifier_team_mismatch",
            "invalid_identifier",
            "duplicate_in_file",
            "duplicate_issue",
            "number_conflict",
        )
    ),
    *(
        Exemption(
            check="producer",
            subject=f"field:{key}",
            ticket="HIFA-9",
            reason="the export envelope of the transfer component, which the tree does not carry",
        )
        for key in ("format", "schema_version", "exported_at", "app_version")
    ),
    *(
        Exemption(
            check="producer",
            subject=f"field:{key}",
            ticket="HIFA-107",
            reason="the release engine reads its contract from a config file, not from a module",
        )
        for key in ("branches", "tagFormat", "plugins")
    ),
)

#: Published counts that name no command and no dated file.
PROVENANCE_EXEMPTIONS: tuple[Exemption, ...] = (
    Exemption(
        check="provenance",
        subject="the measured database has zero",
        ticket="HIFA-104",
        reason="the count names no command or dated file; the ticket puts the provenance in it",
    ),
    Exemption(
        check="provenance",
        subject="33 of the 34 rows of the measured database",
        ticket="HIFA-104",
        reason="the count names no command or dated file; the ticket puts the provenance in it",
    ),
    Exemption(
        check="provenance",
        subject="holds batched rank rows",
        ticket="HIFA-104",
        reason="the sentence names the file and not the command or the day; the ticket adds them",
    ),
    Exemption(
        check="provenance",
        subject="33 of its 34 rows share their",
        ticket="HIFA-104",
        reason="the decision log published the rotted count; the ticket dates it",
    ),
    Exemption(
        check="provenance",
        subject="33 of the measured database's 34 `board_order` rows",
        ticket="HIFA-104",
        reason="the decision log published the rotted count; the ticket dates it",
    ),
    Exemption(
        check="provenance",
        subject="The measured database agreed with the rule",
        ticket="HIFA-104",
        reason="the decision log published the rotted count; the ticket dates it",
    ),
)


def exemptions() -> list[Exemption]:
    """Every accepted gap, in force."""
    return [*PRODUCER_EXEMPTIONS, *PROVENANCE_EXEMPTIONS]


def stale(
    report: Report,
    tree: Tree,
    readings: Sequence[Accounting],
    needed: set[str] | None = None,
    checks: Sequence[str] = CHECKS,
) -> list[Violation]:
    """An exemption the tree no longer needs is a row nobody can justify.

    A row is needed while the claim it accepts still fails: a spec that stopped declaring the name,
    or a writer that landed, leaves the row behind and the row fails until it is deleted. `needed`
    is what the checks collected; a caller that ran none of them gets the claim set here. The
    accepted scenario keys are staleness of the same kind: a key no unaccounted claim carries is a
    scenario the corpus no longer has, and it fails until `--accept` rewrites the set.
    """
    violations: list[Violation] = []
    declared = needed if needed is not None else set()
    if needed is None:  # a caller that ran no check still gets the rows that no longer apply
        check_producer(tree, [], declared)
        check_provenance(tree, [], declared)
    for exemption in report.exemptions:
        if exemption.check not in checks:
            continue
        if exemption.check == "provenance":
            still = any(
                exemption.subject and exemption.subject in sentence for sentence in declared
            )
        else:
            still = exemption.subject in declared
        if not still:
            violations.append(
                Violation(
                    exemption.check, exemption.ticket, f"stale exemption for {exemption.subject}"
                )
            )
    if "scenario" in checks:
        carried_keys = {claim_key(reading.claim) for reading in readings if unaccounted(reading)}
        for key in sorted(report.debts.keys - carried_keys):
            violations.append(
                Violation(
                    "scenario",
                    key,
                    "accepted key names no unaccounted scenario: re-run --accept to drop it",
                )
            )
    return violations


# --- the report ---------------------------------------------------------------------------


def build_report(
    tree: Tree, only: Sequence[str] = CHECKS, registry: DebtRegistry | None = None
) -> Report:
    """Run the checks and collect their verdicts, with the exemptions in force."""
    accepted = exemptions()
    debts = registry if registry is not None else DebtRegistry.read()
    report = Report(exemptions=list(accepted), debts=debts)
    readings = account(tree, debts)
    needed: set[str] = set()
    if "producer" in only:
        report.violations.extend(check_producer(tree, accepted, needed))
    if "scenario" in only:
        report.violations.extend(check_scenario(readings, debts))
    if "provenance" in only:
        report.violations.extend(check_provenance(tree, accepted, needed))
    report.violations.extend(check_exemptions(accepted, debts, only))
    report.violations.extend(stale(report, tree, readings, needed, only))
    report.readings = readings
    report.counts = scenario_counts(readings, debts)
    report.counts["documents"] = len(tree.specs) + len(tree.features)
    return report


def summary(report: Report) -> str:
    """The one line every run prints: the verdict, the exemptions, and the scenarios by answer."""
    accepted = len(report.exemptions)
    counts = report.counts
    verdict = "ok" if report.ok else f"{len(report.violations)} violation(s)"
    return (
        f"spec claims: {verdict}; {accepted} exemption(s) in force "
        f"(producer {len(report.of('producer'))}, provenance {len(report.of('provenance'))}); "
        f"scenario: {counts.get('accounted', 0)} accounted, "
        f"{counts.get('deferred', 0)} deferred by ticket, "
        f"{counts.get('accepted', 0)} unaccounted but accepted, {counts.get('new', 0)} new, "
        f"{counts.get('broken', 0)} broken "
        f"of {counts.get('scenarios', 0)} in {counts.get('documents', 0)} document(s)"
    )


def accept(tree: Tree, registry: DebtRegistry) -> tuple[DebtRegistry, int, int, int]:
    """Rewrite the accepted set from the tree: every unaccounted key kept, stale ones dropped.

    Returns the registry, how many keys it now holds, how many it dropped and how many it would
    add. An addition is the caller's decision, not this function's: a key that is not already in
    the set is a claim nobody has tested, and writing it here would silence the gate that found it.
    The default cluster is the one a new key joins; a key an existing cluster owns is left where a
    human put it.
    """
    readings = account(tree, registry)
    live = [claim_key(reading.claim) for reading in readings if unaccounted(reading)]
    dropped = len(registry.keys - set(live))
    added = len(set(live) - registry.keys)
    reason = next((cluster.reason for cluster in registry.clusters), "")
    return registry.accepted(live, reason), len(live), dropped, added


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that what a spec declares has a producer, a test or a provenance."
    )
    parser.add_argument("--specs", type=Path, default=SPECS_DIR, help="directory of the specs")
    parser.add_argument("--src", type=Path, default=SRC_DIR, help="directory of the producers")
    parser.add_argument("--tests", type=Path, default=TESTS_DIR, help="directory of the tests")
    parser.add_argument(
        "--acceptance", type=Path, default=None, help="directory of the acceptance corpus"
    )
    parser.add_argument(
        "--only", action="append", choices=CHECKS, default=None, help="run one check only"
    )
    parser.add_argument(
        "--debt", type=Path, default=DEBT_FILE, help="the accepted scenarios and known tickets"
    )
    parser.add_argument(
        "--accept", action="store_true", help="rewrite the accepted scenarios from the tree"
    )
    parser.add_argument(
        "--allow-growth",
        action="store_true",
        help="let --accept add a key no accepted scenario carried, which is a decision",
    )
    parser.add_argument(
        "--list-debt", action="store_true", help="print every accepted key, by cluster"
    )
    parser.add_argument("--quiet", action="store_true", help="only the summary line")
    parser.add_argument("--json", action="store_true", help="the verdict as JSON")
    args = parser.parse_args(argv)

    acceptance = args.acceptance if args.acceptance is not None else args.specs / "acceptance"
    tree = Tree.read(args.specs, args.src, args.tests, acceptance)
    registry = DebtRegistry.read(args.debt)

    if args.accept:
        if not registry.default_ticket:
            print(
                f"refusing to accept: {args.debt} names no ticket to own the keys",
                file=sys.stderr,
            )
            return 2
        written, kept, dropped, added = accept(tree, registry)
        if added and not args.allow_growth:
            print(
                f"refusing to grow the accepted set: {added} key(s) have no test and no ticket; "
                "a claim needs a test or a registered ticket, and adding its key is a decision "
                "somebody makes in a pull request, not the remedy this gate suggests. Pass "
                "--allow-growth to make it here.",
                file=sys.stderr,
            )
            return 2
        written.write(args.debt)
        print(
            f"accepted {kept} key(s) in {args.debt}: {dropped} stale key(s) dropped, "
            f"{added} added, ticket(s) {', '.join(sorted(written.tickets))}"
        )
        return 0

    report = build_report(tree, args.only or CHECKS, registry)

    if args.json:
        print(
            json.dumps(
                {
                    "ok": report.ok,
                    "violations": [v.line() for v in report.violations],
                    "exemptions": [
                        {"check": e.check, "subject": e.subject, "ticket": e.ticket}
                        for e in report.exemptions
                    ],
                    "debts": [
                        {
                            "ticket": cluster.ticket,
                            "accepted": len(cluster.keys),
                            "reason": cluster.reason,
                        }
                        for cluster in report.debts.clusters
                    ],
                    "checks": report.counts,
                },
                indent=2,
            )
        )
        return 0 if report.ok else 1

    if not args.quiet:
        for violation in report.violations:
            print(f"FAIL: {violation.line()}")
    if args.list_debt and not args.quiet:
        for line in debt_report(report.readings, report.debts):
            print(line)
    print(summary(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
