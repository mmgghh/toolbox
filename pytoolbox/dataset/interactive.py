"""The ``--interactive`` flow: show what was inferred, then ask what to build.

Every prompt goes to stderr so that ``--sql -`` still pipes a clean script
into ``psql`` while the conversation happens on the terminal.
"""

from __future__ import annotations

from collections.abc import Sequence

import click

from pytoolbox.core import tables
from pytoolbox.dataset import render, summarize
from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.schema import Column, SchemaNode
from pytoolbox.dataset.sources import RecordSource
from pytoolbox.dataset.sql import table as table_module
from pytoolbox.dataset.sql.dialects import adapt
from pytoolbox.dataset.sql.table import TableSpec
from pytoolbox.dataset.types import CONTAINER_TYPES

#: How many primary-key suggestions to offer.
_MAX_SUGGESTIONS = 4


def choose(
    source: RecordSource,
    root: SchemaNode,
    columns: Sequence[Column],
    defaults: TableSpec,
) -> TableSpec:
    """Show a summary, then ask for the table name, primary key and indexes."""
    _show(source, root, columns)

    name = click.prompt(
        "Table name",
        default=defaults.name or table_module.default_table_name(source.origin, source.root),
        err=True,
    )

    candidates = key_candidates(source.records, columns)
    if candidates:
        click.echo(f"Columns unique and complete enough for a key: {', '.join(candidates)}", err=True)
    primary_key = _ask_columns(
        "Primary key (comma-separated, blank for none)",
        default=defaults.primary_key or ((candidates[0],) if candidates else ()),
        columns=columns,
    )

    click.echo("Indexes: comma-separated columns; join with + for a composite.", err=True)
    indexes = _ask_index_groups("Indexes (blank for none)", defaults.indexes, columns)
    unique_indexes = _ask_index_groups(
        "Unique indexes (blank for none)", defaults.unique_indexes, columns
    )

    spec = table_module.build_spec(
        name=name,
        primary_key=primary_key,
        indexes=[",".join(group) for group in indexes],
        unique_indexes=[",".join(group) for group in unique_indexes],
        if_exists=defaults.if_exists,
        nested=defaults.nested,
        batch=defaults.batch,
    )
    _confirm(spec, len(source.records))
    return spec


def _show(source: RecordSource, root: SchemaNode, columns: Sequence[Column]) -> None:
    click.echo(render.header(source, root), err=True)
    click.echo("", err=True)
    rows = summarize.summarize(root, columns)
    click.echo(tables.render_table(rows, list(summarize.HEADERS)), err=True)
    click.echo("", err=True)


def key_candidates(records: Sequence[dict], columns: Sequence[Column]) -> list[str]:
    """Columns whose values are present in every record and never repeat."""
    found = []
    for column in columns:
        # A JSON column can be unique by accident; it is never a sensible key.
        if column.nullable or column.type in CONTAINER_TYPES:
            continue
        values = [adapt(record.get(column.source)) for record in records]
        if any(value is None for value in values):
            continue
        keys = [str(value) for value in values]
        if len(set(keys)) == len(keys):
            found.append(column.name)
        if len(found) == _MAX_SUGGESTIONS:
            break
    return found


def _ask_columns(
    prompt: str,
    default: tuple[str, ...],
    columns: Sequence[Column],
) -> tuple[str, ...]:
    known = {column.name for column in columns}
    while True:
        answer = click.prompt(prompt, default=", ".join(default), show_default=bool(default), err=True)
        chosen = tuple(part.strip() for part in answer.split(",") if part.strip())
        unknown = [name for name in chosen if name not in known]
        if not unknown:
            return chosen
        click.echo(f"No column called {unknown[0]!r}. Try again.", err=True)


def _ask_index_groups(
    prompt: str,
    default: tuple[tuple[str, ...], ...],
    columns: Sequence[Column],
) -> tuple[tuple[str, ...], ...]:
    known = {column.name for column in columns}
    shown = ", ".join("+".join(group) for group in default)
    while True:
        answer = click.prompt(prompt, default=shown, show_default=bool(shown), err=True)
        groups = tuple(
            tuple(name.strip() for name in part.split("+") if name.strip())
            for part in answer.split(",")
            if part.strip()
        )
        unknown = [name for group in groups for name in group if name not in known]
        if not unknown:
            return groups
        click.echo(f"No column called {unknown[0]!r}. Try again.", err=True)


def _confirm(spec: TableSpec, rows: int) -> None:
    click.echo("", err=True)
    click.echo(f"Table    {spec.name}", err=True)
    click.echo(f"Rows     {rows}", err=True)
    click.echo(f"Key      {', '.join(spec.primary_key) or '(none)'}", err=True)
    for group in spec.indexes:
        click.echo(f"Index    {', '.join(group)}", err=True)
    for group in spec.unique_indexes:
        click.echo(f"Unique   {', '.join(group)}", err=True)
    click.echo("", err=True)
    if not click.confirm("Go ahead?", default=True, err=True):
        raise DataError("Cancelled.")
