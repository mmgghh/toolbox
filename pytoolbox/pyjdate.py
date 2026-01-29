"""Jalali/Gregorian date utilities and CLI commands."""

# pylint: disable=line-too-long,missing-function-docstring

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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


@dataclass(frozen=True)
class DateParts:
    year: int
    month: int
    day: int


@dataclass(frozen=True)
class TimeParts:
    hour: int = 0
    minute: int = 0
    second: int = 0
    microsecond: int = 0

    def has_time(self) -> bool:
        return any((self.hour, self.minute, self.second, self.microsecond))


@dataclass(frozen=True)
class DateInputOptions:
    full_date: Optional[str]
    epoch: Optional[str]
    year: Optional[int]
    month: Optional[str]
    day: Optional[int]
    hour: Optional[int]
    minute: Optional[int]
    second: Optional[int]


@dataclass(frozen=True)
class IntervalOptions:
    calendar: str
    start: Optional[str]
    end: Optional[str]
    year: Optional[int]
    month: Optional[str]
    day: Optional[int]


def date_parts_from_tuple(parts: tuple[int, int, int]) -> DateParts:
    return DateParts(*parts)


def time_parts_from_datetime(dt: datetime) -> TimeParts:
    return TimeParts(dt.hour, dt.minute, dt.second, dt.microsecond)


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
    if jump - n < 6: # type: ignore
        n = n - jump + (jump + 4) // 33 * 33 # type: ignore
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


def format_datetime(calendar: str, date_parts: DateParts, time_parts: TimeParts, show_time: bool) -> str:
    base = f"{date_parts.year:04d}-{date_parts.month:02d}-{date_parts.day:02d}"
    if show_time or time_parts.has_time():
        time_part = f"{time_parts.hour:02d}:{time_parts.minute:02d}:{time_parts.second:02d}"
        if time_parts.microsecond:
            time_part = f"{time_part}.{time_parts.microsecond:06d}"
        return f"{base} {time_part} ({month_name(calendar, date_parts.month)})"
    return f"{base} ({month_name(calendar, date_parts.month)})"


def local_timezone() -> timezone:
    tzinfo = datetime.now().astimezone().tzinfo
    if tzinfo is not None and isinstance(tzinfo, timezone):
        return tzinfo
    return timezone.utc


def validate_time(hour: int, minute: int, second: int, microsecond: int = 0) -> None:
    if hour < 0 or hour > 23:
        raise click.ClickException("Hour must be between 0 and 23.")
    if minute < 0 or minute > 59:
        raise click.ClickException("Minute must be between 0 and 59.")
    if second < 0 or second > 59:
        raise click.ClickException("Second must be between 0 and 59.")
    if microsecond < 0 or microsecond > 999999:
        raise click.ClickException("Microsecond must be between 0 and 999999.")


def parse_timezone_offset(offset: str) -> timezone:
    raw = offset.strip()
    if raw.upper() == "Z":
        return timezone.utc
    sign = 1 if raw[0] == "+" else -1
    payload = raw[1:]
    hours = minutes = "00"
    if ":" in payload:
        hours, minutes = payload.split(":", 1)
    elif len(payload) in (2, 4):
        hours = payload[:2]
        minutes = payload[2:] if len(payload) == 4 else "00"
    else:
        raise click.ClickException(f"Invalid timezone offset: {offset}")
    delta = timedelta(hours=int(hours), minutes=int(minutes)) * sign
    return timezone(delta)


def normalize_calendar(calendar: str) -> str:
    cal = calendar.lower()
    if cal in ("g", "gregorian"):
        return "gregorian"
    if cal in ("j", "jalali"):
        return "jalali"
    raise click.ClickException(f"Unknown calendar: {calendar}")


def parse_date_parts(calendar: str, date_part: str) -> DateParts:
    raw = date_part.strip().replace(",", "")
    if raw.isdigit() and len(raw) == 8:
        return DateParts(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
    sep = "-" if "-" in raw else "/" if "/" in raw else None
    if sep is None:
        if calendar == "gregorian":
            for fmt in ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y"):
                try:
                    dt = datetime.strptime(raw, fmt)
                except ValueError:
                    continue
                return DateParts(dt.year, dt.month, dt.day)
        raise click.ClickException(f"Invalid {calendar} date: {date_part}")
    parts = raw.split(sep)
    if len(parts) != 3:
        raise click.ClickException(f"Invalid {calendar} date: {date_part}")
    if len(parts[0]) == 4:
        year, month, day = parts
    elif len(parts[2]) == 4:
        day, month, year = parts
    else:
        raise click.ClickException(f"Invalid {calendar} date: {date_part}")
    return DateParts(int(year), int(month), int(day))


def split_datetime_parts(raw: str) -> tuple[str, str, str]:
    date_part = raw
    time_part = ""
    tz_part = ""
    if "T" in raw:
        date_part, time_part = raw.split("T", 1)
    elif " " in raw:
        tokens = raw.split()
        time_index = next((i for i, token in enumerate(tokens) if ":" in token), None)
        if time_index is not None:
            date_part = " ".join(tokens[:time_index])
            time_part = tokens[time_index]
            if time_index + 1 < len(tokens) and tokens[time_index + 1][0] in "+-":
                time_part = f"{time_part}{tokens[time_index + 1]}"
        else:
            date_part = raw
    if time_part:
        if time_part.upper().endswith("Z"):
            tz_part = "Z"
            time_part = time_part[:-1]
        else:
            plus = time_part.rfind("+")
            minus = time_part.rfind("-")
            idx = max(plus, minus)
            if idx > 0:
                tz_part = time_part[idx:]
                time_part = time_part[:idx]
    return date_part, time_part, tz_part


def parse_time_parts(time_part: str, value: str, calendar: str) -> tuple[TimeParts, bool]:
    if not time_part:
        return TimeParts(), False
    time_provided = True
    microsecond = 0
    if "." in time_part:
        time_part, micro_str = time_part.split(".", 1)
        micro_str = micro_str.ljust(6, "0")[:6]
        microsecond = int(micro_str)
    parts = time_part.split(":")
    if len(parts) == 1:
        hour = int(parts[0])
        minute = 0
        second = 0
    elif len(parts) == 2:
        hour = int(parts[0])
        minute = int(parts[1])
        second = 0
    elif len(parts) == 3:
        hour = int(parts[0])
        minute = int(parts[1])
        second = int(parts[2])
    else:
        raise click.ClickException(f"Invalid {calendar} time: {value}")
    return TimeParts(hour, minute, second, microsecond), time_provided


def parse_full_date(calendar: str, value: str) -> tuple[DateParts, TimeParts, timezone, bool]:
    raw = value.strip()
    date_part, time_part, tz_part = split_datetime_parts(raw)
    date_parts = parse_date_parts(calendar, date_part)
    time_parts, time_provided = parse_time_parts(time_part, value, calendar)
    tzinfo = parse_timezone_offset(tz_part) if tz_part else local_timezone()
    return date_parts, time_parts, tzinfo, time_provided


def parse_epoch(value: str) -> datetime:
    try:
        ts = float(value.strip())
    except ValueError as exc:
        raise click.ClickException(f"Invalid unix timestamp: {value}") from exc
    return datetime.fromtimestamp(ts, tz=local_timezone())


def is_epoch_candidate(value: str) -> bool:
    raw = value.strip()
    if raw.startswith(("+", "-")):
        raw = raw[1:]
    if not raw:
        return False
    if "." in raw:
        left, right = raw.split(".", 1)
        return left.isdigit() and right.isdigit()
    return raw.isdigit() and len(raw) >= 10


def parse_interval_endpoint(calendar: str, value: str) -> tuple[datetime, bool]:
    if is_epoch_candidate(value):
        return parse_epoch(value), True
    date_parts, time_parts, tzinfo, time_provided = parse_full_date(calendar, value)
    validate_date(calendar, date_parts.year, date_parts.month, date_parts.day)
    validate_time(time_parts.hour, time_parts.minute, time_parts.second, time_parts.microsecond)
    dt = build_datetime(calendar, date_parts, time_parts, tzinfo)
    show_time = time_provided or time_parts.has_time()
    return dt, show_time


def build_datetime(calendar: str, date_parts: DateParts, time_parts: TimeParts, tzinfo: timezone) -> datetime:
    year, month, day = date_parts.year, date_parts.month, date_parts.day
    if calendar == "jalali":
        year, month, day = jalali_to_gregorian(year, month, day)
    tzinfo = tzinfo or local_timezone()
    return datetime(
        year,
        month,
        day,
        time_parts.hour,
        time_parts.minute,
        time_parts.second,
        time_parts.microsecond,
        tzinfo=tzinfo,
    )


def format_unix_timestamp(dt: datetime) -> str:
    timestamp = dt.timestamp()
    if dt.microsecond:
        return f"{timestamp:.6f}".rstrip("0").rstrip(".")
    return str(int(timestamp))


def format_total_value(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def days_in_month(calendar: str, year: int, month: int) -> int:
    if calendar == "gregorian":
        month_days = [0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        if is_leap_gregorian(year):
            month_days[2] = 29
    else:
        month_days = [0, 31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29]
        if is_leap_jalali(year):
            month_days[12] = 30
    return month_days[month]


def calendar_date_from_gregorian(calendar: str, g_date: date) -> tuple[int, int, int]:
    if calendar == "gregorian":
        return g_date.year, g_date.month, g_date.day
    return gregorian_to_jalali(g_date.year, g_date.month, g_date.day)


def _normalize_local_datetimes(start_dt: datetime, end_dt: datetime) -> tuple[datetime, datetime]:
    start_local = start_dt.astimezone(local_timezone()).replace(microsecond=0)
    end_local = end_dt.astimezone(local_timezone()).replace(microsecond=0)
    if end_local < start_local:
        start_local, end_local = end_local, start_local
    return start_local, end_local


def _time_diff_parts(start_local: datetime, end_local: datetime) -> tuple[int, int, int, date]:
    start_time_seconds = start_local.hour * 3600 + start_local.minute * 60 + start_local.second
    end_time_seconds = end_local.hour * 3600 + end_local.minute * 60 + end_local.second
    end_date = end_local.date()
    if end_time_seconds < start_time_seconds:
        end_date = (end_local - timedelta(days=1)).date()
        end_time_seconds += 86400
    time_diff = end_time_seconds - start_time_seconds
    hours = time_diff // 3600
    minutes = (time_diff % 3600) // 60
    seconds = time_diff % 60
    return int(hours), int(minutes), int(seconds), end_date


def _date_diff_parts(calendar: str, start_date: date, end_date: date) -> tuple[int, int, int]:
    start_y, start_m, start_d = calendar_date_from_gregorian(calendar, start_date)
    end_y, end_m, end_d = calendar_date_from_gregorian(calendar, end_date)
    if end_d < start_d:
        if end_m == 1:
            end_y -= 1
            end_m = 12
        else:
            end_m -= 1
        end_d += days_in_month(calendar, end_y, end_m)
    if end_m < start_m:
        end_y -= 1
        end_m += 12
    years = end_y - start_y
    months = end_m - start_m
    days = end_d - start_d
    return years, months, days


def diff_calendar_components(
    calendar: str,
    start_dt: datetime,
    end_dt: datetime,
) -> tuple[int, int, int, int, int, int]:
    start_local, end_local = _normalize_local_datetimes(start_dt, end_dt)
    hours, minutes, seconds, end_date = _time_diff_parts(start_local, end_local)
    years, months, days = _date_diff_parts(calendar, start_local.date(), end_date)
    return years, months, days, hours, minutes, seconds


def parse_input_datetime(calendar: str, options: DateInputOptions) -> datetime:
    if options.epoch:
        if any(
            value is not None
            for value in (options.full_date, options.year, options.month, options.day, options.hour, options.minute, options.second)
        ):
            raise click.ClickException("Use --epoch alone; it is incompatible with other date inputs.")
        return parse_epoch(options.epoch)
    if options.full_date:
        if any(value is not None for value in (options.year, options.month, options.day, options.hour, options.minute, options.second)):
            raise click.ClickException("Use --full-date or -y/-m/-d options, not both.")
        date_parts, time_parts, tzinfo, _time_provided = parse_full_date(calendar, options.full_date)
        validate_date(calendar, date_parts.year, date_parts.month, date_parts.day)
        validate_time(time_parts.hour, time_parts.minute, time_parts.second, time_parts.microsecond)
        return build_datetime(calendar, date_parts, time_parts, tzinfo)
    if options.year is None or options.month is None or options.day is None:
        raise click.ClickException("Year, month, and day are required when --full-date is not used.")
    m = parse_month(options.month, calendar)
    validate_date(calendar, options.year, m, options.day)
    time_parts = TimeParts(options.hour or 0, options.minute or 0, options.second or 0, 0)
    validate_time(time_parts.hour, time_parts.minute, time_parts.second, time_parts.microsecond)
    date_parts = DateParts(options.year, m, options.day)
    return build_datetime(calendar, date_parts, time_parts, local_timezone())


def _resolve_conversion_inputs(
    calendar: str,
    options: DateInputOptions,
) -> tuple[DateParts, TimeParts, timezone, bool]:
    if options.full_date:
        date_parts, time_parts, tzinfo, time_provided = parse_full_date(calendar, options.full_date)
        validate_date(calendar, date_parts.year, date_parts.month, date_parts.day)
        validate_time(time_parts.hour, time_parts.minute, time_parts.second, time_parts.microsecond)
        return date_parts, time_parts, tzinfo, time_provided

    if options.year is None or options.month is None or options.day is None:
        raise click.ClickException("Year, month, and day are required when --full-date is not used.")
    m = parse_month(options.month, calendar)
    validate_date(calendar, options.year, m, options.day)
    time_provided = any(value is not None for value in (options.hour, options.minute, options.second))
    time_parts = TimeParts(options.hour or 0, options.minute or 0, options.second or 0, 0)
    tzinfo = local_timezone()
    validate_time(time_parts.hour, time_parts.minute, time_parts.second, time_parts.microsecond)
    return DateParts(options.year, m, options.day), time_parts, tzinfo, time_provided


def print_distance(start_dt: datetime, end_dt: datetime) -> None:
    start_utc = start_dt.astimezone(timezone.utc)
    end_utc = end_dt.astimezone(timezone.utc)
    if end_utc < start_utc:
        start_utc, end_utc = end_utc, start_utc
        start_dt, end_dt = end_dt, start_dt
    delta = end_utc - start_utc
    total_seconds = delta.total_seconds()
    total_days = total_seconds / 86400

    g_parts = diff_calendar_components("gregorian", start_dt, end_dt)
    j_parts = diff_calendar_components("jalali", start_dt, end_dt)

    click.echo(
        "Gregorian: "
        f"{g_parts[0]} years, {g_parts[1]} months, {g_parts[2]} days, "
        f"{g_parts[3]} hours, {g_parts[4]} minutes, {g_parts[5]} seconds"
    )
    click.echo(
        "Jalali:    "
        f"{j_parts[0]} years, {j_parts[1]} months, {j_parts[2]} days, "
        f"{j_parts[3]} hours, {j_parts[4]} minutes, {j_parts[5]} seconds"
    )
    click.echo(f"Total days:    {format_total_value(total_days)}")
    click.echo(f"Total seconds: {format_total_value(total_seconds)}")


def convert_from(calendar: str, year: int, month: int, day: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if calendar == "gregorian":
        j = gregorian_to_jalali(year, month, day)
        return (year, month, day), j
    g = jalali_to_gregorian(year, month, day)
    return g, (year, month, day)


def _emit_datetime_block(label: Optional[str], dt: datetime) -> None:
    g_parts = DateParts(dt.year, dt.month, dt.day)
    j_parts = date_parts_from_tuple(gregorian_to_jalali(g_parts.year, g_parts.month, g_parts.day))
    time_parts = time_parts_from_datetime(dt)
    prefix = ""
    if label:
        click.echo(label)
        prefix = "  "
    click.echo(f"{prefix}Gregorian: {format_datetime('gregorian', g_parts, time_parts, True)}")
    click.echo(f"{prefix}Jalali:    {format_datetime('jalali', j_parts, time_parts, True)}")
    click.echo(f"{prefix}Unix:      {format_unix_timestamp(dt)}")


def _resolve_interval_dates(
    calendar: str,
    year: int,
    month: Optional[str],
    day: Optional[int],
) -> tuple[DateParts, DateParts]:
    if month is None:
        start_date = DateParts(year, 1, 1)
        if calendar == "gregorian":
            end_date = DateParts(year, 12, 31)
        else:
            end_day = 30 if is_leap_jalali(year) else 29
            end_date = DateParts(year, 12, end_day)
        return start_date, end_date

    m = parse_month(month, calendar)
    if day is None:
        start_date = DateParts(year, m, 1)
        end_date = DateParts(year, m, days_in_month(calendar, year, m))
        return start_date, end_date

    validate_date(calendar, year, m, day)
    start_date = DateParts(year, m, day)
    return start_date, start_date


@click.group()
def jdate_cli():
    return None


@click.command()
def current():
    """Print current date in both calendars."""
    now = datetime.now().astimezone().replace(microsecond=0)
    g = DateParts(now.year, now.month, now.day)
    j = date_parts_from_tuple(gregorian_to_jalali(g.year, g.month, g.day))
    time_parts = TimeParts(now.hour, now.minute, now.second, 0)
    click.echo(
        f"Gregorian: {format_datetime('gregorian', g, time_parts, True)}"
    )
    click.echo(
        f"Jalali:    {format_datetime('jalali', j, time_parts, True)}"
    )
    click.echo(f"Unix:      {format_unix_timestamp(now)}")


@click.command()
@click.option(
    "-c",
    "--calendar",
    type=click.Choice(["gregorian", "jalali", "g", "j"], case_sensitive=False),
    required=True,
    help="Input calendar (gregorian|jalali, shortcuts: g|j).",
)
@click.option(
    "--full-date",
    type=str,
    required=False,
    help=(
        "Full date/time string. Examples: "
        "'2026-01-04 10:43:45.024995+03:30', '2026/01/04 10:43', "
        "'2026-01-04', 'Jan 04 2026', '1404/10/14 10:44:46'."
    ),
)
@click.option(
    "-e",
    "--epoch",
    type=str,
    required=False,
    help="Unix timestamp (seconds, optional fraction). Displayed in local timezone.",
)
@click.option("-y", "--year", type=int, required=False, help="Year number.")
@click.option("-m", "--month", type=str, required=False, help="Month number or name.")
@click.option("-d", "--day", type=int, required=False, help="Day of month.")
@click.option("-H", "--hour", type=int, required=False, default=None, help="Hour (0-23).")
@click.option("--minute", type=int, required=False, default=None, help="Minute (0-59).")
@click.option("--second", type=int, required=False, default=None, help="Second (0-59).")
def convert(**kwargs):
    """Convert a date between Jalali and Gregorian."""
    calendar = kwargs.pop("calendar")
    options = DateInputOptions(**kwargs)
    cal = normalize_calendar(calendar)
    if options.epoch:
        if any(
            value is not None
            for value in (options.full_date, options.year, options.month, options.day, options.hour, options.minute, options.second)
        ):
            raise click.ClickException("Use --epoch alone; it is incompatible with other date inputs.")
        dt = parse_epoch(options.epoch)
        _emit_datetime_block(None, dt)
        return
    if options.full_date and any(
        value is not None
        for value in (options.year, options.month, options.day, options.hour, options.minute, options.second)
    ):
        raise click.ClickException("Use --full-date or -y/-m/-d options, not both.")

    date_parts, time_parts, tzinfo, time_provided = _resolve_conversion_inputs(cal, options)
    g_tuple, j_tuple = convert_from(cal, date_parts.year, date_parts.month, date_parts.day)
    g = date_parts_from_tuple(g_tuple)
    j = date_parts_from_tuple(j_tuple)
    show_time = time_provided or time_parts.has_time()
    click.echo(f"Gregorian: {format_datetime('gregorian', g, time_parts, show_time)}")
    click.echo(f"Jalali:    {format_datetime('jalali', j, time_parts, show_time)}")
    ts = build_datetime(cal, date_parts, time_parts, tzinfo)
    click.echo(f"Unix:      {format_unix_timestamp(ts)}")


@click.command()
@click.option(
    "-c",
    "--calendar",
    type=click.Choice(["gregorian", "jalali", "g", "j"], case_sensitive=False),
    required=True,
    help="Input calendar (gregorian|jalali, shortcuts: g|j).",
)
@click.option(
    "-s",
    "--start",
    type=str,
    required=False,
    help=(
        "Interval start (unix timestamp or full date/time string). "
        "Must be provided with --end."
    ),
)
@click.option(
    "-e",
    "--end",
    type=str,
    required=False,
    help=(
        "Interval end (unix timestamp or full date/time string). "
        "Must be provided with --start."
    ),
)
@click.option("-y", "--year", type=int, required=False, help="Year number.")
@click.option("-m", "--month", type=str, required=False, help="Month number or name.")
@click.option("-d", "--day", type=int, required=False, help="Day of month.")
def interval(**kwargs):
    """Show period start/end in both calendars."""
    options = IntervalOptions(**kwargs)
    cal = normalize_calendar(options.calendar)
    if (options.start is None) != (options.end is None):
        raise click.ClickException("Start and end must be provided together.")
    if options.start is not None and any(value is not None for value in (options.year, options.month, options.day)):
        raise click.ClickException("Start/end are incompatible with year/month/day inputs.")

    if options.start is not None:
        start_dt, _start_time_provided = parse_interval_endpoint(cal, options.start)
        end_dt, _end_time_provided = parse_interval_endpoint(cal, options.end)  # type: ignore[arg-type]
        _emit_datetime_block("Start:", start_dt)
        _emit_datetime_block("End:", end_dt)
        return

    if options.year is None:
        raise click.ClickException("Year is required when start/end are not provided.")
    if options.month is None and options.day is not None:
        raise click.ClickException("Day requires month.")

    start_date, end_date = _resolve_interval_dates(cal, options.year, options.month, options.day)
    start_ts = build_datetime(cal, start_date, TimeParts(0, 0, 0, 0), local_timezone())
    end_ts = build_datetime(cal, end_date, TimeParts(23, 59, 59, 0), local_timezone())
    _emit_datetime_block("Start:", start_ts)
    _emit_datetime_block("End:", end_ts)


@click.command()
@click.option(
    "-c",
    "--calendar",
    type=click.Choice(["gregorian", "jalali", "g", "j"], case_sensitive=False),
    required=True,
    help="Input calendar (gregorian|jalali, shortcuts: g|j).",
)
@click.option(
    "--full-date",
    type=str,
    required=False,
    help=(
        "Full date/time string. Examples: "
        "'2026-01-04 10:43:45.024995+03:30', '2026/01/04 10:43', "
        "'2026-01-04', 'Jan 04 2026', '1404/10/14 10:44:46'."
    ),
)
@click.option(
    "-e",
    "--epoch",
    type=str,
    required=False,
    help="Unix timestamp (seconds, optional fraction). Displayed in local timezone.",
)
@click.option("-y", "--year", type=int, required=False, help="Year number.")
@click.option("-m", "--month", type=str, required=False, help="Month number or name.")
@click.option("-d", "--day", type=int, required=False, help="Day of month.")
@click.option("-H", "--hour", type=int, required=False, default=None, help="Hour (0-23).")
@click.option("--minute", type=int, required=False, default=None, help="Minute (0-59).")
@click.option("--second", type=int, required=False, default=None, help="Second (0-59).")
def distance(**kwargs):
    """Show time difference between now and the input date."""
    calendar = kwargs.pop("calendar")
    options = DateInputOptions(**kwargs)
    cal = normalize_calendar(calendar)
    input_dt = parse_input_datetime(cal, options)
    now = datetime.now().astimezone()
    print_distance(now, input_dt)


@click.command(name="distance-between")
@click.option(
    "-c",
    "--calendar",
    type=click.Choice(["gregorian", "jalali", "g", "j"], case_sensitive=False),
    required=True,
    help="Input calendar (gregorian|jalali, shortcuts: g|j).",
)
@click.option(
    "-s",
    "--start",
    type=str,
    required=True,
    help=(
        "Start date/time (unix timestamp or full date/time string). "
        "Examples: '2026-01-04 10:43:45+03:30', '2026-01-04', '1404/10/14 10:44:46'."
    ),
)
@click.option(
    "-e",
    "--end",
    type=str,
    required=True,
    help=(
        "End date/time (unix timestamp or full date/time string). "
        "Examples: '2026-02-01 08:00', '2026-02-01', '1404/11/12 12:00:00'."
    ),
)
def distance_between(calendar: str, start: str, end: str):
    """Show time difference between two dates."""
    cal = normalize_calendar(calendar)
    start_dt, _ = parse_interval_endpoint(cal, start)
    end_dt, _ = parse_interval_endpoint(cal, end)
    print_distance(start_dt, end_dt)


for cmd in (current, convert, interval, distance, distance_between):
    jdate_cli.add_command(cmd)


if __name__ == "__main__":
    jdate_cli()
