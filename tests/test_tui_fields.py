"""Tests for the pure Click-parameter -> widget-spec / argv logic behind `toolbox tui`.

No Textual runtime involved -- these exercise fields.py directly against
real click.Argument/click.Option objects, the same way test_core_menu.py
exercises menu.py's equivalent prompt-based logic.
"""

from __future__ import annotations

import click

from pytoolbox.cli import toolbox
from pytoolbox.tui.fields import (
    ChoiceField,
    CountField,
    FlagField,
    MultiField,
    TextField,
    build_field,
    render_tokens,
)

#: The literal garbage that fields.py used to produce for "no default was
#: given" on Click >=8.4, which uses a Sentinel.UNSET object instead of
#: None/() to mean that. See _default_str() in fields.py.
_UNSET_STR = "Sentinel.UNSET"


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


# --- "no default" isn't confused with Click's internal sentinel -----------
#
# Click >=8.4 represents "no default was given" with a Sentinel.UNSET object,
# not None/(). fields.py must never treat that as a real default and
# stringify it into the literal "Sentinel.UNSET" (crashes ChoiceField's
# Select widget outright, and silently corrupts TextField's prefilled value).
# These tests don't hardcode which sentinel this Click version uses -- they
# just check the observable outcome stays correct either way.


def test_argument_with_no_default_never_surfaces_the_unset_sentinel():
    param = _argument(["name"])
    spec = build_field(param)
    assert isinstance(spec, TextField)
    assert spec.default == ""
    assert spec.default != _UNSET_STR


def test_option_with_no_default_never_surfaces_the_unset_sentinel():
    param = _option(["--label"])
    spec = build_field(param)
    assert isinstance(spec, TextField)
    assert spec.default == ""
    assert spec.default != _UNSET_STR


def test_choice_argument_with_no_default_is_none_not_the_unset_sentinel():
    param = _argument(["mode"], type=click.Choice(["a", "b"]))
    spec = build_field(param)
    assert isinstance(spec, ChoiceField)
    assert spec.default is None


def test_choice_option_with_no_default_is_none_not_the_unset_sentinel():
    param = _option(["--mode"], type=click.Choice(["a", "b"]))
    spec = build_field(param)
    assert isinstance(spec, ChoiceField)
    assert spec.default is None


# --- smoke test against the real command tree ------------------------------


def _iter_leaf_commands(group, ctx):
    """Yield every non-hidden leaf command reachable from `group`, recursively.

    Mirrors the walk in pytoolbox.core.menu._browse and
    tests/test_core_menu.py's real-tree tests. Skips a subcommand outright if
    fetching it raises ImportError (an optional dependency it needs for even
    its lazy-import wrapper isn't installed) -- LazyGroup normally swallows
    this itself and returns a _MissingDependencyCommand instead, but this
    stays defensive in case some other group doesn't.
    """
    for name in group.list_commands(ctx):
        try:
            cmd = group.get_command(ctx, name)
        except ImportError:
            continue
        if cmd is None or cmd.hidden:
            continue
        if isinstance(cmd, click.Group):
            sub_ctx = click.Context(cmd, info_name=cmd.name or name, parent=ctx)
            yield from _iter_leaf_commands(cmd, sub_ctx)
        else:
            yield cmd


def test_real_tree_never_builds_a_field_from_the_unset_sentinel():
    """Walk every real `toolbox` leaf command's parameters through build_field
    and check none of them ever surfaces the literal "Sentinel.UNSET" string
    as a default or choice -- guards against this exact class of bug
    recurring for future Click versions or new toolbox commands.
    """
    root_ctx = click.Context(toolbox, info_name="toolbox")
    for command in _iter_leaf_commands(toolbox, root_ctx):
        for param in command.params:
            if not getattr(param, "expose_value", True):
                continue
            spec = build_field(param)
            if spec is None:
                continue
            default = getattr(spec, "default", None)
            assert default != _UNSET_STR, f"{command.name} {param.name}: default was {default!r}"
            choices = getattr(spec, "choices", None) or []
            assert _UNSET_STR not in choices, f"{command.name} {param.name}: choices was {choices!r}"
