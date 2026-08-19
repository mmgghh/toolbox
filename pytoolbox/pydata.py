#!/usr/bin/env python3
"""Structure, summarize, filter and load JSON, CSV and Excel.

Exposes the ``pydata`` console script, also available as ``toolbox data``.

Four subcommands read the same inferred schema: ``tree`` draws it, ``summary``
puts statistics beside it, ``filter`` selects part of it, and ``sql`` turns its
top level into a table -- written straight into a SQLite file, or emitted as a
script for SQLite or PostgreSQL.

Nothing here needs a driver or an ORM: SQLite comes from the standard library
and PostgreSQL is reached by writing a ``.sql`` file you run yourself.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

import click

from pytoolbox.core import console, tables
from pytoolbox.core.options import (
    CONTEXT_SETTINGS,
    AliasedGroup,
    encoding_options,
    format_option,
    version_option,
)
from pytoolbox.dataset import interactive, readers, render, schema, select, sources, summarize
from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.sql import dialects, emit
from pytoolbox.dataset.sql import execute as sql_execute
from pytoolbox.dataset.sql import table as table_module

#: ``--dialect`` choices, in the order they appear in help.
DIALECT_NAMES = ("sqlite", "postgres")


def source_options(func):
    """The options every subcommand shares for finding and reading the data."""
    func = click.option(
        "--limit",
        type=int,
        default=None,
        help="Read only the first N records.",
    )(func)
    func = click.option(
        "--raw-names",
        is_flag=True,
        help="Use keys as column names verbatim instead of folding them to snake_case.",
    )(func)
    func = click.option(
        "--no-infer",
        is_flag=True,
        help="Keep CSV and Excel values as text instead of inferring types.",
    )(func)
    func = encoding_options(func)
    func = click.option(
        "--delimiter",
        default=None,
        help="CSV delimiter (default: sniffed from the file).",
    )(func)
    func = click.option(
        "--sheet",
        default=None,
        help="Excel sheet name (default: the active sheet).",
    )(func)
    func = click.option(
        "--from",
        "kind",
        type=click.Choice(list(readers.KINDS), case_sensitive=False),
        default=None,
        help="Input kind, when the suffix does not say.",
    )(func)
    return click.option(
        "--root",
        default=None,
        help="Dotted path to the records inside a JSON document, e.g. data.items.",
    )(func)


def _load(path: Path, kind, root, sheet, delimiter, encoding, errors, no_infer, limit):
    """Read the source and report any choices it made on stderr."""
    source = sources.load(
        path,
        kind=kind,
        root=root,
        sheet=sheet,
        delimiter=delimiter,
        encoding=encoding,
        errors=errors,
        infer=not no_infer,
        limit=limit,
    )
    for note in source.notes:
        console.info(note)
    return source


def _analyze(source, raw_names: bool = False):
    """Infer the schema and resolve the top level into columns."""
    root = schema.infer(source.records, source.columns)
    return root, schema.columns_of(root, raw=raw_names)


@click.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
@version_option
def data_cli() -> None:
    """Inspect JSON, CSV and Excel, and load them into SQL tables.

    \b
    Examples:
      pydata tree api.json
      pydata summary sales.csv
      pydata filter api.json --type int
      pydata sql sales.csv -t sales --db app.db
    """


@data_cli.command()
@click.argument("path", type=click.Path(path_type=Path, allow_dash=True))
@source_options
@click.option("--depth", type=int, default=None, help="Stop descending after N levels.")
def tree(
    path, root, kind, sheet, delimiter, encoding, errors, raw_names, no_infer, limit, depth
) -> None:
    """Draw the structure of the data: keys, types and how often each occurs.

    \b
    Examples:
      pydata tree api.json
      pydata tree api.json --root data.users --depth 2
      curl -s https://example.com/users | pydata tree - --from json
    """
    source = _load(path, kind, root, sheet, delimiter, encoding, errors, no_infer, limit)
    root_node, _ = _analyze(source, raw_names)
    click.echo(render.render(root_node, source, max_depth=depth))


@data_cli.command()
@click.argument("path", type=click.Path(path_type=Path, allow_dash=True))
@source_options
@format_option()
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Write to this file.")
def summary(
    path, root, kind, sheet, delimiter, encoding, errors, raw_names, no_infer, limit,
    output_format, output,
) -> None:
    """Show per-field statistics: types, nulls, distinct values and extremes.

    \b
    Examples:
      pydata summary sales.csv
      pydata summary api.json --root data.users --format json
      pydata summary staff.xlsx --sheet Q1 -o summary.md --format markdown
    """
    source = _load(path, kind, root, sheet, delimiter, encoding, errors, no_infer, limit)
    root_node, columns = _analyze(source, raw_names)
    if output_format == "table" and output is None:
        console.result(render.header(source, root_node))
        console.result("")
    rows = summarize.summarize(root_node, columns)
    tables.emit(rows, list(summarize.HEADERS), output_format=output_format, output=output)


@data_cli.command("filter")
@click.argument("path", type=click.Path(path_type=Path, allow_dash=True))
@source_options
@click.option("-k", "--key", "keys", multiple=True, help="Keep fields matching this glob (repeatable).")
@click.option("-t", "--type", "types", multiple=True, help="Keep fields of this value type (repeatable).")
@click.option("--drop-empty", is_flag=True, help="Drop fields that are null in every record.")
@click.option("--rows", type=int, default=None, help="Print only the first N rows.")
@format_option()
@click.option("-o", "--output", type=click.Path(path_type=Path), help="Write to this file.")
def filter_command(
    path,
    root,
    kind,
    sheet,
    delimiter,
    encoding,
    errors,
    raw_names,
    no_infer,
    limit,
    keys,
    types,
    drop_empty,
    rows,
    output_format,
    output,
) -> None:
    """Print the data, keeping only the fields you ask for.

    Fields are chosen by name with -k/--key (a glob, matched against both the
    original key and the SQL column name) and by value type with -t/--type.
    Types are OR-ed together; names and types narrow the selection together.

    \b
    Types: null bool int float date datetime str list object json mixed
    ("json" matches any container, "mixed" any field seen with two types.)

    \b
    Examples:
      pydata filter api.json --type int --type float
      pydata filter api.json -k 'addr*' -k 'first*'
      pydata filter sales.csv --drop-empty --rows 20 --format csv
    """
    source = _load(path, kind, root, sheet, delimiter, encoding, errors, no_infer, limit)
    root_node, columns = _analyze(source, raw_names)
    chosen = select.select(root_node, columns, keys=keys, types=types, drop_empty=drop_empty)
    printed = select.rows_for(source.records, chosen, limit=rows)
    tables.emit(
        printed,
        [column.name for column in chosen],
        output_format=output_format,
        output=output,
    )


@data_cli.command()
@click.argument("path", type=click.Path(path_type=Path, allow_dash=True))
@source_options
@click.option("-t", "--table", "table_name", default=None, help="Name of the table to create.")
@click.option("--db", "db_path", type=click.Path(path_type=Path), help="SQLite file to write into.")
@click.option(
    "--sql",
    "sql_path",
    type=click.Path(path_type=Path, allow_dash=True),
    help="Write a .sql script instead of a database ('-' for stdout).",
)
@click.option(
    "--dialect",
    type=click.Choice(list(DIALECT_NAMES), case_sensitive=False),
    default="sqlite",
    show_default=True,
    help="SQL dialect for the generated script.",
)
@click.option("-c", "--column", "renames", multiple=True, metavar="OLD=NEW", help="Rename a column (repeatable).")
@click.option("--pk", "primary_key", multiple=True, help="Primary-key column (repeat for a compound key).")
@click.option("--index", "indexes", multiple=True, metavar="COLS", help="Index on comma-separated columns (repeatable).")
@click.option("--unique-index", "unique_indexes", multiple=True, metavar="COLS", help="Unique index (repeatable).")
@click.option(
    "--if-exists",
    type=click.Choice(list(table_module.IF_EXISTS), case_sensitive=False),
    default="fail",
    show_default=True,
    help="What to do when the table is already there.",
)
@click.option(
    "--nested",
    type=click.Choice(list(table_module.NESTED), case_sensitive=False),
    default="json",
    show_default=True,
    help="Store nested values as jsonb or as text (PostgreSQL only).",
)
@click.option(
    "--batch",
    type=int,
    default=table_module.DEFAULT_BATCH,
    show_default=True,
    help="Rows per INSERT statement in a generated script.",
)
@click.option("-i", "--interactive", "ask", is_flag=True, help="Show a summary, then ask what to build.")
@click.option("-n", "--dry-run", is_flag=True, help="Print the script instead of writing anything.")
def sql(
    path,
    root,
    kind,
    sheet,
    delimiter,
    encoding,
    errors,
    raw_names,
    no_infer,
    limit,
    table_name,
    db_path,
    sql_path,
    dialect,
    renames,
    primary_key,
    indexes,
    unique_indexes,
    if_exists,
    nested,
    batch,
    ask,
    dry_run,
) -> None:
    """Create a table from the data and insert it.

    With --db the table is created in a SQLite file. With --sql a script is
    written instead, in whichever --dialect you name; '--sql -' sends it to
    stdout, ready to pipe into psql or sqlite3.

    \b
    Examples:
      pydata sql sales.csv -t sales --db app.db
      pydata sql api.json -t users --pk id --index email --db app.db
      pydata sql api.json -t users --dialect postgres --sql users.sql
      pydata sql api.json -i --db app.db
    """
    source = _load(path, kind, root, sheet, delimiter, encoding, errors, no_infer, limit)
    root_node = schema.infer(source.records, source.columns)
    columns = schema.columns_of(
        root_node, table_module.parse_renames(renames, raw_names), raw=raw_names
    )

    if db_path is not None and sql_path is not None:
        raise DataError("Use either --db or --sql, not both.")
    if dialect == "postgres" and db_path is not None:
        raise DataError("--db writes SQLite; for PostgreSQL use --sql to generate a script.")

    spec = _spec(
        source, root_node, columns, table_name, primary_key, indexes,
        unique_indexes, if_exists, nested, batch, ask,
    )
    for note in spec.notes:
        console.info(note)
    table_module.validate(spec, columns, source.records)

    engine = dialects.get(dialect)
    if engine.name == "sqlite" and _explicit("nested"):
        console.warn(
            "SQLite has no JSON column type; --nested is ignored and nested values are TEXT."
        )
    if dry_run or sql_path is not None or db_path is None:
        script = emit.build(
            spec, columns, source.records, engine, origin=source.origin, root=source.root
        )
        _write_script(script, sql_path, dry_run)
        return

    written = sql_execute.write(db_path, spec, columns, source.records)
    console.success(
        f"Wrote {console.plural(written, 'row')} to {spec.name} in {db_path}.", threshold=0
    )


def _spec(
    source, root_node, columns, table_name, primary_key, indexes,
    unique_indexes, if_exists, nested, batch, ask,
):
    """Build the table spec, asking for the missing parts when --interactive."""
    defaults = table_module.TableSpec(
        name=table_name or table_module.default_table_name(source.origin, source.root) or "",
        primary_key=tuple(primary_key),
        indexes=tuple(tuple(part.strip() for part in group.split(",") if part.strip()) for group in indexes),
        unique_indexes=tuple(
            tuple(part.strip() for part in group.split(",") if part.strip()) for group in unique_indexes
        ),
        if_exists=if_exists,
        nested=nested,
        batch=batch,
    )
    if ask:
        return interactive.choose(source, root_node, columns, defaults)
    if not table_name:
        raise DataError("A table name is required; pass -t/--table or use -i/--interactive.")
    return table_module.build_spec(
        name=table_name,
        primary_key=primary_key,
        indexes=indexes,
        unique_indexes=unique_indexes,
        if_exists=if_exists,
        nested=nested,
        batch=batch,
    )


def _explicit(name: str) -> bool:
    """True when the user typed this option rather than taking its default."""
    ctx = click.get_current_context(silent=True)
    if ctx is None:
        return False
    source = ctx.get_parameter_source(name)
    return source is not None and source.name == "COMMANDLINE"


def _write_script(script: str, sql_path: Optional[Path], dry_run: bool) -> None:
    if sql_path is None or str(sql_path) == "-":
        click.echo(script, nl=False)
        return
    if dry_run:
        console.dry_run_notice(True)
        click.echo(script, nl=False)
        return
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    sql_path.write_text(script, encoding="utf-8")
    console.success(f"SQL written to {sql_path}.", threshold=0)


def main() -> None:  # pragma: no cover - console-script entry point
    data_cli()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(data_cli())
