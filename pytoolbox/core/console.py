"""Consistent terminal output for every pytoolbox command.

All user-facing text goes through here so that colour, verbosity, JSON mode
and stream selection behave the same way in every CLI. Diagnostics go to
stderr; only real results go to stdout, which keeps ``pyfm ... | xargs`` and
friends working even with ``-v``.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Sequence
from typing import Any, Optional

import click

#: Honoured by the informal https://no-color.org/ convention.
NO_COLOR_ENV = "NO_COLOR"


def color_enabled(stream: Optional[Any] = None) -> bool:
    """Whether ANSI colour should be used for ``stream``."""
    if os.environ.get(NO_COLOR_ENV):
        return False
    if os.environ.get("PYTOOLBOX_FORCE_COLOR"):
        return True
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def style(text: str, **kwargs: Any) -> str:
    """Colourise ``text`` unless colour is disabled."""
    if not color_enabled(sys.stderr):
        return text
    return click.style(text, **kwargs)


def echo(message: str = "", err: bool = False, nl: bool = True) -> None:
    """Print a message verbatim."""
    click.echo(message, err=err, nl=nl)


def result(message: str = "") -> None:
    """Print a primary result to stdout (never suppressed, never coloured)."""
    click.echo(message)


def info(message: str, verbose: int = 1, threshold: int = 1) -> None:
    """Print progress information to stderr when ``verbose >= threshold``."""
    if verbose >= threshold:
        click.echo(style(message, fg="cyan"), err=True)


def success(message: str, verbose: int = 1, threshold: int = 0) -> None:
    """Print a confirmation of completed work to stderr."""
    if verbose >= threshold:
        click.echo(style(message, fg="green"), err=True)


def warn(message: str) -> None:
    """Print a warning to stderr."""
    click.echo(f"{style('warning:', fg='yellow', bold=True)} {message}", err=True)


def error(message: str) -> None:
    """Print an error to stderr without exiting."""
    click.echo(f"{style('error:', fg='red', bold=True)} {message}", err=True)


def fail(message: str, exit_code: int = 1) -> click.ClickException:
    """Return a ClickException carrying ``message``.

    Returned rather than raised so callers keep an explicit ``raise`` at the
    point of failure, which reads better and satisfies static analysis.
    """
    exc = click.ClickException(message)
    exc.exit_code = exit_code
    return exc


def confirm(message: str, assume_yes: bool = False, default: bool = False) -> bool:
    """Ask for confirmation unless ``assume_yes`` short-circuits it.

    Non-interactive sessions (pipes, cron, CI) get the ``default`` answer
    instead of an EOF traceback.
    """
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        return default
    return click.confirm(message, default=default)


def emit_json(payload: Any) -> None:
    """Print ``payload`` as indented UTF-8 JSON on stdout."""
    click.echo(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


def dry_run_notice(enabled: bool) -> None:
    """Announce that no changes will be written."""
    if enabled:
        click.echo(style("dry run: no changes will be written", fg="yellow"), err=True)


def plural(count: int, singular: str, plural_form: Optional[str] = None) -> str:
    """Return ``"1 file"`` / ``"3 files"`` style text."""
    word = singular if count == 1 else (plural_form or f"{singular}s")
    return f"{count} {word}"


def print_rows(rows: Sequence[dict], headers: Sequence[str], as_json: bool = False) -> None:
    """Print tabular data either as JSON or as an aligned text table."""
    from pytoolbox.core.tables import render_table

    if as_json:
        emit_json([{header: row.get(header, "") for header in headers} for row in rows])
        return
    click.echo(render_table(list(rows), list(headers)))
