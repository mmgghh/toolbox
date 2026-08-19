"""Rendering and exporting tabular data (table, markdown, csv, json, excel)."""

from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from pathlib import Path

import click

#: Formats understood by ``--format`` across the CLIs.
OUTPUT_FORMATS = ("table", "csv", "markdown", "json", "excel")


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def render_table(rows: Sequence[dict], headers: Sequence[str]) -> str:
    """Render rows as a column-aligned text table."""
    if not rows:
        return ""
    widths = {header: len(header) for header in headers}
    for row in rows:
        for header in headers:
            widths[header] = max(widths[header], len(_cell(row.get(header))))
    lines = [
        " | ".join(header.ljust(widths[header]) for header in headers),
        "-+-".join("-" * widths[header] for header in headers),
    ]
    for row in rows:
        lines.append(" | ".join(_cell(row.get(header)).ljust(widths[header]) for header in headers))
    return "\n".join(lines)


def render_markdown(rows: Sequence[dict], headers: Sequence[str]) -> str:
    """Render rows as a GitHub-flavoured Markdown table."""
    if not rows:
        return ""
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(_cell(row.get(header)) for header in headers) + " |")
    return "\n".join(lines)


def render_csv(rows: Sequence[dict], headers: Sequence[str]) -> str:
    """Render rows as CSV text."""
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([_cell(row.get(header)) for header in headers])
    return buffer.getvalue()


def render_json(rows: Sequence[dict], headers: Sequence[str]) -> str:
    """Render rows as a JSON array of objects."""
    payload = [{header: row.get(header, "") for header in headers} for row in rows]
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str)


def write_excel(path: Path, rows: Sequence[dict], headers: Sequence[str]) -> None:
    """Write rows to an ``.xlsx`` file.

    ``openpyxl`` is imported lazily and is an optional extra: it pulls a
    compiled dependency chain that is awkward to install on Termux, and every
    other format works without it.
    """
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise click.ClickException(
            "Excel output needs openpyxl. Install it with `pip install 'pytoolbox[excel]'`, "
            "or use --format csv / markdown / json instead."
        ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(list(headers))
    for row in rows:
        sheet.append([row.get(header, "") for header in headers])
    for index, header in enumerate(headers, start=1):
        width = max(len(header), *(len(_cell(row.get(header))) for row in rows)) if rows else len(header)
        sheet.column_dimensions[sheet.cell(row=1, column=index).column_letter].width = min(width + 2, 60)
    workbook.save(path)


def suffix_for(output_format: str) -> str:
    """Default file extension for an output format."""
    return {
        "table": ".txt",
        "csv": ".csv",
        "markdown": ".md",
        "json": ".json",
        "excel": ".xlsx",
    }.get(output_format, ".txt")


def emit(
    rows: Sequence[dict],
    headers: Sequence[str],
    output_format: str = "table",
    output: Path | None = None,
) -> None:
    """Print rows in ``output_format``, or write them to ``output``.

    Returns nothing; a message naming the written file goes to stderr so that
    piping ``--format json`` without ``-o`` stays clean.
    """
    output_format = output_format.lower()
    if output is not None:
        if output_format == "excel":
            write_excel(output, rows, headers)
        else:
            renderer = {
                "table": render_table,
                "csv": render_csv,
                "markdown": render_markdown,
                "json": render_json,
            }[output_format]
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(renderer(rows, headers) + "\n", encoding="utf-8")
        click.echo(f"{output_format.capitalize()} written to {output}", err=True)
        return

    if output_format == "excel":
        raise click.ClickException("--format excel requires -o/--output.")
    renderer = {
        "table": render_table,
        "csv": render_csv,
        "markdown": render_markdown,
        "json": render_json,
    }[output_format]
    click.echo(renderer(rows, headers))
