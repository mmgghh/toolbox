"""What to build: the table's name, key, indexes, and the checks on them.

Validation happens once, before either back end runs, so that a bad
``--pk`` fails with a sentence rather than as a constraint violation halfway
through an insert.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Optional

from pytoolbox.dataset import naming
from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.schema import Column
from pytoolbox.dataset.sql import dialects

#: What to do when the table is already there.
IF_EXISTS = ("fail", "replace", "append")

#: How nested values are stored. Only PostgreSQL can tell the two apart.
NESTED = ("json", "text")

#: Rows per multi-row INSERT in a generated script.
DEFAULT_BATCH = 100


@dataclass(frozen=True)
class TableSpec:
    """Everything the user chose about the table itself."""

    name: str
    primary_key: tuple[str, ...] = ()
    indexes: tuple[tuple[str, ...], ...] = ()
    unique_indexes: tuple[tuple[str, ...], ...] = ()
    if_exists: str = "fail"
    nested: str = "json"
    batch: int = DEFAULT_BATCH
    notes: tuple[str, ...] = field(default_factory=tuple)


def build_spec(
    name: str,
    primary_key: Sequence[str] = (),
    indexes: Sequence[str] = (),
    unique_indexes: Sequence[str] = (),
    if_exists: str = "fail",
    nested: str = "json",
    batch: int = DEFAULT_BATCH,
) -> TableSpec:
    """Normalize the raw option values into a :class:`TableSpec`."""
    if not name or not name.strip():
        raise DataError("A table name is required; pass -t/--table.")
    safe = naming.sanitize(name)
    notes = []
    if safe != name:
        notes.append(f"Table name {name!r} used as {safe!r}.")
    if if_exists not in IF_EXISTS:
        raise DataError(f"--if-exists must be one of: {', '.join(IF_EXISTS)}.")
    if nested not in NESTED:
        raise DataError(f"--nested must be one of: {', '.join(NESTED)}.")
    if batch < 1:
        raise DataError("--batch must be at least 1.")
    return TableSpec(
        name=safe,
        primary_key=tuple(primary_key),
        indexes=tuple(_split(group) for group in indexes),
        unique_indexes=tuple(_split(group) for group in unique_indexes),
        if_exists=if_exists,
        nested=nested,
        batch=batch,
        notes=tuple(notes),
    )


def _split(group: str) -> tuple[str, ...]:
    """Split a comma-separated ``--index`` value into its columns."""
    parts = tuple(part.strip() for part in group.split(",") if part.strip())
    if not parts:
        raise DataError("An index needs at least one column name.")
    return parts


def parse_renames(pairs: Sequence[str], raw: bool = False) -> dict[str, str]:
    """Turn ``--column old=new`` values into a mapping.

    The new name is folded like any other, unless ``raw`` is set -- ``--raw-names``
    means "do not rewrite my names" everywhere, including here.
    """
    renames: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise DataError(f"--column wants OLD=NEW, got {pair!r}.")
        old, new = pair.split("=", 1)
        old, new = old.strip(), new.strip()
        if not old or not new:
            raise DataError(f"--column wants OLD=NEW, got {pair!r}.")
        renames[old] = naming.as_identifier(new, raw)
    return renames


def validate(
    spec: TableSpec,
    columns: Sequence[Column],
    records: Sequence[dict],
) -> None:
    """Check the spec against the data, raising on anything that cannot work.

    Unknown column names are an error with a suggestion. A primary key that is
    not actually unique, or that has missing values, is an error too: it would
    fail at insert time anyway, and finding out now costs nothing.
    """
    names = [column.name for column in columns]
    by_name = {column.name: column for column in columns}
    for group, label in _referenced(spec):
        for name in group:
            if name not in by_name:
                raise DataError(f"{label} column {name!r} is not in the data.{_suggest(name, names)}")

    _check_renames(columns)
    if spec.primary_key:
        _check_primary_key(spec, by_name, records)


def _referenced(spec: TableSpec) -> list[tuple[Sequence[str], str]]:
    referenced: list[tuple[Sequence[str], str]] = [(spec.primary_key, "Primary key")]
    referenced.extend((group, "Index") for group in spec.indexes)
    referenced.extend((group, "Unique index") for group in spec.unique_indexes)
    return referenced


def _suggest(name: str, names: Sequence[str]) -> str:
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.5)
    if close:
        return " Did you mean: " + ", ".join(close) + "?"
    return " Columns: " + ", ".join(names) + "."


def _check_renames(columns: Sequence[Column]) -> None:
    seen: dict[str, str] = {}
    for column in columns:
        if column.name in seen:
            raise DataError(
                f"Two fields map to the column {column.name!r}: "
                f"{seen[column.name]!r} and {column.source!r}. Separate them with --column."
            )
        seen[column.name] = column.source


def _check_primary_key(
    spec: TableSpec,
    by_name: dict[str, Column],
    records: Sequence[dict],
) -> None:
    sources = [by_name[name].source for name in spec.primary_key]
    label = ", ".join(spec.primary_key)
    seen: dict[tuple, int] = {}
    for record in records:
        key = tuple(dialects.adapt(record.get(source)) for source in sources)
        if any(part is None for part in key):
            raise DataError(
                f"Primary key ({label}) has missing values; it cannot be NOT NULL."
            )
        key = tuple(str(part) for part in key)
        if key in seen:
            example = ", ".join(key)
            raise DataError(
                f"Primary key ({label}) is not unique: ({example}) appears more than once."
            )
        seen[key] = 1


def wide_columns(columns: Sequence[Column], records: Sequence[dict]) -> set[str]:
    """Columns holding an integer too large for a 64-bit column."""
    wide = set()
    for column in columns:
        if column.type.value != "int":
            continue
        if any(dialects.is_wide(record.get(column.source)) for record in records):
            wide.add(column.name)
    return wide


def values_for(record: dict, columns: Sequence[Column]) -> list[object]:
    """The record's values, in column order."""
    return [record.get(column.source) for column in columns]


def default_table_name(origin: str, root: str = "") -> Optional[str]:
    """A table name guessed from the source, used as the interactive default."""
    if root:
        return naming.sanitize(root.split(".")[-1])
    if origin and origin != "stdin":
        from pathlib import Path

        return naming.sanitize(Path(origin).stem)
    return None
