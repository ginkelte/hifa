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
    feature files, must be accounted for: a test that pins it, or an explicit deferral. A
    scenario is pinned when a test names it — the normalised title appears in a test name, a
    docstring or a comment, or a requirement id it carries is cited there, or a literal of its
    steps appears there, or most of its title's words appear in one test's name and docstring —
    or when it carries `@test(<path>::<name>)` and that test exists. A scenario nothing pins must
    carry `@deferred(KEY-NN)`; an unticketed deferral in a spec is a violation, because a
    deferral without a ticket is a claim nobody owns.

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

The scenario rule carries its legacy tail differently, because the tail is not a list of defects
but one fact about the corpus: the specs name behaviours in prose and the tests name them after
the behaviour, and nothing joins the two. A row there is a document and the number of its
scenarios that no test names, with the ticket that adds the link; the count may not grow, so a
scenario added without a test fails even where the document already carries debt, and a count the
document no longer needs fails until it is lowered. `--list-debt` prints every accepted scenario
by name and every document's count, so the debt is visible without being a list the check trusts.

Usage
-----
    python scripts/spec_claims_check.py                  # every check, human-readable
    python scripts/spec_claims_check.py --only producer  # one check
    python scripts/spec_claims_check.py --list-debt      # the accepted scenarios, by name
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
DEFERRED_RE = re.compile(r"^@deferred(?:\((?P<ticket>[^)]+)\))?$")
TEST_POINTER_RE = re.compile(r"^@test\((?P<path>[^:)]+?)::(?P<name>[^)]+)\)$")
REQUIREMENT_RE = re.compile(r"^@(REQ-[A-Z]?[0-9]{3}-[0-9]+)$")
QUOTED_RE = re.compile(r"`([^`\n]{5,80})`|\"([^\"\n]{5,80})\"")
WORDS = re.compile(r"[A-Za-z0-9]+")

#: A word this common names a hundred tests, so it cannot be what identifies one of them.
COMMON_WORD_LIMIT = 300

#: The line a test is defined on: module-level and class-level tests read the same.
TEST_DEF_RE = re.compile(r"^\s*(?:async[ \t]+)?def[ \t]+(?P<name>test\w*)[ \t]*\(")

#: Words that carry no subject of their own; they are what makes a title read like a sentence.
GLUE = frozenset(
    (
        "a",
        "an",
        "the",
        "of",
        "to",
        "in",
        "on",
        "for",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "does",
        "do",
        "did",
        "its",
        "it",
        "by",
        "with",
        "and",
        "or",
        "not",
        "no",
        "as",
        "that",
        "this",
        "these",
        "those",
        "from",
        "at",
        "can",
        "has",
        "have",
        "had",
        "when",
        "then",
        "given",
        "each",
        "every",
        "one",
        "two",
        "also",
        "into",
        "than",
        "there",
        "which",
        "whose",
        "will",
        "still",
        "only",
        "both",
        "same",
        "other",
        "such",
        "what",
        "must",
        "may",
        "now",
        "all",
        "any",
        "but",
        "so",
        "if",
        "while",
        "during",
        "before",
        "after",
        "once",
        "per",
    )
)

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
    """The scenarios of one document that no test names, and the ticket that closes them."""

    document: str
    ticket: str
    accepted: int
    reason: str


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
    debts: list[ScenarioDebt] = field(default_factory=list)
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


def words(text: str) -> set[str]:
    """The subject words of a phrase: lowercased, stemmed, glue removed."""
    out: set[str] = set()
    for raw in WORDS.findall(text.lower()):
        if len(raw) <= 3 or raw in GLUE:
            continue
        out.add(_stem(raw))
    return out


def _stem(word: str) -> str:
    """A crude suffix strip, enough to match `reordered` with `reorder` and `rows` with `row`."""
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def slug(title: str) -> str:
    """The hyphenated form of a title, which is how a test name can carry it."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


# --- the tree under test ------------------------------------------------------------------


@dataclass
class Tree:
    """What the checks read: the specs' text, the producers and the tests."""

    specs: list[Path]
    features: list[Path]
    producers: Producers
    test_text: str
    test_text_slug: str
    test_units: list[tuple[str, set[str]]]
    prose: dict[str, list[tuple[str, str]]]
    units_by_word: dict[str, list[int]]
    root: Path

    @classmethod
    def read(cls, specs_dir: Path, src_dir: Path, tests_dir: Path, acceptance_dir: Path) -> Tree:
        """Read the three trees once, so a check is a walk over memory."""
        sources = {p: p.read_text(encoding="utf-8") for p in src_dir.rglob("*.py")}
        written = producers(sources)
        for path in src_dir.rglob("*.html"):
            written.literals.append(path.read_text(encoding="utf-8"))
        test_files = sorted(tests_dir.rglob("*.py")) if tests_dir.is_dir() else []
        texts = {p: p.read_text(encoding="utf-8") for p in test_files}
        test_text = "\n".join(texts.values())
        units: list[tuple[str, set[str]]] = []
        for path, text in texts.items():
            units.extend(_test_units(path, text))
        index: dict[str, list[int]] = {}
        for position, (_, unit_words) in enumerate(units):
            for word in unit_words:
                index.setdefault(word, []).append(position)
        spec_texts = {p.name: p.read_text(encoding="utf-8") for p in spec_files(specs_dir)}
        return cls(
            prose={name: prose_units(text) for name, text in spec_texts.items()},
            units_by_word=index,
            root=tests_dir.parent,
            specs=spec_files(specs_dir),
            features=feature_files(acceptance_dir),
            producers=written,
            test_text=test_text,
            test_text_slug=re.sub(r"[^a-z0-9]+", "-", test_text.lower()),
            test_units=units,
        )


def _test_units(path: Path, text: str) -> Iterator[tuple[str, set[str]]]:
    """Every test of a module as `(where, its words)` — the name, the docstring and the comment.

    The lines are read rather than parsed: the check needs the name and the prose around it, and a
    test that the reader cannot see from the source text is not one a scenario can be named by.
    """
    label = str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if TEST_DEF_RE.match(line)]
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        match = TEST_DEF_RE.match(lines[start])
        if match is None:  # pragma: no cover - the line matched to get here
            continue
        name = match.group("name")
        following = "\n".join(lines[start + 1 : min(start + 14, end)])
        quoted = re.search(r'"""(?P<doc>.*?)"""', following, re.DOTALL) or re.search(
            r"'''(?P<doc>.*?)'''", following, re.DOTALL
        )
        docstring = quoted.group("doc") if quoted else ""
        comments: list[str] = []
        index = start - 1
        while index >= 0:
            stripped = lines[index].strip()
            if stripped.startswith("#") or stripped.startswith("@"):
                comments.append(stripped)
            elif stripped:
                break
            index -= 1
        yield f"{label}::{name}", words(f"{name} {docstring} {' '.join(comments)}")


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


def account_for(
    claim: ScenarioClaim, tree: Tree, cache: dict[str, bool] | None = None
) -> tuple[str, str]:
    """How a scenario is accounted for, as `(kind, evidence)`; `("", "")` when nothing pins it."""
    for tag in claim.tags:
        deferred = DEFERRED_RE.match(tag)
        if deferred:
            ticket = deferred.group("ticket") or ""
            if ticket and not TICKET_RE.match(ticket):
                return "broken", f"{tag} names no ticket id"
            if not ticket and claim.origin == "spec":
                return "broken", "a deferral in a spec must carry the ticket that closes it"
            return "deferred", ticket
        pointer = TEST_POINTER_RE.match(tag)
        if pointer:
            target = tree.root / pointer.group("path")
            name = pointer.group("name")
            if not target.is_file():
                return "broken", f"{tag} names a file that does not exist"
            if f"def {name}(" not in target.read_text(encoding="utf-8"):
                return "broken", f"{tag} names a test that does not exist"
            return "test", f"{pointer.group('path')}::{name}"
    evidence = named_by_test(claim, tree, {} if cache is None else cache)
    if evidence:
        return "test", evidence
    return "", ""


def named_by_test(claim: ScenarioClaim, tree: Tree, cache: dict[str, bool]) -> str:
    """The name of the test that pins a scenario, or an empty string when none does."""
    title_slug = slug(claim.title)
    if title_slug and _in_test_text(title_slug, tree.test_text_slug, cache):
        return "the title appears in the tests"
    for tag in claim.tags:
        requirement = REQUIREMENT_RE.match(tag)
        if not requirement:
            continue
        identifier = requirement.group(1)
        if identifier in tree.test_text or identifier.split("-", 1)[1] in tree.test_text:
            return f"a test cites {identifier}"
    literals = sorted(
        {(backticked or quoted).strip() for backticked, quoted in QUOTED_RE.findall(claim.body)},
        key=len,
        reverse=True,
    )
    for literal in [value for value in literals if " " in value]:
        if _in_test_text(literal, tree.test_text, cache):
            return f"a test carries {literal!r}"
    title_words = words(claim.title)
    if title_words:
        threshold = 0.6 * len(title_words)
        seen: dict[int, int] = {}
        for word in title_words:
            postings = tree.units_by_word.get(word, ())
            if len(postings) > COMMON_WORD_LIMIT:
                continue
            for position in postings:
                seen[position] = seen.get(position, 0) + 1
                if seen[position] >= threshold:
                    return f"{tree.test_units[position][0]} reads as this scenario"
    return ""


@dataclass(frozen=True)
class Accounting:
    """One scenario and how the tree accounts for it."""

    claim: ScenarioClaim
    kind: str
    evidence: str


def account(tree: Tree) -> list[Accounting]:
    """Every scenario of the corpus with what accounts for it, read once and shared."""
    cache: dict[str, bool] = {}
    return [Accounting(claim, *account_for(claim, tree, cache)) for claim in scenarios(tree)]


def _in_test_text(needle: str, haystack: str, cache: dict[str, bool]) -> bool:
    """Whether a phrase occurs in the tests, remembered across the scenarios that quote it."""
    found = cache.get(needle)
    if found is not None:
        return found
    found = needle in haystack
    cache[needle] = found
    return found


def check_scenario(
    readings: Sequence[Accounting], debts: Sequence[ScenarioDebt]
) -> list[Violation]:
    """Every scenario is pinned by a test or deferred with a ticket; the tail is a bounded debt.

    A scenario no test names and no ticket defers is a violation unless its document's debt row
    accepts it. The row is a count, so a revision that adds a scenario without a test fails here
    even though the document already carries debt: the count cannot grow. `--list-debt` prints
    every accepted scenario by name, so the debt is visible without being a list the check trusts.
    """
    accepted = {debt.document: debt for debt in debts}
    unaccounted: dict[str, list[ScenarioClaim]] = {}
    violations: list[Violation] = []
    for reading in readings:
        if reading.kind in {"test", "deferred"}:
            continue
        if reading.kind == "broken":
            violations.append(Violation("scenario", reading.claim.location, reading.evidence))
            continue
        unaccounted.setdefault(reading.claim.document, []).append(reading.claim)
    for document, claims in unaccounted.items():
        debt = accepted.get(document)
        allowed = debt.accepted if debt else 0
        for claim in claims[allowed:]:
            violations.append(
                Violation(
                    "scenario",
                    claim.location,
                    "no test pins it and it carries no deferral with a ticket",
                )
            )
    return violations


def debt_report(readings: Sequence[Accounting], debts: Sequence[ScenarioDebt]) -> list[str]:
    """One line per document: the debt it accepts and the debt it carries today."""
    carried: dict[str, int] = {}
    for reading in readings:
        if reading.kind in {"", "broken"}:
            carried[reading.claim.document] = carried.get(reading.claim.document, 0) + 1
    lines: list[str] = []
    for document in sorted(set(carried) | {debt.document for debt in debts}):
        debt = next((entry for entry in debts if entry.document == document), None)
        allowed = debt.accepted if debt else 0
        ticket = debt.ticket if debt else "-"
        lines.append(
            f"  {document}: {carried.get(document, 0)} unaccounted, {allowed} accepted ({ticket})"
        )
    return lines


def unaccounted_names(readings: Sequence[Accounting]) -> list[str]:
    """Every scenario no test names and no deferral covers, in reading order."""
    return [reading.claim.location for reading in readings if reading.kind in {"", "broken"}]


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

#: Scenarios each document states that no test in the tree names, with the ticket that adds the
#: linkage. One row per document: the counts are what the corpus carries today, so a revision that
#: adds a scenario without a test fails even where the document already carries debt.
SCENARIO_DEBT: tuple[ScenarioDebt, ...] = (
    ScenarioDebt(
        "000-system-architecture.md",
        "HIFA-105",
        2,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "01-init.feature",
        "HIFA-105",
        3,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "010-data-model.md",
        "HIFA-105",
        1,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "02-teams-workflow-states.feature",
        "HIFA-105",
        7,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "020-domain-core.md",
        "HIFA-105",
        12,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "03-issues-crud.feature",
        "HIFA-105",
        4,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "030-sqlite-adapter.md",
        "HIFA-105",
        15,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "04-soft-delete.feature",
        "HIFA-105",
        3,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "040-application-services.md",
        "HIFA-105",
        18,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "05-list-filters-order.feature",
        "HIFA-105",
        17,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "050-cli.md",
        "HIFA-105",
        16,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "06-board-ranking.feature",
        "HIFA-105",
        2,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "060-api-rest.md",
        "HIFA-105",
        16,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "07-hierarchy-relations.feature",
        "HIFA-105",
        3,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "070-web-dashboard.md",
        "HIFA-105",
        29,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "08-comments-attachments.feature",
        "HIFA-105",
        6,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "080-transfer-csv-json.md",
        "HIFA-105",
        11,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "090-search-fts.md",
        "HIFA-105",
        5,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "10-search.feature",
        "HIFA-105",
        1,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "100-config-bootstrap.md",
        "HIFA-105",
        13,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "11-cycles-projects.feature",
        "HIFA-105",
        6,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "110-release.md",
        "HIFA-105",
        22,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "12-history.feature",
        "HIFA-105",
        3,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "13-transfer-csv.feature",
        "HIFA-105",
        6,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "14-transfer-json.feature",
        "HIFA-105",
        1,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "15-cli-contract.feature",
        "HIFA-105",
        6,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "16-dashboard.feature",
        "HIFA-105",
        14,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
    ),
    ScenarioDebt(
        "17-dashboard-hardening.feature",
        "HIFA-105",
        3,
        "the corpus names behaviour in tests and titles in specs, and nothing joins the two",
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
    is what the checks collected; a caller that ran none of them gets the claim set here.
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
    documents = {path.name for path in [*tree.specs, *tree.features]}
    carried: dict[str, int] = {}
    for reading in readings:
        if reading.kind in {"", "broken"}:
            carried[reading.claim.document] = carried.get(reading.claim.document, 0) + 1
    for debt in report.debts:
        if debt.document not in documents:
            violations.append(
                Violation("scenario", debt.ticket, f"stale debt row for {debt.document}")
            )
        elif carried.get(debt.document, 0) < debt.accepted:
            violations.append(
                Violation(
                    "scenario",
                    debt.ticket,
                    f"{debt.document} carries {carried.get(debt.document, 0)} unaccounted, "
                    f"the row accepts {debt.accepted}: lower it",
                )
            )
    return violations


# --- the report ---------------------------------------------------------------------------


def build_report(tree: Tree, only: Sequence[str] = CHECKS) -> Report:
    """Run the checks and collect their verdicts, with the exemptions in force."""
    accepted = exemptions()
    report = Report(exemptions=list(accepted), debts=list(SCENARIO_DEBT))
    readings = account(tree)
    needed: set[str] = set()
    if "producer" in only:
        report.violations.extend(check_producer(tree, accepted, needed))
    if "scenario" in only:
        report.violations.extend(check_scenario(readings, report.debts))
    if "provenance" in only:
        report.violations.extend(check_provenance(tree, accepted, needed))
    report.violations.extend(stale(report, tree, readings, needed, only))
    report.readings = readings
    report.counts = {
        "scenarios": len(readings),
        "unaccounted": sum(1 for reading in readings if reading.kind in {"", "broken"}),
        "documents": len(tree.specs) + len(tree.features),
    }
    return report


def summary(report: Report) -> str:
    """The one line every run prints: the verdict and the exemptions in force."""
    accepted = len(report.exemptions)
    debt = sum(debt.accepted for debt in report.debts)
    verdict = "ok" if report.ok else f"{len(report.violations)} violation(s)"
    return (
        f"spec claims: {verdict}; {accepted} exemption(s) in force "
        f"(producer {len(report.of('producer'))}, provenance {len(report.of('provenance'))}), "
        f"scenario debt {debt} in {len(report.debts)} document(s), "
        f"{report.counts.get('unaccounted', 0)} of {report.counts.get('scenarios', 0)}"
        " scenarios unaccounted"
    )


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
        "--list-debt", action="store_true", help="print every accepted scenario by name"
    )
    parser.add_argument("--quiet", action="store_true", help="only the summary line")
    parser.add_argument("--json", action="store_true", help="the verdict as JSON")
    args = parser.parse_args(argv)

    acceptance = args.acceptance if args.acceptance is not None else args.specs / "acceptance"
    tree = Tree.read(args.specs, args.src, args.tests, acceptance)
    report = build_report(tree, args.only or CHECKS)

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
                    "debt": [
                        {"document": d.document, "accepted": d.accepted, "ticket": d.ticket}
                        for d in report.debts
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
        for name in unaccounted_names(report.readings):
            print(f"  accepted: {name}")
    print(summary(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
