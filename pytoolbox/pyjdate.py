from __future__ import annotations

from datetime import date
from typing import Optional

import click

GREGORIAN_MONTHS = [
    None,
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]

JALALI_MONTHS = [
    None,
    "Farvardin",
    "Ordibehesht",
    "Khordad",
    "Tir",
    "Mordad",
    "Shahrivar",
    "Mehr",
    "Aban",
    "Azar",
    "Dey",
    "Bahman",
    "Esfand",
]

GREGORIAN_MONTH_ALIASES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

JALALI_MONTH_ALIASES = {
    "far": 1,
    "farvardin": 1,
    "ord": 2,
    "ordibehesht": 2,
    "kho": 3,
    "khordad": 3,
    "tir": 4,
    "mor": 5,
    "mordad": 5,
    "sha": 6,
    "shahrivar": 6,
    "meh": 7,
    "mehr": 7,
    "aba": 8,
    "aban": 8,
    "aza": 9,
    "azar": 9,
    "dey": 10,
    "bah": 11,
    "bahman": 11,
    "esf": 12,
    "esfand": 12,
}


def is_leap_gregorian(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def is_leap_jalali(year: int) -> bool:
    # Jalaali leap year algorithm (from jalaali-js).
    breaks = [
        -61, 9, 38, 199, 426, 686, 756, 818, 1111, 1181, 1210, 1635, 2060,
        2097, 2192, 2262, 2324, 2394, 2456, 3178,
    ]
    leap_j = -14
    jp = breaks[0]
    jm = 0
    for jm in breaks[1:]:
        jump = jm - jp
        if year < jm:
            break
        leap_j += jump // 33 * 8 + (jump % 33 + 3) // 4
        jp = jm
    n = year - jp
    leap_j += n // 33 * 8 + (n % 33 + 3) // 4
    if jump - n < 6:
        n = n - jump + (jump + 4) // 33 * 33
    leap = ((n + 1) % 33 - 1) % 4
    if leap == -1:
        leap = 4
    return leap == 0


def gregorian_to_jalali(gy: int, gm: int, gd: int) -> tuple[int, int, int]:
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    gy2 = gy - 1600
    gm2 = gm - 1
    gd2 = gd - 1

    g_day_no = 365 * gy2 + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400
    g_day_no += g_d_m[gm2] + gd2
    if gm > 2 and is_leap_gregorian(gy):
        g_day_no += 1

    j_day_no = g_day_no - 79
    j_np = j_day_no // 12053
    j_day_no %= 12053

    jy = 979 + 33 * j_np + 4 * (j_day_no // 1461)
    j_day_no %= 1461

    if j_day_no >= 366:
        jy += (j_day_no - 1) // 365
        j_day_no = (j_day_no - 1) % 365

    if j_day_no < 186:
        jm = 1 + j_day_no // 31
        jd = 1 + (j_day_no % 31)
    else:
        jm = 7 + (j_day_no - 186) // 30
        jd = 1 + (j_day_no - 186) % 30

    return jy, jm, jd


def jalali_to_gregorian(jy: int, jm: int, jd: int) -> tuple[int, int, int]:
    jy += 1595
    days = -355668 + (365 * jy) + (jy // 33) * 8 + ((jy % 33 + 3) // 4) + jd
    if jm < 7:
        days += (jm - 1) * 31
    else:
        days += (jm - 7) * 30 + 186

    gy = 400 * (days // 146097)
    days %= 146097
    if days > 36524:
        gy += 100 * ((days - 1) // 36524)
        days = (days - 1) % 36524
        if days >= 365:
            days += 1
    gy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        gy += (days - 1) // 365
        days = (days - 1) % 365
    gd = days + 1

    month_days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if is_leap_gregorian(gy):
        month_days[2] = 29
    gm = 1
    while gm <= 12 and gd > month_days[gm]:
        gd -= month_days[gm]
        gm += 1

    return gy, gm, gd


def parse_month(month: str | int, calendar: str) -> int:
    if isinstance(month, int):
        m = month
    else:
        raw = month.strip()
        if raw.isdigit():
            m = int(raw)
        else:
            key = raw.lower()
            aliases = GREGORIAN_MONTH_ALIASES if calendar == "gregorian" else JALALI_MONTH_ALIASES
            if key not in aliases:
                raise click.ClickException(f"Unknown {calendar} month: {month}")
            m = aliases[key]

    if not 1 <= m <= 12:
        raise click.ClickException(f"Month out of range: {month}")
    return m


def month_name(calendar: str, month: int) -> str:
    return (GREGORIAN_MONTHS if calendar == "gregorian" else JALALI_MONTHS)[month]


def validate_date(calendar: str, year: int, month: int, day: int) -> None:
    if month < 1 or month > 12:
        raise click.ClickException("Month must be between 1 and 12.")
    if calendar == "gregorian":
        month_days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        if is_leap_gregorian(year):
            month_days[2] = 29
    else:
        month_days = [0, 31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
        if is_leap_jalali(year):
            month_days[12] = 30
    if day < 1 or day > month_days[month]:
        raise click.ClickException(f"Day out of range for {calendar} {year}-{month}.")


def format_date(calendar: str, year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d} ({month_name(calendar, month)})"


def convert_from(calendar: str, year: int, month: int, day: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if calendar == "gregorian":
        j = gregorian_to_jalali(year, month, day)
        return (year, month, day), j
    g = jalali_to_gregorian(year, month, day)
    return g, (year, month, day)


@click.group()
def jdate_cli():
    pass


@click.command()
def current():
    """Print current date in both calendars."""
    today = date.today()
    g = (today.year, today.month, today.day)
    j = gregorian_to_jalali(*g)
    click.echo(f"Gregorian: {format_date('gregorian', *g)}")
    click.echo(f"Jalali:    {format_date('jalali', *j)}")


@click.command()
@click.option(
    "-c",
    "--calendar",
    type=click.Choice(["gregorian", "jalali"], case_sensitive=False),
    required=True,
    help="Input calendar.",
)
@click.option("-y", "--year", type=int, required=True)
@click.option("-m", "--month", type=str, required=True, help="Month number or name.")
@click.option("-d", "--day", type=int, required=True)
def convert(calendar: str, year: int, month: str, day: int):
    """Convert a date between Jalali and Gregorian."""
    cal = calendar.lower()
    m = parse_month(month, cal)
    validate_date(cal, year, m, day)
    g, j = convert_from(cal, year, m, day)
    click.echo(f"Gregorian: {format_date('gregorian', *g)}")
    click.echo(f"Jalali:    {format_date('jalali', *j)}")


@click.command()
@click.option(
    "-c",
    "--calendar",
    type=click.Choice(["gregorian", "jalali"], case_sensitive=False),
    required=True,
    help="Input calendar.",
)
@click.option("-y", "--year", type=int, required=True)
@click.option("-m", "--month", type=str, required=False, help="Month number or name.")
@click.option("-d", "--day", type=int, required=False)
def interval(calendar: str, year: int, month: Optional[str], day: Optional[int]):
    """Show period start/end in both calendars."""
    cal = calendar.lower()
    if month is None and day is not None:
        raise click.ClickException("Day requires month.")

    if month is None:
        start = (year, 1, 1)
        if cal == "gregorian":
            end = (year, 12, 31)
        else:
            end_day = 30 if is_leap_jalali(year) else 29
            end = (year, 12, end_day)
    else:
        m = parse_month(month, cal)
        if day is None:
            if cal == "gregorian":
                month_days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
                if is_leap_gregorian(year):
                    month_days[2] = 29
            else:
                month_days = [0, 31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
                if is_leap_jalali(year):
                    month_days[12] = 30
            start = (year, m, 1)
            end = (year, m, month_days[m])
        else:
            validate_date(cal, year, m, day)
            start = (year, m, day)
            end = (year, m, day)

    g_start, j_start = convert_from(cal, *start)
    g_end, j_end = convert_from(cal, *end)

    click.echo("Start:")
    click.echo(f"  Gregorian: {format_date('gregorian', *g_start)}")
    click.echo(f"  Jalali:    {format_date('jalali', *j_start)}")
    click.echo("End:")
    click.echo(f"  Gregorian: {format_date('gregorian', *g_end)}")
    click.echo(f"  Jalali:    {format_date('jalali', *j_end)}")


for cmd in (current, convert, interval):
    jdate_cli.add_command(cmd)


if __name__ == "__main__":
    jdate_cli()
