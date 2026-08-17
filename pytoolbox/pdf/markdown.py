"""Blocks to Markdown text.

Smaller than the docx writer because a PDF carries none of what makes that one
complicated: no comments and no footnotes.
"""

from __future__ import annotations

import posixpath
from typing import Optional

from pytoolbox.core.markdown import emphasis
from pytoolbox.pdf.structure import (
    Block,
    Cell,
    Heading,
    Image,
    ListItem,
    PageBreak,
    Paragraph,
    Run,
    Table,
)

MAX_HEADING = 6

#: Width of a bullet marker, and the indent one nesting level costs under it.
BULLET_WIDTH = 2


def render(blocks: list[Block], *, assets_dir: Optional[str] = None) -> str:
    """Render ``blocks`` as a Markdown document."""
    # Each piece carries whether it is a list item, so consecutive items can be
    # packed without a blank line between them. Inspecting the rendered text
    # instead would mistake a paragraph that opens with "- " for an item.
    pieces: list[tuple[str, bool]] = []
    counters: dict[int, int] = {}
    widths: dict[int, int] = {}

    for block in blocks:
        if isinstance(block, ListItem):
            pieces.append((_list_item(block, counters, widths), True))
            continue

        # Any other block closes the list, so numbering restarts after it.
        counters.clear()
        widths.clear()

        if isinstance(block, Heading):
            level = max(1, min(block.level, MAX_HEADING))
            # Headings are set bold in the PDF almost by definition, and a
            # "# **Title**" would carry that weight twice over.
            pieces.append((f"{'#' * level} {_inline(block.runs, bold=False)}", False))
        elif isinstance(block, Paragraph):
            text = _inline(block.runs)
            if text:
                pieces.append((text, False))
        elif isinstance(block, Image):
            if assets_dir:
                pieces.append((f"![]({posixpath.join(assets_dir, block.name)})", False))
        elif isinstance(block, Table):
            text = _table(block)
            if text:
                pieces.append((text, False))
        elif isinstance(block, PageBreak):
            pieces.append(("---", False))

    return _assemble(pieces)


def _table(block: Table) -> str:
    """A grid as a GitHub-flavoured Markdown table.

    Markdown has no row spans and no line breaks inside a cell, so a cell that
    wrapped over several lines is put back as one, and the top row becomes the
    header whether or not it was one. Both are lossy, and both are what a
    reader would write out by hand from the same page.
    """
    width = max((len(row) for row in block.rows), default=0)
    if not width or not block.rows:
        return ""
    alignment = ("---:" if block.rtl else ":---",) * width
    lines = [_row(block.rows[0], width), "| " + " | ".join(alignment) + " |"]
    lines.extend(_row(row, width) for row in block.rows[1:])
    return "\n".join(lines)


def _row(row: list[Cell], width: int) -> str:
    filled = list(row) + [Cell() for _ in range(width - len(row))]
    return "| " + " | ".join(_cell(cell) for cell in filled) + " |"


def _cell(cell: Cell) -> str:
    """One cell's blocks on a single line, with the table syntax neutralised."""
    parts: list[str] = []
    for block in cell.blocks:
        if isinstance(block, Heading):
            parts.append(_inline(block.runs, bold=False))
        elif isinstance(block, (Paragraph, ListItem)):
            parts.append(_inline(block.runs))
    return " ".join(part for part in parts if part).replace("|", r"\|")


def _assemble(pieces: list[tuple[str, bool]]) -> str:
    """Join blocks with a blank line, except between items of one list."""
    if not pieces:
        return ""
    out = [pieces[0][0]]
    for (_, previous_item), (text, is_item) in zip(pieces, pieces[1:]):
        out.append("\n" if previous_item and is_item else "\n\n")
        out.append(text)
    return "".join(out) + "\n"


def _list_item(block: ListItem, counters: dict[int, int], widths: dict[int, int]) -> str:
    """One list item, indented to line up under its parent's text."""
    level = max(1, block.level)
    # Deeper levels end when a shallower item appears, so their numbering and
    # marker widths must not survive into the next sublist.
    for deeper in [key for key in counters if key > level]:
        del counters[deeper]
    for deeper in [key for key in widths if key > level]:
        del widths[deeper]

    if block.ordered:
        counters[level] = counters.get(level, 0) + 1
        marker = f"{counters[level]}."
    else:
        marker = "-"
    widths[level] = len(marker) + 1

    indent = " " * sum(widths.get(parent, BULLET_WIDTH) for parent in range(1, level))
    return f"{indent}{marker} {_inline(block.runs)}"


def _inline(runs: list[Run], *, bold: bool = True) -> str:
    return "".join(_run(run, bold=bold) for run in runs).strip()


def _run(run: Run, *, bold: bool = True) -> str:
    return emphasis(run.text, bold=run.bold and bold, italic=run.italic, link=run.link)
