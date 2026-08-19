"""``styles.xml``: the paragraph properties a paragraph does not spell out.

A Word paragraph rarely carries its own formatting. It names a style, and the
style -- possibly through a chain of ``basedOn`` parents -- supplies the rest.
List membership is the case that matters here: paragraphs written with the
built-in *List Bullet* style hold no ``numPr`` at all, so a reader that looks
only at the paragraph sees body text where the author saw bullets. Outline
level is the same story for headings: a document written with its own chapter
styles, rather than with *Heading 1*, records the depth once in the style.
"""

from __future__ import annotations

from typing import Optional
from xml.etree import ElementTree as ET

from pytoolbox.docx.package import Package, attr, qn

#: A style chain deeper than this is a loop Word itself would not survive.
_MAX_DEPTH = 32


class Styles:
    """Resolves style ids to the paragraph properties they imply."""

    def __init__(
        self,
        parents: dict[str, str],
        lists: dict[str, tuple[Optional[str], Optional[int]]],
        outlines: Optional[dict[str, int]] = None,
    ) -> None:
        self._parents = parents
        self._lists = lists
        self._outlines = outlines or {}

    def outline_level(self, style_id: Optional[str]) -> Optional[int]:
        """The ``w:outlineLvl`` a style contributes, inherited like the rest.

        The value is returned as written, including Word's 9 for "body text";
        deciding what counts as a heading is the caller's business.
        """
        for current in self.chain(style_id):
            if current in self._outlines:
                return self._outlines[current]
        return None

    def list_properties(self, style_id: Optional[str]) -> tuple[Optional[str], Optional[int]]:
        """The ``numId`` and nesting depth a style contributes, if any.

        Values are inherited: a style that sets neither takes both from the
        style it is ``basedOn``, and so on up the chain. The nearest definition
        wins, which is how Word merges the two.
        """
        num_id: Optional[str] = None
        ilvl: Optional[int] = None
        for current in self.chain(style_id):
            found_id, found_level = self._lists.get(current, (None, None))
            if num_id is None:
                num_id = found_id
            if ilvl is None:
                ilvl = found_level
            if num_id is not None and ilvl is not None:
                break
        return num_id, ilvl

    def chain(self, style_id: Optional[str]) -> list[str]:
        """A style and its ancestors, nearest first, stopping at any cycle."""
        chain: list[str] = []
        current = style_id
        while current is not None and current not in chain and len(chain) < _MAX_DEPTH:
            chain.append(current)
            current = self._parents.get(current)
        return chain


def load_styles(pkg: Package) -> Styles:
    """Build the lookup from ``word/styles.xml``, which is often absent."""
    part = pkg.part("word/styles.xml")
    if part is None:
        return Styles({}, {})

    parents: dict[str, str] = {}
    lists: dict[str, tuple[Optional[str], Optional[int]]] = {}
    outlines: dict[str, int] = {}

    for style in part.findall(qn("w:style")):
        style_id = attr(style, "w:styleId")
        # Character and table styles cannot make a paragraph a list item.
        if style_id is None or attr(style, "w:type", "paragraph") != "paragraph":
            continue

        based_on = style.find(qn("w:basedOn"))
        parent = attr(based_on, "w:val") if based_on is not None else None
        if parent:
            parents[style_id] = parent

        props = style.find(qn("w:pPr"))
        num_pr = props.find(qn("w:numPr")) if props is not None else None
        if num_pr is not None:
            lists[style_id] = (_value(num_pr, "w:numId"), _level(num_pr))

        outline = props.find(qn("w:outlineLvl")) if props is not None else None
        raw = attr(outline, "w:val") if outline is not None else None
        if raw is not None and raw.isdigit():
            outlines[style_id] = int(raw)

    return Styles(parents, lists, outlines)


def _value(num_pr: ET.Element, tag: str) -> Optional[str]:
    element = num_pr.find(qn(tag))
    return attr(element, "w:val") if element is not None else None


def _level(num_pr: ET.Element) -> Optional[int]:
    raw = _value(num_pr, "w:ilvl")
    return int(raw) if raw is not None and raw.isdigit() else None
