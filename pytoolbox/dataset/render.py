"""Drawing the inferred structure as a tree."""

from __future__ import annotations

from typing import Optional

from pytoolbox.dataset.schema import SchemaNode
from pytoolbox.dataset.sources import RecordSource
from pytoolbox.dataset.types import ValueType

_BRANCH = "|-- "
_LAST = "`-- "
_PIPE = "|   "
_BLANK = "    "


def header(source: RecordSource, root: SchemaNode) -> str:
    """One line naming the source and one describing its top-level shape."""
    where = f"{source.origin}"
    if source.root:
        where += f"  --root {source.root}"
    if source.single:
        shape = f"one object, {len(root.children)} keys"
    else:
        shape = f"list of {len(source.records)} objects, {len(root.children)} keys"
    return f"{where}\n{shape}"


def render(
    root: SchemaNode,
    source: Optional[RecordSource] = None,
    max_depth: Optional[int] = None,
) -> str:
    """Render ``root`` as an indented tree of fields, types and counts."""
    rows: list[tuple[str, str, str, str]] = []
    _collect(root, rows, prefix="", depth=0, max_depth=max_depth)
    if not rows:
        return header(source, root) if source else ""

    label_width = max(len(row[0]) for row in rows)
    type_width = max(len(row[1]) for row in rows)
    count_width = max(len(row[2]) for row in rows)
    lines = [
        "  ".join(
            (
                label.ljust(label_width),
                type_label.ljust(type_width),
                counts.rjust(count_width),
                note,
            )
        ).rstrip()
        for label, type_label, counts, note in rows
    ]
    body = "\n".join(lines)
    return f"{header(source, root)}\n\n{body}" if source else body


def _collect(
    node: SchemaNode,
    rows: list[tuple[str, str, str, str]],
    prefix: str,
    depth: int,
    max_depth: Optional[int],
) -> None:
    children = _visible_children(node)
    for index, child in enumerate(children):
        last = index == len(children) - 1
        connector = _LAST if last else _BRANCH
        rows.append(
            (
                prefix + connector + child.name,
                child.type_label,
                f"{child.present - child.nulls}/{child.total}",
                "nullable" if child.nullable else "",
            )
        )
        if max_depth is not None and depth + 1 >= max_depth:
            continue
        _collect(
            child,
            rows,
            prefix + (_BLANK if last else _PIPE),
            depth + 1,
            max_depth,
        )


def _visible_children(node: SchemaNode) -> list[SchemaNode]:
    """Children worth drawing.

    A list of objects is drawn through its element node, so the structure of
    the elements shows up; a list of scalars is not, because ``list[str]``
    already says everything the element node would.
    """
    children = list(node.children.values())
    if node.item is not None and (node.item.children or node.item.item):
        children.append(node.item)
    return children


def type_legend() -> str:
    """The type names ``--type`` accepts, for help text and errors."""
    names = [value.value for value in ValueType]
    return ", ".join(names + ["mixed"])
