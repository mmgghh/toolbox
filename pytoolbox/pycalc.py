#!/usr/bin/env python3
"""A calculator for the shell.

Exposes the ``pycalc`` console script, also available as ``toolbox calc``.

The expression is parsed with Python's own parser and then walked node by
node, evaluating only the arithmetic that is explicitly allowed here. Nothing
is handed to ``eval``: names that are not a listed constant or function are an
error, so an expression can compute a number and do nothing else.

``^`` means "to the power of", the way it does on a calculator and in a
spreadsheet, rather than Python's bitwise exclusive-or. ``--caret xor`` puts
Python's meaning back; ``xor(a, b)`` is always available under either.
"""

from __future__ import annotations

import ast
import difflib
import math
import operator
import sys
from typing import Any, Optional

import click

from pytoolbox.core.options import CONTEXT_SETTINGS, version_option

#: Operators, by the AST node that stands for them.
BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.BitAnd: operator.and_,
    ast.BitOr: operator.or_,
    ast.BitXor: operator.xor,
    ast.LShift: operator.lshift,
    ast.RShift: operator.rshift,
}
UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
    ast.Invert: operator.invert,
}
COMPARISONS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}

CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    "nan": math.nan,
}

FUNCTIONS: dict[str, Any] = {
    "abs": abs,
    "round": round,
    "int": int,
    "float": float,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    # math.cbrt is 3.11+, and the cube root of a negative number is real.
    "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "exp": math.exp,
    "log": math.log,
    "ln": math.log,
    "log2": math.log2,
    "log10": math.log10,
    "pow": math.pow,
    "floor": math.floor,
    "ceil": math.ceil,
    "trunc": math.trunc,
    "fabs": math.fabs,
    "copysign": math.copysign,
    "hypot": math.hypot,
    "gcd": math.gcd,
    "lcm": math.lcm,
    "factorial": math.factorial,
    "comb": math.comb,
    "perm": math.perm,
    "degrees": math.degrees,
    "radians": math.radians,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    # Reachable under either meaning of ^, which is the point of it.
    "xor": operator.xor,
}

#: Guards against an expression that is short to type and impossible to
#: finish: 9**9**9 has more digits than there is memory to hold them.
MAX_RESULT_DIGITS = 100_000
MAX_FACTORIAL = 10_000

#: How many significant digits a float answer is rounded to before printing.
#: Twelve is enough to keep 1/3 useful and few enough to hide the noise that
#: makes 0.1 + 0.2 print as 0.30000000000000004.
DEFAULT_PRECISION = 12

BASES = ("dec", "hex", "bin", "oct")
CARET_MEANINGS = ("power", "xor")

#: Typed at an interactive prompt, these end the session rather than evaluate.
QUIT_WORDS = ("q", "quit", "exit")


class CalculationError(click.ClickException):
    """A problem with the expression itself, reported without a traceback."""


def prepare(expression: str, caret: str = "power") -> str:
    """Rewrite the caret when it means exponentiation.

    An expression holds no strings and no comments, so there is nowhere for a
    ``^`` to hide: replacing every one of them is the whole transformation.
    """
    return expression.replace("^", "**") if caret == "power" else expression


def _unknown(kind: str, used: str, known: set) -> CalculationError:
    """Report a name nobody defined, with the nearest ones that exist.

    Same courtesy the command groups extend to a mistyped subcommand: a
    calculator's vocabulary is not something anyone memorises.
    """
    close = difflib.get_close_matches(used, sorted(known), n=3, cutoff=0.4)
    if not close:
        close = sorted(name for name in known if name.startswith(used[:1]))[:3]
    suffix = f" Did you mean: {', '.join(close)}?" if close else ""
    return CalculationError(f"Unknown {kind} {used!r}.{suffix}")


def _check_power(base: Any, exponent: Any) -> None:
    """Refuse a power whose answer could not be held, let alone printed."""
    if not isinstance(base, int) or not isinstance(exponent, int) or exponent <= 0:
        return
    if base in (0, 1, -1):
        return
    if exponent * math.log10(abs(base)) > MAX_RESULT_DIGITS:
        raise CalculationError(
            f"{base}**{exponent} has more than {MAX_RESULT_DIGITS:,} digits; refusing to compute it."
        )


def _call(name: str, function: Any, arguments: list) -> Any:
    """Apply one allowed function, guarding the ones that explode."""
    if name in ("factorial", "comb", "perm"):
        for argument in arguments:
            if isinstance(argument, int) and argument > MAX_FACTORIAL:
                raise CalculationError(f"{name}() is limited to arguments up to {MAX_FACTORIAL:,}.")
    try:
        return function(*arguments)
    except TypeError as exc:
        raise CalculationError(f"{name}(): {exc}") from exc
    except ValueError as exc:
        raise CalculationError(f"{name}(): {exc}") from exc
    except OverflowError as exc:
        raise CalculationError(f"{name}(): result is too large ({exc}).") from exc


def _evaluate_node(node: ast.AST, names: dict[str, Any]) -> Any:
    """Evaluate one node of the expression tree, or refuse to."""
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body, names)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, complex)) and not isinstance(node.value, bool):
            return node.value
        raise CalculationError(f"Not a number: {node.value!r}")

    if isinstance(node, ast.Name):
        for source in (names, CONSTANTS):
            if node.id in source:
                return source[node.id]
        raise _unknown("name", node.id, {*names, *CONSTANTS, *FUNCTIONS})

    if isinstance(node, ast.BinOp):
        function = BINARY_OPERATORS.get(type(node.op))
        if function is None:
            raise CalculationError(f"Unsupported operator: {type(node.op).__name__}")
        left, right = _evaluate_node(node.left, names), _evaluate_node(node.right, names)
        if isinstance(node.op, ast.Pow):
            _check_power(left, right)
        try:
            return function(left, right)
        except ZeroDivisionError as exc:
            raise CalculationError("Division by zero.") from exc
        except (TypeError, ValueError) as exc:
            raise CalculationError(str(exc)) from exc
        except OverflowError as exc:
            raise CalculationError(f"Result is too large ({exc}).") from exc

    if isinstance(node, ast.UnaryOp):
        function = UNARY_OPERATORS.get(type(node.op))
        if function is None:
            raise CalculationError(f"Unsupported operator: {type(node.op).__name__}")
        try:
            return function(_evaluate_node(node.operand, names))
        except TypeError as exc:
            raise CalculationError(str(exc)) from exc

    if isinstance(node, ast.Compare):
        left = _evaluate_node(node.left, names)
        for op, right_node in zip(node.ops, node.comparators):
            function = COMPARISONS.get(type(op))
            if function is None:
                raise CalculationError(f"Unsupported comparison: {type(op).__name__}")
            right = _evaluate_node(right_node, names)
            if not function(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise CalculationError("Only plain function calls are allowed.")
        function = FUNCTIONS.get(node.func.id)
        if function is None:
            raise _unknown("function", node.func.id, set(FUNCTIONS))
        if node.keywords:
            raise CalculationError(f"{node.func.id}() takes no keyword arguments here.")
        return _call(node.func.id, function, [_evaluate_node(arg, names) for arg in node.args])

    raise CalculationError(f"Not allowed in an expression: {type(node).__name__}")


def evaluate(expression: str, caret: str = "power", names: Optional[dict[str, Any]] = None) -> Any:
    """Evaluate one arithmetic expression and return its value."""
    text = prepare(expression, caret).strip()
    if not text:
        raise CalculationError("Nothing to calculate.")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalculationError(f"Could not read {expression.strip()!r}: {exc.msg}.") from exc
    return _evaluate_node(tree, names or {})


def format_result(value: Any, precision: int = DEFAULT_PRECISION, base: str = "dec") -> str:
    """Render a result the way someone reading it would want to see it."""
    if isinstance(value, bool):
        return str(value)

    if isinstance(value, float) and value.is_integer() and abs(value) < 1e16:
        # 6/2 is 3, not 3.0 -- but 1e300 stays in exponent form.
        value = int(value)

    if isinstance(value, int):
        if base == "hex":
            return hex(value)
        if base == "bin":
            return bin(value)
        if base == "oct":
            return oct(value)
        return str(value)

    if base != "dec":
        raise CalculationError(f"--base {base} needs a whole number; the answer is {value}.")

    if isinstance(value, complex):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        return str(value)

    # %g drops the noise of binary floating point and any trailing zeros with
    # it, which is what makes 0.1 + 0.2 print as 0.3.
    text = f"{value:.{precision}g}"
    return text


# A leading minus is a negative number far more often than it is a typo for an
# option, so `pycalc -2**2` has to work; unknown options are handed to the
# parser as part of the expression. Known ones (-p, --base) still parse.
@click.command(context_settings={**CONTEXT_SETTINGS, "ignore_unknown_options": True})
@click.argument("expression", nargs=-1)
@click.option(
    "--caret",
    type=click.Choice(CARET_MEANINGS, case_sensitive=False),
    default="power",
    show_default=True,
    help="What ^ means: 'power' as on a calculator, or 'xor' as in Python.",
)
@click.option(
    "--base",
    type=click.Choice(BASES, case_sensitive=False),
    default="dec",
    show_default=True,
    help="Base for whole-number answers.",
)
@click.option(
    "-p",
    "--precision",
    type=click.IntRange(1, 17),
    default=DEFAULT_PRECISION,
    show_default=True,
    help="Significant digits for answers that are not whole. 17 shows the exact binary value.",
)
@version_option
def calc_cli(expression: tuple[str, ...], caret: str, base: str, precision: int) -> None:
    """Evaluate an arithmetic EXPRESSION and print the answer.

    \b
    Supports + - * / // % ** and ^ (both mean "to the power of"), bitwise
    & | ~ << >>, comparisons, parentheses, decimal, hex (0x1f), binary (0b101)
    and scientific (1.5e3) numbers, pi/e/tau, and functions: sqrt, cbrt, exp,
    log, ln, log2, log10, floor, ceil, round, abs, min, max, gcd, lcm,
    factorial, comb, perm, hypot, degrees, radians, sin, cos, tan and the rest
    of the trigonometric family, plus xor.

    \b
    Quote anything containing * or ( so the shell does not eat it. With no
    EXPRESSION, expressions are read one per line from stdin, where `ans`
    holds the previous answer.

    \b
    Examples:
      pycalc '2**5+56-1'                # 87
      pycalc '2^10'                     # 1024
      pycalc '(1+2)*3 / 4'              # 2.25
      pycalc 'sqrt(2) * sin(pi/4)'      # 1
      pycalc '255' --base hex           # 0xff
      echo '2+2' | pycalc
    """
    if expression:
        # Joined rather than taken one at a time, so `pycalc 2 + 3` works as
        # well as `pycalc '2 + 3'` once the shell has split it up.
        click.echo(format_result(evaluate(" ".join(expression), caret), precision, base))
        return

    names: dict[str, Any] = {}
    interactive = sys.stdin.isatty()
    if interactive:
        click.echo("Enter an expression per line; `ans` is the last answer. Ctrl-D or `quit` to stop.", err=True)
    for line in sys.stdin:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower() in QUIT_WORDS:
            break
        try:
            value = evaluate(line, caret, names)
        except CalculationError as exc:
            # One bad line should not end a session, but it must be reported
            # and, in a pipeline, must not pass silently either.
            if interactive:
                click.secho(f"error: {exc.format_message()}", fg="red", err=True)
                continue
            raise
        names["ans"] = value
        click.echo(format_result(value, precision, base))


if __name__ == "__main__":  # pragma: no cover
    calc_cli()
