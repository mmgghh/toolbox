"""The one exception type the ``pydata`` pipeline raises.

It subclasses :class:`click.ClickException` so that a failure deep in the
reader still reaches the user as a plain one-line message and exit code 1,
without a traceback, while remaining catchable by name in tests.
"""

from __future__ import annotations

import click


class DataError(click.ClickException):
    """A problem with the input data, the options, or the target database."""
