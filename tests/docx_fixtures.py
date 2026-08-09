"""Build minimal ``.docx`` files for the pydocx2md tests.

Word documents are zips of XML, so fixtures are generated rather than checked
in: every byte a test depends on stays readable in the diff.

The helpers take raw WordprocessingML fragments. That is deliberate -- these
tests are about reading real OOXML, so hiding the XML behind a friendly builder
would test the builder instead.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Optional

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
WP = 'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"'
A = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
PIC = 'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"'
W15 = 'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"'

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Default Extension="png" ContentType="image/png"/>
</Types>"""

_ROOT_RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Target="word/document.xml"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>
</Relationships>"""


def document(body: str) -> str:
    """Wrap body fragments in a ``word/document.xml`` part."""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f"<w:document {W} {R} {WP} {A} {PIC}><w:body>{body}</w:body></w:document>"
    )


def comments(*entries: str) -> str:
    """Wrap ``<w:comment>`` fragments in a ``word/comments.xml`` part."""
    return f'<?xml version="1.0" encoding="UTF-8"?><w:comments {W} {R}>{"".join(entries)}</w:comments>'


def comment(cid: str, author: str, date: str = "2026-03-14T10:00:00Z", text: str = "", initials: str = "") -> str:
    """One ``<w:comment>`` holding a single paragraph of plain text."""
    return (
        f'<w:comment w:id="{cid}" w:author="{author}" w:initials="{initials}" w:date="{date}">'
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:comment>"
    )


def para(text: str = "", style: Optional[str] = None, runs: Optional[str] = None) -> str:
    """A paragraph, optionally with a style id and hand-written run XML."""
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    body = runs if runs is not None else f"<w:r><w:t>{text}</w:t></w:r>"
    return f"<w:p>{props}{body}</w:p>"


def run(text: str, bold: bool = False, italic: bool = False, strike: bool = False) -> str:
    """A run with optional character formatting."""
    marks = ""
    if bold:
        marks += "<w:b/>"
    if italic:
        marks += "<w:i/>"
    if strike:
        marks += "<w:strike/>"
    props = f"<w:rPr>{marks}</w:rPr>" if marks else ""
    return f'<w:r>{props}<w:t xml:space="preserve">{text}</w:t></w:r>'


def commented(cid: str, inner: str) -> str:
    """Wrap run XML in a comment range plus its reference mark."""
    return (
        f'<w:commentRangeStart w:id="{cid}"/>{inner}<w:commentRangeEnd w:id="{cid}"/>'
        f'<w:r><w:commentReference w:id="{cid}"/></w:r>'
    )


def build_docx(path: Path, body: str, parts: Optional[dict] = None) -> Path:
    """Write a ``.docx`` whose ``word/document.xml`` contains ``body``.

    ``parts`` adds or overrides any other part, e.g.
    ``{"word/comments.xml": comments(comment("1", "Sara", text="hi"))}``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    contents = {
        "[Content_Types].xml": _CONTENT_TYPES,
        "_rels/.rels": _ROOT_RELS,
        "word/document.xml": document(body),
    }
    contents.update(parts or {})
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in contents.items():
            z.writestr(name, data)
    return path
