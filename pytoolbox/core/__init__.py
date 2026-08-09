"""Shared building blocks used by every ``pytoolbox`` command.

The individual CLIs (``pyfm``, ``pystr``, ``pyjdate``, ``pytime``, ``pyssh``,
``pynet``, ``pymd2pdf``) stay thin by delegating anything cross-cutting --
paths, output formatting, interval parsing, filesystem walking, clipboard
access -- to this package.
"""

from pytoolbox.core import clipboard, console, fs, intervals, options, paths, tables

__all__ = [
    "clipboard",
    "console",
    "fs",
    "intervals",
    "options",
    "paths",
    "tables",
]
