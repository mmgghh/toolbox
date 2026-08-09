"""``document.xml`` as a flat list of blocks.

The output is deliberately plain: dataclasses holding inline items, with no
Markdown anywhere. That seam is what lets the writer be tested without building
a ``.docx``, and what would let a second writer (HTML, say) reuse all of this.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Union
from xml.etree import ElementTree as ET

from pytoolbox.docx.inline import Item, parse_inline
from pytoolbox.docx.numbering import Numbering
from pytoolbox.docx.package import Package, attr, qn

#: ``Heading1`` … ``Heading9``. Style *ids* are language-independent in OOXML,
#: while the display names in styles.xml are localised -- matching on the name
#: would find nothing in a document authored in a localised Word.
_HEADING_ID = re.compile(r"^Heading(\d+)$", re.IGNORECASE)

MAX_HEADING = 6


@dataclass
class Paragraph:
    items: list[Item] = field(default_factory=list)


@dataclass
class Heading:
    level: int
    items: list[Item] = field(default_factory=list)


@dataclass
class ListItem:
    level: int
    ordered: bool
    items: list[Item] = field(default_factory=list)


#: A table cell holds block content of its own, though in practice paragraphs.
Cell = list[Union[Paragraph, Heading, ListItem]]


@dataclass
class Table:
    rows: list[list[Cell]] = field(default_factory=list)
    header_rows: int = 1


Block = Union[Paragraph, Heading, ListItem, Table]


def items_of(block: Block) -> list[Item]:
    """Flatten a block's inline items, descending into table cells."""
    if isinstance(block, Table):
        return [
            item
            for row in block.rows
            for cell in row
            for cell_block in cell
            for item in items_of(cell_block)
        ]
    return list(block.items)


def parse_document(pkg: Package, numbering: Numbering) -> list[Block]:
    """Walk the document body into blocks, in document order."""
    body = pkg.document.find(qn("w:body"))
    if body is None:
        return []
    return _blocks(body, pkg, numbering)


def _blocks(parent: ET.Element, pkg: Package, numbering: Numbering) -> list[Block]:
    blocks: list[Block] = []
    for child in parent:
        if child.tag == qn("w:p"):
            blocks.append(_paragraph(child, pkg, numbering))
        elif child.tag == qn("w:tbl"):
            blocks.append(_table(child, pkg, numbering))
        elif child.tag == qn("w:sdt"):
            # A content control wraps real content; step through it.
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                blocks.extend(_blocks(content, pkg, numbering))
    return blocks


def _paragraph(element: ET.Element, pkg: Package, numbering: Numbering) -> Block:
    """Classify one ``w:p`` as a heading, a list item or plain body text."""
    items = parse_inline(element, pkg)
    props = element.find(qn("w:pPr"))

    num_id, ilvl = _list_properties(props)
    if num_id is not None:
        return ListItem(level=ilvl, ordered=numbering.is_ordered(num_id, ilvl), items=items)

    level = _heading_level(props)
    if level is not None:
        return Heading(level=level, items=items)

    return Paragraph(items=items)


def _heading_level(props: Optional[ET.Element]) -> Optional[int]:
    """Heading depth from the style id, falling back to the outline level."""
    if props is None:
        return None

    style = props.find(qn("w:pStyle"))
    if style is not None:
        match = _HEADING_ID.match(attr(style, "w:val") or "")
        if match:
            return min(int(match.group(1)), MAX_HEADING)

    outline = props.find(qn("w:outlineLvl"))
    if outline is not None:
        value = attr(outline, "w:val")
        if value is not None and value.isdigit():
            # w:outlineLvl is zero-based, Markdown headings are one-based.
            return min(int(value) + 1, MAX_HEADING)
    return None


def _list_properties(props: Optional[ET.Element]) -> tuple[Optional[str], int]:
    """Return the paragraph's ``numId`` and nesting depth, if it is in a list."""
    if props is None:
        return None, 0
    num_pr = props.find(qn("w:numPr"))
    if num_pr is None:
        return None, 0

    # A bare numPr is legal: the numbering can come from the paragraph style.
    # Such a paragraph is still a list item, just one with no format to look up.
    num_el = num_pr.find(qn("w:numId"))
    num_id = attr(num_el, "w:val") if num_el is not None else ""
    ilvl_el = num_pr.find(qn("w:ilvl"))
    raw_level = attr(ilvl_el, "w:val") if ilvl_el is not None else "0"
    level = int(raw_level) if raw_level and raw_level.isdigit() else 0
    return num_id, level


def _table(element: ET.Element, pkg: Package, numbering: Numbering) -> Table:
    """Read a table's grid, noting how many leading rows repeat as headers."""
    rows: list[list[Cell]] = []
    header_rows = 0
    counting_header = True

    for row in element.findall(qn("w:tr")):
        cells: list[Cell] = []
        for cell in row.findall(qn("w:tc")):
            cells.append(_blocks(cell, pkg, numbering))
        rows.append(cells)

        if counting_header and _is_header_row(row):
            header_rows += 1
        else:
            counting_header = False

    # Markdown pipe tables require a header, so a table that declares none
    # still gets one: its first row.
    return Table(rows=rows, header_rows=header_rows or (1 if rows else 0))


def _is_header_row(row: ET.Element) -> bool:
    props = row.find(qn("w:trPr"))
    return props is not None and props.find(qn("w:tblHeader")) is not None
