"""The ``--interactive`` flow: show what was inferred, then ask what to build.

Every prompt goes to stderr so that ``--sql -`` still pipes a clean script
into ``psql`` while the conversation happens on the terminal.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Optional

import click

from pytoolbox.core import tables
from pytoolbox.dataset import naming, render, summarize
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
) -> tuple[TableSpec, list[Column]]:
    """Show a summary, then ask what the table should be.

    Returns the spec and the columns to build it from, which are every column
    unless the user narrowed them.
    """
    _show(source, root, columns)

    name = click.prompt(
        "Table name",
        default=defaults.name or table_module.default_table_name(source.origin, source.root),
        err=True,
    )

    total = len(columns)
    columns = _ask_included(columns)

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
    _confirm(spec, len(source.records), len(columns), total)
    return spec, list(columns)


def _ask_included(columns: Sequence[Column]) -> list[Column]:
    """Ask which columns the table should have; blank keeps all of them."""
    listing = ", ".join(f"{index + 1} {column.name}" for index, column in enumerate(columns))
    click.echo(f"Columns: {listing}", err=True)
    while True:
        answer = click.prompt(
            "Columns to include (numbers or names, blank for all)",
            default="",
            show_default=False,
            err=True,
        )
        parts = [part.strip() for part in answer.split(",") if part.strip()]
        if not parts:
            return list(columns)
        try:
            return _resolve_included(parts, columns)
        except DataError as problem:
            click.echo(f"{problem.message} Try again.", err=True)


def _resolve_included(parts: Sequence[str], columns: Sequence[Column]) -> list[Column]:
    """The columns named by numbers, names, or a mix of the two.

    The order typed is the order kept, so a table can be given a column order
    of its own, and a column named twice is only taken once.
    """
    chosen: list[Column] = []
    for part in parts:
        column = _one_column(part, columns)
        if column not in chosen:
            chosen.append(column)
    return chosen


def _one_column(part: str, columns: Sequence[Column]) -> Column:
    # isascii() too: a column position is always plain ASCII, and a
    # digit-like character isdigit() accepts but int() can't parse (e.g. a
    # superscript) would otherwise crash here instead of falling through to
    # the name match.
    if part.isascii() and part.isdigit():
        position = int(part)
        if not 1 <= position <= len(columns):
            raise DataError(f"There is no column {position}; they are numbered 1 to {len(columns)}.")
        return columns[position - 1]
    wanted = part.casefold()
    for column in columns:
        if wanted in (column.name.casefold(), column.source.casefold()):
            return column
    raise DataError(f"No column called {part!r}.")


def rename_columns(
    source: RecordSource,
    root: SchemaNode,
    columns: Sequence[Column],
    names: Sequence[str],
    presets: Optional[dict[int, str]] = None,
    suggest: bool = False,
) -> list[tuple[str, str]]:
    """Ask for every name in turn, and return the ones that changed.

    Enter keeps the name as it is, so walking the whole list without typing
    anything is a no-op. ``--suggest`` offers the SQL spelling as the default
    instead, and a rename already given on the command line pre-fills its own.
    """
    _show(source, root, columns)
    click.echo("Enter keeps a name as it is.", err=True)

    presets = presets or {}
    chosen = list(names)
    for index, name in enumerate(names):
        default = presets.get(index) or (naming.sanitize(name) if suggest else name)
        chosen[index] = _ask_name(f"[{index + 1}/{len(names)}] {name}", default, chosen, index)
    return [(name, chosen[index]) for index, name in enumerate(names) if chosen[index] != name]


def _ask_name(prompt: str, default: str, chosen: Sequence[str], index: int) -> str:
    """One name, asked until the answer is usable."""
    taken = {name for position, name in enumerate(chosen) if position != index}
    while True:
        answer = click.prompt(prompt, default=default, err=True).strip()
        if not answer:
            click.echo("A name cannot be empty. Try again.", err=True)
        elif answer in taken:
            click.echo(f"Another column is already called {answer!r}. Try again.", err=True)
        else:
            return answer


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


def _confirm(spec: TableSpec, rows: int, kept: int, total: int) -> None:
    click.echo("", err=True)
    click.echo(f"Table    {spec.name}", err=True)
    click.echo(f"Rows     {rows}", err=True)
    if kept != total:
        click.echo(f"Columns  {kept} of {total}", err=True)
    click.echo(f"Key      {', '.join(spec.primary_key) or '(none)'}", err=True)
    for group in spec.indexes:
        click.echo(f"Index    {', '.join(group)}", err=True)
    for group in spec.unique_indexes:
        click.echo(f"Unique   {', '.join(group)}", err=True)
    click.echo("", err=True)
    if not click.confirm("Go ahead?", default=True, err=True):
        raise DataError("Cancelled.")
