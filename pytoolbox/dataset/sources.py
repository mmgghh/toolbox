"""Turning a file into records, including working out what the rows are.

A JSON document is rarely already a list of rows. The usual shape is an
envelope -- ``{"data": {"items": [...]}, "meta": {...}}`` -- so the rows are
found by looking for lists of objects reachable through the document's
dictionaries. One candidate is used; several are an error that names them all,
because guessing wrong here silently loads the wrong table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pytoolbox.core.console import plural
from pytoolbox.dataset import readers
from pytoolbox.dataset.errors import DataError

#: Column name given to a list of bare scalars, which has no key of its own.
SCALAR_COLUMN = "value"


@dataclass(frozen=True)
class RecordSource:
    """Records ready for inference, plus where they came from."""

    records: list[dict]
    columns: list[str]
    origin: str
    kind: str
    #: The dotted path the records were taken from; empty for the whole document.
    root: str = ""
    #: The delimiter a CSV was read with; empty for the other kinds.
    delimiter: str = ""
    #: True when the source was a single object, so the table gets one row.
    single: bool = False
    #: Human-readable notes about choices made while reading, for stderr.
    notes: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.records)


@dataclass(frozen=True)
class RootCandidate:
    """A list of objects that could be the rows."""

    path: str
    count: int
    keys: int


def find_candidates(document: object, prefix: str = "") -> list[RootCandidate]:
    """Every list-of-objects reachable through the document's dictionaries.

    Only dictionary values are followed. Descending into list elements as well
    would turn every nested collection into a candidate and make the choice
    meaningless.
    """
    candidates: list[RootCandidate] = []
    if not isinstance(document, dict):
        return candidates
    for key, value in document.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, list) and value and all(isinstance(item, dict) for item in value):
            keys = {name for item in value for name in item}
            candidates.append(RootCandidate(path=path, count=len(value), keys=len(keys)))
        elif isinstance(value, dict):
            candidates.extend(find_candidates(value, path))
    return candidates


def resolve_path(document: object, path: str) -> object:
    """Follow a dotted path into a document."""
    current = document
    walked: list[str] = []
    for segment in path.split("."):
        if not isinstance(current, dict) or segment not in current:
            where = ".".join(walked) or "the document"
            raise DataError(f"No key {segment!r} under {where}.")
        current = current[segment]
        walked.append(segment)
    return current


def records_from(document: object, root: Optional[str] = None) -> tuple[list[dict], str, bool, list[str]]:
    """Pick the rows out of a parsed JSON document.

    Returns the records, the path they came from, whether the source was a
    single object, and any notes worth showing the user.
    """
    notes: list[str] = []
    if root:
        selected = resolve_path(document, root)
        return _as_records(selected, root), root, not isinstance(selected, list), notes

    if isinstance(document, list):
        return _as_records(document, ""), "", False, notes

    if not isinstance(document, dict):
        raise DataError(
            "The document is a single scalar; there is nothing to build a table from."
        )

    candidates = find_candidates(document)
    if len(candidates) == 1:
        chosen = candidates[0]
        notes.append(f"Using --root {chosen.path} ({chosen.count} records).")
        return _as_records(resolve_path(document, chosen.path), chosen.path), chosen.path, False, notes
    if len(candidates) > 1:
        width = max(len(candidate.path) for candidate in candidates)
        lines = "\n".join(
            f"  --root {candidate.path.ljust(width)}  "
            f"list of {plural(candidate.count, 'object')}, {plural(candidate.keys, 'key')}"
            for candidate in candidates
        )
        raise DataError("Several row sources found; pick one with --root:\n" + lines)

    return [document], "", True, notes


def _as_records(selected: object, path: str) -> list[dict]:
    """Normalize whatever the path pointed at into a list of records."""
    where = f"--root {path}" if path else "the document"
    if isinstance(selected, dict):
        return [selected]
    if not isinstance(selected, list):
        raise DataError(f"{where} is a {type(selected).__name__}, not a list or an object.")
    if not selected:
        raise DataError(f"{where} is an empty list.")
    # A list of bare scalars is still a table: one column, one row per element.
    if any(not isinstance(item, dict) for item in selected):
        return [item if isinstance(item, dict) else {SCALAR_COLUMN: item} for item in selected]
    return selected


def ordered_columns(records: list[dict]) -> list[str]:
    """Every key across the records, in the order they are first seen."""
    columns: dict[str, None] = {}
    for record in records:
        for key in record:
            columns.setdefault(key, None)
    return list(columns)


def load(
    path: Path,
    kind: Optional[str] = None,
    root: Optional[str] = None,
    sheet: Optional[str] = None,
    delimiter: Optional[str] = None,
    encoding: str = "utf-8",
    errors: str = "replace",
    infer: bool = True,
    limit: Optional[int] = None,
) -> RecordSource:
    """Read ``path`` into a :class:`RecordSource`."""
    kind = kind or readers.detect_kind(path)
    if kind is None:
        if str(path) == "-":
            raise DataError("Reading from stdin needs --from json|csv|excel.")
        raise DataError(
            f"Cannot tell what kind of file {path.name!r} is; "
            "say so with --from json|csv|excel."
        )

    notes: list[str] = []
    single = False
    used_root = ""
    used_delimiter = ""

    if kind == "excel":
        if str(path) == "-":
            raise DataError("Excel cannot be read from stdin; give a path to the .xlsx file.")
        _reject_root(root, kind)
        records, columns, notes = readers.read_excel(path, sheet=sheet, infer=infer)
    elif kind == "csv":
        _reject_root(root, kind)
        text = readers.read_text(path, encoding=encoding, errors=errors)
        used_delimiter = readers.resolve_delimiter(text, path, delimiter)
        records, columns = readers.read_csv(text, delimiter=used_delimiter, infer=infer)
    elif kind == "json":
        text = readers.read_text(path, encoding=encoding, errors=errors)
        document = readers.read_json(text)
        records, used_root, single, notes = records_from(document, root)
        columns = ordered_columns(records)
    else:
        raise DataError(f"Unknown input kind {kind!r}; use one of: {', '.join(readers.KINDS)}.")

    if limit is not None and limit >= 0 and len(records) > limit:
        notes.append(f"Stopped at --limit {limit} of {len(records)} records.")
        records = records[:limit]

    return RecordSource(
        records=records,
        columns=columns,
        origin="stdin" if str(path) == "-" else str(path),
        kind=kind,
        root=used_root,
        delimiter=used_delimiter,
        single=single,
        notes=notes,
    )


def _reject_root(root: Optional[str], kind: str) -> None:
    if root:
        raise DataError(f"--root applies to JSON only; {kind} input is already a table.")
