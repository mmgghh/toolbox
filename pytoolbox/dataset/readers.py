"""Reading JSON, CSV and Excel into records.

Each reader returns plain Python values plus the key order of the source, so
that everything downstream sees one shape regardless of where the data came
from. JSON already carries types; CSV carries none and gets them inferred;
Excel carries its own and keeps them.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Optional

from pytoolbox.dataset import naming
from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.types import ValueType, parse_text, unify_all

#: Suffix -> reader kind, for detecting the input without being told.
SUFFIXES = {
    ".json": "json",
    ".ndjson": "json",
    ".jsonl": "json",
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".xlsx": "excel",
    ".xlsm": "excel",
}

#: The kinds ``--from`` accepts.
KINDS = ("json", "csv", "excel")

#: Bytes of a CSV handed to the sniffer when no delimiter was given.
_SNIFF_BYTES = 8192


def detect_kind(path: Path) -> Optional[str]:
    """Guess the reader from a filename, or return None if the suffix is new."""
    return SUFFIXES.get(path.suffix.lower())


def read_text(path: Path, encoding: str = "utf-8", errors: str = "replace") -> str:
    """Read a file, or standard input when ``path`` is ``-``."""
    if str(path) == "-":
        import sys

        data = sys.stdin.buffer.read()
        return data.decode(encoding, errors)
    try:
        return path.read_text(encoding=encoding, errors=errors)
    except FileNotFoundError as exc:
        raise DataError(f"No such file: {path}") from exc
    except OSError as exc:
        raise DataError(f"Cannot read {path}: {exc}") from exc


def read_json(text: str) -> object:
    """Parse a JSON document, or a stream of one JSON value per line.

    Newline-delimited JSON is common enough as an export format that failing
    on it would be a papercut; it is tried only after a whole-document parse
    fails, so a pretty-printed file is never mistaken for it.
    """
    stripped = text.strip()
    if not stripped:
        raise DataError("The input is empty.")
    try:
        return json.loads(stripped)
    except json.JSONDecodeError as whole_document_error:
        lines = [line for line in stripped.splitlines() if line.strip()]
        if len(lines) > 1:
            try:
                return [json.loads(line) for line in lines]
            except json.JSONDecodeError:
                pass
        raise DataError(
            f"Not valid JSON: {whole_document_error.msg} "
            f"(line {whole_document_error.lineno}, column {whole_document_error.colno})"
        ) from whole_document_error


def resolve_delimiter(text: str, path: Optional[Path] = None, delimiter: Optional[str] = None) -> str:
    """The delimiter to read a file with: the given one, the suffix, or a sniff.

    Every caller resolves it here so that a file is never read with one
    delimiter and written back with another.
    """
    if delimiter:
        return delimiter
    if path is not None and path.suffix.lower() == ".tsv":
        return "\t"
    return _sniff_delimiter(text)


def read_csv(
    text: str,
    delimiter: Optional[str] = None,
    infer: bool = True,
) -> tuple[list[dict], list[str]]:
    """Read delimited text into records, inferring column types by default."""
    if not text.strip():
        raise DataError("The input is empty.")
    delimiter = resolve_delimiter(text, None, delimiter)

    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration as exc:  # pragma: no cover - guarded by the emptiness check
        raise DataError("The input has no header row.") from exc

    columns = _header_columns(header)
    raw_rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not raw_rows:
        raise DataError("The input has a header row but no data rows.")

    cells = _align(raw_rows, len(columns))
    values = _infer_columns(cells, len(columns)) if infer else _as_text(cells, len(columns))
    records = [
        {column: values[index][row] for index, column in enumerate(columns)}
        for row in range(len(cells))
    ]
    return records, columns


def read_excel(
    path: Path,
    sheet: Optional[str] = None,
    infer: bool = True,
) -> tuple[list[dict], list[str]]:
    """Read a worksheet into records, keeping the cell types Excel recorded.

    Excel already distinguishes a number from a date from text, so there is
    nothing to infer; ``infer=False`` flattens every cell back to text for the
    cases where the recorded types are wrong.
    """
    try:
        from openpyxl import load_workbook
    except ImportError as exc:
        raise DataError(
            "Reading .xlsx needs openpyxl. Install it with: pip install 'pytoolbox[excel]'"
        ) from exc

    if not path.exists():
        raise DataError(f"No such file: {path}")
    workbook = load_workbook(filename=str(path), read_only=True, data_only=True)
    try:
        if sheet is None:
            worksheet = workbook.active
        elif sheet in workbook.sheetnames:
            worksheet = workbook[sheet]
        else:
            available = ", ".join(workbook.sheetnames)
            raise DataError(f"No sheet named {sheet!r}. This workbook has: {available}")

        rows = worksheet.iter_rows(values_only=True)
        try:
            header = next(rows)
        except StopIteration as exc:
            raise DataError(f"Sheet {worksheet.title!r} is empty.") from exc

        columns = _header_columns(["" if cell is None else str(cell) for cell in header])
        records = []
        for row in rows:
            if all(cell is None or (isinstance(cell, str) and not cell.strip()) for cell in row):
                continue
            record = {}
            for index, column in enumerate(columns):
                value = row[index] if index < len(row) else None
                if isinstance(value, str) and not value.strip():
                    value = None
                elif not infer and value is not None:
                    value = str(value)
                record[column] = value
            records.append(record)
    finally:
        workbook.close()

    if not records:
        raise DataError(f"Sheet {worksheet.title!r} has a header row but no data rows.")
    return records, columns


def _sniff_delimiter(text: str) -> str:
    """Work out the delimiter, falling back to a comma when unsure."""
    sample = text[:_SNIFF_BYTES]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _header_columns(header: list[str]) -> list[str]:
    """Name every header cell, filling in blanks and breaking duplicates."""
    named = [cell.strip() or f"{naming.FALLBACK}_{index + 1}" for index, cell in enumerate(header)]
    seen: dict[str, int] = {}
    columns = []
    for name in named:
        count = seen.get(name, 0) + 1
        seen[name] = count
        columns.append(name if count == 1 else f"{name}_{count}")
    return columns


def _align(rows: list[list[str]], width: int) -> list[list[str]]:
    """Pad short rows and drop the overflow of long ones."""
    return [(row + [""] * width)[:width] for row in rows]


def _as_text(cells: list[list[str]], width: int) -> list[list[object]]:
    """Keep every cell as text, with blanks as nulls."""
    return [[(row[index].strip() or None) for row in cells] for index in range(width)]


def _infer_columns(cells: list[list[str]], width: int) -> list[list[object]]:
    """Type each column as a whole; one unparseable cell keeps it as text.

    Deciding per column rather than per cell is what stops a mostly-numeric
    column with one ``n/a`` in it from becoming a column of mixed types.
    """
    columns: list[list[object]] = []
    for index in range(width):
        raw = [row[index] for row in cells]
        parsed = [parse_text(cell) for cell in raw]
        resolved = unify_all(value_type for value_type, _ in parsed)
        if resolved in (ValueType.STR, ValueType.NULL):
            columns.append([cell.strip() or None for cell in raw])
        else:
            columns.append([value for _, value in parsed])
    return columns
