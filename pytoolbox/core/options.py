"""Reusable Click options, groups and context settings.

Every pytoolbox CLI shares these so that ``-h``, ``-v``, ``-y``, ``-n`` and
``--version`` mean the same thing everywhere.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from typing import Any, Callable, Optional

import click

#: ``-h`` as well as ``--help``, and a wider help body than Click's 80 columns.
CONTEXT_SETTINGS = {
    "help_option_names": ["-h", "--help"],
    "max_content_width": 100,
}

F = Callable[..., Any]


def get_version() -> str:
    """Return the installed pytoolbox version."""
    from pytoolbox import __version__

    return __version__


class AliasedGroup(click.Group):
    """A Click group that accepts unambiguous command prefixes and suggests fixes.

    ``pyfm part`` resolves to ``pyfm partition``; a typo gets a "did you mean"
    list instead of a bare "no such command", which is the single biggest
    usability win for CLIs with many subcommands.
    """

    def get_command(self, ctx: click.Context, cmd_name: str) -> Optional[click.Command]:
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            return command

        matches = [name for name in self.list_commands(ctx) if name.startswith(cmd_name)]
        if len(matches) == 1 and matches[0] != cmd_name:
            # Resolve through self, not super(), so subclasses that load
            # commands lazily still get a chance to produce the real command.
            return self.get_command(ctx, matches[0])
        if len(matches) > 1:
            ctx.fail(f"Ambiguous command {cmd_name!r}: matches {', '.join(sorted(matches))}.")

        close = difflib.get_close_matches(cmd_name, self.list_commands(ctx), n=3, cutoff=0.5)
        if close:
            ctx.fail(f"No such command {cmd_name!r}. Did you mean: {', '.join(close)}?")
        return None

    def resolve_command(self, ctx: click.Context, args: Sequence[str]):
        # Report the full command name rather than the abbreviation the user typed,
        # so usage/error lines stay copy-pasteable.
        _, command, rest = super().resolve_command(ctx, list(args))
        return command.name, command, rest


def version_option(func: F) -> F:
    """Add ``-V/--version`` printing the pytoolbox version."""
    return click.version_option(
        get_version(),
        "-V",
        "--version",
        prog_name="pytoolbox",
        message="%(prog)s %(version)s",
    )(func)


def verbose_option(func: F) -> F:
    """Add repeatable ``-v/--verbose``."""
    return click.option(
        "-v",
        "--verbose",
        count=True,
        help="Increase output detail. Repeat for more (-vv).",
    )(func)


def quiet_option(func: F) -> F:
    """Add ``-q/--quiet`` to silence progress output."""
    return click.option(
        "-q", "--quiet", is_flag=True, help="Suppress progress and summary output."
    )(func)


def yes_option(func: F) -> F:
    """Add ``-y/--yes`` to skip confirmation prompts."""
    return click.option(
        "-y", "--yes", "assume_yes", is_flag=True, help="Do not ask for confirmation."
    )(func)


def dry_run_option(func: F) -> F:
    """Add ``-n/--dry-run`` to preview an operation without changing anything."""
    return click.option(
        "-n",
        "--dry-run",
        is_flag=True,
        help="Show what would happen without changing anything.",
    )(func)


def json_option(func: F) -> F:
    """Add ``--json`` for machine-readable output."""
    return click.option(
        "--json", "as_json", is_flag=True, help="Print results as JSON."
    )(func)


def format_option(
    default: str = "table",
    choices: Sequence[str] = ("table", "csv", "markdown", "json", "excel"),
) -> Callable[[F], F]:
    """Add ``--format`` with the shared output-format choices."""

    def decorator(func: F) -> F:
        return click.option(
            "--format",
            "output_format",
            type=click.Choice(list(choices), case_sensitive=False),
            default=default,
            show_default=True,
            help="Output format.",
        )(func)

    return decorator


def encoding_options(func: F) -> F:
    """Add ``--encoding`` and ``--errors`` for text IO."""
    func = click.option(
        "--errors",
        default="replace",
        type=click.Choice(["strict", "ignore", "replace"], case_sensitive=False),
        help="How to handle undecodable bytes.",
        show_default=True,
    )(func)
    return click.option(
        "--encoding", default="utf-8", show_default=True, help="Text encoding for file IO."
    )(func)
