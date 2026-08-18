"""Tests for pycalc: evaluation, formatting, refusals and the CLI."""

from __future__ import annotations

import pytest

from pytoolbox.pycalc import (
    CalculationError,
    calc_cli,
    evaluate,
    format_result,
    prepare,
)


def value(expression: str, **kwargs):
    return evaluate(expression, **kwargs)


def shown(expression: str, **kwargs) -> str:
    return format_result(evaluate(expression), **kwargs)


# ── arithmetic ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2**5+56-1", 87),
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("7 // 2", 3),
        ("10 % 3", 1),
        ("-2**2", -4),
        ("2 ** -1", 0.5),
        ("1e3 * 2", 2000.0),
        ("0x1f + 0b101 + 0o7", 43),
        ("1_000_000 / 4", 250000.0),
        ("6 & 3", 2),
        ("6 | 3", 7),
        ("1 << 10", 1024),
        ("~5", -6),
        ("3 < 5", True),
        ("1 < 2 < 3", True),
        ("2 == 3", False),
    ],
)
def test_operators(expression, expected):
    assert value(expression) == expected


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("sqrt(16)", 4),
        ("cbrt(-27)", -3),
        ("abs(-3.5)", 3.5),
        ("round(2.567, 1)", 2.6),
        ("max(1, 7, 3)", 7),
        ("factorial(5)", 120),
        ("gcd(12, 18)", 6),
        ("lcm(4, 6)", 12),
        ("comb(5, 2)", 10),
        ("log10(1000)", 3),
        ("log(8, 2)", 3),
        ("ln(e)", 1),
        ("floor(2.7) + ceil(0.2)", 3),
        ("degrees(pi)", 180),
        ("xor(6, 3)", 5),
    ],
)
def test_functions(expression, expected):
    assert value(expression) == pytest.approx(expected)


def test_caret_is_exponentiation_by_default():
    assert prepare("2^10") == "2**10"
    assert value("2^10") == 1024
    assert value("3^2^2") == 81  # right-associative, as ** is


def test_caret_can_mean_python_xor():
    assert prepare("2^10", caret="xor") == "2^10"
    assert value("2^10", caret="xor") == 8
    # xor() reaches the operation under either meaning.
    assert value("xor(2, 10)") == 8


# ── formatting ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("0.1 + 0.2", "0.3"),
        ("1/3", "0.333333333333"),
        ("6/2", "3"),
        ("2**100", "1267650600228229401496703205376"),
        ("sqrt(2)", "1.41421356237"),
        ("1/8", "0.125"),
        ("3 > 2", "True"),
    ],
)
def test_answers_read_the_way_a_person_would_write_them(expression, expected):
    """Binary floating-point noise is an implementation detail, not an answer."""
    assert shown(expression) == expected


def test_precision_is_adjustable():
    assert shown("1/3", precision=3) == "0.333"
    assert shown("0.1 + 0.2", precision=17) == "0.30000000000000004"


def test_whole_answers_in_another_base():
    assert shown("255", base="hex") == "0xff"
    assert shown("5", base="bin") == "0b101"
    assert shown("8", base="oct") == "0o10"


def test_a_fractional_answer_has_no_other_base():
    with pytest.raises(CalculationError):
        shown("1/3", base="hex")


# ── refusals ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd')",
        "(1).__class__",
        "[1, 2][0]",
        "lambda: 1",
        "print(1)",
        "1 if True else 2",
        "x := 4",
    ],
)
def test_anything_that_is_not_arithmetic_is_refused(expression):
    """The parser is Python's; what may be evaluated is not."""
    with pytest.raises(CalculationError):
        value(expression)


def test_division_by_zero_is_a_message_not_a_traceback():
    with pytest.raises(CalculationError, match="Division by zero"):
        value("1/0")


def test_an_unknown_name_suggests_the_ones_that_exist():
    with pytest.raises(CalculationError, match="factorial"):
        value("fact(3)+1")


def test_a_syntax_error_quotes_what_was_typed():
    with pytest.raises(CalculationError, match="2\\+\\*3"):
        value("2+*3")


def test_an_impossible_power_is_refused_rather_than_attempted():
    """9**9**9 is quick to type and cannot be held in memory."""
    with pytest.raises(CalculationError, match="digits"):
        value("9**9**9")


def test_an_impossible_factorial_is_refused():
    with pytest.raises(CalculationError, match="limited"):
        value("factorial(1000000)")


def test_empty_input_says_so():
    with pytest.raises(CalculationError, match="Nothing to calculate"):
        value("   ")


# ── CLI ─────────────────────────────────────────────────────────────


def test_cli_prints_the_answer(runner):
    result = runner.invoke(calc_cli, ["2**5+56-1"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "87"


def test_cli_joins_arguments_the_shell_split(runner):
    assert runner.invoke(calc_cli, ["2", "+", "3"]).stdout.strip() == "5"


def test_cli_accepts_a_leading_minus(runner):
    """`pycalc -2**2` is a calculation, not a mistyped option."""
    result = runner.invoke(calc_cli, ["-2**2"])
    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == "-4"


def test_cli_options(runner):
    assert runner.invoke(calc_cli, ["255", "--base", "hex"]).stdout.strip() == "0xff"
    assert runner.invoke(calc_cli, ["1/3", "-p", "3"]).stdout.strip() == "0.333"
    assert runner.invoke(calc_cli, ["2^10", "--caret", "xor"]).stdout.strip() == "8"


def test_cli_reads_expressions_from_stdin(runner):
    result = runner.invoke(calc_cli, input="2+2\nans*10\n\n# a note\nquit\n99\n")
    assert result.exit_code == 0, result.output
    # `ans` carries the previous answer; blanks, comments and `quit` are not
    # calculations, and nothing after `quit` is read.
    assert result.stdout.split() == ["4", "40"]


def test_cli_reports_a_bad_line_from_a_pipe(runner):
    result = runner.invoke(calc_cli, input="2+2\nbad+\n")
    assert result.exit_code != 0
    assert result.stdout.startswith("4")
