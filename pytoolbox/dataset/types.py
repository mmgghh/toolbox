"""The value-type lattice shared by every stage of ``pydata``.

One vocabulary of types is inferred once from the data and then reused by the
tree view, the summary, the filter and both SQL dialects. Types unify pairwise,
so a field seen as ``int`` in one record and ``float`` in the next resolves to
``float`` rather than to "mixed"; anything that cannot be reconciled falls back
to ``str``, which every dialect can store without losing information.
"""

from __future__ import annotations

import datetime as dt
import re
from decimal import Decimal
from enum import Enum
from typing import Optional


class ValueType(str, Enum):
    """A type a field can hold, in widening order for the numeric pair."""

    NULL = "null"
    BOOL = "bool"
    INT = "int"
    FLOAT = "float"
    DATE = "date"
    DATETIME = "datetime"
    STR = "str"
    LIST = "list"
    OBJECT = "object"
    JSON = "json"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


#: Types stored as a JSON document rather than as a scalar column.
CONTAINER_TYPES = frozenset({ValueType.LIST, ValueType.OBJECT, ValueType.JSON})

#: Types whose values support arithmetic in the summary.
NUMERIC_TYPES = frozenset({ValueType.INT, ValueType.FLOAT})

#: Types whose values are ordered points in time.
TEMPORAL_TYPES = frozenset({ValueType.DATE, ValueType.DATETIME})

_INT_RE = re.compile(r"[+-]?\d+\Z")
_FLOAT_RE = re.compile(r"[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?\Z")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?\Z")

#: Spellings accepted as booleans when inferring types from text.
_TRUE_WORDS = frozenset({"true", "yes"})
_FALSE_WORDS = frozenset({"false", "no"})

#: Text that means "no value" in a CSV or a spreadsheet.
_NULL_WORDS = frozenset({"", "null", "none", "nan", "n/a", "na"})


def classify(value: object) -> ValueType:
    """Return the type of an already-typed Python value."""
    if value is None:
        return ValueType.NULL
    # bool is a subclass of int, so it has to be tested first.
    if isinstance(value, bool):
        return ValueType.BOOL
    if isinstance(value, int):
        return ValueType.INT
    if isinstance(value, (float, Decimal)):
        return ValueType.FLOAT
    if isinstance(value, dt.datetime):
        return ValueType.DATETIME
    if isinstance(value, dt.date):
        return ValueType.DATE
    if isinstance(value, str):
        return ValueType.STR
    if isinstance(value, (list, tuple)):
        return ValueType.LIST
    if isinstance(value, dict):
        return ValueType.OBJECT
    return ValueType.STR


def unify(left: ValueType, right: ValueType) -> ValueType:
    """Return the narrowest type that can hold both ``left`` and ``right``.

    ``null`` never widens anything -- nullability is tracked separately -- and
    the only silent widenings are ``int``/``float`` and ``date``/``datetime``.
    A container mixed with anything becomes ``json``; every other disagreement
    becomes ``str``, since text is the one type that loses nothing.
    """
    if left == right:
        return left
    if left == ValueType.NULL:
        return right
    if right == ValueType.NULL:
        return left
    pair = {left, right}
    if pair == {ValueType.INT, ValueType.FLOAT}:
        return ValueType.FLOAT
    if pair == {ValueType.DATE, ValueType.DATETIME}:
        return ValueType.DATETIME
    if pair & CONTAINER_TYPES:
        return ValueType.JSON
    return ValueType.STR


def unify_all(types: object) -> ValueType:
    """Unify an iterable of types, ignoring ``null``."""
    resolved = ValueType.NULL
    for value_type in types:  # type: ignore[union-attr]
        resolved = unify(resolved, value_type)
    return resolved


def parse_text(text: str) -> tuple[ValueType, object]:
    """Infer a type from a CSV cell and return it with the converted value.

    Leading zeros keep a field as text: ``007`` and ``01730`` are identifiers
    (part numbers, postcodes, phone numbers), and turning them into integers
    silently destroys them.
    """
    stripped = text.strip()
    if stripped.lower() in _NULL_WORDS:
        return ValueType.NULL, None

    lowered = stripped.lower()
    if lowered in _TRUE_WORDS:
        return ValueType.BOOL, True
    if lowered in _FALSE_WORDS:
        return ValueType.BOOL, False

    if _INT_RE.match(stripped) and not _has_leading_zero(stripped):
        try:
            return ValueType.INT, int(stripped)
        except ValueError:  # pragma: no cover - the regex already guarantees this
            pass
    if _FLOAT_RE.match(stripped) and not _has_leading_zero(stripped):
        try:
            return ValueType.FLOAT, float(stripped)
        except ValueError:  # pragma: no cover - the regex already guarantees this
            pass

    if _DATE_RE.match(stripped):
        parsed_date = _parse_date(stripped)
        if parsed_date is not None:
            return ValueType.DATE, parsed_date
    if _DATETIME_RE.match(stripped):
        parsed_datetime = _parse_datetime(stripped)
        if parsed_datetime is not None:
            return ValueType.DATETIME, parsed_datetime

    return ValueType.STR, text


def _has_leading_zero(text: str) -> bool:
    digits = text.lstrip("+-")
    return len(digits) > 1 and digits[0] == "0" and not digits.startswith("0.")


def _parse_date(text: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        return None


def _parse_datetime(text: str) -> Optional[dt.datetime]:
    candidate = text.replace(" ", "T")
    # ``fromisoformat`` only learned to read a trailing Z in 3.11.
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(candidate)
    except ValueError:
        return None
