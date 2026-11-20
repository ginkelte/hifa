"""Unit tests for the label rules of `app.labels` and the shape of its commands.

The transaction and the SQL are covered by `tests/integration/app/test_labels.py`; here we pin
the pure decisions: what a name, a color and a group type are, and which local combination of
`CreateLabel`/`UpdateLabel`/`AddLabel` fields is refused before any row is read.

The three validators are private to `app/labels.py` because `domain/` publishes a closed symbol
table and a new public rule there would be a contract change; they are exercised through the
module, which is where the use cases call them.
"""

from __future__ import annotations

import pytest

from hifa.app.commands import (
    UNSET,
    Actor,
    AddLabel,
    CreateLabel,
    UpdateLabel,
)
from hifa.app.labels import (
    DEFAULT_LABEL_COLOR,
    _validate_group_type,
    _validate_label_color,
    _validate_label_name,
)
from hifa.domain.enums import LabelGroupType
from hifa.domain.errors import Validation

ACTOR = Actor(source="cli", label="cli")


# --- the name -------------------------------------------------------------------------------


def test_a_name_is_stored_trimmed() -> None:
    assert _validate_label_name("  infra  ") == "infra"


@pytest.mark.parametrize("blank", ["", "   ", "\t\n "])
def test_a_blank_name_is_refused_with_the_name_field(blank: str) -> None:
    with pytest.raises(Validation) as caught:
        _validate_label_name(blank)

    assert (caught.value.rule, caught.value.field) == ("LABEL_NAME_EMPTY", "name")
    assert caught.value.code == "LABEL_NAME_EMPTY"


# --- the color ------------------------------------------------------------------------------


@pytest.mark.parametrize("color", ["#EB5757", "#eb5757", "#000000", "#FFFFFF"])
def test_a_rrggbb_color_is_accepted_in_either_case(color: str) -> None:
    assert _validate_label_color(color) == color


@pytest.mark.parametrize("color", ["red", "#EB575", "#EB57577", "EB5757", "", None, "#GG5757"])
def test_anything_else_is_a_color_format_refusal(color: object) -> None:
    with pytest.raises(Validation) as caught:
        _validate_label_color(color)  # type: ignore[arg-type]

    assert (caught.value.rule, caught.value.field) == ("COLOR_FORMAT", "color")


def test_the_default_color_is_the_one_the_edge_publishes() -> None:
    assert DEFAULT_LABEL_COLOR == "#6B6F76"


# --- the group ------------------------------------------------------------------------------


@pytest.mark.parametrize("token", ["multiSelect", "singleSelect"])
def test_the_two_group_types_are_the_two_values_of_the_column(token: str) -> None:
    assert _validate_group_type(token) is LabelGroupType(token)


def test_a_group_type_that_is_not_in_the_vocabulary_is_a_group_refusal() -> None:
    with pytest.raises(Validation) as caught:
        _validate_group_type("multi-select")

    assert (caught.value.rule, caught.value.field) == ("LABEL_GROUP_INVALID", "group_type")


# --- CreateLabel: the local combinations ----------------------------------------------------


def test_a_group_without_a_type_is_refused_on_the_group_type_field() -> None:
    with pytest.raises(Validation) as caught:
        CreateLabel(name="Severity", is_group=True)

    assert (caught.value.rule, caught.value.field) == ("LABEL_GROUP_INVALID", "group_type")


def test_a_group_with_a_parent_is_refused_on_the_parent_field() -> None:
    with pytest.raises(Validation) as caught:
        CreateLabel(name="Severity", is_group=True, group_type="singleSelect", parent="Area")

    assert (caught.value.rule, caught.value.field) == ("LABEL_GROUP_INVALID", "parent")


def test_a_child_carrying_a_group_type_is_refused() -> None:
    with pytest.raises(Validation) as caught:
        CreateLabel(name="High", parent="Severity", group_type="singleSelect")

    assert (caught.value.rule, caught.value.field) == ("LABEL_GROUP_INVALID", "group_type")


def test_the_command_normalizes_the_description_and_the_level() -> None:
    cmd = CreateLabel(name="  High  ", team="  ENG ", description="   ")

    assert (cmd.name, cmd.team, cmd.description) == ("High", "ENG", None)


def test_a_group_keeps_the_token_and_the_use_case_parses_it() -> None:
    """Membership in the enum is decided once, by the use case, like every other vocabulary."""
    cmd = CreateLabel(name="Severity", is_group=True, group_type=" singleSelect ")

    assert cmd.group_type == "singleSelect"


# --- UpdateLabel: `UNSET` versus `None` ------------------------------------------------------


def test_an_absent_flag_leaves_the_field_unset() -> None:
    cmd = UpdateLabel(label="Bug")

    assert (cmd.name, cmd.color, cmd.description) == (UNSET, UNSET, UNSET)


def test_a_cleared_description_is_none_and_an_empty_one_is_none_too() -> None:
    assert UpdateLabel(label="Bug", description=None).description is None
    assert UpdateLabel(label="Bug", description="   ").description is None


def test_names_and_colors_arrive_trimmed() -> None:
    cmd = UpdateLabel(label="  Bug ", name=" Defect ", color=" #EB5757 ")

    assert (cmd.label, cmd.name, cmd.color) == ("Bug", "Defect", "#EB5757")


def test_an_empty_reference_is_a_command_field_error() -> None:
    with pytest.raises(Validation) as caught:
        UpdateLabel(label="   ")

    assert (caught.value.rule, caught.value.field) == ("COMMAND_FIELD", "label")


# --- AddLabel -------------------------------------------------------------------------------


def test_create_if_missing_requires_a_color() -> None:
    with pytest.raises(Validation) as caught:
        AddLabel(issue="ENG-1", label="infra", create_if_missing=True)

    assert (caught.value.rule, caught.value.field) == ("COMMAND_FIELD", "color")


def test_a_plain_add_carries_no_color_and_creates_nothing() -> None:
    cmd = AddLabel(issue=" ENG-1 ", label=" infra ")

    assert (cmd.issue, cmd.label, cmd.color, cmd.create_if_missing) == (
        "ENG-1",
        "infra",
        None,
        False,
    )
