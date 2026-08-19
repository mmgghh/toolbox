"""PostgreSQL-style interval parsing shared by ``pyjdate`` and ``pytime``.

Accepts the forms people actually type::

    1 y            3 days          -3.4 hours
    2 days 04:30   1y2mon10d       90 minutes

Years and months are kept separate from days/seconds on purpose: adding
"1 month" to Jan 31 must land on Feb 28/29, which a plain ``timedelta``
cannot express.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

import click

UNIT_ALIASES = {
    "year": "years",
    "years": "years",
    "yr": "years",
    "yrs": "years",
    "y": "years",
    "month": "months",
    "months": "months",
    "mon": "months",
    "mons": "months",
    "mo": "months",
    "week": "weeks",
    "weeks": "weeks",
    "wk": "weeks",
    "wks": "weeks",
    "w": "weeks",
    "day": "days",
    "days": "days",
    "d": "days",
    "hour": "hours",
    "hours": "hours",
    "hr": "hours",
    "hrs": "hours",
    "h": "hours",
    "minute": "minutes",
    "minutes": "minutes",
    "min": "minutes",
    "mins": "minutes",
    "m": "minutes",
    "second": "seconds",
    "seconds": "seconds",
    "sec": "seconds",
    "secs": "seconds",
    "s": "seconds",
}

TOKEN_RE = re.compile(
    r"(?P<time>[+-]?\d+:\d{2}(?::\d{2}(?:\.\d+)?)?)|"
    r"(?P<value>[+-]?\d+(?:\.\d+)?)\s*(?P<unit>[A-Za-z]+)",
)

_GREGORIAN_MONTH_DAYS = (0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


@dataclass(frozen=True)
class IntervalDelta:
    """A calendar-aware duration.

    ``years``/``months`` are applied by shifting the calendar month, while
    ``days``/``seconds`` are applied as an exact elapsed offset.
    """

    years: int = 0
    months: int = 0
    days: float = 0.0
    seconds: float = 0.0

    def with_(self, **changes: float) -> IntervalDelta:
        """Return a copy with the given fields replaced."""
        return IntervalDelta(
            years=int(changes.get("years", self.years)),
            months=int(changes.get("months", self.months)),
            days=float(changes.get("days", self.days)),
            seconds=float(changes.get("seconds", self.seconds)),
        )


def _is_leap_gregorian(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def gregorian_days_in_month(year: int, month: int) -> int:
    """Number of days in a Gregorian month."""
    if month == 2 and _is_leap_gregorian(year):
        return 29
    return _GREGORIAN_MONTH_DAYS[month]


def _add_time_literal(delta: IntervalDelta, value: str) -> IntervalDelta:
    sign = -1 if value.startswith("-") else 1
    payload = value[1:] if value[0] in "+-" else value
    parts = payload.split(":")
    if len(parts) < 2 or len(parts) > 3:
        raise click.ClickException(f"Invalid interval time segment: {value}")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2]) if len(parts) == 3 else 0.0
    total_seconds = sign * (hours * 3600 + minutes * 60 + seconds)
    return delta.with_(seconds=delta.seconds + total_seconds)


def _apply_interval_token(delta: IntervalDelta, unit: str, value: float) -> IntervalDelta:
    if unit == "years":
        return delta.with_(years=delta.years + int(value))
    if unit == "months":
        return delta.with_(months=delta.months + int(value))
    if unit == "weeks":
        return delta.with_(days=delta.days + value * 7)
    if unit == "days":
        return delta.with_(days=delta.days + value)
    if unit == "hours":
        return delta.with_(seconds=delta.seconds + value * 3600)
    if unit == "minutes":
        return delta.with_(seconds=delta.seconds + value * 60)
    if unit == "seconds":
        return delta.with_(seconds=delta.seconds + value)
    raise click.ClickException(f"Unsupported interval unit: {unit}")


def parse_pg_interval(value: str) -> IntervalDelta:
    """Parse a PostgreSQL-like interval string into an :class:`IntervalDelta`."""
    raw = value.strip()
    if not raw:
        raise click.ClickException("Interval cannot be empty.")

    delta = IntervalDelta()
    pos = 0
    matched = False

    for match in TOKEN_RE.finditer(raw):
        if raw[pos:match.start()].strip(" ,"):
            raise click.ClickException(f"Invalid interval segment: {raw[pos:match.start()].strip()}")
        pos = match.end()
        matched = True

        if match.group("time"):
            delta = _add_time_literal(delta, match.group("time"))
            continue

        value_str = match.group("value") or "0"
        unit_str = match.group("unit") or ""
        unit_key = UNIT_ALIASES.get(unit_str.lower())
        if unit_key is None:
            known = ", ".join(sorted(set(UNIT_ALIASES.values())))
            raise click.ClickException(f"Unknown interval unit: {unit_str}. Known units: {known}.")

        numeric = float(value_str)
        if unit_key in ("years", "months") and not numeric.is_integer():
            raise click.ClickException(f"{unit_key} must be whole numbers in interval values.")

        delta = _apply_interval_token(delta, unit_key, numeric)

    if raw[pos:].strip(" ,"):
        raise click.ClickException(f"Invalid interval segment: {raw[pos:].strip()}")

    if not matched:
        raise click.ClickException(
            f"Interval format not recognized: {value!r}. Try '2 days', '-3.4 hours', or '1 y 2 mon'."
        )

    return delta


def shift_months(dt: datetime, months: int) -> datetime:
    """Shift a datetime by whole months, clamping the day to the target month."""
    if months == 0:
        return dt
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    day = min(dt.day, gregorian_days_in_month(year, month))
    return dt.replace(year=year, month=month, day=day)


def apply_interval(dt: datetime, delta: IntervalDelta, direction: int = 1) -> datetime:
    """Add ``delta`` to ``dt``; pass ``direction=-1`` to subtract it."""
    shifted = shift_months(dt, direction * (delta.years * 12 + delta.months))
    return shifted + timedelta(days=direction * delta.days, seconds=direction * delta.seconds)


def format_total_value(value: float) -> str:
    """Render a float without trailing zeros (``3.0`` -> ``"3"``)."""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def format_duration(seconds: float) -> str:
    """Render a number of seconds as a compact human duration (``1h 5m 3s``)."""
    negative = seconds < 0
    remaining = int(abs(round(seconds)))
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, secs = divmod(remaining, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    text = " ".join(parts)
    return f"-{text}" if negative else text


def format_hours_minutes(hours: float) -> str:
    """Render a float number of hours as ``HH:MM``."""
    total_minutes = int(round(abs(hours) * 60))
    sign = "-" if hours < 0 else ""
    return f"{sign}{total_minutes // 60:02d}:{total_minutes % 60:02d}"
