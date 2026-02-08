"""Time tracking utilities and CLI commands."""

from __future__ import annotations

import csv
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

import click

from pytoolbox.pyjdate import (
    DateParts,
    TimeParts,
    build_datetime,
    days_in_month,
    format_datetime,
    format_total_value,
    format_unix_timestamp,
    gregorian_to_jalali,
    local_timezone,
    normalize_calendar,
    parse_interval_endpoint,
    parse_full_date,
    split_datetime_parts,
    validate_date,
    validate_time,
)


DEFAULT_DB_PATH = Path.home() / ".pytime" / "pytime.db"
DEFAULT_OUTPUT_PREFIX = "pytime"


@dataclass(frozen=True)
class TimeRecord:
    """Normalized time entry data."""

    entry_id: int
    project: Optional[str]
    task: str
    start_dt: datetime
    end_dt: Optional[datetime]
    duration_hours: float


@dataclass(frozen=True)
class IntervalDelta:
    """Parsed interval components."""

    years: int = 0
    months: int = 0
    days: float = 0.0
    seconds: float = 0.0


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
    "week": "weeks",
    "weeks": "weeks",
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


def resolve_db_path(db_path: Optional[Path]) -> Path:
    """Return database path, creating its parent directory when needed."""
    path = db_path or DEFAULT_DB_PATH
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect(db_path: Path) -> sqlite3.Connection:
    """Create a SQLite connection and initialize the schema."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.create_function("REGEXP", 2, _regexp)
    _init_schema(conn)
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS time_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT,
            task TEXT NOT NULL,
            start_ts REAL NOT NULL,
            end_ts REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_time_entries_start ON time_entries(start_ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_time_entries_end ON time_entries(end_ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_time_entries_project ON time_entries(project)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_time_entries_task ON time_entries(task)")
    conn.commit()


def _regexp(pattern: str, value: Optional[str]) -> int:
    if value is None:
        return 0
    return 1 if re.search(pattern, value) else 0


def escape_like(value: str) -> str:
    """Escape LIKE wildcards for literal matching."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def format_dt_triplet(dt: datetime) -> dict[str, str]:
    """Return formatted datetime strings for Gregorian, Jalali, and epoch."""
    local_dt = dt.astimezone(local_timezone())
    g_parts = DateParts(local_dt.year, local_dt.month, local_dt.day)
    j_parts = DateParts(*gregorian_to_jalali(local_dt.year, local_dt.month, local_dt.day))
    time_parts = TimeParts(
        local_dt.hour,
        local_dt.minute,
        local_dt.second,
        local_dt.microsecond,
    )
    return {
        "gregorian": format_datetime("gregorian", g_parts, time_parts, True),
        "jalali": format_datetime("jalali", j_parts, time_parts, True),
        "epoch": format_unix_timestamp(local_dt),
    }


def emit_datetime_block(label: str, dt: datetime) -> None:
    """Print a datetime in Gregorian, Jalali, and epoch formats."""
    triplet = format_dt_triplet(dt)
    click.echo(label)
    click.echo(f"  Gregorian: {triplet['gregorian']}")
    click.echo(f"  Jalali:    {triplet['jalali']}")
    click.echo(f"  Unix:      {triplet['epoch']}")


def parse_pg_interval(value: str) -> IntervalDelta:
    """Parse a PostgreSQL-like interval string into components."""
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
            raise click.ClickException(f"Unknown interval unit: {unit_str}")

        numeric = float(value_str)
        if unit_key in ("years", "months") and not numeric.is_integer():
            raise click.ClickException(f"{unit_key} must be whole numbers in interval values.")

        delta = _apply_interval_token(delta, unit_key, numeric)

    if raw[pos:].strip(" ,"):
        raise click.ClickException(f"Invalid interval segment: {raw[pos:].strip()}")

    if not matched:
        raise click.ClickException("Interval format not recognized.")

    return delta


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
    return IntervalDelta(
        years=delta.years,
        months=delta.months,
        days=delta.days,
        seconds=delta.seconds + total_seconds,
    )


def _apply_interval_token(delta: IntervalDelta, unit: str, value: float) -> IntervalDelta:
    if unit == "years":
        return IntervalDelta(
            years=delta.years + int(value),
            months=delta.months,
            days=delta.days,
            seconds=delta.seconds,
        )
    if unit == "months":
        return IntervalDelta(
            years=delta.years,
            months=delta.months + int(value),
            days=delta.days,
            seconds=delta.seconds,
        )
    if unit == "weeks":
        return IntervalDelta(
            years=delta.years,
            months=delta.months,
            days=delta.days + value * 7,
            seconds=delta.seconds,
        )
    if unit == "days":
        return IntervalDelta(
            years=delta.years,
            months=delta.months,
            days=delta.days + value,
            seconds=delta.seconds,
        )
    if unit == "hours":
        return IntervalDelta(
            years=delta.years,
            months=delta.months,
            days=delta.days,
            seconds=delta.seconds + value * 3600,
        )
    if unit == "minutes":
        return IntervalDelta(
            years=delta.years,
            months=delta.months,
            days=delta.days,
            seconds=delta.seconds + value * 60,
        )
    if unit == "seconds":
        return IntervalDelta(
            years=delta.years,
            months=delta.months,
            days=delta.days,
            seconds=delta.seconds + value,
        )
    raise click.ClickException(f"Unsupported interval unit: {unit}")


def apply_interval(dt: datetime, delta: IntervalDelta, direction: int = 1) -> datetime:
    """Apply an interval delta to a datetime."""
    month_delta = direction * (delta.years * 12 + delta.months)
    shifted = shift_months(dt, month_delta)
    shifted = shifted + timedelta(days=direction * delta.days, seconds=direction * delta.seconds)
    return shifted


def shift_months(dt: datetime, months: int) -> datetime:
    """Shift a datetime by a number of months while clamping the day."""
    if months == 0:
        return dt
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    max_day = days_in_month("gregorian", year, month)
    day = min(dt.day, max_day)
    return dt.replace(year=year, month=month, day=day)


def parse_calendar(value: Optional[str], required: bool = False) -> tuple[str, bool]:
    """Return normalized calendar value and whether fallback detection is allowed."""
    _ = required
    if value is None:
        return "jalali", True
    return normalize_calendar(value), False


def parse_datetime_value(calendar: str, value: str, allow_fallback: bool = False) -> datetime:
    """Parse a datetime value using pyjdate parsing rules with optional fallback."""
    try:
        dt, _ = parse_interval_endpoint(calendar, value)
    except click.ClickException:
        if allow_fallback and calendar == "jalali":
            return _parse_datetime_fallback(calendar, value, None)
        raise

    if allow_fallback and calendar == "jalali":
        return _parse_datetime_fallback(calendar, value, dt)
    return dt


def build_output_path(output: Optional[str], suffix: str) -> Path:
    """Resolve output path with a default name when none is provided."""
    if output:
        return Path(output).expanduser()
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    return Path(f"{DEFAULT_OUTPUT_PREFIX}-{timestamp}{suffix}")


def render_table(rows: list[dict[str, object]], headers: list[str]) -> str:
    """Render rows as an aligned text table."""
    if not rows:
        return ""
    widths = {header: len(header) for header in headers}
    for row in rows:
        for header in headers:
            widths[header] = max(widths[header], len(str(row.get(header, ""))))
    header_line = " | ".join(header.ljust(widths[header]) for header in headers)
    separator = "-+-".join("-" * widths[header] for header in headers)
    lines = [header_line, separator]
    for row in rows:
        line = " | ".join(str(row.get(header, "")).ljust(widths[header]) for header in headers)
        lines.append(line)
    return "\n".join(lines)


def render_markdown(rows: list[dict[str, object]], headers: list[str]) -> str:
    """Render rows as a Markdown table."""
    if not rows:
        return ""
    header_line = "| " + " | ".join(headers) + " |"
    separator = "| " + " | ".join("---" for _ in headers) + " |"
    lines = [header_line, separator]
    for row in rows:
        line = "| " + " | ".join(str(row.get(header, "")) for header in headers) + " |"
        lines.append(line)
    return "\n".join(lines)


def write_csv(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    """Write rows to a CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row.get(header, "") for header in headers])


def write_markdown(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    """Write rows to a Markdown file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown(rows, headers), encoding="utf-8")


def write_excel(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    """Write rows to an Excel file."""
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise click.ClickException("openpyxl is required for Excel output. Install it and try again.") from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(headers)
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    workbook.save(path)


def fetch_records(
    conn: sqlite3.Connection,
    clauses: Iterable[str],
    params: list[object],
) -> list[TimeRecord]:
    """Fetch records from the database and normalize them."""
    query = "SELECT id, project, task, start_ts, end_ts FROM time_entries"
    clause_list = list(clauses)
    if clause_list:
        query += " WHERE " + " AND ".join(clause_list)
    query += " ORDER BY start_ts ASC, id ASC"

    now = datetime.now().astimezone()
    records = []
    for row in conn.execute(query, params).fetchall():
        start_dt = datetime.fromtimestamp(row["start_ts"], tz=local_timezone())
        end_dt = datetime.fromtimestamp(row["end_ts"], tz=local_timezone()) if row["end_ts"] is not None else None
        duration_end = end_dt or now
        duration_hours = (duration_end - start_dt).total_seconds() / 3600
        records.append(
            TimeRecord(
                entry_id=row["id"],
                project=row["project"],
                task=row["task"],
                start_dt=start_dt,
                end_dt=end_dt,
                duration_hours=duration_hours,
            )
        )
    return records


def record_to_row(record: TimeRecord, include_end: bool = True) -> dict[str, object]:
    """Convert a record to a printable/exportable row."""
    start_triplet = format_dt_triplet(record.start_dt)
    row: dict[str, object] = {
        "id": record.entry_id,
        "project": record.project or "",
        "task": record.task,
        "start_gregorian": start_triplet["gregorian"],
        "start_jalali": start_triplet["jalali"],
        "start_epoch": start_triplet["epoch"],
        "duration_hours": format_total_value(record.duration_hours),
    }
    if include_end:
        if record.end_dt is not None:
            end_triplet = format_dt_triplet(record.end_dt)
            row.update(
                {
                    "end_gregorian": end_triplet["gregorian"],
                    "end_jalali": end_triplet["jalali"],
                    "end_epoch": end_triplet["epoch"],
                }
            )
        else:
            row.update({"end_gregorian": "", "end_jalali": "", "end_epoch": ""})
    return row


def group_records(
    records: list[TimeRecord],
    group_by: list[str],
    calendar: str,
) -> tuple[list[dict[str, object]], list[str]]:
    """Group records and return aggregated rows with headers."""
    group_by_set = set(group_by)
    grouped: dict[tuple[object, ...], dict[str, object]] = {}

    for record in records:
        parts: list[object] = []
        values: dict[str, object] = {}

        if "project" in group_by_set:
            values["project"] = record.project or ""
            parts.append(values["project"])
        if "task" in group_by_set:
            values["task"] = record.task
            parts.append(values["task"])

        y = m = d = None
        if group_by_set & {"year", "month", "day"}:
            if calendar == "gregorian":
                y, m, d = record.start_dt.year, record.start_dt.month, record.start_dt.day
            else:
                y, m, d = gregorian_to_jalali(record.start_dt.year, record.start_dt.month, record.start_dt.day)

            if "year" in group_by_set:
                values["year"] = y
                parts.append(y)
            if "month" in group_by_set:
                values["month"] = m
                parts.append(m)
            if "day" in group_by_set:
                values["day"] = d
                parts.append(d)

        key = tuple(parts)
        if key not in grouped:
            grouped[key] = {
                **values,
                "duration_hours": 0.0,
                "_date_triplet": None,
            }
            if group_by_set & {"year", "month", "day"}:
                year_val = y if y is not None else record.start_dt.year
                month_val = m if m is not None else 1
                day_val = d if d is not None else 1
                if "month" not in group_by_set:
                    month_val = 1
                if "day" not in group_by_set:
                    day_val = 1
                date_parts = DateParts(int(year_val), int(month_val), int(day_val))
                period_start = build_datetime(calendar, date_parts, TimeParts(0, 0, 0, 0), local_timezone())
                grouped[key]["_date_triplet"] = format_dt_triplet(period_start)

        grouped[key]["duration_hours"] += record.duration_hours

    headers: list[str] = []
    for field in ("project", "task", "year", "month", "day"):
        if field in group_by_set:
            headers.append(field)

    if group_by_set & {"year", "month", "day"}:
        headers.extend(["date_gregorian", "date_jalali", "date_epoch"])

    headers.append("duration_hours")

    rows: list[dict[str, object]] = []
    for grouped_row in grouped.values():
        row: dict[str, object] = {field: grouped_row.get(field, "") for field in headers}
        triplet = grouped_row.get("_date_triplet")
        if triplet:
            row["date_gregorian"] = triplet["gregorian"]
            row["date_jalali"] = triplet["jalali"]
            row["date_epoch"] = triplet["epoch"]
        row["duration_hours"] = format_total_value(grouped_row["duration_hours"])
        rows.append(row)

    return rows, headers


@click.group()
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="SQLite database path (defaults to ~/.pytime/pytime.db).",
)
@click.pass_context
def time_cli(ctx: click.Context, db_path: Optional[Path]) -> None:
    """Track time entries with SQLite-backed storage."""
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = resolve_db_path(db_path)


@time_cli.command()
@click.option("-p", "--project", type=str, help="Project name (optional).")
@click.argument("task", type=str)
def start(project: Optional[str], task: str) -> None:
    """Start a new time entry and close any unfinished entries."""
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    now = datetime.now().astimezone()
    _end_entries(db_path, entry_id=None, project=None, task=None, emit=True, allow_empty=True)
    with connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO time_entries (project, task, start_ts) VALUES (?, ?, ?)",
            (project, task, now.timestamp()),
        )
        entry_id = cursor.lastrowid

    click.echo(f"Id: {entry_id}")
    click.echo(f"Project: {project or ''}")
    click.echo(f"Task: {task}")
    emit_datetime_block("Start:", now)


@time_cli.command()
@click.option("-i", "--id", "entry_id", type=int, help="Entry id to stop (optional).")
@click.option("-p", "--project", type=str, help="Project name filter (optional).")
@click.option("-t", "--task", "task", type=str, help="Task name filter (optional).")
@click.option("--name", "task", type=str, help="Alias for --task.")
def end(entry_id: Optional[int], project: Optional[str], task: Optional[str]) -> None:
    """Stop an active time entry."""
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    _end_entries(db_path, entry_id=entry_id, project=project, task=task, emit=True, allow_empty=False)


@time_cli.command()
@click.option("-i", "--id", "entry_id", type=int, help="Entry id filter (optional).")
@click.option("-p", "--project", type=str, help="Project filter (optional).")
@click.option("-t", "--task", type=str, help="Task filter (optional).")
@click.option(
    "--regex/--literal",
    default=False,
    help="Treat project/task filters as regex or literal strings (default: literal).",
)
@click.option(
    "--interval",
    "interval_value",
    type=str,
    help="Relative interval (PostgreSQL style). When set, start/end are ignored.",
)
@click.option("-s", "--start", "start_value", type=str, help="Report start time (optional).")
@click.option("-e", "--end", "end_value", type=str, help="Report end time (optional).")
@click.option(
    "-g",
    "--group-by",
    "group_by",
    multiple=True,
    help="Group by fields (project, task, year, month, day).",
)
@click.option(
    "-c",
    "--calendar",
    type=click.Choice(["gregorian", "jalali", "g", "j"], case_sensitive=False),
    help="Calendar for date inputs/grouping (jalali/gregorian).",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["table", "excel", "csv", "markdown"], case_sensitive=False),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.option("-o", "--output", type=str, help="Output file path.")
def report(
    entry_id: Optional[int],
    project: Optional[str],
    task: Optional[str],
    regex: bool,
    interval_value: Optional[str],
    start_value: Optional[str],
    end_value: Optional[str],
    group_by: tuple[str, ...],
    calendar: Optional[str],
    output_format: str,
    output: Optional[str],
) -> None:
    """Generate time tracking reports."""
    group_items = _normalize_group_by(group_by)
    if interval_value and (start_value or end_value):
        raise click.ClickException("Interval is incompatible with start/end filters.")

    if {"month", "day"} & set(group_items) and "year" not in group_items:
        raise click.ClickException("Grouping by month/day requires year.")
    if "day" in group_items and "month" not in group_items:
        raise click.ClickException("Grouping by day requires month.")

    calendar_value, allow_fallback = parse_calendar(
        calendar,
        required=bool(set(group_items) & {"year", "month", "day"}),
    )

    clauses: list[str] = []
    params: list[object] = []

    if entry_id is not None:
        clauses.append("id = ?")
        params.append(entry_id)

    if project:
        _validate_regex(project, regex)
        if regex:
            clauses.append("project REGEXP ?")
            params.append(project)
        else:
            clauses.append("project LIKE ? ESCAPE '\\' COLLATE NOCASE")
            params.append(f"%{escape_like(project)}%")

    if task:
        _validate_regex(task, regex)
        if regex:
            clauses.append("task REGEXP ?")
            params.append(task)
        else:
            clauses.append("task LIKE ? ESCAPE '\\' COLLATE NOCASE")
            params.append(f"%{escape_like(task)}%")

    if interval_value:
        delta = parse_pg_interval(interval_value)
        end_dt = datetime.now().astimezone()
        start_dt = apply_interval(end_dt, delta, direction=-1)
        clauses.append("start_ts >= ?")
        params.append(start_dt.timestamp())
        clauses.append("start_ts <= ?")
        params.append(end_dt.timestamp())
    else:
        if start_value:
            start_dt = parse_datetime_value(calendar_value, start_value, allow_fallback)
            clauses.append("start_ts >= ?")
            params.append(start_dt.timestamp())
        if end_value:
            end_dt = parse_datetime_value(calendar_value, end_value, allow_fallback)
            clauses.append("start_ts <= ?")
            params.append(end_dt.timestamp())

    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    with connect(db_path) as conn:
        records = fetch_records(conn, clauses, params)

    if not records:
        click.echo("No records found.")
        return

    if group_items:
        rows, headers = group_records(records, group_items, calendar_value)
    else:
        rows = [record_to_row(record) for record in records]
        headers = [
            "id",
            "project",
            "task",
            "start_gregorian",
            "start_jalali",
            "start_epoch",
            "end_gregorian",
            "end_jalali",
            "end_epoch",
            "duration_hours",
        ]

    output_format = output_format.lower()
    if output_format == "table":
        table = render_table(rows, headers)
        if output:
            path = Path(output).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(table, encoding="utf-8")
        else:
            click.echo(table)
        return

    if output_format == "csv":
        path = build_output_path(output, ".csv")
        write_csv(path, rows, headers)
        click.echo(f"CSV report written to {path}")
        return

    if output_format == "markdown":
        path = build_output_path(output, ".md")
        write_markdown(path, rows, headers)
        click.echo(f"Markdown report written to {path}")
        return

    if output_format == "excel":
        path = build_output_path(output, ".xlsx")
        write_excel(path, rows, headers)
        click.echo(f"Excel report written to {path}")
        return

    raise click.ClickException(f"Unknown format: {output_format}")


@time_cli.command()
@click.option("-i", "--id", "entry_id", type=int, help="Entry id to edit.")
@click.option("--last", "edit_last", is_flag=True, help="Edit the last entry.")
@click.option("-s", "--start", "start_value", type=str, help="New start time (optional).")
@click.option("-e", "--end", "end_value", type=str, help="New end time (optional).")
@click.option("--duration", type=str, help="Duration (PostgreSQL interval) to compute end time.")
@click.option("-p", "--project", type=str, help="New project name (optional).")
@click.option("-t", "--task", type=str, help="New task name (optional).")
@click.option(
    "-c",
    "--calendar",
    type=click.Choice(["gregorian", "jalali", "g", "j"], case_sensitive=False),
    help="Calendar for parsing start/end inputs.",
)
def edit(
    entry_id: Optional[int],
    edit_last: bool,
    start_value: Optional[str],
    end_value: Optional[str],
    duration: Optional[str],
    project: Optional[str],
    task: Optional[str],
    calendar: Optional[str],
) -> None:
    """Edit a time entry."""
    if entry_id is not None and edit_last:
        raise click.ClickException("Use either --id or --last, not both.")
    if entry_id is None and not edit_last:
        raise click.ClickException("Provide --id or --last to select an entry.")
    if duration and end_value:
        raise click.ClickException("Duration is incompatible with an explicit end time.")

    calendar_value, allow_fallback = parse_calendar(calendar)

    updates: list[str] = []
    params: list[object] = []

    if start_value:
        start_dt = parse_datetime_value(calendar_value, start_value, allow_fallback)
        updates.append("start_ts = ?")
        params.append(start_dt.timestamp())
    else:
        start_dt = None

    if end_value:
        end_dt = parse_datetime_value(calendar_value, end_value, allow_fallback)
        updates.append("end_ts = ?")
        params.append(end_dt.timestamp())
    else:
        end_dt = None

    if project is not None:
        updates.append("project = ?")
        params.append(project)

    if task is not None:
        updates.append("task = ?")
        params.append(task)

    if not updates and not duration:
        raise click.ClickException("No updates specified.")

    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    with connect(db_path) as conn:
        if entry_id is None:
            row = conn.execute(
                "SELECT id, project, task, start_ts, end_ts FROM time_entries ORDER BY id DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, project, task, start_ts, end_ts FROM time_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()

        if row is None:
            raise click.ClickException("Entry not found.")

        current_start = datetime.fromtimestamp(row["start_ts"], tz=local_timezone())
        current_end = (
            datetime.fromtimestamp(row["end_ts"], tz=local_timezone()) if row["end_ts"] is not None else None
        )

        if duration:
            delta = parse_pg_interval(duration)
            base_start = start_dt or current_start
            computed_end = apply_interval(base_start, delta, direction=1)
            end_dt = computed_end
            updates.append("end_ts = ?")
            params.append(end_dt.timestamp())

        effective_start = start_dt or current_start
        effective_end = end_dt if end_dt is not None else current_end
        if effective_end is not None and effective_end < effective_start:
            raise click.ClickException("End time cannot be before start time.")

        if updates:
            params.append(row["id"])
            query = "UPDATE time_entries SET " + ", ".join(updates) + " WHERE id = ?"
            conn.execute(query, params)

        updated = conn.execute(
            "SELECT id, project, task, start_ts, end_ts FROM time_entries WHERE id = ?",
            (row["id"],),
        ).fetchone()

    if updated is None:
        raise click.ClickException("Failed to load updated entry.")

    updated_start = datetime.fromtimestamp(updated["start_ts"], tz=local_timezone())
    updated_end = (
        datetime.fromtimestamp(updated["end_ts"], tz=local_timezone()) if updated["end_ts"] is not None else None
    )
    duration_end = updated_end or datetime.now().astimezone()
    duration_hours = (duration_end - updated_start).total_seconds() / 3600

    click.echo(f"Id: {updated['id']}")
    click.echo(f"Project: {updated['project'] or ''}")
    click.echo(f"Task: {updated['task']}")
    emit_datetime_block("Start:", updated_start)
    if updated_end:
        emit_datetime_block("End:", updated_end)
        click.echo(f"Duration (hours): {format_total_value(duration_hours)}")


@time_cli.command()
@click.option("-p", "--project", type=str, help="Project name (optional).")
@click.option(
    "-c",
    "--calendar",
    type=click.Choice(["gregorian", "jalali", "g", "j"], case_sensitive=False),
    help="Calendar for parsing start/end inputs.",
)
@click.option("--end", "end_value", type=str, help="End time (full-date, minute/second precision).")
@click.option("--duration", type=str, help="Duration (PostgreSQL interval) to compute end time.")
@click.argument("task", type=str)
@click.argument("start_value", type=str)
def add(
    project: Optional[str],
    task: str,
    start_value: str,
    end_value: Optional[str],
    duration: Optional[str],
    calendar: Optional[str],
) -> None:
    """Insert a completed time entry."""
    if end_value and duration:
        raise click.ClickException("Use either --end or --duration, not both.")
    if not end_value and not duration:
        raise click.ClickException("Provide --end or --duration.")

    calendar_value, allow_fallback = parse_calendar(calendar)
    start_dt = _parse_full_date_minutes(calendar_value, start_value, label="start", allow_fallback=allow_fallback)

    if end_value:
        end_dt = _parse_full_date_minutes(calendar_value, end_value, label="end", allow_fallback=allow_fallback)
    else:
        delta = parse_pg_interval(duration or "")
        end_dt = apply_interval(start_dt, delta, direction=1)

    if end_dt < start_dt:
        raise click.ClickException("End time cannot be before start time.")

    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    with connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO time_entries (project, task, start_ts, end_ts) VALUES (?, ?, ?, ?)",
            (project, task, start_dt.timestamp(), end_dt.timestamp()),
        )
        entry_id = cursor.lastrowid

    duration_hours = (end_dt - start_dt).total_seconds() / 3600
    click.echo(f"Id: {entry_id}")
    click.echo(f"Project: {project or ''}")
    click.echo(f"Task: {task}")
    emit_datetime_block("Start:", start_dt)
    emit_datetime_block("End:", end_dt)
    click.echo(f"Duration (hours): {format_total_value(duration_hours)}")
    if duration_hours > 5:
        click.echo("Warning: duration exceeds 5 hours. You can edit this row afterwards if needed.")


@time_cli.command()
@click.option("-i", "--id", "entry_id", type=int, help="Entry id filter (optional).")
@click.option("-p", "--project", type=str, help="Project filter (optional).")
@click.option("-t", "--task", type=str, help="Task filter (optional).")
@click.option(
    "--regex/--literal",
    default=False,
    help="Treat project/task filters as regex or literal strings (default: literal).",
)
@click.option(
    "--interval",
    "interval_value",
    type=str,
    help="Relative interval (PostgreSQL style). When set, start/end are ignored.",
)
@click.option("-s", "--start", "start_value", type=str, help="Delete start time (optional).")
@click.option("-e", "--end", "end_value", type=str, help="Delete end time (optional).")
@click.option(
    "-c",
    "--calendar",
    type=click.Choice(["gregorian", "jalali", "g", "j"], case_sensitive=False),
    help="Calendar for date inputs.",
)
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Delete without confirmation.")
@click.option("--backup", is_flag=True, help="Export matching rows to Excel before deleting.")
def delete(
    entry_id: Optional[int],
    project: Optional[str],
    task: Optional[str],
    regex: bool,
    interval_value: Optional[str],
    start_value: Optional[str],
    end_value: Optional[str],
    calendar: Optional[str],
    assume_yes: bool,
    backup: bool,
) -> None:
    """Delete time entries matching filters."""
    if interval_value and (start_value or end_value):
        raise click.ClickException("Interval is incompatible with start/end filters.")

    calendar_value, allow_fallback = parse_calendar(calendar)

    clauses: list[str] = []
    params: list[object] = []

    if entry_id is not None:
        clauses.append("id = ?")
        params.append(entry_id)

    if project:
        _validate_regex(project, regex)
        if regex:
            clauses.append("project REGEXP ?")
            params.append(project)
        else:
            clauses.append("project LIKE ? ESCAPE '\\' COLLATE NOCASE")
            params.append(f"%{escape_like(project)}%")

    if task:
        _validate_regex(task, regex)
        if regex:
            clauses.append("task REGEXP ?")
            params.append(task)
        else:
            clauses.append("task LIKE ? ESCAPE '\\' COLLATE NOCASE")
            params.append(f"%{escape_like(task)}%")

    if interval_value:
        delta = parse_pg_interval(interval_value)
        end_dt = datetime.now().astimezone()
        start_dt = apply_interval(end_dt, delta, direction=-1)
        clauses.append("start_ts >= ?")
        params.append(start_dt.timestamp())
        clauses.append("start_ts <= ?")
        params.append(end_dt.timestamp())
    else:
        if start_value:
            start_dt = parse_datetime_value(calendar_value, start_value, allow_fallback)
            clauses.append("start_ts >= ?")
            params.append(start_dt.timestamp())
        if end_value:
            end_dt = parse_datetime_value(calendar_value, end_value, allow_fallback)
            clauses.append("start_ts <= ?")
            params.append(end_dt.timestamp())

    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    with connect(db_path) as conn:
        records = fetch_records(conn, clauses, params)

        if not records:
            click.echo("No records matched the delete filters.")
            return

        total_duration = sum(record.duration_hours for record in records)
        if not assume_yes:
            click.echo(f"Records to delete: {len(records)}")
            click.echo(f"Total duration (hours): {format_total_value(total_duration)}")
            if not click.confirm("Proceed with deletion?"):
                click.echo("Deletion canceled.")
                return

        if backup:
            rows = [record_to_row(record) for record in records]
            headers = [
                "id",
                "project",
                "task",
                "start_gregorian",
                "start_jalali",
                "start_epoch",
                "end_gregorian",
                "end_jalali",
                "end_epoch",
                "duration_hours",
            ]
            backup_path = build_output_path(None, "-backup.xlsx")
            write_excel(backup_path, rows, headers)
            click.echo(f"Backup written to {backup_path}")

        delete_query = "DELETE FROM time_entries"
        if clauses:
            delete_query += " WHERE " + " AND ".join(clauses)
        conn.execute(delete_query, params)

    click.echo("Delete completed.")


def _normalize_group_by(group_by: tuple[str, ...]) -> list[str]:
    items: list[str] = []
    for value in group_by:
        for part in value.split(","):
            part = part.strip().lower()
            if not part:
                continue
            if part not in {"project", "task", "year", "month", "day"}:
                raise click.ClickException(f"Unknown group-by field: {part}")
            if part not in items:
                items.append(part)
    return items


def _validate_regex(pattern: str, enabled: bool) -> None:
    if not enabled:
        return
    try:
        re.compile(pattern)
    except re.error as exc:
        raise click.ClickException(f"Invalid regex pattern: {exc}") from exc


def _parse_full_date_minutes(
    calendar: str,
    value: str,
    label: str,
    allow_fallback: bool = False,
) -> datetime:
    _date_part, time_part, _tz_part = split_datetime_parts(value.strip())
    if not time_part or ":" not in time_part:
        raise click.ClickException(f"{label} time must include hours and minutes.")

    def parse_for(cal: str) -> datetime:
        date_parts, time_parts, tzinfo, time_provided = parse_full_date(cal, value)
        if not time_provided:
            raise click.ClickException(f"{label} time must include hours and minutes.")
        validate_date(cal, date_parts.year, date_parts.month, date_parts.day)
        validate_time(time_parts.hour, time_parts.minute, time_parts.second, time_parts.microsecond)
        return build_datetime(cal, date_parts, time_parts, tzinfo)

    try:
        dt = parse_for(calendar)
    except click.ClickException:
        if allow_fallback and calendar == "jalali":
            return _parse_datetime_fallback(calendar, value, None, full_date=True, label=label)
        raise

    if allow_fallback and calendar == "jalali":
        return _parse_datetime_fallback(calendar, value, dt, full_date=True, label=label)
    return dt


def _parse_datetime_fallback(
    calendar: str,
    value: str,
    primary_dt: Optional[datetime],
    full_date: bool = False,
    label: str = "date",
) -> datetime:
    now = datetime.now().astimezone()
    threshold = now - timedelta(days=365)
    if primary_dt is None:
        try:
            primary_dt, _ = parse_interval_endpoint(calendar, value)
        except click.ClickException:
            primary_dt = None
    if primary_dt is not None and primary_dt >= threshold:
        return primary_dt

    try:
        if full_date:
            date_parts, time_parts, tzinfo, time_provided = parse_full_date("gregorian", value)
            if not time_provided:
                raise click.ClickException(f"{label} time must include hours and minutes.")
            validate_date("gregorian", date_parts.year, date_parts.month, date_parts.day)
            validate_time(time_parts.hour, time_parts.minute, time_parts.second, time_parts.microsecond)
            g_dt = build_datetime("gregorian", date_parts, time_parts, tzinfo)
        else:
            g_dt, _ = parse_interval_endpoint("gregorian", value)
    except click.ClickException:
        return primary_dt if primary_dt is not None else parse_interval_endpoint(calendar, value)[0]

    if primary_dt is None:
        return g_dt

    primary_delta = abs((primary_dt - now).total_seconds())
    gregorian_delta = abs((g_dt - now).total_seconds())
    if gregorian_delta < primary_delta:
        return g_dt
    return primary_dt


def _end_entries(
    db_path: Path,
    entry_id: Optional[int],
    project: Optional[str],
    task: Optional[str],
    emit: bool,
    allow_empty: bool,
) -> None:
    now = datetime.now().astimezone()
    clauses = ["end_ts IS NULL"]
    params: list[object] = []

    if entry_id is not None:
        clauses.append("id = ?")
        params.append(entry_id)
    if project:
        clauses.append("project = ?")
        params.append(project)
    if task:
        clauses.append("task = ?")
        params.append(task)

    query = "SELECT id, project, task, start_ts FROM time_entries WHERE " + " AND ".join(clauses)
    query += " ORDER BY start_ts DESC, id DESC"

    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
        if not rows:
            if allow_empty:
                return
            raise click.ClickException("No unfinished entry matches the provided filters.")

        for row in rows:
            start_dt = datetime.fromtimestamp(row["start_ts"], tz=local_timezone())
            if now < start_dt:
                raise click.ClickException("Cannot end an entry before its start time.")

        conn.executemany(
            "UPDATE time_entries SET end_ts = ? WHERE id = ?",
            [(now.timestamp(), row["id"]) for row in rows],
        )

    if not emit:
        return

    for row in rows:
        start_dt = datetime.fromtimestamp(row["start_ts"], tz=local_timezone())
        duration_hours = (now - start_dt).total_seconds() / 3600
        click.echo(f"Id: {row['id']}")
        click.echo(f"Project: {row['project'] or ''}")
        click.echo(f"Task: {row['task']}")
        emit_datetime_block("Start:", start_dt)
        emit_datetime_block("End:", now)
        click.echo(f"Duration (hours): {format_total_value(duration_hours)}")
        if duration_hours > 5:
            click.echo("Warning: duration exceeds 5 hours. You can edit this row afterwards if needed.")
        click.echo("")


if __name__ == "__main__":
    time_cli()
