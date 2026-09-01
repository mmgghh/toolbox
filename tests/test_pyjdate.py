"""Tests for Jalali/Gregorian conversion and the pyjdate CLI."""

from __future__ import annotations

import json
from datetime import date

import click
import pytest

from pytoolbox import pyjdate
from pytoolbox.pyjdate import jdate_cli

# (gregorian, jalali) pairs, including both calendars' leap-year edges.
KNOWN_DATES = [
    ((2026, 1, 4), (1404, 10, 14)),
    ((2026, 3, 21), (1405, 1, 1)),
    ((2025, 3, 21), (1404, 1, 1)),
    ((1979, 2, 11), (1357, 11, 22)),
    ((2024, 2, 29), (1402, 12, 10)),
    ((2000, 1, 1), (1378, 10, 11)),
]


@pytest.mark.parametrize(("gregorian", "jalali"), KNOWN_DATES)
def test_gregorian_to_jalali(gregorian, jalali):
    assert pyjdate.gregorian_to_jalali(*gregorian) == jalali


@pytest.mark.parametrize(("gregorian", "jalali"), KNOWN_DATES)
def test_jalali_to_gregorian(gregorian, jalali):
    assert pyjdate.jalali_to_gregorian(*jalali) == gregorian


def test_conversion_roundtrips_across_a_year():
    current = date(2025, 1, 1)
    for _ in range(400):
        jalali = pyjdate.gregorian_to_jalali(current.year, current.month, current.day)
        assert pyjdate.jalali_to_gregorian(*jalali) == (current.year, current.month, current.day)
        current = date.fromordinal(current.toordinal() + 1)


@pytest.mark.parametrize("year", [1403, 1408, 1412])
def test_known_jalali_leap_years(year):
    assert pyjdate.is_leap_jalali(year)


@pytest.mark.parametrize("year", [1404, 1405, 1406])
def test_known_jalali_common_years(year):
    assert not pyjdate.is_leap_jalali(year)


def test_days_in_month():
    assert pyjdate.days_in_month("jalali", 1404, 1) == 31
    assert pyjdate.days_in_month("jalali", 1404, 7) == 30
    assert pyjdate.days_in_month("jalali", 1404, 12) == 29
    assert pyjdate.days_in_month("jalali", 1403, 12) == 30
    assert pyjdate.days_in_month("gregorian", 2024, 2) == 29


def test_weekday_names():
    # 2026-01-04 is a Sunday, which is Yekshanbeh in the Jalali week.
    sunday = date(2026, 1, 4)
    assert pyjdate.weekday_name("gregorian", sunday) == "Sunday"
    assert pyjdate.weekday_name("jalali", sunday) == "Yekshanbeh"
    assert pyjdate.weekday_name("jalali", date(2026, 1, 3)) == "Shanbeh"


def test_parse_month_accepts_names_and_numbers():
    assert pyjdate.parse_month("feb", "gregorian") == 2
    assert pyjdate.parse_month("February", "gregorian") == 2
    assert pyjdate.parse_month("mehr", "jalali") == 7
    assert pyjdate.parse_month("7", "jalali") == 7
    with pytest.raises(click.ClickException):
        pyjdate.parse_month("brumaire", "gregorian")


def test_parse_month_accepts_persian_digits():
    """isdecimal(), not isdigit(): Persian digits must keep working."""
    assert pyjdate.parse_month("۷", "jalali") == 7


def test_parse_month_rejects_a_digit_lookalike_cleanly():
    """A superscript passes isdigit() but int() cannot parse it; this must
    raise a clean ClickException rather than an unhandled ValueError."""
    with pytest.raises(click.ClickException):
        pyjdate.parse_month("²", "jalali")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-01-04", (2026, 1, 4)),
        ("2026/01/04", (2026, 1, 4)),
        ("20260104", (2026, 1, 4)),
        ("04-01-2026", (2026, 1, 4)),
        ("Jan 04 2026", (2026, 1, 4)),
    ],
)
def test_parse_date_parts(text, expected):
    parts = pyjdate.parse_date_parts("gregorian", text)
    assert (parts.year, parts.month, parts.day) == expected


def test_parse_date_parts_accepts_persian_digits():
    """isdecimal(), not isdigit(): Persian digits must keep working."""
    parts = pyjdate.parse_date_parts("gregorian", "۲۰۲۶۰۱۰۴")
    assert (parts.year, parts.month, parts.day) == (2026, 1, 4)


def test_parse_date_parts_rejects_a_digit_lookalike_cleanly():
    """A superscript passes isdigit() but int() cannot parse it; falling
    through to the separator parse must raise ClickException, not crash."""
    with pytest.raises(click.ClickException):
        pyjdate.parse_date_parts("gregorian", "²²²²²²²²")


def test_validate_date_rejects_impossible_days():
    with pytest.raises(click.ClickException):
        pyjdate.validate_date("gregorian", 2026, 2, 30)
    with pytest.raises(click.ClickException):
        pyjdate.validate_date("jalali", 1404, 12, 30)  # 1404 is not a leap year


# ── CLI ─────────────────────────────────────────────────────────────

def test_now_prints_both_calendars(runner):
    result = runner.invoke(jdate_cli, ["now"])
    assert result.exit_code == 0
    assert "Gregorian:" in result.output
    assert "Jalali:" in result.output
    assert "Unix:" in result.output


def test_convert_gregorian_to_jalali(runner):
    result = runner.invoke(jdate_cli, ["convert", "-g", "2026-01-04 10:43"])
    assert result.exit_code == 0
    assert "1404-10-14" in result.output
    assert "Sunday" in result.output


def test_convert_json_output(runner):
    result = runner.invoke(jdate_cli, ["convert", "-g", "2026-01-04", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["jalali"]["date"] == "1404-10-14"
    assert payload["gregorian"]["weekday"] == "Sunday"


def test_convert_persian_script(runner):
    result = runner.invoke(jdate_cli, ["convert", "-j", "1404/07/01", "--fa"])
    assert result.exit_code == 0
    assert "مهر" in result.output


def test_convert_requires_exactly_one_calendar(runner):
    result = runner.invoke(jdate_cli, ["convert", "-g", "-j", "2026-01-04"])
    assert result.exit_code != 0
    assert "exactly one" in result.stderr.lower()


def test_convert_rejects_invalid_date(runner):
    result = runner.invoke(jdate_cli, ["convert", "-g", "2026-02-31"])
    assert result.exit_code != 0


def test_interval_of_a_jalali_month(runner):
    result = runner.invoke(jdate_cli, ["interval", "-j", "-y", "1404", "-m", "mehr"])
    assert result.exit_code == 0
    assert "1404-07-01" in result.output
    assert "1404-07-30" in result.output


def test_distance_between_json(runner):
    result = runner.invoke(
        jdate_cli,
        ["distance-between", "-g", "-s", "2026-01-01 00:00", "--end", "2026-01-02 12:00", "--json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["total_hours"] == 36.0
    assert payload["gregorian"]["days"] == 1
    assert payload["human"] == "1d 12h"


def test_command_prefix_is_accepted(runner):
    result = runner.invoke(jdate_cli, ["conv", "-g", "2026-01-04"])
    assert result.exit_code == 0
    assert "1404-10-14" in result.output


def test_unknown_command_suggests_alternatives(runner):
    result = runner.invoke(jdate_cli, ["converrt", "-g", "2026-01-04"])
    assert result.exit_code != 0
    assert "did you mean" in result.stderr.lower()


def test_interval_json_output(runner):
    result = runner.invoke(
        jdate_cli, ["interval", "-g", "-y", "2026", "-m", "02", "--json"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert set(payload) == {"start", "end"}
    assert payload["start"]["gregorian"]["date"] == "2026-02-01"
    assert payload["end"]["gregorian"]["date"] == "2026-02-28"
