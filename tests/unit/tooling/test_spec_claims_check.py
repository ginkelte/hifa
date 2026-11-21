"""Tests of `scripts/spec_claims_check.py`.

The script is not a package module: it is loaded from its path, the way it runs in CI. The
synthetic trees below keep the rules independent of the corpus, and each one reproduces a claim
that used to escape review by hand: a field nothing writes, a scenario nothing pins, a count with
no source. The last block runs the script against the real tree, which is what guards the corpus
from drift.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "spec_claims_check.py"

SPEC_NAME = "030-adapter.md"


def _load() -> ModuleType:
    """Load the script by path, as the CI run does."""
    spec = importlib.util.spec_from_file_location("spec_claims_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


claims = _load()

#: A dataclass a fenced block declares, with one field written and one nothing writes.
SPEC_DATACLASS = """
## 2. The row

```python
@dataclass(frozen=True)
class HistoryRow:
    id: int
    sentence: str
    dot_color: str | None
```
"""

SOURCE_DATACLASS = """from dataclasses import dataclass


@dataclass(frozen=True)
class HistoryRow:
    id: int
    sentence: str
    dot_color: str | None


def build() -> HistoryRow:
    return HistoryRow(id=1, sentence="created the issue")
"""

#: A section 7 scenario about a batch no test names.
SPEC_SCENARIO = """
## 7. Acceptance

```gherkin
  Scenario: history is written in a single batch
    Given an issue with 3 fields changed
    When the history is recorded
    Then one statement writes 3 rows with the same instant
```
"""

TEST_ELSEWHERE = '''def test_something_else() -> None:
    """A test about another behaviour entirely."""
    assert True
'''

#: The count that rotted: a number about a database, and nothing saying which file or which day.
SPEC_COUNT_NAKED = """
## 2. The vocabulary

The measured database has 34 such rows, and the rule below rests on it.
"""

SPEC_COUNT_SOURCED = """
## 2. The vocabulary

The measured database (`~/.local/share/hifa/hifa.db`, 2026-11-20) has 34 such rows.
"""


def build_tree(
    tmp_path: Path,
    *,
    spec: str,
    source: str = "",
    tests: str = "",
    spec_name: str = SPEC_NAME,
) -> ModuleType:
    """Write a spec, a source tree and a test tree, and read them as the script does."""
    specs = tmp_path / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / spec_name).write_text(spec, encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir(exist_ok=True)
    if source:
        (src / "rows.py").write_text(source, encoding="utf-8")
    suite = tmp_path / "tests"
    suite.mkdir(exist_ok=True)
    if tests:
        (suite / "test_rows.py").write_text(tests, encoding="utf-8")
    return claims.Tree.read(specs, src, suite, specs / "acceptance")


def producer_violations(tree: ModuleType, exemptions: tuple[Any, ...] = ()) -> list[Any]:
    """The producer violations of one tree."""
    return claims.check_producer(tree, list(exemptions))


def scenario_violations(tree: ModuleType, debts: tuple[Any, ...] = ()) -> list[Any]:
    """The scenario violations of one tree."""
    return claims.check_scenario(claims.account(tree), list(debts))


def provenance_violations(tree: ModuleType, exemptions: tuple[Any, ...] = ()) -> list[Any]:
    """The provenance violations of one tree."""
    return claims.check_provenance(tree, list(exemptions))


# --- rule 1: a declared name needs a producer ---------------------------------------------


def test_a_declared_field_no_code_assigns_is_a_violation(tmp_path: Path) -> None:
    """The row shape declared a colour and the read model could not fill it."""
    tree = build_tree(tmp_path, spec=SPEC_DATACLASS, source=SOURCE_DATACLASS)

    violations = producer_violations(tree)

    assert [v.check for v in violations] == ["producer"]
    assert "HistoryRow.dot_color" in violations[0].detail
    assert "nothing assigns it" in violations[0].detail


def test_a_field_the_code_assigns_is_not_a_violation(tmp_path: Path) -> None:
    """The same row with the colour written is clean: the rule reads the writer, not the name."""
    source = SOURCE_DATACLASS.replace(
        'sentence="created the issue")', 'sentence="created the issue", dot_color=None)'
    )
    tree = build_tree(tmp_path, spec=SPEC_DATACLASS, source=source)

    assert producer_violations(tree) == []


def test_a_field_only_a_docstring_mentions_has_no_producer(tmp_path: Path) -> None:
    """Quoting a field's name is not writing it: prose about the code cannot satisfy the rule."""
    source = SOURCE_DATACLASS.replace(
        "def build() -> HistoryRow:",
        'def build() -> HistoryRow:\n    """Sets `dot_color` from the state."""',
    )
    tree = build_tree(tmp_path, spec=SPEC_DATACLASS, source=source)

    violations = producer_violations(tree)

    assert [v.detail for v in violations if "dot_color" in v.detail]


def test_a_dataclass_the_tree_does_not_carry_is_not_checked(tmp_path: Path) -> None:
    """A contract for a component nobody has built declares nothing the tree can contradict."""
    tree = build_tree(tmp_path, spec=SPEC_DATACLASS, source="VALUE = 1\n")

    assert producer_violations(tree) == []


def test_a_vocabulary_table_names_every_value_a_writer_must_store(tmp_path: Path) -> None:
    """A closed vocabulary enumerates the values the writers produce, one row per value."""
    spec = """
## 2. The vocabulary

| `field` | writer |
|---|---|
| `title` | `update_issue` |
| `truncated_rows` | `update_issue` |
"""
    source = """
def update_issue(field: str) -> str:
    return field


def write() -> str:
    return update_issue(field="title")
"""
    tree = build_tree(tmp_path, spec=spec, source=source)

    violations = producer_violations(tree)

    assert [v.detail for v in violations] == ["the name 'truncated_rows' has no producer"]


def test_a_sentence_table_names_every_wording_a_renderer_builds(tmp_path: Path) -> None:
    """A grammar table enumerates renderings; one no literal renders has no producer."""
    spec = """
## 2. The sentences

| `field` | old | new | sentence |
|---|---|---|---|
| `title` | old | new | `changed the title to “new”` |
| `project_id` | – | name | `moved to project X` |
"""
    source = """
def sentence(field: str, new: str) -> str | None:
    if field == "title":
        return f"changed the title to \\u201c{new}\\u201d"
    return None
"""
    tree = build_tree(tmp_path, spec=spec, source=source)

    violations = producer_violations(tree)

    assert [v.detail for v in violations] == ["the sentence 'moved to project X' has no producer"]


def test_an_accepted_gap_is_accepted_and_an_unneeded_one_fails(tmp_path: Path) -> None:
    """An exemption carries a ticket; once the writer lands, the row fails until it is deleted."""
    accepted = claims.Exemption(
        check="producer",
        subject="dataclass:HistoryRow.dot_color",
        ticket="HIFA-1",
        reason="the colour lands with the rail",
    )
    failing = build_tree(tmp_path / "open", spec=SPEC_DATACLASS, source=SOURCE_DATACLASS)

    assert producer_violations(failing, (accepted,)) == []

    fixed = build_tree(
        tmp_path / "fixed",
        spec=SPEC_DATACLASS,
        source=SOURCE_DATACLASS.replace(
            'sentence="created the issue")', 'sentence="created the issue", dot_color=None)'
        ),
    )
    report = claims.Report(exemptions=[accepted])
    stale = claims.stale(report, fixed, claims.account(fixed))
    assert [v.detail for v in stale] == ["stale exemption for dataclass:HistoryRow.dot_color"]


# --- rule 2: a scenario needs a test or a ticketed deferral -------------------------------


def test_a_scenario_no_test_names_is_a_violation(tmp_path: Path) -> None:
    """The batch scenario had no test for a whole revision, and nothing said so."""
    tree = build_tree(tmp_path, spec=SPEC_SCENARIO, tests=TEST_ELSEWHERE)

    violations = scenario_violations(tree)

    assert [v.check for v in violations] == ["scenario"]
    assert "history is written in a single batch" in violations[0].location
    assert "no test pins it" in violations[0].detail


def test_a_scenario_a_test_names_is_not_a_violation(tmp_path: Path) -> None:
    """A test whose name reads as the scenario pins it."""
    tests = '''def test_history_is_written_in_a_single_batch() -> None:
    """One statement, three rows, one instant."""
    assert True
'''
    tree = build_tree(tmp_path, spec=SPEC_SCENARIO, tests=tests)

    assert scenario_violations(tree) == []


def test_a_scenario_deferring_to_a_ticket_is_accounted_for(tmp_path: Path) -> None:
    """An explicit deferral is a claim with an owner, and the owner is a ticket."""
    spec = SPEC_SCENARIO.replace(
        "  Scenario: history", "  @deferred(HIFA-112)\n  Scenario: history"
    )
    tree = build_tree(tmp_path, spec=spec, tests=TEST_ELSEWHERE)

    assert scenario_violations(tree) == []


def test_an_unticketed_deferral_in_a_spec_is_a_violation(tmp_path: Path) -> None:
    """A deferral without a ticket is a claim nobody owns."""
    spec = SPEC_SCENARIO.replace("  Scenario: history", "  @deferred\n  Scenario: history")
    tree = build_tree(tmp_path, spec=spec, tests=TEST_ELSEWHERE)

    violations = scenario_violations(tree)

    assert [v.check for v in violations] == ["scenario"]
    assert "must carry the ticket" in violations[0].detail


def test_a_test_pointer_that_resolves_pins_the_scenario(tmp_path: Path) -> None:
    """A pointer names the test; the gate resolves it instead of trusting the words."""
    spec = SPEC_SCENARIO.replace(
        "  Scenario: history",
        "  @test(tests/test_rows.py::test_something_else)\n  Scenario: history",
    )
    tree = build_tree(tmp_path, spec=spec, tests=TEST_ELSEWHERE)

    assert scenario_violations(tree) == []


def test_a_test_pointer_that_resolves_to_nothing_is_a_violation(tmp_path: Path) -> None:
    """A pointer to a test nobody wrote is the defect the pointer was meant to prevent."""
    spec = SPEC_SCENARIO.replace(
        "  Scenario: history", "  @test(tests/test_rows.py::test_absent)\n  Scenario: history"
    )
    tree = build_tree(tmp_path, spec=spec, tests=TEST_ELSEWHERE)

    violations = scenario_violations(tree)

    assert [v.detail for v in violations] == [
        "@test(tests/test_rows.py::test_absent) names a test that does not exist"
    ]


def test_the_debt_of_a_document_cannot_grow(tmp_path: Path) -> None:
    """A document already in debt still fails when a revision adds a scenario with no test."""
    tree = build_tree(tmp_path, spec=SPEC_SCENARIO, tests=TEST_ELSEWHERE)
    debt = claims.ScenarioDebt(
        SPEC_NAME, "HIFA-105", 1, "the corpus names behaviour, not scenarios"
    )

    assert scenario_violations(tree, (debt,)) == []

    two_scenarios = SPEC_SCENARIO.replace(
        "```\n",
        """
  Scenario: the rank a state change carried is not a row of its own
    Given a move whose rank moved with it in one call
    Then the move is one row
```
""",
    )
    grown = build_tree(tmp_path / "grown", spec=two_scenarios, tests=TEST_ELSEWHERE)

    violations = scenario_violations(grown, (debt,))
    assert len(violations) == 1
    assert "the rank a state change carried" in violations[0].location


def test_a_debt_row_the_document_no_longer_needs_fails_until_it_is_lowered(
    tmp_path: Path,
) -> None:
    """Paying debt is not enough: the row has to be lowered, or it keeps accepting the next gap."""
    tests = """def test_history_is_written_in_a_single_batch() -> None:
    assert True
"""
    tree = build_tree(tmp_path, spec=SPEC_SCENARIO, tests=tests)
    debt = claims.ScenarioDebt(
        SPEC_NAME, "HIFA-105", 1, "the corpus names behaviour, not scenarios"
    )

    violations = scenario_violations(tree, (debt,))
    assert violations == []

    report = claims.Report(debts=[debt])
    stale = claims.stale(report, tree, claims.account(tree))
    assert [v.check for v in stale] == ["scenario"]
    assert "lower it" in stale[0].detail


# --- rule 3: a published count names where it came from -----------------------------------


def test_a_measured_count_with_no_source_is_a_violation(tmp_path: Path) -> None:
    """The row count that was published as fact and was wrong within days."""
    tree = build_tree(tmp_path, spec=SPEC_COUNT_NAKED)

    violations = provenance_violations(tree)

    assert [v.check for v in violations] == ["provenance"]
    assert "names no file, command or date" in violations[0].detail


def test_a_measured_count_that_names_its_file_is_not_a_violation(tmp_path: Path) -> None:
    """A count beside the file and the day it was read is a measurement, not an assertion."""
    tree = build_tree(tmp_path, spec=SPEC_COUNT_SOURCED)

    assert provenance_violations(tree) == []


def test_a_budget_is_not_a_database_count(tmp_path: Path) -> None:
    """A latency budget or a fixture size is not a fact about a file, so it is not this rule."""
    spec = """
## 4. Performance

The budget is measured at **< 20 ms p95** over the 10,000-issue fixture.
"""
    tree = build_tree(tmp_path, spec=spec)

    assert provenance_violations(tree) == []


# --- the four proofs, one tree each, against the real corpus ------------------------------


def test_a_clean_spec_has_no_violation(tmp_path: Path) -> None:
    """A spec whose claims all reach the tree passes every rule: no false positive."""
    spec = (
        SPEC_DATACLASS.replace("    sentence: str\n", "    sentence: str\n")
        + SPEC_SCENARIO
        + SPEC_COUNT_SOURCED
    )
    source = SOURCE_DATACLASS.replace(
        'sentence="created the issue")', 'sentence="created the issue", dot_color="#F2C94C")'
    )
    tests = '''def test_history_is_written_in_a_single_batch() -> None:
    """One statement, three rows, one instant."""
    assert True
'''
    tree = build_tree(tmp_path, spec=spec, source=source, tests=tests)

    assert producer_violations(tree) == []
    assert scenario_violations(tree) == []
    assert provenance_violations(tree) == []


def test_the_real_tree_passes_every_rule() -> None:
    """The corpus as it stands: no violation, and the accepted gaps are printed in the summary."""
    tree = claims.Tree.read(
        claims.SPECS_DIR, claims.SRC_DIR, claims.TESTS_DIR, claims.ACCEPTANCE_DIR
    )

    report = claims.build_report(tree)

    assert report.violations == []
    text = claims.summary(report)
    assert "exemption(s) in force" in text
    assert "scenario debt" in text


def test_the_json_envelope_names_every_accepted_gap(capsys: object) -> None:
    """`--json` publishes the same verdict the human line does, for a CI step to read."""
    tree = claims.Tree.read(
        claims.SPECS_DIR, claims.SRC_DIR, claims.TESTS_DIR, claims.ACCEPTANCE_DIR
    )
    report = claims.build_report(tree, ["producer"])

    assert report.ok
    assert report.exemptions
    assert all(claims.TICKET_RE.match(e.ticket) for e in report.exemptions)
    assert all(e.reason for e in report.exemptions)
