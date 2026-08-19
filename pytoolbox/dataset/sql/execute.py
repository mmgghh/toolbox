"""Creating and filling a real SQLite database.

The script emitter renders literals; this back end binds parameters instead,
so no value is ever re-parsed as SQL and a very large insert stays fast.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.schema import Column
from pytoolbox.dataset.sql import emit
from pytoolbox.dataset.sql import table as table_module
from pytoolbox.dataset.sql.dialects import SQLite, adapt
from pytoolbox.dataset.sql.table import TableSpec


def write(
    db_path: Path,
    spec: TableSpec,
    columns: Sequence[Column],
    records: Sequence[dict],
) -> int:
    """Create the table in ``db_path`` and insert the records. Returns the count."""
    dialect = SQLite()
    wide = table_module.wide_columns(columns, records)

    db_path = Path(db_path)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(str(db_path))
    try:
        if spec.if_exists == "fail" and _table_exists(connection, spec.name):
            raise DataError(
                f"Table {spec.name!r} already exists in {db_path}. "
                "Use --if-exists replace to rebuild it, or append to add rows."
            )
        with connection:
            if spec.if_exists == "replace":
                connection.execute(f'DROP TABLE IF EXISTS {dialect.quote(spec.name)}')
            if not (spec.if_exists == "append" and _table_exists(connection, spec.name)):
                for statement in _statements(
                    emit.create_table(spec, columns, dialect, wide)
                ):
                    connection.execute(statement)
                for statement in _statements(emit.create_indexes(spec, dialect)):
                    connection.execute(statement)

            placeholders = ", ".join("?" for _ in columns)
            names = ", ".join(dialect.quote(column.name) for column in columns)
            sql = f"INSERT INTO {dialect.quote(spec.name)} ({names}) VALUES ({placeholders})"
            rows = [
                [adapt(value) for value in table_module.values_for(record, columns)]
                for record in records
            ]
            connection.executemany(sql, rows)
    except sqlite3.Error as exc:
        raise DataError(f"SQLite refused the write: {exc}") from exc
    finally:
        connection.close()
    return len(records)


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _statements(script: str) -> list[str]:
    """Split a generated block into statements SQLite will accept one at a time.

    The generated DDL only ever puts semicolons at the end of a statement, so
    splitting on them is safe here -- unlike for the INSERT block, which is
    never run through this path.
    """
    return [statement.strip() for statement in script.split(";\n") if statement.strip()]
