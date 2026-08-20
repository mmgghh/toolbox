"""Working out which names to change, and to what.

This module decides; it never touches the disk. ``plan`` resolves every
``OLD=NEW`` pair against the names the readers found and refuses anything that
would lose data -- an unknown name, two columns renamed to the same thing, a
new name already taken -- so that a rename either happens completely or does
not start.

The plan is positional, because a spreadsheet column is a position: a blank
header cell shown as ``column_3`` is still addressable. JSON keys have no
position, so the JSON writer matches on the old name instead.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence
from dataclasses import dataclass

from pytoolbox.dataset import naming
from pytoolbox.dataset.errors import DataError


@dataclass(frozen=True)
class Rename:
    """One name to change: where it is, what it was, what it becomes."""

    index: int
    old: str
    new: str


@dataclass(frozen=True)
class RenamePlan:
    """Every change to make, against the names as they are now."""

    renames: tuple[Rename, ...]
    names: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(self.renames)

    def applied(self) -> tuple[str, ...]:
        """The names as they will be once the plan is written."""
        after = list(self.names)
        for item in self.renames:
            after[item.index] = item.new
        return tuple(after)


def parse_pairs(pairs: Sequence[str]) -> list[tuple[str, str]]:
    """Turn ``OLD=NEW`` strings into pairs, refusing the malformed ones."""
    parsed = []
    for pair in pairs:
        old, sep, new = pair.partition("=")
        old, new = old.strip(), new.strip()
        if not sep or not old or not new:
            raise DataError(f"--rename wants OLD=NEW, got {pair!r}.")
        parsed.append((old, new))
    return parsed


def resolve(names: Sequence[str], old: str) -> int:
    """The position of ``old`` among ``names``, however it was spelled.

    Exact first, then case-insensitively, then by the SQL spelling, so the
    name shown by ``tree`` and the name shown by ``summary`` both find it.
    """
    if old in names:
        return names.index(old)
    folded = old.casefold()
    for index, name in enumerate(names):
        if name.casefold() == folded:
            return index
    wanted = naming.sanitize(old)
    for index, name in enumerate(names):
        if naming.sanitize(name) == wanted:
            return index
    raise DataError(f"No column called {old!r}.{_suggest(old, names)}")


def plan(names: Sequence[str], pairs: Sequence[str]) -> RenamePlan:
    """Resolve ``OLD=NEW`` pairs into a plan, or refuse to make one."""
    return build(names, parse_pairs(pairs))


def build(names: Sequence[str], pairs: Sequence[tuple[str, str]]) -> RenamePlan:
    """The plan for already-parsed pairs, as interactive mode produces them."""
    names = tuple(names)
    renames: dict[int, Rename] = {}
    for old, new in pairs:
        index = resolve(names, old)
        if names[index] == new:
            continue  # Renaming a name to itself is nothing to do, not an error.
        renames[index] = Rename(index=index, old=names[index], new=new)
    ordered = tuple(renames[index] for index in sorted(renames))
    _check_collisions(names, ordered)
    return RenamePlan(renames=ordered, names=names)


def _check_collisions(names: Sequence[str], renames: Sequence[Rename]) -> None:
    """Refuse a plan that would leave two columns sharing a name."""
    moved = {item.index for item in renames}
    taken = {name: index for index, name in enumerate(names) if index not in moved}
    for item in renames:
        if item.new in taken:
            other = names[taken[item.new]]
            raise DataError(
                f"Renaming {item.old!r} to {item.new!r} would collide with the "
                f"column already called {other!r}."
            )
        taken[item.new] = item.index


def _suggest(name: str, names: Sequence[str]) -> str:
    """Point at a real column, by likeness when there is one."""
    close = difflib.get_close_matches(name, names, n=3, cutoff=0.5)
    if close:
        return " Did you mean: " + ", ".join(close) + "?"
    return " Columns: " + ", ".join(names) + "."
