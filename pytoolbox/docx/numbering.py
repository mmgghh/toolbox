"""``numbering.xml``: whether a given list level is a bullet or a number.

Word stores list formatting indirectly. A paragraph names a ``numId``; that
points at an ``abstractNum``; that holds one ``lvl`` per nesting depth, and the
``numFmt`` inside decides bullet versus number. Markdown needs only that last
bit, so everything else is collapsed away here.
"""

from __future__ import annotations

from typing import Optional
from xml.etree import ElementTree as ET

from pytoolbox.docx.package import Package, attr, qn

#: Every numFmt that is not one of these renders as a numbered item.
_UNORDERED = {"bullet", "none"}


class Numbering:
    """Answers one question: is this list level ordered?"""

    def __init__(self, levels: dict[tuple[str, int], str]) -> None:
        self._levels = levels

    def is_ordered(self, num_id: Optional[str], ilvl: int) -> bool:
        """True when the level renders as ``1.``, false when it renders as ``-``.

        Unknown ids and levels fall back to a bullet: a wrong marker is a small
        cosmetic loss, while raising here would fail the whole conversion over
        a list style Word never fully defined.
        """
        if num_id is None:
            return False
        fmt = self._levels.get((num_id, ilvl))
        if fmt is None:
            return False
        return fmt not in _UNORDERED


def load_numbering(pkg: Package) -> Numbering:
    """Build the lookup from ``word/numbering.xml``, which is often absent."""
    part = pkg.part("word/numbering.xml")
    if part is None:
        return Numbering({})

    formats: dict[str, dict[int, str]] = {}
    for abstract in part.findall(qn("w:abstractNum")):
        abstract_id = attr(abstract, "w:abstractNumId")
        if abstract_id is None:
            continue
        formats[abstract_id] = _levels_of(abstract)

    levels: dict[tuple[str, int], str] = {}
    for num in part.findall(qn("w:num")):
        num_id = attr(num, "w:numId")
        ref = num.find(qn("w:abstractNumId"))
        if num_id is None or ref is None:
            continue
        for ilvl, fmt in formats.get(attr(ref, "w:val") or "", {}).items():
            levels[(num_id, ilvl)] = fmt

    return Numbering(levels)


def _levels_of(abstract: ET.Element) -> dict[int, str]:
    """Map nesting depth to numFmt for one abstract list definition."""
    result: dict[int, str] = {}
    for lvl in abstract.findall(qn("w:lvl")):
        ilvl = attr(lvl, "w:ilvl")
        fmt_el = lvl.find(qn("w:numFmt"))
        if ilvl is None or fmt_el is None:
            continue
        try:
            result[int(ilvl)] = attr(fmt_el, "w:val") or "bullet"
        except ValueError:
            continue
    return result
