"""The two SQL dialects, and how a Python value reaches each of them.

A dialect answers three questions: what to call a type, how to quote an
identifier, and how to write a literal. Everything else about generating a
table is the same for both, which is why the emitter takes a dialect rather
than branching on a name.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from decimal import Decimal

from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.types import CONTAINER_TYPES, ValueType

#: Largest value a 64-bit signed integer column can hold.
INT64_MAX = 2**63 - 1
INT64_MIN = -(2**63)


class Dialect:
    """Base class; see :class:`SQLite` and :class:`PostgreSQL`."""

    name = ""
    #: Type used for a column whose integers do not fit in 64 bits.
    wide_int_type = "text"
    types: dict[ValueType, str] = {}

    def quote(self, identifier: str) -> str:
        """Quote an identifier, doubling any embedded quote character."""
        escaped = identifier.replace('"', '""')
        return f'"{escaped}"'

    def sql_type(self, value_type: ValueType, wide: bool = False, nested: str = "json") -> str:
        """The column type for an inferred value type."""
        if value_type in CONTAINER_TYPES:
            return self.json_type(nested)
        if value_type is ValueType.INT and wide:
            return self.wide_int_type
        return self.types[value_type]

    def json_type(self, nested: str = "json") -> str:
        raise NotImplementedError

    def literal(self, value: object, value_type: ValueType, nested: str = "json") -> str:
        """Render one value as a SQL literal."""
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return self.bool_literal(value)
        if isinstance(value, (list, dict)):
            return self.json_literal(value, nested)
        if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, float):
            # Neither dialect has a portable spelling for these, and a missing
            # number is closer to the truth than a wrong one.
            if math.isnan(value) or math.isinf(value):
                return "NULL"
            return repr(value)
        if isinstance(value, (dt.datetime, dt.date, dt.time)):
            return self.quote_string(value.isoformat())
        if value_type in CONTAINER_TYPES and isinstance(value, str):
            return self.json_literal(value, nested)
        return self.quote_string(str(value))

    def bool_literal(self, value: bool) -> str:
        raise NotImplementedError

    def json_literal(self, value: object, nested: str = "json") -> str:
        raise NotImplementedError

    def quote_string(self, text: str) -> str:
        """Quote a string literal. Both dialects double the single quote."""
        return "'" + text.replace("'", "''") + "'"


class SQLite(Dialect):
    """SQLite, where types are storage classes and JSON lives in TEXT."""

    name = "sqlite"
    # SQLite integers are 64-bit too, so anything wider has to be text.
    wide_int_type = "TEXT"
    types = {
        ValueType.NULL: "TEXT",
        ValueType.BOOL: "INTEGER",
        ValueType.INT: "INTEGER",
        ValueType.FLOAT: "REAL",
        ValueType.DATE: "TEXT",
        ValueType.DATETIME: "TEXT",
        ValueType.STR: "TEXT",
    }

    def json_type(self, nested: str = "json") -> str:
        # SQLite has no JSON column type at all: json_extract() and friends
        # read TEXT. --nested is therefore a PostgreSQL-only choice here.
        return "TEXT"

    def bool_literal(self, value: bool) -> str:
        return "1" if value else "0"

    def json_literal(self, value: object, nested: str = "json") -> str:
        return self.quote_string(_dumps(value))


class PostgreSQL(Dialect):
    """PostgreSQL, with real date, boolean and jsonb types."""

    name = "postgres"
    wide_int_type = "numeric"
    types = {
        ValueType.NULL: "text",
        ValueType.BOOL: "boolean",
        ValueType.INT: "bigint",
        ValueType.FLOAT: "double precision",
        ValueType.DATE: "date",
        ValueType.DATETIME: "timestamp",
        ValueType.STR: "text",
    }

    def json_type(self, nested: str = "json") -> str:
        return "text" if nested == "text" else "jsonb"

    def bool_literal(self, value: bool) -> str:
        return "TRUE" if value else "FALSE"

    def json_literal(self, value: object, nested: str = "json") -> str:
        quoted = self.quote_string(_dumps(value))
        return quoted if nested == "text" else f"{quoted}::jsonb"


#: Every dialect, by the name ``--dialect`` uses.
DIALECTS = {"sqlite": SQLite, "postgres": PostgreSQL}


def get(name: str) -> Dialect:
    """Look up a dialect by name."""
    try:
        return DIALECTS[name.lower()]()
    except KeyError as exc:
        raise DataError(
            f"Unknown dialect {name!r}. Known dialects: {', '.join(sorted(DIALECTS))}."
        ) from exc


def adapt(value: object) -> object:
    """Convert a value into something ``sqlite3`` can bind as a parameter."""
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (list, dict)):
        return _dumps(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, int) and not INT64_MIN <= value <= INT64_MAX:
        return str(value)
    return value


def is_wide(value: object) -> bool:
    """True for an integer too large for a 64-bit column."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and not INT64_MIN <= value <= INT64_MAX
    )


def _dumps(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
