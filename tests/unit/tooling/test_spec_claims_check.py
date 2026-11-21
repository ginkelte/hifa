"""Tests of `scripts/spec_claims_check.py`.

The script is not a package module: it is loaded from its path, the way it runs in CI. The
synthetic trees below keep the rules independent of the corpus, and each one reproduces a claim
that used to escape review by hand: a field nothing writes, a scenario nothing pins, a count with
no source. The last block runs the script against the real tree, which is what guards the corpus
from drift.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from textwrap import dedent
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


def scenario_violations(tree: ModuleType, registry: Any = None) -> list[Any]:
    """The scenario violations of one tree, judged against an accepted set or an empty one."""
    accepted = registry if registry is not None else claims.DebtRegistry.read()
    return claims.check_scenario(claims.account(tree, accepted), accepted)


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


# --- rule 2: a scenario needs a pointer, an exact title or a known ticket ------------------

#: Two scenarios in one document, neither pinned by a test, in the shape of the real corpus.
SPEC_TWO_SCENARIOS = """
## 7. Acceptance

```gherkin
  Scenario: the first unaccounted behaviour
    Given a thing
    Then it happens

  Scenario: the second unaccounted behaviour
    Given another thing
    Then it happens too
```
"""


def registry_for(keys: set[str], ticket: str = "HIFA-1") -> Any:
    """A debt registry that accepts exactly `keys` under one ticket."""
    cluster = claims.ScenarioDebt(ticket=ticket, reason="a probe", keys=frozenset(keys))
    return claims.DebtRegistry([cluster], frozenset({ticket}))


def corpus_report(tmp_path: Path, document: str, text: str, registry: Any) -> Any:
    """The whole corpus with one document edited: the only defect is the edit.

    Every document is copied, because the accepted set is keyed by document and a tree of one
    file would report every other key as stale.
    """
    specs = tmp_path / "specs"
    shutil.copytree(claims.SPECS_DIR, specs)
    (specs / document).write_text(text, encoding="utf-8")
    (tmp_path / "src").mkdir(exist_ok=True)
    tree = claims.Tree.read(specs, tmp_path / "src", claims.TESTS_DIR, specs / "acceptance")
    return claims.build_report(tree, ["scenario"], registry)


def with_scenario(text: str, scenario: str) -> str:
    """The document with one more acceptance section at the end, holding one scenario."""
    return f"{text}\n\n## 7. Acceptance\n\n```gherkin\n{scenario}\n```\n"


def without_scenario(text: str, document: str, key: str) -> str:
    """The document with the scenario that `key` names cut out, its tags included."""
    lines = text.splitlines(keepends=True)
    start = next(
        index
        for index, line in enumerate(lines)
        if (match := claims.SCENARIO_RE.match(line))
        and f"{document}::{claims.normalised_title(match.group('title'))}" == key
    )
    end = start + 1
    while end < len(lines) and not (
        claims.SCENARIO_RE.match(lines[end]) or lines[end].strip() == "```"
    ):
        end += 1
    while start and (not lines[start - 1].strip() or lines[start - 1].strip().startswith("@")):
        start -= 1
    return "".join(lines[:start] + lines[end:])


def corpus() -> str:
    """The joined source of the real suite, which is what the old matcher searched."""
    return "".join(
        path.read_text(encoding="utf-8") for path in sorted(claims.TESTS_DIR.rglob("*.py"))
    )


def test_a_scenario_no_test_names_is_a_violation(tmp_path: Path) -> None:
    """The batch scenario had no test for a whole revision, and nothing said so."""
    tree = build_tree(tmp_path, spec=SPEC_SCENARIO, tests=TEST_ELSEWHERE)

    violations = scenario_violations(tree)

    assert [v.check for v in violations] == ["scenario"]
    assert "history is written in a single batch" in violations[0].location
    assert "no test pins it" in violations[0].detail


def test_a_test_named_after_the_title_accounts_for_the_scenario(tmp_path: Path) -> None:
    """A test whose name is the title, compared whole, is the link the rule asks for."""
    tests = dedent(
        '''
        def test_history_is_written_in_a_single_batch() -> None:
            """One statement, three rows, one instant."""
            assert True
        '''
    )
    tree = build_tree(tmp_path, spec=SPEC_SCENARIO, tests=tests)

    assert scenario_violations(tree) == []


def test_a_test_sharing_most_of_the_words_is_not_a_match(tmp_path: Path) -> None:
    """Three of four words is not the title: similarity is what the old matcher guessed with."""
    tests = dedent(
        '''
        def test_history_is_written_batchwise_by_a_cron() -> None:
            """A batch, written by a cron, three times a day."""
            assert True
        '''
    )
    tree = build_tree(tmp_path, spec=SPEC_SCENARIO, tests=tests)

    violations = scenario_violations(tree)

    assert [v.check for v in violations] == ["scenario"]
    assert "history is written in a single batch" in violations[0].location


def test_a_quoted_step_that_appears_in_the_suite_accounts_for_nothing(tmp_path: Path) -> None:
    """A literal quoted in the steps is not evidence: the same words sit in hundreds of tests."""
    quoted = "In Progress"
    assert quoted in corpus(), (
        "the phrase has to occur in the suite for this probe to mean anything"
    )
    scenario = "".join(
        (
            "  Scenario: a scenario added this revision that quotes a state literal\n",
            f"    Given an issue whose state is `{quoted}`\n",
            "    Then the board shows it\n",
        )
    )
    registry = claims.DebtRegistry.read()
    document = sorted(registry.keys)[0].split("::", 1)[0]
    key = f"{document}::a scenario added this revision that quotes a state literal"
    assert key not in registry.keys
    text = (claims.SPECS_DIR / document).read_text(encoding="utf-8")

    report = corpus_report(tmp_path, document, with_scenario(text, scenario), registry)

    assert [v.check for v in report.violations] == ["scenario"]
    assert key in report.violations[0].detail
    assert "no test pins it" in report.violations[0].detail


def test_a_deferral_to_a_ticket_the_registry_carries_is_deferred(tmp_path: Path) -> None:
    """A deferral is a claim with an owner, and the owner has to be a ticket that exists."""
    registry = claims.DebtRegistry.read()
    ticket = sorted(registry.tickets)[0]
    spec = SPEC_SCENARIO.replace(
        "  Scenario: history", f"  @deferred({ticket})\n  Scenario: history"
    )
    tree = build_tree(tmp_path, spec=spec, tests=TEST_ELSEWHERE)

    assert scenario_violations(tree, registry) == []
    assert claims.scenario_counts(claims.account(tree, registry), registry)["deferred"] == 1


def test_a_deferral_to_a_ticket_nobody_issued_is_a_violation(tmp_path: Path) -> None:
    """A ticket id checked for its shape alone accepts a ticket that does not exist."""
    registry = claims.DebtRegistry.read()
    assert "HIFA-9999" not in registry.tickets
    spec = SPEC_SCENARIO.replace(
        "  Scenario: history", "  @deferred(HIFA-9999)\n  Scenario: history"
    )
    tree = build_tree(tmp_path, spec=spec, tests=TEST_ELSEWHERE)

    violations = scenario_violations(tree, registry)

    assert [v.check for v in violations] == ["scenario"]
    assert "HIFA-9999" in violations[0].detail
    assert "does not carry" in violations[0].detail


def test_a_deferral_with_no_ticket_accounts_for_nothing(tmp_path: Path) -> None:
    """A bare tag is not an owner, in a spec or in a feature file."""
    spec = SPEC_SCENARIO.replace("  Scenario: history", "  @deferred\n  Scenario: history")
    tree = build_tree(tmp_path, spec=spec, tests=TEST_ELSEWHERE)

    violations = scenario_violations(tree, claims.DebtRegistry.read())

    assert [v.check for v in violations] == ["scenario"]
    assert "names no ticket" in violations[0].detail


def test_a_deferral_citing_a_malformed_ticket_is_broken(tmp_path: Path) -> None:
    """`@deferred(someday)` is not a ticket id, so nothing owns the scenario."""
    spec = SPEC_SCENARIO.replace("  Scenario: history", "  @deferred(someday)\n  Scenario: history")
    tree = build_tree(tmp_path, spec=spec, tests=TEST_ELSEWHERE)

    violations = scenario_violations(tree, claims.DebtRegistry.read())

    assert "names no ticket id" in violations[0].detail


def test_a_test_pointer_to_a_test_that_exists_accounts_for_the_scenario(tmp_path: Path) -> None:
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


def test_an_accepted_key_is_accounted_for_and_nothing_else_is(tmp_path: Path) -> None:
    """The key is matched exactly: same document, same normalised title."""
    tree = build_tree(tmp_path, spec=SPEC_TWO_SCENARIOS, tests=TEST_ELSEWHERE)
    key = f"{SPEC_NAME}::the first unaccounted behaviour"

    violations = scenario_violations(tree, registry_for({key}))

    assert [violation.location for violation in violations] == [
        f"{SPEC_NAME}:the second unaccounted behaviour"
    ]


def test_deleting_an_accepted_scenario_and_adding_another_fails(tmp_path: Path) -> None:
    """The debt is a set, so one key cannot be traded for a different scenario.

    The document keeps the same number of accepted keys before and after the edit, which is
    exactly what a count per document accepted; the set does not.
    """
    registry = claims.DebtRegistry.read()
    key = sorted(registry.keys)[0]
    document = key.split("::", 1)[0]
    text = (claims.SPECS_DIR / document).read_text(encoding="utf-8")
    assert key in registry.keys

    before = corpus_report(tmp_path / "before", document, text, registry)
    assert before.violations == []

    added = "".join(
        (
            "  Scenario: a behaviour nobody has a test for\n",
            "    Given a thing nobody built\n",
            "    Then nothing pins it\n",
        )
    )
    edited = with_scenario(without_scenario(text, document, key), added)
    after = corpus_report(tmp_path / "after", document, edited, registry)

    details = [violation.detail for violation in after.violations]
    assert any("accepted key names no unaccounted scenario" in detail for detail in details), (
        details
    )
    assert any("a behaviour nobody has a test for" in detail for detail in details), details
    assert len(registry.keys) == len(claims.DebtRegistry.read().keys)


def test_an_accepted_key_no_scenario_carries_fails_until_accept_drops_it(tmp_path: Path) -> None:
    """A key the corpus no longer needs is stale: the set shrinks by running --accept."""
    registry = registry_for(
        {
            f"{SPEC_NAME}::a scenario that was deleted",
            f"{SPEC_NAME}::the first unaccounted behaviour",
        }
    )
    tree = build_tree(tmp_path, spec=SPEC_TWO_SCENARIOS, tests=TEST_ELSEWHERE)

    report = claims.build_report(tree, ["scenario"], registry)

    stale = [v for v in report.violations if "accepted key names no unaccounted" in v.detail]
    assert [v.location for v in stale] == [f"{SPEC_NAME}::a scenario that was deleted"]
    assert "--accept" in stale[0].detail

    written, kept, dropped = claims.accept(tree, registry)
    assert (kept, dropped) == (2, 1)
    assert f"{SPEC_NAME}::a scenario that was deleted" not in written.keys


def test_accept_writes_the_keys_of_every_unaccounted_scenario(tmp_path: Path) -> None:
    """`--accept` is the only writer, and it takes its keys from the tree it just read."""
    tree = build_tree(tmp_path, spec=SPEC_TWO_SCENARIOS, tests=TEST_ELSEWHERE)
    path = tmp_path / "debt.json"
    seed = claims.DebtRegistry([], frozenset({"HIFA-1"}), path, "HIFA-1")

    written, kept, dropped = claims.accept(tree, seed)
    written.write(path)

    assert (kept, dropped) == (2, 0)
    again = claims.DebtRegistry.read(path)
    assert again.keys == {
        f"{SPEC_NAME}::the first unaccounted behaviour",
        f"{SPEC_NAME}::the second unaccounted behaviour",
    }
    assert again.tickets == {"HIFA-1"}
    assert again.document()["version"] == claims.DEBT_VERSION
    assert claims.check_scenario(claims.account(tree, again), again) == []


def test_the_cli_fails_then_accepts_and_then_passes(tmp_path: Path, capsys: Any) -> None:
    """Exit 1 for an unaccounted scenario, then 0 once `--accept` wrote its key."""
    build_tree(tmp_path, spec=SPEC_TWO_SCENARIOS, tests=TEST_ELSEWHERE)
    specs, src, suite = tmp_path / "specs", tmp_path / "src", tmp_path / "tests"
    debt = tmp_path / "debt.json"
    debt.write_text(
        json.dumps(
            {"version": 1, "default_ticket": "HIFA-1", "tickets": ["HIFA-1"], "clusters": []}
        ),
        encoding="utf-8",
    )
    argv = [
        "--specs",
        str(specs),
        "--src",
        str(src),
        "--tests",
        str(suite),
        "--debt",
        str(debt),
        "--only",
        "scenario",
        "--quiet",
    ]

    assert claims.main(argv) == 1
    assert "2 new" in capsys.readouterr().out

    assert claims.main([*argv, "--accept"]) == 0
    assert "accepted 2 key(s)" in capsys.readouterr().out

    assert claims.main(argv) == 0
    assert "0 new" in capsys.readouterr().out


def test_a_registry_that_is_not_there_accepts_nothing(tmp_path: Path) -> None:
    """A missing file is not a licence: every unaccounted scenario fails, and says how to fix it."""
    tree = build_tree(tmp_path, spec=SPEC_TWO_SCENARIOS, tests=TEST_ELSEWHERE)

    registry = claims.DebtRegistry.read(tmp_path / "absent.json")

    assert registry.keys == frozenset()
    assert len(scenario_violations(tree, registry)) == 2


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
    assert "in the accepted set" in text


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
