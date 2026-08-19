"""Per-field statistics over the inferred schema.

The columns are deliberately uniform -- every field gets a ``min``, ``max``
and ``mean`` -- but what they mean follows the field's type: extremes and an
average for numbers and dates, lengths for text, element counts for lists and
objects. That keeps the whole summary in one table you can also pipe out as
CSV or JSON.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Sequence
from typing import Optional

from pytoolbox.dataset.schema import Column, SchemaNode
from pytoolbox.dataset.types import CONTAINER_TYPES, NUMERIC_TYPES, TEMPORAL_TYPES, ValueType

#: The summary's columns, in order.
HEADERS = (
    "field",
    "column",
    "type",
    "non_null",
    "nulls",
    "distinct",
    "min",
    "max",
    "mean",
    "top",
)

#: How much of a sample value to show before cutting it off.
_SAMPLE_WIDTH = 28


def summarize(root: SchemaNode, columns: Sequence[Column]) -> list[dict]:
    """One row of statistics per top-level field."""
    by_source = {column.source: column for column in columns}
    rows = []
    for source, node in root.children.items():
        column = by_source.get(source)
        rows.append(_row(source, node, column))
    return rows


def _row(source: str, node: SchemaNode, column: Optional[Column]) -> dict:
    present = [value for value in node.values if value is not None]
    row = {
        "field": source,
        "column": column.name if column is not None else "",
        "type": node.type_label,
        "non_null": len(present),
        "nulls": node.missing,
        "distinct": _distinct(present),
        "min": "",
        "max": "",
        "mean": "",
        "top": _top(present),
    }
    row.update(_extremes(node, present))
    return row


def _extremes(node: SchemaNode, present: list) -> dict:
    """The min/max/mean triple, read according to the field's type."""
    if not present:
        return {}
    value_type = node.type

    if value_type in NUMERIC_TYPES:
        numbers = [value for value in present if isinstance(value, (int, float))]
        if not numbers:
            return {}
        return {
            "min": _number(min(numbers)),
            "max": _number(max(numbers)),
            "mean": _number(statistics.fmean(numbers)),
        }

    if value_type in TEMPORAL_TYPES:
        moments = sorted(str(value) for value in present)
        return {"min": moments[0], "max": moments[-1], "mean": ""}

    if value_type in CONTAINER_TYPES:
        sizes = [len(value) if isinstance(value, (list, dict)) else 1 for value in present]
        return {
            "min": f"{min(sizes)} items",
            "max": f"{max(sizes)} items",
            "mean": f"{_number(statistics.fmean(sizes))} items",
        }

    if value_type is ValueType.BOOL:
        counts = Counter(bool(value) for value in present)
        return {"min": "", "max": "", "mean": f"{counts[True]} true / {counts[False]} false"}

    lengths = [len(str(value)) for value in present]
    return {
        "min": f"len {min(lengths)}",
        "max": f"len {max(lengths)}",
        "mean": f"len {_number(statistics.fmean(lengths))}",
    }


def _distinct(present: list) -> int:
    return len({_key(value) for value in present})


def _top(present: list) -> str:
    """The most common value, with how often it occurs."""
    if not present:
        return ""
    counts = Counter(_key(value) for value in present)
    value, count = counts.most_common(1)[0]
    if count == 1:
        return ""
    return f"{_clip(value)} ({count})"


def _key(value: object) -> str:
    """A hashable, comparable stand-in for any value, including containers."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    return str(value)


def _clip(text: str) -> str:
    return text if len(text) <= _SAMPLE_WIDTH else text[: _SAMPLE_WIDTH - 1] + "…"


def _number(value: float) -> str:
    """Print a number without a pointless trailing ``.0``."""
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return f"{value:.4g}"
