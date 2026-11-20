"""The query grammar: normalization, terms, the two FTS5 expressions and the filters.

Every test here pins a property of the text and of what it compiles to. The adversarial corpus
runs against a real FTS5 table: no input of the suite may produce a `MATCH` the engine refuses,
which is the whole reason the terms are quoted instead of interpolated.
"""

from __future__ import annotations

import pytest

from hifa.adapters.sqlite.connection import open_db
from hifa.adapters.sqlite.migrate import apply_migrations
from hifa.app.search import (
    MAX_TERM_LEN,
    SearchError,
    TextPredicate,
    build_match_expression,
    build_text_predicate,
    build_title_match_expression,
    escape_like,
    identifier_candidate,
    normalize,
    parse_query,
    sanitize_terms,
)

#: Inputs that would break an unquoted `MATCH`, plus the shapes the grammar accepts.
HOSTILE = (
    "*",
    '"',
    '""',
    "deploy*",
    "deploy **",
    "-",
    "--",
    "-bug",
    "a -b -c",
    "(deploy)",
    "NEAR(deploy bug, 5)",
    "AND",
    "OR",
    "NOT",
    "a:b",
    "title:eng",
    "ENG-42",
    'x" OR 1=1 --',
    "100%",
    "under_score",
    "diseño",
    '"deploy roto"',
    '"unclosed phrase',
    "   ",
    "\\",
    "^deploy$",
    "{deploy}",
    "deploy:",
    "-100%",
    "a" * 100,
)


def test_normalization_folds_typographic_quotes_and_drops_control_characters() -> None:
    """NFKC plus the two quote pairs; a tab and a newline become one space each."""
    assert normalize("“deploy”\t‘roto’\n") == "\"deploy\" 'roto' "
    assert normalize("dise\u00f1o") == "diseño"
    assert normalize("a\x00b") == "ab"


def test_a_quoted_phrase_keeps_its_word_order_as_one_term() -> None:
    assert sanitize_terms('"deploy roto"') == ("deploy roto",)
    assert build_match_expression('"deploy roto"') == '"deploy roto"'


def test_an_unclosed_quote_consumes_the_rest_of_the_text() -> None:
    assert sanitize_terms('deploy "roto en produccion') == ("deploy", "roto en produccion")


def test_boolean_tokens_are_words_and_never_operators() -> None:
    """`OR`, `NOT` and `NEAR` are searched as text: the expression only joins quoted atoms."""
    expression = build_match_expression("foo OR bar NOT baz NEAR")
    assert expression == '"foo" "OR" "bar" "NOT" "baz" "NEAR"'


def test_parentheses_and_braces_are_punctuation() -> None:
    assert sanitize_terms("(deploy) {roto} ^x^") == ("deploy", "roto", "x")


def test_a_trailing_asterisk_is_a_prefix_and_the_star_stays_outside_the_quotes() -> None:
    assert sanitize_terms("autent*") == ("autent*",)
    assert build_match_expression("autent*") == '"autent"*'


def test_terms_without_an_alphanumeric_character_are_discarded() -> None:
    assert sanitize_terms("- () && ...  100% under_score") == ("100%", "under_score")


@pytest.mark.parametrize(
    ("text", "like_terms", "not_terms"),
    [
        ("deploy", ("deploy",), ()),
        ("-bug", (), ("bug",)),
        ("deploy -bug", ("deploy",), ("bug",)),
        ("-bug deploy", ("deploy",), ("bug",)),
        ("deploy -bug -infra", ("deploy",), ("bug", "infra")),
        ("", (), ()),
    ],
)
def test_the_predicate_splits_the_negated_terms_with_the_marker_stripped(
    text: str, like_terms: tuple[str, ...], not_terms: tuple[str, ...]
) -> None:
    predicate = build_text_predicate(text)

    assert predicate.like_terms == like_terms
    assert predicate.not_terms == not_terms


def test_a_positive_expression_with_negations_excludes_them_inside_a_not() -> None:
    assert build_match_expression("deploy -bug -infra") == '("deploy") NOT ("bug" OR "infra")'


def test_a_query_of_negations_only_has_no_expression_at_all() -> None:
    predicate = build_text_predicate("-bug")

    assert predicate == TextPredicate(
        match=None, match_title=None, like_terms=(), not_terms=("bug",)
    )


def test_an_empty_query_compiles_to_no_predicate() -> None:
    assert build_text_predicate("   ") == TextPredicate(None, None, (), ())


def test_the_title_expression_is_restricted_to_the_title_column() -> None:
    assert build_title_match_expression("deploy roto") == 'title : ("deploy" "roto")'
    assert build_title_match_expression("-bug") is None


def test_escape_like_makes_the_three_special_characters_literal() -> None:
    assert escape_like("100%") == "100\\%"
    assert escape_like("a_b") == "a\\_b"
    assert escape_like("a\\b") == "a\\\\b"


def test_an_identifier_candidate_is_the_uppercased_complete_identifier() -> None:
    assert identifier_candidate("eng-42") == "ENG-42"
    assert identifier_candidate("ENG-") is None
    assert identifier_candidate("not-an-identifier-x") is None


def test_more_terms_than_the_bound_is_a_refusal() -> None:
    with pytest.raises(SearchError) as raised:
        sanitize_terms(" ".join(f"t{index}" for index in range(17)))

    assert raised.value.code == "query_too_long"
    assert raised.value.field == "q"


def test_a_term_longer_than_the_bound_is_a_refusal() -> None:
    with pytest.raises(SearchError) as raised:
        sanitize_terms("x" * (MAX_TERM_LEN + 1))

    assert raised.value.code == "query_too_long"


def test_a_query_longer_than_the_bound_is_a_refusal() -> None:
    with pytest.raises(SearchError) as raised:
        sanitize_terms("x " * 300)

    assert raised.value.code == "query_too_long"


def test_parse_query_extracts_an_embedded_filter_and_leaves_the_text_alone() -> None:
    parsed = parse_query('deploy state:"In Progress"')

    assert parsed.text == "deploy"
    assert parsed.filters.state == "In Progress"
    assert parsed.terms == ("deploy",)


def test_parse_query_reads_a_negated_filter_into_negate() -> None:
    parsed = parse_query("deploy -state:Done -label:bug")

    assert parsed.text == "deploy"
    assert parsed.filters.state == "Done"
    assert parsed.filters.label == "bug"
    assert parsed.filters.negate == ("state", "label")


def test_parse_query_folds_an_unknown_field_back_into_the_text() -> None:
    parsed = parse_query("foo:bar deploy")

    assert parsed.filters == type(parsed.filters)()
    assert parsed.text == "foo:bar deploy"
    assert parsed.terms == ("foobar", "deploy")


def test_parse_query_normalizes_a_priority_name_to_its_number() -> None:
    assert parse_query("priority:urgent").filters.priority == "1"
    assert parse_query('priority:"no priority"').filters.priority == "0"


@pytest.mark.parametrize("raw", ["priority:urgentísimo", "due:ayer", "-due:today", "-is:open"])
def test_an_invalid_filter_value_is_a_refusal_that_names_the_field(raw: str) -> None:
    with pytest.raises(SearchError) as raised:
        parse_query(raw)

    assert raised.value.code == "invalid_filter"
    assert raised.value.field in {"priority", "due", "is"}
    assert "'" in raised.value.message


def test_parse_query_is_empty_only_when_no_term_and_no_filter_survives() -> None:
    assert parse_query("").is_empty is True
    assert parse_query("   ").is_empty is True
    assert parse_query("state:Done").is_empty is False
    assert parse_query("deploy").is_empty is False


def test_no_input_of_the_corpus_produces_a_match_the_engine_refuses(tmp_path) -> None:
    """Every input compiles to an expression the FTS5 engine accepts, or to no expression."""
    conn = open_db(tmp_path / "fts.db")
    try:
        apply_migrations(conn)
        for text in HOSTILE:
            expression = build_match_expression(text)
            if expression is None:
                continue
            rows = conn.execute(
                "SELECT rowid FROM issue_fts WHERE issue_fts MATCH ?", (expression,)
            ).fetchall()
            assert rows == []
    finally:
        conn.close()
