"""Time tracking backed by SQLite (``pytime``).

Entries live in a single-table SQLite database, so the data stays readable
with any SQLite client and syncs as one file. Both calendars are recorded on
output; internally everything is a UTC-based epoch timestamp.
"""

from __future__ import annotations

import json
import os
import re
import signal
import sqlite3
import time as time_module
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import click

from pytoolbox.core import activity_service, console, procinfo
from pytoolbox.core.activewindow import (
    backend_hint,
    detect_backend,
    get_active_window,
    get_idle_seconds,
    is_screen_locked,
)
from pytoolbox.core.activity_blocks import fold_short_entries, suggest_blocks
from pytoolbox.core.activity_rules import (
    Classified,
    Rules,
    RulesError,
    classify_window,
    default_rules_path,
    load_rules,
)
from pytoolbox.core.activity_shell import SHELLS, snippet
from pytoolbox.core.intervals import (
    apply_interval,
    format_duration,
    format_hours_minutes,
    parse_pg_interval,
)
from pytoolbox.core.options import (
    CONTEXT_SETTINGS,
    AliasedGroup,
    format_option,
    version_option,
)
from pytoolbox.core.tables import emit as emit_rows
from pytoolbox.core.tables import render_table, suffix_for, write_excel
from pytoolbox.pyjdate import (
    DateParts,
    TimeParts,
    build_datetime,
    format_datetime,
    format_total_value,
    format_unix_timestamp,
    gregorian_to_jalali,
    local_timezone,
    normalize_calendar,
    parse_full_date,
    parse_interval_endpoint,
    split_datetime_parts,
    validate_date,
    validate_time,
)

DEFAULT_DB_PATH = Path.home() / ".pytime" / "pytime.db"
DEFAULT_OUTPUT_PREFIX = "pytime"

#: Overrides the database location without passing --db every time.
DB_ENV_VAR = "PYTIME_DB"

#: Entries longer than this are flagged as probably-forgotten timers.
LONG_ENTRY_HOURS = 5


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
class ActivityRecord:
    """Normalized auto-tracked activity entry data (see ``pytime auto``)."""

    entry_id: int
    category: str
    app: str
    project: Optional[str]
    detail: Optional[str]
    ext: Optional[str]
    start_dt: datetime
    end_dt: Optional[datetime]
    duration_hours: float


def resolve_db_path(db_path: Optional[Path]) -> Path:
    """Return the database path, creating its parent directory when needed.

    Precedence: explicit ``--db``, then ``$PYTIME_DB``, then the default under
    the home directory (kept at ``~/.pytime`` so existing databases keep
    working after an upgrade).
    """
    if db_path is None:
        env_value = os.environ.get(DB_ENV_VAR)
        db_path = Path(env_value) if env_value else DEFAULT_DB_PATH
    path = db_path.expanduser()
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

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            app TEXT NOT NULL,
            project TEXT,
            detail TEXT,
            ext TEXT,
            start_ts REAL NOT NULL,
            end_ts REAL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_entries_start ON activity_entries(start_ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_entries_end ON activity_entries(end_ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_entries_project ON activity_entries(project)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_entries_ext ON activity_entries(ext)")
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

    now = datetime.now().astimezone().replace(microsecond=0)
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


def fetch_activity_records(
    conn: sqlite3.Connection,
    clauses: Iterable[str],
    params: list[object],
) -> list[ActivityRecord]:
    """Fetch auto-tracked activity entries from the database and normalize them."""
    query = "SELECT id, category, app, project, detail, ext, start_ts, end_ts FROM activity_entries"
    clause_list = list(clauses)
    if clause_list:
        query += " WHERE " + " AND ".join(clause_list)
    query += " ORDER BY start_ts ASC, id ASC"

    now = datetime.now().astimezone().replace(microsecond=0)
    records = []
    for row in conn.execute(query, params).fetchall():
        start_dt = datetime.fromtimestamp(row["start_ts"], tz=local_timezone())
        end_dt = datetime.fromtimestamp(row["end_ts"], tz=local_timezone()) if row["end_ts"] is not None else None
        duration_end = end_dt or now
        duration_hours = (duration_end - start_dt).total_seconds() / 3600
        records.append(
            ActivityRecord(
                entry_id=row["id"],
                category=row["category"],
                app=row["app"],
                project=row["project"],
                detail=row["detail"],
                ext=row["ext"],
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


def activity_record_to_row(record: ActivityRecord, include_end: bool = True) -> dict[str, object]:
    """Convert an activity record to a printable/exportable row."""
    start_triplet = format_dt_triplet(record.start_dt)
    row: dict[str, object] = {
        "id": record.entry_id,
        "category": record.category,
        "app": record.app,
        "project": record.project or "",
        "detail": record.detail or "",
        "ext": record.ext or "",
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


def _group_records_generic(
    records: Sequence,
    group_by_set: set[str],
    dimension_fields: Sequence[str],
    calendar: str,
) -> tuple[list[dict[str, object]], list[str]]:
    """Group records by any mix of ``dimension_fields`` plus year/month/day.

    Shared by ``group_records`` (project/task) and ``group_activity_records``
    (category/app/project/ext), which only differ in which attributes on the
    record identify a group.
    """
    grouped: dict[tuple[object, ...], dict[str, object]] = {}

    for record in records:
        parts: list[object] = []
        values: dict[str, object] = {}

        for field_name in dimension_fields:
            if field_name in group_by_set:
                values[field_name] = getattr(record, field_name) or ""
                parts.append(values[field_name])

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

    headers: list[str] = [field for field in dimension_fields if field in group_by_set]

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


def group_records(
    records: list[TimeRecord],
    group_by: list[str],
    calendar: str,
) -> tuple[list[dict[str, object]], list[str]]:
    """Group time entries and return aggregated rows with headers."""
    return _group_records_generic(records, set(group_by), ("project", "task"), calendar)


def group_activity_records(
    records: list[ActivityRecord],
    group_by: list[str],
    calendar: str,
) -> tuple[list[dict[str, object]], list[str]]:
    """Group auto-tracked activity entries and return aggregated rows with headers."""
    return _group_records_generic(records, set(group_by), ("category", "app", "project", "ext"), calendar)


@click.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
@click.option(
    "--db",
    "db_path",
    type=click.Path(dir_okay=False, path_type=Path),
    help="SQLite database path (default: $PYTIME_DB or ~/.pytime/pytime.db).",
)
@version_option
@click.pass_context
def time_cli(ctx: click.Context, db_path: Optional[Path]) -> None:
    """Track where your time goes, stored in a single SQLite file.

    \b
    Examples:
      pytime start -p toolbox "write docs"
      pytime status
      pytime end
      pytime resume
      pytime report --interval "7 days" --group-by project
      pytime report --format markdown -o week.md
    """
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = resolve_db_path(db_path)


@time_cli.command()
@click.option("-p", "--project", type=str, help="Project name (optional).")
@click.argument("task", type=str)
def start(project: Optional[str], task: str) -> None:
    """Start timing a task, closing any entry still running.

    \b
    Examples:
      pytime start "review PR"
      pytime start -p toolbox "write docs"
    """
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    now = datetime.now().astimezone().replace(microsecond=0)
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
    """Stop the running entry.

    \b
    Examples:
      pytime end
      pytime end --task "write docs"
      pytime end -i 12
    """
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    _end_entries(db_path, entry_id=entry_id, project=project, task=task, emit=True, allow_empty=False)


@time_cli.command()
@click.option("--json", "as_json", is_flag=True, help="Print status as JSON.")
def status(as_json: bool) -> None:
    """Show what is currently being timed, if anything.

    \b
    Examples:
      pytime status
      pytime status --json
    """
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    with connect(db_path) as conn:
        records = fetch_records(conn, ["end_ts IS NULL"], [])

    if not records:
        if as_json:
            console.emit_json({"running": False})
            return
        console.result("Nothing is being timed. Start with: pytime start \"task name\"")
        return

    payload = []
    for record in records:
        elapsed = record.duration_hours * 3600
        payload.append(
            {
                "id": record.entry_id,
                "project": record.project or "",
                "task": record.task,
                "started": record.start_dt.isoformat(),
                "elapsed": format_duration(elapsed),
                "elapsed_hours": round(record.duration_hours, 4),
            }
        )
    if as_json:
        console.emit_json({"running": True, "entries": payload})
        return
    for entry, record in zip(payload, records):
        console.result(f"Id: {entry['id']}")
        console.result(f"Project: {entry['project']}")
        console.result(f"Task: {entry['task']}")
        emit_datetime_block("Start:", record.start_dt)
        console.result(f"Elapsed: {entry['elapsed']} ({format_hours_minutes(record.duration_hours)})")


@time_cli.command()
@click.option("-i", "--id", "entry_id", type=int, help="Entry to copy (default: the most recent one).")
def resume(entry_id: Optional[int]) -> None:
    """Start a new entry with the same project and task as a previous one.

    \b
    Examples:
      pytime resume
      pytime resume -i 12
    """
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    with connect(db_path) as conn:
        if entry_id is None:
            row = conn.execute(
                "SELECT id, project, task FROM time_entries ORDER BY start_ts DESC, id DESC LIMIT 1"
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, project, task FROM time_entries WHERE id = ?", (entry_id,)
            ).fetchone()
    if row is None:
        raise click.ClickException("No entry to resume.")

    now = datetime.now().astimezone().replace(microsecond=0)
    _end_entries(db_path, entry_id=None, project=None, task=None, emit=True, allow_empty=True)
    with connect(db_path) as conn:
        cursor = conn.execute(
            "INSERT INTO time_entries (project, task, start_ts) VALUES (?, ?, ?)",
            (row["project"], row["task"], now.timestamp()),
        )
        new_id = cursor.lastrowid

    click.echo(f"Id: {new_id}")
    click.echo(f"Project: {row['project'] or ''}")
    click.echo(f"Task: {row['task']}")
    emit_datetime_block("Start:", now)


@time_cli.command("projects")
@click.option("--json", "as_json", is_flag=True, help="Print as JSON.")
def projects_command(as_json: bool) -> None:
    """List known projects with their entry counts and total hours.

    \b
    Examples:
      pytime projects
      pytime projects --json
    """
    _list_dimension("project", as_json)


@time_cli.command("tasks")
@click.option("-p", "--project", type=str, help="Only tasks in this project.")
@click.option("--json", "as_json", is_flag=True, help="Print as JSON.")
def tasks_command(project: Optional[str], as_json: bool) -> None:
    """List known tasks with their entry counts and total hours.

    \b
    Examples:
      pytime tasks
      pytime tasks -p toolbox
    """
    _list_dimension("task", as_json, project=project)


def _list_dimension(field: str, as_json: bool, project: Optional[str] = None) -> None:
    """Summarise entries grouped by ``project`` or ``task``."""
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    clauses: list[str] = []
    params: list[object] = []
    if project:
        clauses.append("project LIKE ? ESCAPE '\\' COLLATE NOCASE")
        params.append(f"%{escape_like(project)}%")

    with connect(db_path) as conn:
        records = fetch_records(conn, clauses, params)

    if not records:
        console.result("No records found.")
        return

    totals: dict[str, dict[str, float]] = {}
    for record in records:
        key = (record.project or "") if field == "project" else record.task
        bucket = totals.setdefault(key, {"entries": 0, "hours": 0.0, "last": 0.0})
        bucket["entries"] += 1
        bucket["hours"] += record.duration_hours
        bucket["last"] = max(bucket["last"], record.start_dt.timestamp())

    rows = [
        {
            field: key or "(none)",
            "entries": int(value["entries"]),
            "hours": format_total_value(round(value["hours"], 3)),
            "last_used": datetime.fromtimestamp(value["last"], tz=local_timezone()).strftime("%Y-%m-%d"),
        }
        for key, value in sorted(totals.items(), key=lambda item: item[1]["hours"], reverse=True)
    ]
    emit_rows(rows, [field, "entries", "hours", "last_used"], "json" if as_json else "table")



#: Columns of an ungrouped report, one row per tracked entry.
_RECORD_HEADERS = [
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

#: Columns of an ungrouped ``pytime auto report``, one row per activity entry.
_ACTIVITY_HEADERS = [
    "id",
    "category",
    "app",
    "project",
    "detail",
    "ext",
    "start_gregorian",
    "start_jalali",
    "start_epoch",
    "end_gregorian",
    "end_jalali",
    "end_epoch",
    "duration_hours",
]


def _entry_filters(
    entry_id: Optional[int],
    project: Optional[str],
    task: Optional[str],
    regex: bool,
    interval_value: Optional[str],
    start_value: Optional[str],
    end_value: Optional[str],
    calendar_value: Optional[str],
    allow_fallback: bool,
) -> tuple[list[str], list[object]]:
    """Turn the shared entry-selection options into SQL clauses and parameters.

    ``report`` and ``delete`` offer the same filters and must agree on what
    they select, so they ask the same question here rather than each building
    the clause list themselves.
    """
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
    return clauses, params


def _emit_report(
    rows: Sequence[dict],
    headers: Sequence[str],
    records: Sequence,
    output_format: str,
    output: Optional[str],
    no_total: bool,
) -> None:
    """Render a finished report as a table, or export it in another format."""
    output_format = output_format.lower()
    total_hours = sum(record.duration_hours for record in records)

    if output_format == "table":
        table = render_table(rows, headers)
        footer = (
            ""
            if no_total
            else (
                f"\nTotal: {format_total_value(round(total_hours, 3))} hours "
                f"({format_hours_minutes(total_hours)}) "
                f"across {len(records)} entr{'y' if len(records) == 1 else 'ies'}"
            )
        )
        if output:
            path = Path(output).expanduser()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(table + footer + "\n", encoding="utf-8")
            click.echo(f"Report written to {path}", err=True)
        else:
            click.echo(table + footer)
        return

    if output_format == "json" and not output:
        emit_rows(rows, headers, "json")
        return
    path = build_output_path(output, suffix_for(output_format))
    emit_rows(rows, headers, output_format, path)


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
@format_option()
@click.option("-o", "--output", type=str, help="Write the report to this file instead of stdout.")
@click.option("--no-total", is_flag=True, help="Omit the totals line under table output.")
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
    no_total: bool,
) -> None:
    """Report on tracked time, optionally grouped and exported.

    \b
    Examples:
      pytime report --interval "7 days"
      pytime report -p toolbox -g project -g year,month,day -c g
      pytime report --format json
      pytime report --format excel -o ./report.xlsx
    """
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

    clauses, params = _entry_filters(
        entry_id, project, task, regex, interval_value,
        start_value, end_value, calendar_value, allow_fallback,
    )

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
        headers = _RECORD_HEADERS

    _emit_report(rows, headers, records, output_format, output, no_total)


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
    if duration_hours > LONG_ENTRY_HOURS:
        console.warn(f"duration exceeds {LONG_ENTRY_HOURS} hours -- did a timer stay running? Use `pytime edit` to fix it.")


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

    clauses, params = _entry_filters(
        entry_id, project, task, regex, interval_value,
        start_value, end_value, calendar_value, allow_fallback,
    )

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


def _normalize_group_by_fields(group_by: tuple[str, ...], allowed: set[str]) -> list[str]:
    items: list[str] = []
    for value in group_by:
        for part in value.split(","):
            part = part.strip().lower()
            if not part:
                continue
            if part not in allowed:
                raise click.ClickException(f"Unknown group-by field: {part}")
            if part not in items:
                items.append(part)
    return items


def _normalize_group_by(group_by: tuple[str, ...]) -> list[str]:
    return _normalize_group_by_fields(group_by, {"project", "task", "year", "month", "day"})


def _normalize_activity_group_by(group_by: tuple[str, ...]) -> list[str]:
    return _normalize_group_by_fields(group_by, {"category", "app", "project", "ext", "year", "month", "day"})


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
    now = datetime.now().astimezone().replace(microsecond=0)
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
    now = datetime.now().astimezone().replace(microsecond=0)
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
        if duration_hours > LONG_ENTRY_HOURS:
            console.warn(f"duration exceeds {LONG_ENTRY_HOURS} hours -- did a timer stay running? Use `pytime edit` to fix it.")
        click.echo("")


# --------------------------------------------------------------------------
# pytime auto -- best-effort automatic tracking from the active window.
# --------------------------------------------------------------------------


def _activity_bucket_key(classified: Classified) -> tuple[str, str, str, str, str]:
    return (classified.category, classified.app, classified.project, classified.detail, classified.ext)


def _close_activity_entry(conn: sqlite3.Connection, entry_id: int, end_ts: float) -> None:
    # MAX: an idle pause backdates the end, which must not precede the start.
    conn.execute("UPDATE activity_entries SET end_ts = MAX(?, start_ts) WHERE id = ?", (end_ts, entry_id))
    conn.commit()


def _open_activity_entry(conn: sqlite3.Connection, classified: Classified, start_ts: float) -> int:
    cursor = conn.execute(
        "INSERT INTO activity_entries (category, app, project, detail, ext, start_ts) VALUES (?, ?, ?, ?, ?, ?)",
        (classified.category, classified.app, classified.project, classified.detail, classified.ext, start_ts),
    )
    conn.commit()
    return cursor.lastrowid


#: Current auto-tracking state: (open entry id, its bucket key), or None.
ActivityState = Optional[tuple[int, tuple[str, str, str, str, str]]]


def advance_activity(
    conn: sqlite3.Connection,
    current: ActivityState,
    classified: Optional[Classified],
    now_ts: float,
) -> ActivityState:
    """Apply one polling tick, opening/closing activity entries as needed.

    ``classified`` is ``None`` when there is no active window to report, or
    the user is judged AFK. Kept dependency-free of the polling loop itself
    so it can be unit-tested with a scripted sequence of ticks.
    """
    if classified is None:
        if current is not None:
            _close_activity_entry(conn, current[0], now_ts)
        return None

    key = _activity_bucket_key(classified)
    if current is not None and current[1] == key:
        return current

    if current is not None:
        _close_activity_entry(conn, current[0], now_ts)

    entry_id = _open_activity_entry(conn, classified, now_ts)
    return (entry_id, key)


@time_cli.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
def auto() -> None:
    """Automatically record time from the active window (best-effort).

    \b
    Built from window titles, not a browser extension or editor plugin --
    it cannot see real file paths or URLs, only what the window title shows.
    See docs/pytime.md for exactly what it can and cannot know. Entries live
    in their own table, entirely separate from manual pytime entries.

    \b
    Examples:
      pytime auto service install --now    # track at every login
      pytime auto today
      pytime auto report -g project -g ext
      pytime auto suggest --apply          # turn today into manual entries
      eval "$(pytime auto shell-init bash)"
    """


def _load_rules_or_fail() -> Rules:
    try:
        return load_rules()
    except RulesError as exc:
        raise click.ClickException(str(exc)) from exc


@auto.command()
@click.option("--interval", type=float, default=5.0, show_default=True, help="Seconds between window checks.")
@click.option(
    "--idle-timeout",
    type=float,
    default=5.0,
    show_default=True,
    help="Minutes without keyboard/mouse input before pausing (GNOME, or X11 with xprintidle).",
)
@click.option("--quiet", is_flag=True, help="Only print pauses, not every activity change.")
def watch(interval: float, idle_timeout: float, quiet: bool) -> None:
    """Poll the active window and record time automatically until interrupted.

    \b
    Runs in the foreground. To run it at every login instead, use
    `pytime auto service install --now`. Ctrl+C or SIGTERM closes the open
    entry cleanly. Pauses while the screen is locked or you're idle.

    \b
    Examples:
      pytime auto watch
      pytime auto watch --interval 10 --idle-timeout 10
    """
    backend = detect_backend()
    if backend is None:
        raise click.ClickException(f"No supported window backend found. {backend_hint()}")
    rules = _load_rules_or_fail()
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])

    click.echo(f"pytime auto: watching via {backend}, every {interval:g}s. Press Ctrl+C to stop.", err=True)
    pause_reason = ""

    stop = {"flag": False}

    def _handle_signal(signum: int, frame: object) -> None:
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    current: ActivityState = None
    idle_seconds_threshold = idle_timeout * 60
    with connect(db_path) as conn:
        try:
            while not stop["flag"]:
                now_ts = datetime.now().timestamp()
                idle = get_idle_seconds()
                classified: Optional[Classified] = None
                if is_screen_locked():
                    pause_reason = "screen locked"
                elif idle is not None and idle >= idle_seconds_threshold:
                    pause_reason = "idle"
                    now_ts -= idle
                else:
                    window = get_active_window(backend)
                    if window is None:
                        pause_reason = "no focused window"
                    else:
                        classified = classify_window(window, rules)
                        if classified is None:
                            pause_reason = "ignored window"

                previous_key = current[1] if current else None
                current = advance_activity(conn, current, classified, now_ts)
                new_key = current[1] if current else None

                if new_key != previous_key:
                    if new_key is None:
                        click.echo(f"(paused: {pause_reason})", err=True)
                    elif not quiet:
                        category, app, project, detail, _ext = new_key
                        label = f"{app} [{category}] project={project or '-'}"
                        if detail:
                            label += f" | {detail[:80]}"
                        click.echo(label, err=True)

                for _ in range(int(interval * 10)):
                    if stop["flag"]:
                        break
                    time_module.sleep(0.1)
        finally:
            if current is not None:
                _close_activity_entry(conn, current[0], datetime.now().timestamp())


@auto.command("status")
@click.option("--json", "as_json", is_flag=True, help="Print status as JSON.")
def auto_status(as_json: bool) -> None:
    """Show what pytime auto is currently tracking, if a watcher is running.

    \b
    Examples:
      pytime auto status
      pytime auto status --json
    """
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    with connect(db_path) as conn:
        records = fetch_activity_records(conn, ["end_ts IS NULL"], [])

    if not records:
        if as_json:
            console.emit_json({"running": False})
            return
        console.result("Nothing is being auto-tracked. Start with: pytime auto watch")
        return

    record = records[-1]
    elapsed = record.duration_hours * 3600
    payload = {
        "category": record.category,
        "app": record.app,
        "project": record.project or "",
        "detail": record.detail or "",
        "ext": record.ext or "",
        "started": record.start_dt.isoformat(),
        "elapsed": format_duration(elapsed),
        "elapsed_hours": round(record.duration_hours, 4),
    }
    if as_json:
        console.emit_json({"running": True, **payload})
        return
    console.result(f"App: {payload['app']}")
    console.result(f"Project: {payload['project']}")
    if payload["detail"]:
        console.result(f"Detail: {payload['detail']}")
    emit_datetime_block("Start:", record.start_dt)
    console.result(f"Elapsed: {payload['elapsed']} ({format_hours_minutes(record.duration_hours)})")


_ACTIVITY_FILTER_OPTIONS = (
    click.option("-i", "--id", "entry_id", type=int, help="Entry id filter (optional)."),
    click.option("-p", "--project", type=str, help="Project filter, substring (optional)."),
    click.option("--no-project", is_flag=True, help="Only entries with no project."),
    click.option("--app", type=str, help="App filter, substring (optional)."),
    click.option(
        "--category",
        type=click.Choice(["editor", "terminal", "browser", "other"], case_sensitive=False),
        help="Category filter (optional).",
    ),
    click.option("--ext", type=str, help="File extension filter (optional)."),
    click.option(
        "-q", "--search", type=str, help="Substring of the captured title/file/page (the detail column)."
    ),
    click.option(
        "--interval",
        "interval_value",
        type=str,
        help="Relative interval (PostgreSQL style). When set, start/end are ignored.",
    ),
    click.option("-s", "--start", "start_value", type=str, help="Start time (optional)."),
    click.option("-e", "--end", "end_value", type=str, help="End time (optional)."),
    click.option(
        "-c",
        "--calendar",
        type=click.Choice(["gregorian", "jalali", "g", "j"], case_sensitive=False),
        help="Calendar for date inputs/grouping (jalali/gregorian).",
    ),
)


def _activity_filter_options(func):
    for option in reversed(_ACTIVITY_FILTER_OPTIONS):
        func = option(func)
    return func


def _activity_filters(
    entry_id: Optional[int],
    project: Optional[str],
    no_project: bool,
    app: Optional[str],
    category: Optional[str],
    ext: Optional[str],
    search: Optional[str],
    interval_value: Optional[str],
    start_value: Optional[str],
    end_value: Optional[str],
    calendar_value: str,
    allow_fallback: bool,
) -> tuple[list[str], list[object]]:
    """SQL clauses for the filters ``auto report`` and ``auto delete`` share."""
    if interval_value and (start_value or end_value):
        raise click.ClickException("Interval is incompatible with start/end filters.")
    if project and no_project:
        raise click.ClickException("Use either --project or --no-project, not both.")

    clauses: list[str] = []
    params: list[object] = []
    if entry_id is not None:
        clauses.append("id = ?")
        params.append(entry_id)
    if no_project:
        clauses.append("(project IS NULL OR project = '')")
    for column, value in (("project", project), ("app", app), ("detail", search)):
        if value:
            clauses.append(f"{column} LIKE ? ESCAPE '\\' COLLATE NOCASE")
            params.append(f"%{escape_like(value)}%")
    if category:
        clauses.append("category = ?")
        params.append(category.lower())
    if ext:
        clauses.append("ext = ?")
        params.append(ext.lower().lstrip("."))

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
    return clauses, params


@auto.command("report")
@_activity_filter_options
@click.option(
    "-g",
    "--group-by",
    "group_by",
    multiple=True,
    help="Group by fields (category, app, project, ext, year, month, day).",
)
@format_option()
@click.option("-o", "--output", type=str, help="Write the report to this file instead of stdout.")
@click.option("--no-total", is_flag=True, help="Omit the totals line under table output.")
@click.option(
    "--min-seconds",
    type=float,
    default=10.0,
    show_default=True,
    help="Fold back-to-back entries shorter than this into the one before (0 shows every entry).",
)
def auto_report(
    entry_id: Optional[int],
    project: Optional[str],
    no_project: bool,
    app: Optional[str],
    category: Optional[str],
    ext: Optional[str],
    search: Optional[str],
    interval_value: Optional[str],
    start_value: Optional[str],
    end_value: Optional[str],
    calendar: Optional[str],
    group_by: tuple[str, ...],
    output_format: str,
    output: Optional[str],
    no_total: bool,
    min_seconds: float,
) -> None:
    """Report on auto-tracked time, optionally filtered, grouped and exported.

    \b
    Quick switches (under --min-seconds) are folded into the entry before
    them, so an alt-tab through three windows doesn't become three rows.
    Stored entries are never changed; pass --min-seconds 0 to see them all.

    \b
    Examples:
      pytime auto report --interval "7 days"
      pytime auto report -g app -g project
      pytime auto report --category editor -g ext
      pytime auto report -q chatgpt                # search captured titles
      pytime auto report --no-project -g app       # what's still unattributed
      pytime auto report --format json
    """
    group_items = _normalize_activity_group_by(group_by)
    if {"month", "day"} & set(group_items) and "year" not in group_items:
        raise click.ClickException("Grouping by month/day requires year.")
    if "day" in group_items and "month" not in group_items:
        raise click.ClickException("Grouping by day requires month.")

    calendar_value, allow_fallback = parse_calendar(calendar)
    clauses, params = _activity_filters(
        entry_id, project, no_project, app, category, ext, search,
        interval_value, start_value, end_value, calendar_value, allow_fallback,
    )

    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    with connect(db_path) as conn:
        records = fetch_activity_records(conn, clauses, params)

    if not records:
        click.echo("No records found.")
        return

    records = fold_short_entries(records, min_seconds, datetime.now().astimezone())
    if group_items:
        rows, headers = group_activity_records(records, group_items, calendar_value)
    else:
        rows = [activity_record_to_row(record) for record in records]
        headers = _ACTIVITY_HEADERS

    _emit_report(rows, headers, records, output_format, output, no_total)


def _since_midnight(days: int) -> datetime:
    """Start of the local day ``days - 1`` days ago (``1`` = today)."""
    today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=max(days, 1) - 1)


def _fetch_activity_since(start: datetime) -> list[ActivityRecord]:
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    with connect(db_path) as conn:
        return fetch_activity_records(conn, ["COALESCE(end_ts, ?) > ?"], [datetime.now().timestamp(), start.timestamp()])


def _clip_to(records: Sequence[ActivityRecord], start: datetime) -> list[ActivityRecord]:
    """Drop the part of each entry before ``start`` (an entry running over midnight)."""
    clipped = []
    for record in records:
        if record.start_dt < start:
            trimmed = (start - record.start_dt).total_seconds() / 3600
            record = replace(record, start_dt=start, duration_hours=max(record.duration_hours - trimmed, 0.0))
        clipped.append(record)
    return clipped


def _share_rows(records: Sequence[ActivityRecord], field_name: str, total: float, top: int) -> list[dict]:
    totals: dict[str, float] = {}
    for record in records:
        key = getattr(record, field_name) or "(none)"
        totals[key] = totals.get(key, 0.0) + record.duration_hours
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)[:top]
    return [
        {
            field_name: key,
            "hours": format_total_value(round(hours, 2)),
            "time": format_hours_minutes(hours),
            "share": f"{hours / total * 100:.0f}%" if total else "-",
        }
        for key, hours in ranked
    ]


@auto.command("today")
@click.option("--days", type=int, default=1, show_default=True, help="Cover this many days, ending today.")
@click.option("--top", type=int, default=10, show_default=True, help="Rows per table.")
@click.option("--json", "as_json", is_flag=True, help="Print as JSON.")
def auto_today(days: int, top: int, as_json: bool) -> None:
    """Summarize today's auto-tracked time by project and by app.

    \b
    Examples:
      pytime auto today
      pytime auto today --days 7
      pytime auto today --json
    """
    start = _since_midnight(days)
    records = _clip_to(_fetch_activity_since(start), start)
    total = sum(record.duration_hours for record in records)
    projects = _share_rows(records, "project", total, top)
    apps = _share_rows(records, "app", total, top)

    if as_json:
        console.emit_json(
            {"since": start.isoformat(), "total_hours": round(total, 4), "projects": projects, "apps": apps}
        )
        return
    if not records:
        console.result("Nothing auto-tracked yet. Start with: pytime auto watch")
        return
    label = "today" if days <= 1 else f"last {days} days"
    console.result(f"Auto-tracked {label}: {format_hours_minutes(total)} ({format_total_value(round(total, 2))} h)")
    console.result("")
    console.result(render_table(projects, ["project", "hours", "time", "share"]))
    console.result("")
    console.result(render_table(apps, ["app", "hours", "time", "share"]))


@auto.command("suggest")
@click.option("--days", type=int, default=1, show_default=True, help="Cover this many days, ending today.")
@click.option(
    "--merge-gap",
    type=float,
    default=10.0,
    show_default=True,
    help="Minutes: breaks and detours this short don't split a block.",
)
@click.option("--min-block", type=float, default=15.0, show_default=True, help="Minutes: drop shorter blocks.")
@click.option("-t", "--task", type=str, default="auto-tracked", show_default=True, help="Task name for created entries.")
@click.option("--apply", "apply_", is_flag=True, help="Add the suggested blocks as manual pytime entries.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="With --apply, don't ask for confirmation.")
def auto_suggest(
    days: int,
    merge_gap: float,
    min_block: float,
    task: str,
    apply_: bool,
    assume_yes: bool,
) -> None:
    """Propose manual time entries built from auto-tracked time.

    \b
    Joins a project's entries across short breaks, absorbs brief detours
    (a two-minute look at chat inside an hour of coding), and drops
    unattributed time. Blocks overlapping an existing manual entry are
    skipped, so running it twice never double-counts.

    \b
    Examples:
      pytime auto suggest
      pytime auto suggest --days 2 --merge-gap 15
      pytime auto suggest --apply
    """
    start = _since_midnight(days)
    now = datetime.now().astimezone()
    records = _clip_to(_fetch_activity_since(start), start)
    blocks = suggest_blocks(records, now, merge_gap * 60, min_block * 60)
    if not blocks:
        console.result("No blocks to suggest (nothing attributed to a project for long enough).")
        return

    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    rows = []
    with connect(db_path) as conn:
        for block in blocks:
            overlap = conn.execute(
                "SELECT id FROM time_entries WHERE start_ts < ? AND COALESCE(end_ts, ?) > ? LIMIT 1",
                (block.end.timestamp(), now.timestamp(), block.start.timestamp()),
            ).fetchone()
            rows.append(
                {
                    "project": block.project,
                    "start": block.start.strftime("%Y-%m-%d %H:%M"),
                    "end": block.end.strftime("%H:%M"),
                    "time": format_hours_minutes(block.seconds / 3600),
                    "apps": ", ".join(block.top_apps()),
                    "status": f"skip: overlaps entry {overlap['id']}" if overlap else "new",
                    "_block": block,
                }
            )

        console.result(render_table(rows, ["project", "start", "end", "time", "apps", "status"]))
        new_rows = [row for row in rows if row["status"] == "new"]
        if not apply_:
            if new_rows:
                console.result("")
                console.result("Add these with: pytime auto suggest --apply")
            return
        if not new_rows:
            console.result("Nothing new to add.")
            return
        if not assume_yes and not click.confirm(f"Add {len(new_rows)} manual entries?"):
            console.result("Canceled.")
            return
        conn.executemany(
            "INSERT INTO time_entries (project, task, start_ts, end_ts) VALUES (?, ?, ?, ?)",
            [
                (row["project"], task, row["_block"].start.timestamp(), row["_block"].end.timestamp())
                for row in new_rows
            ],
        )
    console.result(f"Added {len(new_rows)} manual entr{'y' if len(new_rows) == 1 else 'ies'}; see `pytime report`.")


@auto.command("shell-init")
@click.argument("shell", type=click.Choice(SHELLS))
def auto_shell_init(shell: str) -> None:
    """Print a shell hook that makes terminal titles say which project you're in.

    \b
    Only needed where /proc can't see what a terminal runs -- inside ssh,
    screen, or a Flatpak terminal; elsewhere (tmux included) pytime reads
    the focused tab's program and directory directly. Sets the title to
    "<command> @ <project dir>" (the git root, or the current directory),
    never a command's arguments, and sets CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1
    so Claude Code doesn't overwrite it. pytime must be on PATH when your rc
    file runs; from a virtualenv, use its full path.

    \b
    Examples:
      echo 'eval "$(pytime auto shell-init bash)"' >> ~/.bashrc
      echo 'eval "$(~/venv/bin/pytime auto shell-init zsh)"' >> ~/.zshrc
    """
    click.echo(snippet(shell), nl=False)


@auto.group("service", cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
def auto_service() -> None:
    """Run the watcher as a systemd user service, started at every login.

    \b
    Examples:
      pytime auto service install --now
      pytime auto service status
      pytime auto service logs -f
      pytime auto service disable --now
      pytime auto service uninstall
    """


def _service_call(func, *args):
    try:
        return func(*args)
    except activity_service.ServiceError as exc:
        raise click.ClickException(str(exc)) from exc


def _require_installed() -> None:
    if not activity_service.is_installed():
        raise click.ClickException("The service isn't installed. Run: pytime auto service install --now")


def _print_service_state() -> None:
    state = _service_call(activity_service.state)
    console.result(f"{activity_service.UNIT_NAME}: {state['active']}, {state['enabled']} at login")


@auto_service.command("install")
@click.option("--interval", type=float, default=5.0, show_default=True, help="Seconds between window checks.")
@click.option("--idle-timeout", type=float, default=5.0, show_default=True, help="Minutes idle before pausing.")
@click.option(
    "--target",
    type=click.Choice(activity_service.TARGETS),
    default="graphical-session.target",
    show_default=True,
    help="Start with this systemd target. Use default.target if your desktop never reaches graphical-session.target.",
)
@click.option("--now", "start_now", is_flag=True, help="Also enable it at login and start it right away.")
@click.option("--force", is_flag=True, help="Overwrite an existing unit file.")
def service_install(interval: float, idle_timeout: float, target: str, start_now: bool, force: bool) -> None:
    """Create the unit file (and optionally enable and start it)."""
    if activity_service.is_installed() and not force:
        raise click.ClickException(
            f"Already installed at {activity_service.unit_path()}. Pass --force to rewrite it."
        )
    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    unit_text = activity_service.render_unit(db_path, interval, idle_timeout, target)
    path = _service_call(activity_service.install, unit_text)
    console.result(f"Wrote {path}")
    if start_now:
        _service_call(activity_service.import_session_environment)
        _service_call(activity_service.systemctl, "enable", "--now", activity_service.UNIT_NAME)
        _print_service_state()
    else:
        console.result("Enable and start it with: pytime auto service enable --now")


@auto_service.command("uninstall")
def service_uninstall() -> None:
    """Stop and disable the service, and remove its unit file."""
    path = _service_call(activity_service.uninstall)
    console.result(f"Removed {path}" if path else "Not installed; nothing to do.")


@auto_service.command("enable")
@click.option("--now", "start_now", is_flag=True, help="Also start it right away.")
def service_enable(start_now: bool) -> None:
    """Start the service automatically at login."""
    _require_installed()
    if start_now:
        _service_call(activity_service.import_session_environment)
    args = ["enable", "--now"] if start_now else ["enable"]
    _service_call(activity_service.systemctl, *args, activity_service.UNIT_NAME)
    _print_service_state()


@auto_service.command("disable")
@click.option("--now", "stop_now", is_flag=True, help="Also stop it right away.")
def service_disable(stop_now: bool) -> None:
    """Stop starting the service at login."""
    _require_installed()
    args = ["disable", "--now"] if stop_now else ["disable"]
    _service_call(activity_service.systemctl, *args, activity_service.UNIT_NAME)
    _print_service_state()


@auto_service.command("start")
def service_start() -> None:
    """Start the service now (without changing whether it starts at login)."""
    _require_installed()
    _service_call(activity_service.import_session_environment)
    _service_call(activity_service.systemctl, "start", activity_service.UNIT_NAME)
    _print_service_state()


@auto_service.command("stop")
def service_stop() -> None:
    """Stop the service now (it still starts at next login if enabled)."""
    _require_installed()
    _service_call(activity_service.systemctl, "stop", activity_service.UNIT_NAME)
    _print_service_state()


@auto_service.command("restart")
def service_restart() -> None:
    """Restart the service -- needed after editing ~/.pytime/rules.json."""
    _require_installed()
    _service_call(activity_service.import_session_environment)
    _service_call(activity_service.systemctl, "restart", activity_service.UNIT_NAME)
    _print_service_state()


@auto_service.command("status")
def service_status() -> None:
    """Show whether the service is installed, running, and enabled at login."""
    if not activity_service.is_installed():
        console.result("Not installed. Install with: pytime auto service install --now")
        return
    console.result(f"Unit file: {activity_service.unit_path()}")
    _print_service_state()


@auto_service.command("logs")
@click.option("-n", "--lines", type=int, default=50, show_default=True, help="How many recent lines.")
@click.option("-f", "--follow", is_flag=True, help="Keep printing new lines.")
def service_logs(lines: int, follow: bool) -> None:
    """Show the watcher's output (activity changes and pauses) from the journal."""
    _service_call(activity_service.logs, lines, follow)


@auto.command("delete")
@_activity_filter_options
@click.option("--all", "delete_all", is_flag=True, help="Allow deleting with no filters (every auto entry).")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Delete without confirmation.")
def auto_delete(
    entry_id: Optional[int],
    project: Optional[str],
    no_project: bool,
    app: Optional[str],
    category: Optional[str],
    ext: Optional[str],
    search: Optional[str],
    interval_value: Optional[str],
    start_value: Optional[str],
    end_value: Optional[str],
    calendar: Optional[str],
    delete_all: bool,
    assume_yes: bool,
) -> None:
    """Delete auto-tracked entries matching filters (manual entries are never touched).

    \b
    Takes the same filters as `pytime auto report`, so run the report first
    to see exactly what will go.

    \b
    Examples:
      pytime auto delete --id 12
      pytime auto delete --app Chrome -q "YouTube" --yes
      pytime auto delete --interval "1 hour"
      pytime auto delete --all --yes
    """
    calendar_value, allow_fallback = parse_calendar(calendar)
    clauses, params = _activity_filters(
        entry_id, project, no_project, app, category, ext, search,
        interval_value, start_value, end_value, calendar_value, allow_fallback,
    )
    if not clauses and not delete_all:
        raise click.ClickException("No filters given; pass --all to delete every auto-tracked entry.")

    db_path = resolve_db_path(click.get_current_context().obj["db_path"])
    with connect(db_path) as conn:
        records = fetch_activity_records(conn, clauses, params)
        if not records:
            click.echo("No records matched the delete filters.")
            return

        total_hours = sum(record.duration_hours for record in records)
        if not assume_yes:
            click.echo(f"Records to delete: {len(records)}")
            click.echo(f"Total duration (hours): {format_total_value(round(total_hours, 3))}")
            if not click.confirm("Proceed with deletion?"):
                click.echo("Deletion canceled.")
                return

        conn.executemany("DELETE FROM activity_entries WHERE id = ?", [(r.entry_id,) for r in records])

    click.echo(f"Deleted {len(records)} auto-tracked entr{'y' if len(records) == 1 else 'ies'}.")


@auto.command("probe")
@click.option(
    "--delay",
    type=float,
    default=3.0,
    show_default=True,
    help="Seconds to wait first, so you can focus the window you want to inspect.",
)
def auto_probe(delay: float) -> None:
    """Show what the watcher sees for the focused window, raw and classified.

    \b
    Use it when a report shows a blank project or the wrong app: the raw
    title and class it prints are what to put in ~/.pytime/rules.json.

    \b
    Examples:
      pytime auto probe
      pytime auto probe --delay 0
    """
    backend = detect_backend()
    if backend is None:
        raise click.ClickException(f"No supported window backend found. {backend_hint()}")
    if delay > 0:
        click.echo(f"Focus the window to inspect; reading it in {delay:g}s...", err=True)
        time_module.sleep(delay)
    window = get_active_window(backend)
    if window is None:
        raise click.ClickException(f"The {backend} backend reported no focused window.")
    classified = classify_window(window, _load_rules_or_fail())
    context = procinfo.terminal_context(window.pid) if classified and classified.category == "terminal" else None
    console.emit_json(
        {
            "backend": backend,
            "raw": {"wm_class": window.wm_class, "title": window.title, "pid": window.pid},
            "terminal": None
            if context is None
            else {"cwd": context.cwd, "program": context.program, "file": context.file},
            "ignored": classified is None,
            "classified": None
            if classified is None
            else {
                "category": classified.category,
                "app": classified.app,
                "project": classified.project,
                "detail": classified.detail,
                "ext": classified.ext,
            },
        }
    )


@auto.command("rules")
def auto_rules() -> None:
    """Show the active window backend and where to add custom classification rules.

    \b
    Examples:
      pytime auto rules
    """
    path = default_rules_path()
    backend = detect_backend()
    locked = is_screen_locked()
    console.result(f"Window backend: {backend or 'none detected -- ' + backend_hint()}")
    console.result(f"Idle detection: {'available' if get_idle_seconds() is not None else 'unavailable'}")
    console.result(f"Lock detection: {'unavailable' if locked is None else 'available'}")
    if path.is_file():
        try:
            load_rules(path)
            state = "exists, valid"
        except RulesError as exc:
            state = f"INVALID -- {exc}"
    else:
        state = "not created yet"
    console.result(f"Rules override file: {path} ({state})")
    console.result("")
    console.result("Add JSON like this to extend the defaults (every key optional, values merge in):")
    console.result(
        json.dumps(
            {
                "projects": {"SAMT": ["samt", "samt-backend"]},
                "ignore": {"apps": ["org.keepassxc.keepassxc"], "titles": ["bank"]},
                "redact": {"apps": ["thunderbird"], "titles": ["private"]},
                "sites": {"my-site.com": "My Site"},
                "app_projects": {"com.example.app": "Example"},
                "editor_classes": ["my-editor-wm-class"],
                "terminal_classes": [],
                "browser_classes": [],
                "app_labels": {"my-editor-wm-class": "My Editor"},
            },
            indent=2,
        )
    )
    console.result("")
    console.result("Restart the watcher after editing (pytime auto service restart, if you use the service).")


if __name__ == "__main__":
    time_cli()
