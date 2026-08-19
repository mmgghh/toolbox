"""Turning inferred columns into SQL, for SQLite and for PostgreSQL."""

from __future__ import annotations

from pytoolbox.dataset.sql import dialects, emit, execute, table
from pytoolbox.dataset.sql.table import TableSpec, build_spec, parse_renames, validate

__all__ = [
    "TableSpec",
    "build_spec",
    "dialects",
    "emit",
    "execute",
    "parse_renames",
    "table",
    "validate",
]
