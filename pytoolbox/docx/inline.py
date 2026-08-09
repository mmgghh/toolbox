"""The inline content of a paragraph: runs, marks and image references.

Body paragraphs and comment bodies are the same shape in OOXML, so both are
parsed here. Everything downstream works on these small objects rather than on
XML, which is what lets the Markdown writer be tested without a ``.docx``.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from typing import Optional, Union
from xml.etree import ElementTree as ET

from pytoolbox.docx.package import Package, attr, qn


@dataclass
class Run:
    """A stretch of text sharing one set of character properties."""

    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False
    link: Optional[str] = None


@dataclass
class CommentMark:
    """Where a comment's range ends, and so where its marker is drawn."""

    comment_id: str


@dataclass
class FootnoteMark:
    """A footnote or endnote reference."""

    note_id: str
    endnote: bool = False


@dataclass
class ImageRef:
    """An embedded image, named by the package part that holds its bytes."""

    part_name: str
    alt: str = ""


Item = Union[Run, CommentMark, FootnoteMark, ImageRef]

#: Fonts Word uses for inline code. Matching on name is crude but it is the
#: only signal in the file; there is no semantic "code" run property.
_CODE_FONTS = {"consolas", "courier new", "cascadia mono", "cascadia code", "menlo", "monaco"}

#: Revision wrappers whose content is part of the final document.
_KEEP_WRAPPERS = {"w:ins", "w:moveTo", "w:smartTag", "w:sdtContent", "w:bookmarkStart"}
#: Revision wrappers whose content was removed by the author.
_DROP_WRAPPERS = {"w:del", "w:moveFrom"}


def parse_inline(paragraph: ET.Element, pkg: Package) -> list[Item]:
    """Flatten one ``w:p`` into an ordered list of runs and marks."""
    items: list[Item] = []
    _walk(paragraph, pkg, items, link=None)
    return items


def _walk(parent: ET.Element, pkg: Package, items: list[Item], link: Optional[str]) -> None:
    for child in parent:
        tag = child.tag
        if tag == qn("w:r"):
            items.extend(_run_items(child, pkg, link))
        elif tag == qn("w:hyperlink"):
            _walk(child, pkg, items, link=_hyperlink_target(child, pkg))
        elif tag == qn("w:commentRangeEnd"):
            comment_id = attr(child, "w:id")
            if comment_id is not None:
                items.append(CommentMark(comment_id))
        elif tag in {qn(name) for name in _DROP_WRAPPERS}:
            continue
        elif tag in {qn(name) for name in _KEEP_WRAPPERS}:
            _walk(child, pkg, items, link)


def _hyperlink_target(element: ET.Element, pkg: Package) -> Optional[str]:
    """Resolve a hyperlink's relationship, or its internal anchor."""
    rel_id = attr(element, "r:id")
    if rel_id:
        return pkg.rel_target(rel_id)
    anchor = attr(element, "w:anchor")
    return f"#{anchor}" if anchor else None


def _run_items(element: ET.Element, pkg: Package, link: Optional[str]) -> list[Item]:
    """Turn one ``w:r`` into its text, marks and images, in document order."""
    props = _run_properties(element)
    items: list[Item] = []
    text_parts: list[str] = []

    def flush() -> None:
        if text_parts:
            items.append(Run(text="".join(text_parts), link=link, **props))
            text_parts.clear()

    for child in element:
        tag = child.tag
        if tag == qn("w:t"):
            text_parts.append(child.text or "")
        elif tag == qn("w:tab"):
            text_parts.append(" ")
        elif tag in (qn("w:br"), qn("w:cr")):
            text_parts.append("\n")
        elif tag == qn("w:noBreakHyphen"):
            text_parts.append("-")
        elif tag == qn("w:footnoteReference"):
            flush()
            note_id = attr(child, "w:id")
            if note_id is not None:
                items.append(FootnoteMark(note_id))
        elif tag == qn("w:endnoteReference"):
            flush()
            note_id = attr(child, "w:id")
            if note_id is not None:
                items.append(FootnoteMark(note_id, endnote=True))
        elif tag in (qn("w:drawing"), qn("w:pict")):
            flush()
            image = _image_ref(child, pkg)
            if image is not None:
                items.append(image)
        # w:delText is deliberately absent: deleted text never reaches here,
        # because the w:del wrapper is skipped before the run is visited.

    flush()
    return items


def _run_properties(element: ET.Element) -> dict:
    """Read bold/italic/strike/code off a run's ``w:rPr``."""
    props = {"bold": False, "italic": False, "strike": False, "code": False}
    rpr = element.find(qn("w:rPr"))
    if rpr is None:
        return props
    props["bold"] = _toggle(rpr, "w:b")
    props["italic"] = _toggle(rpr, "w:i")
    props["strike"] = _toggle(rpr, "w:strike") or _toggle(rpr, "w:dstrike")
    fonts = rpr.find(qn("w:rFonts"))
    if fonts is not None:
        name = (attr(fonts, "w:ascii") or "").lower()
        props["code"] = name in _CODE_FONTS
    return props


def _toggle(rpr: ET.Element, name: str) -> bool:
    """Read a Word toggle property, where an absent ``w:val`` means on."""
    element = rpr.find(qn(name))
    if element is None:
        return False
    value = attr(element, "w:val")
    return value not in ("0", "false", "off")


def _image_ref(drawing: ET.Element, pkg: Package) -> Optional[ImageRef]:
    """Find the embedded relationship and alt text inside a drawing."""
    alt = ""
    for doc_pr in drawing.iter(qn("wp:docPr")):
        alt = doc_pr.get("descr") or doc_pr.get("title") or ""
        break

    for blip in drawing.iter(qn("a:blip")):
        rel_id = attr(blip, "r:embed") or attr(blip, "r:link")
        if not rel_id:
            continue
        target = pkg.rel_target(rel_id)
        if not target:
            continue
        return ImageRef(part_name=_part_name(target), alt=alt)
    return None


def _part_name(target: str) -> str:
    """Turn a relationship target into a package part name.

    Targets in ``document.xml.rels`` are relative to ``word/``, so
    ``media/image1.png`` is the part ``word/media/image1.png``.
    """
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join("word", target))
