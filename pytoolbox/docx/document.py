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
from pytoolbox.docx.styles import Styles

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


def parse_document(pkg: Package, numbering: Numbering, styles: Styles) -> list[Block]:
    """Walk the document body into blocks, in document order."""
    body = pkg.document.find(qn("w:body"))
    if body is None:
        return []
    return _blocks(body, pkg, numbering, styles)


def _blocks(parent: ET.Element, pkg: Package, numbering: Numbering, styles: Styles) -> list[Block]:
    blocks: list[Block] = []
    for child in parent:
        if child.tag == qn("w:p"):
            blocks.append(_paragraph(child, pkg, numbering, styles))
        elif child.tag == qn("w:tbl"):
            blocks.append(_table(child, pkg, numbering, styles))
        elif child.tag == qn("w:sdt"):
            # A content control wraps real content; step through it.
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                blocks.extend(_blocks(content, pkg, numbering, styles))
    return blocks


def _paragraph(element: ET.Element, pkg: Package, numbering: Numbering, styles: Styles) -> Block:
    """Classify one ``w:p`` as a heading, a list item or plain body text."""
    items = parse_inline(element, pkg)
    props = element.find(qn("w:pPr"))

    num_id, ilvl = _list_properties(props, styles)
    if num_id is not None:
        return ListItem(level=ilvl, ordered=numbering.is_ordered(num_id, ilvl), items=items)

    level = _heading_level(props, styles)
    if level is not None:
        return Heading(level=level, items=items)

    return Paragraph(items=items)


def _heading_level(props: Optional[ET.Element], styles: Styles) -> Optional[int]:
    """Heading depth from the style id, falling back to the outline level."""
    if props is None:
        return None

    # A house style is normally built on a built-in heading rather than used in
    # its place, so the whole basedOn chain counts, not just the style named.
    for style_id in styles.chain(_style_id(props)):
        match = _HEADING_ID.match(style_id)
        if match:
            return min(int(match.group(1)), MAX_HEADING)

    outline = props.find(qn("w:outlineLvl"))
    if outline is not None:
        value = attr(outline, "w:val")
        if value is not None and value.isdigit():
            # w:outlineLvl is zero-based, Markdown headings are one-based.
            return min(int(value) + 1, MAX_HEADING)
    return None


def _list_properties(props: Optional[ET.Element], styles: Styles) -> tuple[Optional[str], int]:
    """Return the paragraph's ``numId`` and nesting depth, if it is in a list.

    Either half can come from the paragraph or from its style, and the
    paragraph wins where both speak. Word writes the ``numPr`` on the paragraph
    for a list made with the toolbar, and leaves it to the style for one made
    with *List Bullet* -- both are lists, and only the second was being missed.
    """
    num_pr = props.find(qn("w:numPr")) if props is not None else None
    style_id = _style_id(props)
    style_num_id, style_ilvl = styles.list_properties(style_id)

    own_num_id = _value(num_pr, "w:numId") if num_pr is not None else None
    own_ilvl = _level(_value(num_pr, "w:ilvl")) if num_pr is not None else None

    num_id = own_num_id if own_num_id is not None else style_num_id
    if num_id == "0":
        # Numbering id 0 is Word's way of cancelling what a style applied.
        return None, 0
    if num_id is None:
        # A bare numPr whose style supplies nothing is still a list item, just
        # one with no format to look up; it falls back to a bullet.
        if num_pr is None:
            return None, 0
        num_id = ""

    level = own_ilvl if own_ilvl is not None else style_ilvl
    return num_id, level or 0


def _style_id(props: Optional[ET.Element]) -> Optional[str]:
    if props is None:
        return None
    style = props.find(qn("w:pStyle"))
    return attr(style, "w:val") if style is not None else None


def _value(parent: ET.Element, tag: str) -> Optional[str]:
    element = parent.find(qn(tag))
    return attr(element, "w:val") if element is not None else None


def _level(raw: Optional[str]) -> Optional[int]:
    return int(raw) if raw is not None and raw.isdigit() else None


def _table(element: ET.Element, pkg: Package, numbering: Numbering, styles: Styles) -> Table:
    """Read a table's grid, noting how many leading rows repeat as headers."""
    rows: list[list[Cell]] = []
    header_rows = 0
    counting_header = True

    for row in element.findall(qn("w:tr")):
        cells: list[Cell] = []
        for cell in row.findall(qn("w:tc")):
            cells.append(_blocks(cell, pkg, numbering, styles))
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
