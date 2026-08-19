"""Selecting fields by name or by value type, and rendering them for printing."""

from __future__ import annotations

import fnmatch
import json
from collections.abc import Sequence
from typing import Optional

from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.schema import Column, SchemaNode
from pytoolbox.dataset.types import CONTAINER_TYPES, ValueType

#: ``--type`` also accepts these, which are not types in the lattice.
PSEUDO_TYPES = ("mixed", "json")

#: Everything ``--type`` accepts.
TYPE_NAMES = tuple(value.value for value in ValueType) + ("mixed",)


def select(
    root: SchemaNode,
    columns: Sequence[Column],
    keys: Sequence[str] = (),
    types: Sequence[str] = (),
    drop_empty: bool = False,
) -> list[Column]:
    """Keep the columns matching every filter that was given.

    ``keys`` are globs, matched against both the original key and the SQL
    column name so that either spelling works. ``types`` are OR-ed together --
    ``--type int --type float`` means "numeric" -- while ``keys`` and ``types``
    are AND-ed, so the two narrow the selection together.
    """
    unknown = [name for name in types if name.lower() not in TYPE_NAMES]
    if unknown:
        raise DataError(
            f"Unknown --type {unknown[0]!r}. Known types: {', '.join(TYPE_NAMES)}."
        )

    chosen = []
    for column in columns:
        node = root.children[column.source]
        if keys and not _matches_key(column, keys):
            continue
        if types and not _matches_type(node, types):
            continue
        if drop_empty and node.present - node.nulls == 0:
            continue
        chosen.append(column)

    if not chosen:
        raise DataError("No field matched the filter.")
    return chosen


def _matches_key(column: Column, patterns: Sequence[str]) -> bool:
    names = {column.name.lower(), column.source.lower()}
    return any(
        fnmatch.fnmatchcase(name, pattern.lower()) for pattern in patterns for name in names
    )


def _matches_type(node: SchemaNode, names: Sequence[str]) -> bool:
    concrete = {value for value in node.type_counts if value is not ValueType.NULL}
    resolved = node.type
    for name in names:
        wanted = name.lower()
        if wanted == "mixed":
            if len(concrete) > 1:
                return True
        elif wanted == ValueType.JSON.value:
            if resolved in CONTAINER_TYPES:
                return True
        elif resolved.value == wanted:
            return True
    return False


def rows_for(
    records: Sequence[dict],
    columns: Sequence[Column],
    limit: Optional[int] = None,
) -> list[dict]:
    """Project the records onto ``columns``, with values ready to print.

    Nested objects and lists become compact JSON, which is both what the table
    renderer can show in one cell and what the SQL step would store.
    """
    chosen = records if limit is None else records[:limit]
    return [
        {column.name: display(record.get(column.source)) for column in columns}
        for record in chosen
    ]


def display(value: object) -> object:
    """Render one value for a text table."""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return value
