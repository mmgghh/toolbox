"""Jalali/Gregorian date conversion, intervals and distances (``pyjdate``)."""

# pylint: disable=line-too-long,missing-function-docstring

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import click

from pytoolbox.core import console
from pytoolbox.core.intervals import (
    TOKEN_RE,
    UNIT_ALIASES,
    IntervalDelta,
    apply_interval,
    format_duration,
    format_total_value,
    parse_pg_interval,
    shift_months,
)
from pytoolbox.core.options import (
    CONTEXT_SETTINGS,
    AliasedGroup,
    json_option,
    version_option,
)

__all__ = [
    "TOKEN_RE",
    "UNIT_ALIASES",
    "IntervalDelta",
    "apply_interval",
    "format_total_value",
    "parse_pg_interval",
    "shift_months",
]

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


#: Indexed by ``datetime.date.weekday()`` (Monday == 0).
GREGORIAN_WEEKDAYS = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

#: The Jalali week starts on Saturday, so the Python weekday index is rotated
#: by two to line the two sequences up.
JALALI_WEEKDAYS = [
    "Shanbeh",
    "Yekshanbeh",
    "Doshanbeh",
    "Seshanbeh",
    "Chaharshanbeh",
    "Panjshanbeh",
    "Jomeh",
]

JALALI_MONTHS_FA = [
    None,
    "فروردین",
    "اردیبهشت",
    "خرداد",
    "تیر",
    "مرداد",
    "شهریور",
    "مهر",
    "آبان",
    "آذر",
    "دی",
    "بهمن",
    "اسفند",
]

JALALI_WEEKDAYS_FA = ["شنبه", "یکشنبه", "دوشنبه", "سه‌شنبه", "چهارشنبه", "پنجشنبه", "جمعه"]


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
    interval: Optional[str]
    epoch: Optional[str]
    year: Optional[int]
    month: Optional[str]
    day: Optional[int]
    hour: Optional[int]
    minute: Optional[int]
    second: Optional[int]


@dataclass(frozen=True)
class IntervalOptions:
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


def month_name(calendar: str, month: int, persian_script: bool = False) -> str:
    if calendar == "gregorian":
        return GREGORIAN_MONTHS[month]
    return (JALALI_MONTHS_FA if persian_script else JALALI_MONTHS)[month]


def weekday_name(calendar: str, g_date: date, persian_script: bool = False) -> str:
    """Weekday name for a Gregorian date, in the requested calendar's naming."""
    index = g_date.weekday()
    if calendar == "gregorian":
        return GREGORIAN_WEEKDAYS[index]
    names = JALALI_WEEKDAYS_FA if persian_script else JALALI_WEEKDAYS
    return names[(index + 2) % 7]


def gregorian_date_of(calendar: str, parts: DateParts) -> date:
    """Return the Gregorian ``date`` for date parts in either calendar."""
    if calendar == "gregorian":
        return date(parts.year, parts.month, parts.day)
    return date(*jalali_to_gregorian(parts.year, parts.month, parts.day))


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


def format_datetime(
    calendar: str,
    date_parts: DateParts,
    time_parts: TimeParts,
    show_time: bool,
    show_weekday: bool = False,
    persian_script: bool = False,
) -> str:
    base = f"{date_parts.year:04d}-{date_parts.month:02d}-{date_parts.day:02d}"
    labels = [month_name(calendar, date_parts.month, persian_script)]
    if show_weekday:
        try:
            labels.append(weekday_name(calendar, gregorian_date_of(calendar, date_parts), persian_script))
        except ValueError:
            # Out-of-range dates still deserve a readable month label.
            pass
    suffix = f"({', '.join(labels)})"
    if show_time or time_parts.has_time():
        time_part = f"{time_parts.hour:02d}:{time_parts.minute:02d}:{time_parts.second:02d}"
        if time_parts.microsecond:
            time_part = f"{time_part}.{time_parts.microsecond:06d}"
        return f"{base} {time_part} {suffix}"
    return f"{base} {suffix}"


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


def parse_calendar_endpoint(calendar: str, value: str) -> tuple[datetime, bool]:
    if is_epoch_candidate(value):
        raise click.ClickException("Use -e/--epoch for Unix timestamp inputs.")
    return parse_interval_endpoint(calendar, value)


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


def detect_date_input_mode(options: DateInputOptions) -> str:
    has_interval = options.interval is not None
    has_full_date = options.full_date is not None
    has_epoch = options.epoch is not None
    has_parts = any(
        value is not None
        for value in (options.year, options.month, options.day, options.hour, options.minute, options.second)
    )
    selected = [name for name, active in (
        ("interval", has_interval),
        ("date", has_full_date),
        ("epoch", has_epoch),
        ("parts", has_parts),
    ) if active]
    if not selected:
        raise click.ClickException(
            "Provide one input: --interval, VALUE, -e VALUE, or -y/-m/-d (-H/--minute/--second optional)."
        )
    if len(selected) > 1:
        raise click.ClickException(
            "Provide only one of --interval, VALUE, -e VALUE, or -y/-m/-d (-H/--minute/--second optional)."
        )
    return selected[0]


def parse_input_datetime(calendar: Optional[str], options: DateInputOptions, now: Optional[datetime] = None) -> datetime:
    mode = detect_date_input_mode(options)
    if mode == "interval":
        base = now or datetime.now().astimezone()
        return apply_interval(base, parse_pg_interval(options.interval or ""))
    if mode == "epoch":
        return parse_epoch(options.epoch or "")

    if calendar is None:
        raise click.ClickException("Calendar is required for VALUE and -y/-m/-d inputs.")
    cal = normalize_calendar(calendar)

    if mode == "date":
        date_parts, time_parts, tzinfo, _time_provided = parse_full_date(cal, options.full_date or "")
        validate_date(cal, date_parts.year, date_parts.month, date_parts.day)
        validate_time(time_parts.hour, time_parts.minute, time_parts.second, time_parts.microsecond)
        return build_datetime(cal, date_parts, time_parts, tzinfo)

    if options.year is None or options.month is None or options.day is None:
        raise click.ClickException("Year, month, and day are required when VALUE is not used.")
    m = parse_month(options.month, cal)
    validate_date(cal, options.year, m, options.day)
    time_parts = TimeParts(options.hour or 0, options.minute or 0, options.second or 0, 0)
    validate_time(time_parts.hour, time_parts.minute, time_parts.second, time_parts.microsecond)
    date_parts = DateParts(options.year, m, options.day)
    return build_datetime(cal, date_parts, time_parts, local_timezone())


def parse_distance_between_endpoint(value: str, calendar: Optional[str], now: datetime, label: str) -> datetime:
    if calendar is not None:
        try:
            dt, _ = parse_interval_endpoint(calendar, value)
            return dt
        except click.ClickException:
            return apply_interval(now, parse_pg_interval(value))

    if is_epoch_candidate(value):
        return parse_epoch(value)
    try:
        return apply_interval(now, parse_pg_interval(value))
    except click.ClickException as exc:
        raise click.ClickException(
            f"Invalid {label} value: {value}. Use -j/--jalali or -g/--gregorian for full-date values."
        ) from exc


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
        raise click.ClickException("Year, month, and day are required when VALUE is not used.")
    m = parse_month(options.month, calendar)
    validate_date(calendar, options.year, m, options.day)
    time_provided = any(value is not None for value in (options.hour, options.minute, options.second))
    time_parts = TimeParts(options.hour or 0, options.minute or 0, options.second or 0, 0)
    tzinfo = local_timezone()
    validate_time(time_parts.hour, time_parts.minute, time_parts.second, time_parts.microsecond)
    return DateParts(options.year, m, options.day), time_parts, tzinfo, time_provided


def print_distance(start_dt: datetime, end_dt: datetime, as_json: bool = False) -> None:
    start_utc = start_dt.astimezone(timezone.utc)
    end_utc = end_dt.astimezone(timezone.utc)
    if end_utc < start_utc:
        start_utc, end_utc = end_utc, start_utc
        start_dt, end_dt = end_dt, start_dt
    delta = end_utc - start_utc
    total_seconds = delta.total_seconds()
    total_hours = total_seconds / 3600
    total_days = total_seconds / 86400

    g_parts = diff_calendar_components("gregorian", start_dt, end_dt)
    j_parts = diff_calendar_components("jalali", start_dt, end_dt)

    if as_json:
        keys = ("years", "months", "days", "hours", "minutes", "seconds")
        console.emit_json(
            {
                "start": start_dt.isoformat(),
                "end": end_dt.isoformat(),
                "gregorian": dict(zip(keys, g_parts)),
                "jalali": dict(zip(keys, j_parts)),
                "total_days": total_days,
                "total_hours": total_hours,
                "total_seconds": total_seconds,
                "human": format_duration(total_seconds),
            }
        )
        return

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
    click.echo(f"Total hours:   {format_total_value(total_hours)}")
    click.echo(f"Total seconds: {format_total_value(total_seconds)}")
    click.echo(f"Human:         {format_duration(total_seconds)}")


def convert_from(calendar: str, year: int, month: int, day: int) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    if calendar == "gregorian":
        j = gregorian_to_jalali(year, month, day)
        return (year, month, day), j
    g = jalali_to_gregorian(year, month, day)
    return g, (year, month, day)


def datetime_payload(dt: datetime, persian_script: bool = False) -> dict:
    """Both calendars plus the epoch value for one datetime, as plain data."""
    g_parts = DateParts(dt.year, dt.month, dt.day)
    j_parts = date_parts_from_tuple(gregorian_to_jalali(g_parts.year, g_parts.month, g_parts.day))
    time_parts = time_parts_from_datetime(dt)
    g_date = date(dt.year, dt.month, dt.day)
    return {
        "gregorian": {
            "date": f"{g_parts.year:04d}-{g_parts.month:02d}-{g_parts.day:02d}",
            "year": g_parts.year,
            "month": g_parts.month,
            "day": g_parts.day,
            "month_name": month_name("gregorian", g_parts.month),
            "weekday": weekday_name("gregorian", g_date),
            "formatted": format_datetime("gregorian", g_parts, time_parts, True, True),
        },
        "jalali": {
            "date": f"{j_parts.year:04d}-{j_parts.month:02d}-{j_parts.day:02d}",
            "year": j_parts.year,
            "month": j_parts.month,
            "day": j_parts.day,
            "month_name": month_name("jalali", j_parts.month, persian_script),
            "weekday": weekday_name("jalali", g_date, persian_script),
            "formatted": format_datetime("jalali", j_parts, time_parts, True, True, persian_script),
        },
        "time": f"{time_parts.hour:02d}:{time_parts.minute:02d}:{time_parts.second:02d}",
        "unix": format_unix_timestamp(dt),
        "iso": dt.isoformat(),
    }


def _emit_datetime_block(
    label: Optional[str],
    dt: datetime,
    show_weekday: bool = True,
    persian_script: bool = False,
) -> None:
    g_parts = DateParts(dt.year, dt.month, dt.day)
    j_parts = date_parts_from_tuple(gregorian_to_jalali(g_parts.year, g_parts.month, g_parts.day))
    time_parts = time_parts_from_datetime(dt)
    prefix = ""
    if label:
        click.echo(label)
        prefix = "  "
    click.echo(
        f"{prefix}Gregorian: "
        f"{format_datetime('gregorian', g_parts, time_parts, True, show_weekday)}"
    )
    click.echo(
        f"{prefix}Jalali:    "
        f"{format_datetime('jalali', j_parts, time_parts, True, show_weekday, persian_script)}"
    )
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


def _selected_date_kind(jalali: bool, gregorian: bool, epoch: bool, allow_epoch: bool = True) -> str:
    selected = [
        name
        for name, active in (
            ("jalali", jalali),
            ("gregorian", gregorian),
            ("epoch", epoch),
        )
        if active
    ]
    allowed = "-j/--jalali, -g/--gregorian, or -e/--epoch" if allow_epoch else "-j/--jalali or -g/--gregorian"
    if len(selected) != 1:
        raise click.ClickException(f"Provide exactly one of {allowed}.")
    if selected[0] == "epoch" and not allow_epoch:
        raise click.ClickException("-e/--epoch is not supported for this input.")
    return selected[0]


def _date_options_from_cli(
    *,
    value: Optional[str],
    jalali: bool,
    gregorian: bool,
    epoch_mode: bool,
    interval: Optional[str],
    year: Optional[int],
    month: Optional[str],
    day: Optional[int],
    hour: Optional[int],
    minute: Optional[int],
    second: Optional[int],
) -> tuple[Optional[str], DateInputOptions]:
    has_kind = any((jalali, gregorian, epoch_mode))
    has_parts = any(part is not None for part in (year, month, day, hour, minute, second))
    if interval is not None:
        if has_kind or value is not None or has_parts:
            raise click.ClickException("--interval is incompatible with -j, -g, -e, VALUE, and -y/-m/-d inputs.")
        return None, DateInputOptions(
            full_date=None,
            interval=interval,
            epoch=None,
            year=None,
            month=None,
            day=None,
            hour=None,
            minute=None,
            second=None,
        )

    kind = _selected_date_kind(jalali, gregorian, epoch_mode)
    if kind == "epoch":
        if value is None:
            raise click.ClickException("VALUE is required with -e/--epoch.")
        if has_parts:
            raise click.ClickException("-e/--epoch is incompatible with -y/-m/-d inputs.")
        return None, DateInputOptions(
            full_date=None,
            interval=None,
            epoch=value,
            year=None,
            month=None,
            day=None,
            hour=None,
            minute=None,
            second=None,
        )

    return kind, DateInputOptions(
        full_date=value,
        interval=None,
        epoch=None,
        year=year,
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        second=second,
    )


@click.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
@version_option
def jdate_cli():
    """Jalali (Persian) and Gregorian dates: convert, compare, measure.

    \b
    Examples:
      pyjdate now
      pyjdate convert -g "2026-01-04 10:43"
      pyjdate convert -j 1404/10/14 --fa
      pyjdate convert -i "1 y 2 mon"
      pyjdate interval -j -y 1404 -m mehr
      pyjdate distance -g "2026-03-21"
      pyjdate distance-between -g -s "-3 days" --end "6 hours"
    """


def _persian_option(func):
    return click.option(
        "--fa",
        "persian_script",
        is_flag=True,
        help="Print Jalali month and weekday names in Persian script.",
    )(func)


@click.command()
@_persian_option
@json_option
def current(persian_script: bool, as_json: bool):
    """Print the current date and time in both calendars.

    \b
    Examples:
      pyjdate current
      pyjdate now --fa
      pyjdate now --json
    """
    now = datetime.now().astimezone().replace(microsecond=0)
    if as_json:
        console.emit_json(datetime_payload(now, persian_script))
        return
    _emit_datetime_block(None, now, show_weekday=True, persian_script=persian_script)


@click.command()
@click.argument("value", required=False)
@click.option("-j", "--jalali", is_flag=True, help="Treat VALUE or -y/-m/-d as a Jalali date.")
@click.option("-g", "--gregorian", is_flag=True, help="Treat VALUE or -y/-m/-d as a Gregorian date.")
@click.option("-e", "--epoch", "epoch_mode", is_flag=True, help="Treat VALUE as a Unix timestamp.")
@click.option(
    "-i",
    "--interval",
    type=str,
    required=False,
    help="Relative interval (PostgreSQL style) added to current datetime. Example: '1 y', '-3.4 hours'.",
)
@click.option("-y", "--year", type=int, required=False, help="Year number.")
@click.option("-m", "--month", type=str, required=False, help="Month number or name.")
@click.option("-d", "--day", type=int, required=False, help="Day of month.")
@click.option("-H", "--hour", type=int, required=False, default=None, help="Hour (0-23).")
@click.option("--minute", type=int, required=False, default=None, help="Minute (0-59).")
@click.option("--second", type=int, required=False, default=None, help="Second (0-59).")
@_persian_option
@json_option
def convert(
    value: Optional[str],
    jalali: bool,
    gregorian: bool,
    epoch_mode: bool,
    interval: Optional[str],
    year: Optional[int],
    month: Optional[str],
    day: Optional[int],
    hour: Optional[int],
    minute: Optional[int],
    second: Optional[int],
    persian_script: bool,
    as_json: bool,
):
    """Convert a date between the Jalali and Gregorian calendars.

    \b
    For a concrete date use exactly one of -j, -g or -e. Dates may be written
    as 2026-01-04, 2026/01/04, 20260104 or 'Jan 04 2026', with an optional
    time and UTC offset.

    \b
    Examples:
      pyjdate convert -g "2026-01-04 10:43"
      pyjdate convert -j "1404/10/14 10:44:46" --fa
      pyjdate convert -e 1700000000 --json
      pyjdate convert -i "-3.4 hours"
      pyjdate convert -g -y 2026 -m feb -d 20
    """
    calendar, options = _date_options_from_cli(
        value=value,
        jalali=jalali,
        gregorian=gregorian,
        epoch_mode=epoch_mode,
        interval=interval,
        year=year,
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        second=second,
    )
    mode = detect_date_input_mode(options)
    if mode in ("epoch", "interval"):
        if mode == "epoch":
            dt = parse_epoch(options.epoch or "")
        else:
            dt = apply_interval(datetime.now().astimezone(), parse_pg_interval(options.interval or ""))
        if as_json:
            console.emit_json(datetime_payload(dt, persian_script))
        else:
            _emit_datetime_block(None, dt, persian_script=persian_script)
        return

    if calendar is None:
        raise click.ClickException("Calendar is required for VALUE and -y/-m/-d inputs.")
    cal = normalize_calendar(calendar)
    date_parts, time_parts, tzinfo, time_provided = _resolve_conversion_inputs(cal, options)
    g_tuple, j_tuple = convert_from(cal, date_parts.year, date_parts.month, date_parts.day)
    g = date_parts_from_tuple(g_tuple)
    j = date_parts_from_tuple(j_tuple)
    show_time = time_provided or time_parts.has_time()
    ts = build_datetime(cal, date_parts, time_parts, tzinfo)

    if as_json:
        payload = datetime_payload(ts, persian_script)
        payload["time_provided"] = show_time
        console.emit_json(payload)
        return

    click.echo(f"Gregorian: {format_datetime('gregorian', g, time_parts, show_time, True)}")
    click.echo(f"Jalali:    {format_datetime('jalali', j, time_parts, show_time, True, persian_script)}")
    click.echo(f"Unix:      {format_unix_timestamp(ts)}")


@click.command()
@click.option("-j", "--jalali", is_flag=True, help="Use Jalali dates for the input range or year/month/day.")
@click.option("-g", "--gregorian", is_flag=True, help="Use Gregorian dates for the input range or year/month/day.")
@click.option("-e", "--epoch", "epoch_mode", is_flag=True, help="Treat --start/--end as Unix timestamps.")
@click.option(
    "-s",
    "--start",
    type=str,
    required=False,
    help=(
        "Interval start (full date/time string; with -e, unix timestamp). "
        "Must be provided with --end."
    ),
)
@click.option(
    "--end",
    type=str,
    required=False,
    help=(
        "Interval end (full date/time string; with -e, unix timestamp). "
        "Must be provided with --start."
    ),
)
@click.option("-y", "--year", type=int, required=False, help="Year number.")
@click.option("-m", "--month", type=str, required=False, help="Month number or name.")
@click.option("-d", "--day", type=int, required=False, help="Day of month.")
@_persian_option
def interval(
    jalali: bool,
    gregorian: bool,
    epoch_mode: bool,
    start: Optional[str],
    end: Optional[str],
    year: Optional[int],
    month: Optional[str],
    day: Optional[int],
    persian_script: bool,
):
    """Show the first and last moment of a year, month, day or explicit range.

    \b
    Use exactly one of -j, -g or -e.

    \b
    Examples:
      pyjdate interval -j -y 1404
      pyjdate interval -g -y 2026 -m 02
      pyjdate interval -j -y 1404 -m mehr -d 12 --fa
      pyjdate interval -g -s "2026-01-01" --end "2026-01-31"
    """
    kind = _selected_date_kind(jalali, gregorian, epoch_mode)
    options = IntervalOptions(start=start, end=end, year=year, month=month, day=day)
    if (options.start is None) != (options.end is None):
        raise click.ClickException("Start and end must be provided together.")
    if options.start is not None and any(value is not None for value in (options.year, options.month, options.day)):
        raise click.ClickException("Start/end are incompatible with year/month/day inputs.")

    if kind == "epoch":
        if options.start is None:
            raise click.ClickException("--start and --end are required with -e/--epoch.")
        start_dt = parse_epoch(options.start)
        end_dt = parse_epoch(options.end or "")
        _emit_datetime_block("Start:", start_dt, persian_script=persian_script)
        _emit_datetime_block("End:", end_dt, persian_script=persian_script)
        return

    cal = kind
    if options.start is not None:
        start_dt, _start_time_provided = parse_calendar_endpoint(cal, options.start)
        end_dt, _end_time_provided = parse_calendar_endpoint(cal, options.end)  # type: ignore[arg-type]
        _emit_datetime_block("Start:", start_dt, persian_script=persian_script)
        _emit_datetime_block("End:", end_dt, persian_script=persian_script)
        return

    if options.year is None:
        raise click.ClickException("Year is required when start/end are not provided.")
    if options.month is None and options.day is not None:
        raise click.ClickException("Day requires month.")

    start_date, end_date = _resolve_interval_dates(cal, options.year, options.month, options.day)
    start_ts = build_datetime(cal, start_date, TimeParts(0, 0, 0, 0), local_timezone())
    end_ts = build_datetime(cal, end_date, TimeParts(23, 59, 59, 0), local_timezone())
    _emit_datetime_block("Start:", start_ts, persian_script=persian_script)
    _emit_datetime_block("End:", end_ts, persian_script=persian_script)


@click.command()
@click.argument("value", required=False)
@click.option("-j", "--jalali", is_flag=True, help="Treat VALUE or -y/-m/-d as a Jalali date.")
@click.option("-g", "--gregorian", is_flag=True, help="Treat VALUE or -y/-m/-d as a Gregorian date.")
@click.option("-e", "--epoch", "epoch_mode", is_flag=True, help="Treat VALUE as a Unix timestamp.")
@click.option(
    "-i",
    "--interval",
    type=str,
    required=False,
    help="Relative interval (PostgreSQL style) added to current datetime. Example: '1 y', '-3.4 hours'.",
)
@click.option("-y", "--year", type=int, required=False, help="Year number.")
@click.option("-m", "--month", type=str, required=False, help="Month number or name.")
@click.option("-d", "--day", type=int, required=False, help="Day of month.")
@click.option("-H", "--hour", type=int, required=False, default=None, help="Hour (0-23).")
@click.option("--minute", type=int, required=False, default=None, help="Minute (0-59).")
@click.option("--second", type=int, required=False, default=None, help="Second (0-59).")
@json_option
def distance(
    value: Optional[str],
    jalali: bool,
    gregorian: bool,
    epoch_mode: bool,
    interval: Optional[str],
    year: Optional[int],
    month: Optional[str],
    day: Optional[int],
    hour: Optional[int],
    minute: Optional[int],
    second: Optional[int],
    as_json: bool,
):
    """Show how far the given date is from now.

    \b
    For a concrete date use exactly one of -j, -g or -e.

    \b
    Examples:
      pyjdate distance -g "2026-03-21 08:00"
      pyjdate distance -j 1405/01/01
      pyjdate distance -i "2 days 4 hours" --json
    """
    calendar, options = _date_options_from_cli(
        value=value,
        jalali=jalali,
        gregorian=gregorian,
        epoch_mode=epoch_mode,
        interval=interval,
        year=year,
        month=month,
        day=day,
        hour=hour,
        minute=minute,
        second=second,
    )
    now = datetime.now().astimezone()
    input_dt = parse_input_datetime(calendar, options, now=now)
    print_distance(now, input_dt, as_json)


@click.command(name="distance-between")
@click.option("-j", "--jalali", is_flag=True, help="Treat full-date endpoints as Jalali dates.")
@click.option("-g", "--gregorian", is_flag=True, help="Treat full-date endpoints as Gregorian dates.")
@click.option("-e", "--epoch", "epoch_mode", is_flag=True, help="Treat --start/--end as Unix timestamps.")
@click.option(
    "-s",
    "--start",
    type=str,
    required=True,
    help=(
        "Start date/time (full date/time string, PostgreSQL interval relative to now, or unix timestamp with -e). "
        "Examples: '2026-01-04 10:43:45+03:30', '2026-01-04', '1404/10/14 10:44:46', '-3.4 hours'."
    ),
)
@click.option(
    "--end",
    type=str,
    required=True,
    help=(
        "End date/time (full date/time string, PostgreSQL interval relative to now, or unix timestamp with -e). "
        "Examples: '2026-02-01 08:00', '2026-02-01', '1404/11/12 12:00:00', '2 days'."
    ),
)
@json_option
def distance_between(
    jalali: bool, gregorian: bool, epoch_mode: bool, start: str, end: str, as_json: bool
):
    """Show the time difference between two dates.

    \b
    Use exactly one of -j, -g or -e. Each endpoint may also be a relative
    interval, which is resolved against the current time.

    \b
    Examples:
      pyjdate distance-between -g -s "2026-01-01" --end "2026-01-02 12:00"
      pyjdate distance-between -j -s 1404/01/01 --end 1405/01/01
      pyjdate distance-between -g -s "-3 days" --end "6 hours" --json
    """
    kind = _selected_date_kind(jalali, gregorian, epoch_mode)
    now = datetime.now().astimezone()
    if kind == "epoch":
        start_dt = parse_epoch(start)
        end_dt = parse_epoch(end)
    else:
        if is_epoch_candidate(start) or is_epoch_candidate(end):
            raise click.ClickException("Use -e/--epoch for Unix timestamp inputs.")
        start_dt = parse_distance_between_endpoint(start, kind, now, "start")
        end_dt = parse_distance_between_endpoint(end, kind, now, "end")
    print_distance(start_dt, end_dt, as_json)


for cmd in (current, convert, interval, distance, distance_between):
    jdate_cli.add_command(cmd)

# `now` reads better than `current` for the most-used command; both work.
jdate_cli.add_command(current, name="now")


if __name__ == "__main__":  # pragma: no cover
    jdate_cli()
