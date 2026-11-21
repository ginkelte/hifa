#!/usr/bin/env python3
"""Check that what a spec declares has a producer, a test or a provenance.

A spec is the source of truth, so a claim it makes has to be reachable from the tree: a name a
closed vocabulary declares must be written by some code, a scenario must be pinned by a test (or
say it is not), and a count published as a measurement must name the file, the command or the day
it came from. This script is that check. It is the mechanical form of the review that reads prose.

The three rules
---------------
producer
    A closed vocabulary table, or a dataclass declared in a fenced block, enumerates names the
    code must be able to produce. For every name the table or the class declares, a literal of
    that name must appear in a writer under `src/`: a keyword argument of a call, a key of a
    dict literal, or an assignment. A field a class declares and nothing ever assigns is a
    violation, and so is a sentence a grammar table declares that no string literal renders.
    Comments and docstrings do not count — quoting a sentence is not producing it. A dataclass
    whose class does not exist in `src/` is not checked: the contract of a component that has
    not been built yet declares nothing the tree can contradict.

scenario
    Every `Scenario` a spec states as an acceptance case, and every one of the acceptance
    feature files, must be accounted for, in this order: `@test(<path>::<name>)` naming a test
    that exists; a test whose name is the scenario's title, compared exactly after both are
    normalised; or `@deferred(KEY-NN)` naming a ticket the accepted-gaps registry knows. Anything
    else is unaccounted. There is no third guess: a literal quoted in the steps, a shared word or
    a similar title does not pin a scenario, because a phrase that appears in a hundred tests
    identifies none of them.

provenance
    A sentence that publishes a count as a fact about a database must name where the number came
    from: the path of the file, the command that answered it, or the date it was read. A number
    with none of the three rots silently — the file changes and the sentence keeps asserting.

Exemptions
----------
The tree carries gaps of all three kinds with tickets against them. Each accepted gap is one row
in the table of its rule: the subject as the spec states it, the ticket that closes it, and the
reason. A row whose subject no longer appears in the specs is stale and fails, so the table can
only shrink honestly. The number of rows in force is printed in the summary.

The scenario rule carries its legacy tail in a file rather than in this one: the specs name
behaviours in prose and most of them were never joined to a test, so the unaccounted scenarios are
accepted by key in `spec_claims_debt.json`, next to this script and versioned with it. A key is
`<document>::<normalised title>`, and the file also holds the ticket that owns each cluster and the
ticket ids a deferral may cite. The set is compared exactly, so it can only shrink: a scenario
deleted from the corpus leaves a key no claim carries and fails until `--accept` drops it, and a
scenario added without a test has no key and fails on the spot. `--accept` rewrites the file from
the tree, which is the only way it changes.

Usage
-----
    python scripts/spec_claims_check.py                  # every check, human-readable
    python scripts/spec_claims_check.py --only producer  # one check
    python scripts/spec_claims_check.py --list-debt      # the accepted keys, by cluster
    python scripts/spec_claims_check.py --accept         # rewrite the accepted keys from the tree
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

#: Files that are records rather than live claims: an index, the reading guide, the template,
#: the decision log (each entry dated) and the dated review reports.
NOT_A_CLAIM = frozenset({"INDEX.md", "README.md", "_template.md", "DECISIONS.md", "notes.md"})

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
QUOTED_RE = re.compile(r"`([^`\n]{5,80})`|\"([^\"\n]{5,80})\"")

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
PROVENANCE = re.compile(
    r"\.db\b|~/[\w./-]+|`hifa +[a-z]|`sqlite3 +\S|\b(?:sqlite3|grep|pytest|python) +-?\w"
    r"|\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2} [A-Z][a-z]{2}\.? \d{4}\b"
)
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
    values: dict[str, tuple[str, ...]] = field(default_factory=dict)


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
    """What the modules of `src/` write: the names they produce, the classes they define, and
    the strings they build, with each interpolated string kept as one template."""

    names: set[str] = field(default_factory=set)
    classes: set[str] = field(default_factory=set)
    literals: list[str] = field(default_factory=list)
    values: set[str] = field(default_factory=set)
    docstrings: set[int] = field(default_factory=set, repr=False)

    def writes(self, name: str) -> bool:
        """True when some code writes `name` as a field: a keyword argument, a key or a target.

        A declaration is not a write: a field a class annotates and nothing ever assigns has no
        producer, which is the defect this rule exists to find.
        """
        return name in self.names

    def stores(self, name: str) -> bool:
        """True when some code writes `name` as a value: a literal, or a key of a dict.

        A vocabulary table enumerates the values a writer stores, and a stored value reaches the
        code as a string — the column's name is the payload, so a literal is its producer.
        """
        return name in self.values or name in self.names


def producers(sources: dict[Path, str]) -> Producers:
    """Read the producers of every module once: one parse, one walk, no regex over the text."""
    found = Producers()
    for text in sources.values():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        found.docstrings = _docstring_nodes(tree)
        _Reader(found).visit(tree)
    return found


class _Reader(ast.NodeVisitor):
    """Collect what each kind of node writes; the walk is the visitor's, not an isinstance chain."""

    def __init__(self, found: Producers) -> None:
        """Read into the producers being built."""
        self.found = found

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """A class defines a name the specs may declare a dataclass for."""
        self.found.classes.add(node.name)
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """An assignment writes its targets."""
        for target in node.targets:
            _target_name(target, self.found)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """An annotated assignment writes its target only when it carries a value."""
        if node.value is not None:
            _target_name(node.target, self.found)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """An augmented assignment writes its target."""
        _target_name(node.target, self.found)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """A keyword argument names a field its callee receives."""
        for keyword in node.keywords:
            if keyword.arg:
                self.found.names.add(keyword.arg)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        """A string key of a dict literal is a name the code produces."""
        for key in node.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self.found.names.add(key.value)
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        """A string constant is a literal the code builds, unless it is a docstring."""
        if isinstance(node.value, str) and id(node) not in self.found.docstrings:
            self.found.literals.append(node.value)
            self.found.values.add(node.value)

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        """An interpolated string is one template; its parts are not literals of their own."""
        self.found.literals.append(_template(node))


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """The nodes that are docstrings: a string expression in the first position of a body."""
    nodes: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", [])
        first = body[0] if body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            nodes.add(id(first.value))
    return nodes


def _target_name(target: ast.AST, found: Producers) -> None:
    """The name an assignment writes, whether to a local or to an attribute."""
    if isinstance(target, ast.Name):
        found.names.add(target.id)
    elif isinstance(target, ast.Attribute):
        found.names.add(target.attr)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for element in target.elts:
            _target_name(element, found)


def _template(node: ast.JoinedStr) -> str:
    """An interpolated string as one template, each substituted value a `{…}` gap."""
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append("{…}")
    return "".join(parts)


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
        for path in src_dir.rglob("*.html"):
            written.literals.append(path.read_text(encoding="utf-8"))
        test_files = sorted(tests_dir.rglob("*.py")) if tests_dir.is_dir() else []
        tests: dict[str, str] = {}
        for path in test_files:
            for where, name in test_names(path, path.read_text(encoding="utf-8")):
                tests.setdefault(test_key(name), where)
        spec_texts = {p.name: p.read_text(encoding="utf-8") for p in spec_files(specs_dir)}
        return cls(
            prose={name: prose_units(text) for name, text in spec_texts.items()},
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
            # A grammar table's first column names the fields its sentences are about; the
            # vocabulary it declares is the wording, and `sentence_tables` reads that column.
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


def sentence_tables(text: str, location: str) -> list[Vocabulary]:
    """The sentence grammar a spec declares: every row names a wording a renderer must produce."""
    vocabularies: list[Vocabulary] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not line.lstrip().startswith("|"):
            continue
        cells = _cells(line)
        if not cells or cells[-1].strip().strip("`").lower() != "sentence":
            continue
        sentences: list[str] = []
        shown: dict[str, tuple[str, ...]] = {}
        for row in lines[index + 1 :]:
            if not row.lstrip().startswith("|"):
                break
            row_cells = _cells(row)
            if len(row_cells) < 3 or set(row_cells[0]) <= set("-: "):
                continue
            values = tuple(
                word
                for cell in row_cells[1:3]
                for word in re.findall(r"[A-Za-z0-9_’-]+", cell)
                if word not in {"–", "-", "name", "ts", "X", "Y", "Z"}
            )
            for part in re.split(r"\s+/\s+", row_cells[-1]):
                quoted = QUOTED_RE.findall(part)
                if not quoted:
                    continue
                text_value = (quoted[0][0] or quoted[0][1]).strip()
                if text_value:
                    sentences.append(text_value)
                    shown[text_value] = values
        if sentences:
            vocabularies.append(
                Vocabulary(
                    location=f"{location}:{index + 1}",
                    kind="sentence",
                    names=tuple(sentences),
                    values=shown,
                )
            )
    return vocabularies


def _cells(row: str) -> list[str]:
    """The cells of a markdown table row."""
    stripped = row.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.strip("|").split("|")]


def dataclass_fields(text: str, location: str) -> list[Vocabulary]:
    """The dataclasses a spec declares in a fenced block, with the fields a writer must fill.

    A field with a default carries its own value, so it is not waiting for a producer.
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
                and statement.value is None
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
        for vocabulary in vocabulary_tables(text, location) + sentence_tables(text, location):
            table = f"table:{vocabulary.location}"
            for name in vocabulary.names:
                subject = _producer_subject(vocabulary, name)
                if vocabulary.kind == "sentence":
                    produced = _sentence_is_rendered(
                        name, tree.producers.literals, vocabulary.values.get(name, ())
                    )
                    detail = f"the sentence {name!r} has no producer"
                else:
                    produced = tree.producers.stores(name)
                    detail = f"the name {name!r} has no producer"
                if produced:
                    continue
                if needed is not None:
                    needed.add(subject)
                    needed.add(table)
                if table in accepted or subject in accepted:
                    continue
                violations.append(
                    Violation("producer", vocabulary.location, detail, subject=subject)
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
    if vocabulary.kind == "sentence":
        return f"sentence:{name}"
    if vocabulary.kind == "dataclass":
        class_name = vocabulary.location.rsplit(":", 1)[-1]
        return f"dataclass:{class_name}.{name}"
    return f"field:{name}"


def _sentence_is_rendered(
    sentence: str, literals: Sequence[str], values: Sequence[str] = ()
) -> bool:
    """True when some literal the code builds narrates the sentence, values and all.

    The table writes the sentence with its values filled in (`moved from Backlog to Todo`) and the
    code builds it from a template (`moved from {old} to {new}`): both sides are reduced to the
    words that are fixed, each substituted value is a wildcard, and the two sequences have to agree
    on almost all of them.
    """
    expected = _sentence_words(sentence, values)
    if not expected:
        return False
    if expected == ["*"]:
        return True
    for literal in literals:
        found = _literal_words(literal)
        if found == expected:
            return True
        if len(found) < 2:
            continue
        if _agreement(expected, found) > 0.75:
            return True
    return False


def _sentence_words(sentence: str, values: Sequence[str] = ()) -> list[str]:
    """The words of a declared sentence: lowercased, each value the row shows a wildcard.

    The values are the row's own — the table's `old` and `new` columns say which words are
    instances — so a noun the wording keeps (`moved to project X` keeps `project`) stays a word
    and has to be found in the literal the code builds.
    """
    known = {value.lower() for value in values}
    flattened = re.sub(r"“[^”]*”", " * ", sentence)
    tokens = re.findall(r"[A-Za-z0-9_*<>{}.':’-]+", flattened)
    out: list[str] = []
    for position, token in enumerate(tokens):
        bare = token.lower()
        if bare in {"x", "y", "z", "<field>"}:
            continue
        is_value = bare in known or (
            position > 0 and (token[:1].isupper() or any(ch.isdigit() for ch in token))
        )
        out.append("*" if is_value else bare)
    return _collapse(out)


def _literal_words(literal: str) -> list[str]:
    """The words of a string the code builds: lowercased, each `{…}` gap a wildcard."""
    tokens = re.findall(r"[A-Za-z0-9_*<>{}.':’-]+", literal.lower())
    out = ["*" if ("{" in token or "…" in token) else token for token in tokens]
    return _collapse(out)


def _collapse(tokens: list[str]) -> list[str]:
    """One wildcard for a run of them, so a date and a name cost the same."""
    out: list[str] = []
    for token in tokens:
        if token == "*" and out and out[-1] == "*":
            continue
        out.append(token)
    return out


def _agreement(expected: Sequence[str], found: Sequence[str]) -> float:
    """The fraction of `expected` that `found` carries in order.

    A wildcard of the sentence stands for a value and matches whatever the code puts there; a
    word of the sentence has to be the code's word, so a gap in the literal cannot swallow it.
    """
    rows, columns = len(expected), len(found)
    table = [[0] * (columns + 1) for _ in range(rows + 1)]
    for i in range(1, rows + 1):
        for j in range(1, columns + 1):
            same = expected[i - 1] == "*" or expected[i - 1] == found[j - 1]
            table[i][j] = max(
                table[i - 1][j], table[i][j - 1], table[i - 1][j - 1] + (1 if same else 0)
            )
    return table[rows][columns] / rows


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


def account_for(claim: ScenarioClaim, tree: Tree, registry: DebtRegistry) -> tuple[str, str]:
    """How a scenario is accounted for, as `(kind, evidence)`; `("", "")` when nothing does.

    Four answers, in this order: a `@test(<path>::<name>)` that resolves; a test whose name is the
    title, normalised on both sides and compared whole; a `@deferred(<ticket>)` whose ticket the
    registry knows; nothing. `broken` is a tag that claims to account for the scenario and does
    not — a pointer into thin air, a ticket nobody issued — and it fails as such, because a broken
    link and a missing one are different defects and only one of them is a lie.
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
            return "", "@deferred names no ticket: a deferral without a ticket is nobody's"
        if not TICKET_RE.match(ticket):
            return "broken", f"{deferral.group(0)} names no ticket id"
        if ticket not in registry.tickets:
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
        if reading.kind in {"test", "deferred"}:
            continue
        key = claim_key(reading.claim)
        if reading.kind == "broken":
            violations.append(Violation("scenario", reading.claim.location, reading.evidence))
            continue
        if key in accepted:
            continue
        detail = reading.evidence or (
            "no test pins it and no ticket defers it: add @test(<path>::<name>), name a test "
            f"after the title, or accept the key {key!r} and run --accept"
        )
        violations.append(Violation("scenario", reading.claim.location, detail))
    return violations


def scenario_counts(
    readings: Sequence[Accounting], registry: DebtRegistry | None = None
) -> dict[str, int]:
    """The four numbers the summary publishes: pinned, deferred, accepted, and new."""
    accepted = (registry or DebtRegistry()).keys
    counts = {"scenarios": len(readings), "accounted": 0, "deferred": 0, "accepted": 0, "new": 0}
    for reading in readings:
        if reading.kind == "test":
            counts["accounted"] += 1
        elif reading.kind == "deferred":
            counts["deferred"] += 1
        elif claim_key(reading.claim) in accepted:
            counts["accepted"] += 1
        else:
            counts["new"] += 1
    return counts


def debt_report(readings: Sequence[Accounting], registry: DebtRegistry | None = None) -> list[str]:
    """One line per cluster and one per accepted key: the debt, named rather than counted."""
    live = {claim_key(reading.claim) for reading in readings if reading.kind in {"", "broken"}}
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
    """A document's prose as `(unit, sentence)` pairs.

    A unit is a paragraph or a bullet: wrapped lines are one unit, and the sentences of a unit are
    what the rule reads, so a number and its provenance have to travel together.
    """
    units: list[tuple[str, str]] = []
    buffer: list[str] = []
    in_fence = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            if buffer:
                units.append(_unit(buffer))
                buffer = []
            continue
        if in_fence:
            continue
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


def check_provenance(
    tree: Tree, exemptions: Sequence[Exemption], needed: set[str] | None = None
) -> list[Violation]:
    """A count published as a fact about a database names its file, its command or its day."""
    accepted = [e for e in exemptions if e.check == "provenance"]
    violations: list[Violation] = []
    for name, units in tree.prose.items():
        for where, sentence in units:
            if not _publishes_a_count(sentence):
                continue
            if PROVENANCE.search(sentence):
                continue
            if needed is not None:
                needed.add(sentence)
            if any(e.subject in sentence for e in accepted if e.subject):
                continue
            violations.append(
                Violation(
                    "provenance",
                    f"{name}:{where[:60]}",
                    f"a measured count names no file, command or date: {sentence[:120]!r}",
                    subject=sentence,
                )
            )
    return violations


# --- the accepted gaps --------------------------------------------------------------------

#: Names a closed vocabulary declares that no code writes yet, one row per name.
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
        subject="sentence:added to cycle Z",
        ticket="HIFA-90",
        reason="the cycle sentence waits for a name map no read carries",
    ),
    Exemption(
        check="producer",
        subject="sentence:removed it from cycle Z",
        ticket="HIFA-90",
        reason="the cycle sentence waits for a name map no read carries",
    ),
    Exemption(
        check="producer",
        subject="sentence:moved to project X",
        ticket="HIFA-90",
        reason="the project sentence waits for a name map no read carries",
    ),
    Exemption(
        check="producer",
        subject="sentence:removed it from project X",
        ticket="HIFA-90",
        reason="the project sentence waits for a name map no read carries",
    ),
    Exemption(
        check="producer",
        subject="sentence:set milestone Y",
        ticket="HIFA-90",
        reason="the milestone sentence waits for a name map no read carries",
    ),
    Exemption(
        check="producer",
        subject="sentence:removed it from milestone Y",
        ticket="HIFA-90",
        reason="the milestone sentence waits for a name map no read carries",
    ),
    Exemption(
        check="producer",
        subject="table:080-transfer-csv-json.md:708",
        ticket="HIFA-9",
        reason="the row-error catalogue of the transfer component, which the tree does not carry",
    ),
)

#: Published counts that name no file, command or date.
PROVENANCE_EXEMPTIONS: tuple[Exemption, ...] = (
    Exemption(
        check="provenance",
        subject="the measured database has 34 such rows",
        ticket="HIFA-104",
        reason="the count names no file or date; the ticket puts the provenance in the sentence",
    ),
    Exemption(
        check="provenance",
        subject="the measured database has zero",
        ticket="HIFA-104",
        reason="the count names no file or date; the ticket puts the provenance in the sentence",
    ),
    Exemption(
        check="provenance",
        subject="33 of the 34 rows of the measured database",
        ticket="HIFA-104",
        reason="the count names no file or date; the ticket puts the provenance in the sentence",
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
        carried_keys = {
            claim_key(reading.claim) for reading in readings if reading.kind in {"", "broken"}
        }
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
        f"{counts.get('accepted', 0)} in the accepted set, {counts.get('new', 0)} new "
        f"of {counts.get('scenarios', 0)} in {counts.get('documents', 0)} document(s)"
    )


def accept(tree: Tree, registry: DebtRegistry) -> tuple[DebtRegistry, int, int]:
    """Rewrite the accepted set from the tree: every unaccounted key kept, stale ones dropped.

    Returns the registry, how many keys it now holds and how many it dropped. The default cluster
    is the one a new key joins; a key an existing cluster owns is left where a human put it.
    """
    readings = account(tree, registry)
    live = [claim_key(reading.claim) for reading in readings if reading.kind == ""]
    dropped = len(registry.keys - set(live))
    reason = next((cluster.reason for cluster in registry.clusters), "")
    return registry.accepted(live, reason), len(live), dropped


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
        written, kept, dropped = accept(tree, registry)
        written.write(args.debt)
        print(
            f"accepted {kept} key(s) in {args.debt}: "
            f"{dropped} stale key(s) dropped, ticket(s) {', '.join(sorted(written.tickets))}"
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
