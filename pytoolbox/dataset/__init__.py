"""Reading JSON, CSV and Excel into a schema, a summary and SQL.

The pipeline is one-way and each stage has one job::

    sources  ->  schema  ->  render / summarize / select / sql

:mod:`~pytoolbox.dataset.sources` finds the records, :mod:`~pytoolbox.dataset.schema`
infers their structure, and everything after that reads the same tree. The CLI
that drives it is :mod:`pytoolbox.pydata`.
"""

from __future__ import annotations

from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.schema import Column, SchemaNode, columns_of, infer
from pytoolbox.dataset.sources import RecordSource, load
from pytoolbox.dataset.types import ValueType

__all__ = [
    "Column",
    "DataError",
    "RecordSource",
    "SchemaNode",
    "ValueType",
    "columns_of",
    "infer",
    "load",
]
