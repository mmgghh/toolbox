"""Tests for the ``--interactive`` column-selection parsing."""

from __future__ import annotations

import pytest

from pytoolbox.dataset import interactive
from pytoolbox.dataset.errors import DataError
from pytoolbox.dataset.schema import Column
from pytoolbox.dataset.types import ValueType

COLUMNS = [
    Column(name="id", source="id", type=ValueType.INT, nullable=False),
    Column(name="name", source="name", type=ValueType.STR, nullable=False),
]


def test_resolve_included_accepts_a_position_or_a_name():
    assert interactive._resolve_included(["1", "name"], COLUMNS) == COLUMNS


def test_resolve_included_rejects_an_out_of_range_position():
    with pytest.raises(DataError):
        interactive._resolve_included(["3"], COLUMNS)


def test_resolve_included_rejects_a_digit_lookalike_cleanly():
    """A superscript passes isdigit() but int() cannot parse it; it must fall
    through to the name match and be reported there, not crash."""
    with pytest.raises(DataError):
        interactive._resolve_included(["²"], COLUMNS)


def test_resolve_included_rejects_non_ascii_digits_as_a_position():
    """A column position is always plain ASCII; Persian digits fall through
    to the name lookup rather than being read as a position."""
    with pytest.raises(DataError):
        interactive._resolve_included(["۱"], COLUMNS)
