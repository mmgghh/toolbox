"""``footnotes.xml`` and ``endnotes.xml``.

Both parts open with two pseudo-notes holding the separator rules Word draws
above real footnotes. They carry no content worth keeping, so they are dropped
here rather than leaking a stray ``[^0]`` into the output.
"""

from __future__ import annotations

from pytoolbox.docx.inline import Item, parse_inline
from pytoolbox.docx.package import Package, attr, qn

#: Note types that exist only to describe the separator rule.
_PSEUDO = {"separator", "continuationSeparator", "continuationNotice"}


def load_notes(pkg: Package) -> dict[str, list[list[Item]]]:
    """Return every real footnote and endnote, keyed by id."""
    notes: dict[str, list[list[Item]]] = {}
    for part_name, tag in (
        ("word/footnotes.xml", "w:footnote"),
        ("word/endnotes.xml", "w:endnote"),
    ):
        part = pkg.part(part_name)
        if part is None:
            continue
        for element in part.findall(qn(tag)):
            note_id = attr(element, "w:id")
            if note_id is None or attr(element, "w:type") in _PSEUDO:
                continue
            notes[note_id] = [parse_inline(p, pkg) for p in element.findall(qn("w:p"))]
    return notes
