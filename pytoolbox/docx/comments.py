"""``comments.xml``, and the reply threading in ``commentsExtended.xml``.

Threading is stored indirectly and is often missing altogether. Word tags the
last paragraph of each comment with a ``w14:paraId``; the extended part then
says which paraId is a reply to which. Documents saved by older Word versions,
or by other tools, have no extended part at all -- there the comments are flat,
which is a normal outcome rather than a failure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from xml.etree import ElementTree as ET

from pytoolbox.docx.inline import Item, Run, parse_inline
from pytoolbox.docx.package import Package, attr, qn


@dataclass
class Comment:
    """One comment: who wrote it, when, what it says, and what it replies to."""

    id: str
    author: str
    date: str
    initials: str = ""
    paragraphs: list[list[Item]] = field(default_factory=list)
    resolved: bool = False
    parent_id: Optional[str] = None

    def plain_text(self) -> str:
        """The comment's text with all formatting dropped, one line per paragraph."""
        return "\n".join(
            "".join(item.text for item in para if isinstance(item, Run)) for para in self.paragraphs
        )


def load_comments(pkg: Package) -> dict[str, Comment]:
    """Read every comment, keyed by its Word id, with replies linked up."""
    part = pkg.part("word/comments.xml")
    if part is None:
        return {}

    found: dict[str, Comment] = {}
    para_id_owner: dict[str, str] = {}

    for element in part.findall(qn("w:comment")):
        comment_id = attr(element, "w:id")
        if comment_id is None:
            continue
        paragraphs = [parse_inline(p, pkg) for p in element.findall(qn("w:p"))]
        found[comment_id] = Comment(
            id=comment_id,
            author=attr(element, "w:author") or "Unknown",
            date=_date(attr(element, "w:date")),
            initials=attr(element, "w:initials") or "",
            paragraphs=paragraphs,
        )
        last_para_id = _last_para_id(element)
        if last_para_id:
            para_id_owner[last_para_id] = comment_id

    _apply_threading(pkg, found, para_id_owner)
    return found


def _date(raw: Optional[str]) -> str:
    """Trim Word's ISO timestamp to a date, leaving anything unexpected alone."""
    if not raw:
        return ""
    head = raw.split("T", 1)[0]
    parts = head.split("-")
    if len(parts) == 3 and all(part.isdigit() for part in parts):
        return head
    return raw


def _last_para_id(element: ET.Element) -> Optional[str]:
    """The ``w14:paraId`` of a comment's final paragraph, which threading keys on."""
    para_id = None
    for paragraph in element.findall(qn("w:p")):
        para_id = attr(paragraph, "w14:paraId") or para_id
    return para_id


def _apply_threading(
    pkg: Package, found: dict[str, Comment], para_id_owner: dict[str, str]
) -> None:
    """Fill in ``resolved`` and ``parent_id`` from ``commentsExtended.xml``."""
    part = pkg.part("word/commentsExtended.xml")
    if part is None:
        return

    for entry in part.findall(qn("w15:commentEx")):
        para_id = attr(entry, "w15:paraId")
        owner = para_id_owner.get(para_id or "")
        if owner is None:
            continue
        comment = found[owner]
        comment.resolved = attr(entry, "w15:done") in ("1", "true")
        parent_para_id = attr(entry, "w15:paraIdParent")
        if parent_para_id:
            # A parent that names a paraId we never saw is left unthreaded
            # rather than dropped: the comment still has something to say.
            comment.parent_id = para_id_owner.get(parent_para_id)
