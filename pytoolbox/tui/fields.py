"""Pure functions: classify a Click parameter into a widget spec, and render
the current form values back into `toolbox` argv tokens.

Mirrors the parameter taxonomy in ``pytoolbox.core.menu`` (``_ask_argument``,
``_ask_option``, ``_preferred_opt``), but as data instead of blocking
prompts, since Textual widgets are event-driven and can't call those
prompt-based functions directly.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Optional, Union

import click

try:
    from click.core import UNSET as _CLICK_UNSET
except ImportError:  # Click <8.4 has no UNSET sentinel -- "no default" is None/().
    _CLICK_UNSET = None


def _default_str(value) -> Optional[str]:
    """Render a Click parameter's default as a string, or None if there isn't one.

    Click >=8.4 uses a ``Sentinel.UNSET`` object instead of ``None``/``()`` to mean
    "no default was given".
    """
    if value is None or value == () or (_CLICK_UNSET is not None and value is _CLICK_UNSET):
        return None
    return str(value)


@dataclass
class TextField:
    label: str
    opt: Optional[str]  # None for a positional argument
    default: str = ""
    required: bool = False
    password: bool = False


@dataclass
class ChoiceField:
    label: str
    opt: Optional[str]
    choices: list = field(default_factory=list)
    default: Optional[str] = None


@dataclass
class FlagField:
    label: str
    on_opt: str
    off_opt: Optional[str]  # set only when there is a --no-... counterpart
    default: bool = False


@dataclass
class CountField:
    label: str
    opt: str
    default: int = 0


@dataclass
class MultiField:
    label: str
    opt: Optional[str]  # None for a variadic (nargs=-1) argument


FieldSpec = Union[TextField, ChoiceField, FlagField, CountField, MultiField]


def build_field(param: click.Parameter) -> Optional[FieldSpec]:
    """Classify one Click parameter, or return None if it needs no widget."""
    if isinstance(param, click.Argument):
        return _build_argument_field(param)
    return _build_option_field(param)


def _build_argument_field(param: click.Argument) -> FieldSpec:
    label = param.human_readable_name
    if param.nargs == -1:
        return MultiField(label=label, opt=None)

    choices = getattr(param.type, "choices", None)
    default = _default_str(param.default)
    if choices:
        return ChoiceField(label=label, opt=None, choices=list(choices), default=default)
    return TextField(label=label, opt=None, default=default or "", required=param.required)


def _build_option_field(param: click.Option) -> Optional[FieldSpec]:
    opt = _preferred_opt(param.opts)
    label = param.help.strip().splitlines()[0] if param.help else param.human_readable_name

    if param.is_flag:
        default_on = bool(param.default)
        if param.secondary_opts:
            return FlagField(label=label, on_opt=opt, off_opt=_preferred_opt(param.secondary_opts), default=default_on)
        if default_on:
            # No `--no-...` counterpart -- nothing this form could turn off.
            return None
        return FlagField(label=label, on_opt=opt, off_opt=None, default=False)

    if param.count:
        return CountField(label=label, opt=opt, default=0)

    if param.multiple:
        return MultiField(label=label, opt=opt)

    choices = getattr(param.type, "choices", None)
    default = _default_str(param.default)
    if choices:
        return ChoiceField(label=label, opt=opt, choices=list(choices), default=default)

    return TextField(
        label=label,
        opt=opt,
        default=default or "",
        required=param.required,
        password=bool(getattr(param, "hide_input", False)),
    )


def _preferred_opt(opts: Sequence[str]) -> str:
    long_opts = [o for o in opts if o.startswith("--")]
    return long_opts[0] if long_opts else opts[0]


def render_tokens(spec: FieldSpec, value) -> list:
    """Render one field's current widget value into its argv tokens."""
    if isinstance(spec, TextField):
        if not value or value == spec.default:
            return []
        return [value] if spec.opt is None else [spec.opt, value]

    if isinstance(spec, ChoiceField):
        if value is None or value == spec.default:
            return []
        return [value] if spec.opt is None else [spec.opt, value]

    if isinstance(spec, FlagField):
        if value == spec.default:
            return []
        if value:
            return [spec.on_opt]
        return [spec.off_opt] if spec.off_opt else []

    if isinstance(spec, CountField):
        n = max(0, int(value or 0))
        return [spec.opt] * n

    if isinstance(spec, MultiField):
        values = [v for v in value if v]
        if spec.opt is None:
            return values
        tokens: list = []
        for v in values:
            tokens += [spec.opt, v]
        return tokens

    raise TypeError(f"Unknown field spec: {spec!r}")  # pragma: no cover
