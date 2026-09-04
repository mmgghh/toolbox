"""Tests for the pure Click-parameter -> widget-spec / argv logic behind `toolbox tui`.

No Textual runtime involved -- these exercise fields.py directly against
real click.Argument/click.Option objects, the same way test_core_menu.py
exercises menu.py's equivalent prompt-based logic.
"""

from __future__ import annotations

import click

from pytoolbox.tui.fields import (
    ChoiceField,
    CountField,
    FlagField,
    MultiField,
    TextField,
    build_field,
    render_tokens,
)


def _argument(names, **kwargs) -> click.Argument:
    return click.Argument(names, **kwargs)


def _option(names, **kwargs) -> click.Option:
    return click.Option(names, **kwargs)


def test_required_text_argument():
    param = _argument(["name"])
    spec = build_field(param)
    assert isinstance(spec, TextField)
    assert spec.opt is None
    assert spec.required is True
    assert render_tokens(spec, "bob") == ["bob"]
    assert render_tokens(spec, "") == []


def test_variadic_argument_is_a_multi_field_with_no_flag():
    param = _argument(["items"], nargs=-1)
    spec = build_field(param)
    assert isinstance(spec, MultiField)
    assert spec.opt is None
    assert render_tokens(spec, ["a", "b"]) == ["a", "b"]
    assert render_tokens(spec, []) == []


def test_choice_argument_omits_tokens_at_default():
    param = _argument(["mode"], type=click.Choice(["a", "b"]), default="a")
    spec = build_field(param)
    assert isinstance(spec, ChoiceField)
    assert spec.choices == ["a", "b"]
    assert spec.default == "a"
    assert render_tokens(spec, "a") == []
    assert render_tokens(spec, "b") == ["b"]


def test_plain_option_uses_its_preferred_long_flag():
    param = _option(["-l", "--label"], default="x")
    spec = build_field(param)
    assert isinstance(spec, TextField)
    assert spec.opt == "--label"
    assert spec.default == "x"
    assert render_tokens(spec, "x") == []
    assert render_tokens(spec, "y") == ["--label", "y"]


def test_hidden_option_is_a_password_text_field():
    param = _option(["--token"], hide_input=True)
    spec = build_field(param)
    assert isinstance(spec, TextField)
    assert spec.password is True


def test_choice_option():
    param = _option(["--mode"], type=click.Choice(["a", "b"]), default="a")
    spec = build_field(param)
    assert isinstance(spec, ChoiceField)
    assert spec.opt == "--mode"


def test_count_option():
    param = _option(["-v", "--verbose"], count=True)
    spec = build_field(param)
    assert isinstance(spec, CountField)
    assert spec.opt == "--verbose"
    assert render_tokens(spec, 0) == []
    assert render_tokens(spec, 2) == ["--verbose", "--verbose"]


def test_multiple_option():
    param = _option(["--tag"], multiple=True)
    spec = build_field(param)
    assert isinstance(spec, MultiField)
    assert spec.opt == "--tag"
    assert render_tokens(spec, ["t1", "t2"]) == ["--tag", "t1", "--tag", "t2"]


def test_flag_with_negation_is_a_two_way_switch():
    param = _option(["--loud/--quiet"], default=False)
    spec = build_field(param)
    assert isinstance(spec, FlagField)
    assert spec.on_opt == "--loud"
    assert spec.off_opt == "--quiet"
    assert spec.default is False
    assert render_tokens(spec, False) == []
    assert render_tokens(spec, True) == ["--loud"]


def test_flag_without_negation_defaulting_off():
    param = _option(["--loud"], is_flag=True, default=False)
    spec = build_field(param)
    assert isinstance(spec, FlagField)
    assert spec.on_opt == "--loud"
    assert spec.off_opt is None
    assert render_tokens(spec, True) == ["--loud"]
    assert render_tokens(spec, False) == []


def test_flag_without_negation_defaulting_on_has_no_field():
    param = _option(["--loud"], is_flag=True, default=True)
    assert build_field(param) is None
