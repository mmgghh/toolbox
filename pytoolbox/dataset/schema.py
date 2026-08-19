"""Inferring a structure tree from records.

Every feature of ``pydata`` reads the same tree: the tree view prints it, the
summary decorates it with statistics, the filter selects branches of it and
the SQL step turns its top level into columns. A node records every type seen
at its path together with how often the path was present and how often it was
null, which is what makes "some records are missing this key" expressible as a
nullable column.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Optional

from pytoolbox.dataset import naming
from pytoolbox.dataset.types import CONTAINER_TYPES, ValueType, classify, unify_all

#: A path segment that stands for "the elements of this list".
ITEM = "[]"


@dataclass
class SchemaNode:
    """One key -- or one list-element position -- in the inferred structure."""

    name: str
    path: str
    #: How many times each concrete type was seen at this path.
    type_counts: Counter[ValueType] = field(default_factory=Counter)
    #: Records in which the path existed at all.
    present: int = 0
    #: Records in which the path existed and held ``null``.
    nulls: int = 0
    #: Records that could have contained the path.
    total: int = 0
    #: Child nodes, for paths that held objects.
    children: dict[str, SchemaNode] = field(default_factory=dict)
    #: The schema of the elements, for paths that held lists.
    item: Optional[SchemaNode] = None
    #: The values seen at this path, kept for the summary.
    values: list = field(default_factory=list)

    @property
    def type(self) -> ValueType:
        """The single type that can hold every value seen here."""
        return unify_all(self.type_counts)

    @property
    def nullable(self) -> bool:
        """True when a record could lack a value here."""
        return self.nulls > 0 or self.present < self.total

    @property
    def missing(self) -> int:
        """Records in which the path was absent or null."""
        return self.nulls + (self.total - self.present)

    @property
    def type_label(self) -> str:
        """A human-readable type, showing the parts of a mixed field.

        ``list[str]`` is more useful than ``list`` in a tree view, and
        ``int|str`` says plainly why a column came out as text.
        """
        concrete = [t for t in self.type_counts if t is not ValueType.NULL]
        if not concrete:
            return str(ValueType.NULL)
        if len(concrete) > 1:
            # Show the parts in the order the lattice widens them.
            ordered = sorted(concrete, key=list(ValueType).index)
            return "|".join(str(t) for t in ordered)
        only = concrete[0]
        if only is ValueType.LIST and self.item is not None:
            return f"list[{self.item.type_label}]"
        return str(only)

    def walk(self) -> Iterable[SchemaNode]:
        """Yield this node and every node beneath it, depth first."""
        yield self
        for child in self.children.values():
            yield from child.walk()
        if self.item is not None:
            yield from self.item.walk()


def infer(records: Sequence[dict], columns: Sequence[str]) -> SchemaNode:
    """Build the schema of a list of records.

    ``columns`` fixes the order of the top level so that the output follows the
    key order of the source rather than the order Python happens to hash them
    in.
    """
    root = SchemaNode(name="", path="", present=len(records), total=len(records))
    root.type_counts[ValueType.OBJECT] = len(records)
    for column in columns:
        values = [record[column] for record in records if column in record]
        root.children[column] = _infer_node(column, column, values, len(records))
    return root


def _infer_node(name: str, path: str, values: Sequence[object], total: int) -> SchemaNode:
    node = SchemaNode(name=name, path=path, present=len(values), total=total)
    node.values = list(values)
    for value in values:
        value_type = classify(value)
        node.type_counts[value_type] += 1
        if value_type is ValueType.NULL:
            node.nulls += 1

    objects = [value for value in values if isinstance(value, dict)]
    if objects:
        for key in _ordered_keys(objects):
            child_values = [obj[key] for obj in objects if key in obj]
            child_path = f"{path}.{key}" if path else key
            node.children[key] = _infer_node(key, child_path, child_values, len(objects))

    lists = [value for value in values if isinstance(value, (list, tuple))]
    if lists:
        elements = [element for value in lists for element in value]
        item_path = f"{path}.{ITEM}" if path else ITEM
        node.item = _infer_node(ITEM, item_path, elements, len(elements))

    return node


def _ordered_keys(objects: Sequence[dict]) -> list[str]:
    """Every key across ``objects``, in the order they are first seen."""
    keys: dict[str, None] = {}
    for obj in objects:
        for key in obj:
            keys.setdefault(key, None)
    return list(keys)


@dataclass(frozen=True)
class Column:
    """One top-level field, resolved to something a table can hold."""

    #: The SQL identifier, after sanitizing and any ``--column`` rename.
    name: str
    #: The original key, as it appears in the data.
    source: str
    type: ValueType
    nullable: bool

    @property
    def renamed(self) -> bool:
        return self.name != self.source


def columns_of(
    root: SchemaNode,
    renames: Optional[dict[str, str]] = None,
    raw: bool = False,
) -> list[Column]:
    """Resolve the top level of ``root`` into table columns.

    Nested objects and lists collapse to a single JSON-typed column, as they
    are stored whole rather than normalized into child tables. ``raw`` keeps
    each key as the column name instead of folding it to snake_case.
    """
    renames = renames or {}
    sources = list(root.children)
    identifiers = naming.unique(sources, raw=raw)
    columns = []
    for source, identifier in zip(sources, identifiers):
        node = root.children[source]
        value_type = node.type
        if value_type in CONTAINER_TYPES:
            value_type = ValueType.JSON
        name = renames.get(identifier, renames.get(source, identifier))
        columns.append(
            Column(name=name, source=source, type=value_type, nullable=node.nullable)
        )
    return columns
