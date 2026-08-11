"""A PDF as positioned text runs, images, links and an outline.

This is the only module that imports :mod:`pypdf`. Everything downstream works
on the dataclasses below, which is what lets the layout and structure rules be
tested without building a PDF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import click

#: Below this many characters, a page carrying a big image is a scan.
SCANNED_MAX_CHARS = 20

#: A scan's image covers most of the page.
SCANNED_MIN_COVERAGE = 0.5

#: Proportion of scanned pages at which the whole document counts as scanned.
SCANNED_DOCUMENT_SHARE = 0.8


@dataclass
class TextRun:
    """A stretch of text drawn with one font at one place on the page."""

    text: str
    x: float
    y: float
    size: float
    font: str = ""
    bold: bool = False
    italic: bool = False


@dataclass
class ImageBox:
    """An image and where it sits on the page."""

    name: str
    data: bytes
    x: float
    y: float
    width: float
    height: float


@dataclass
class LinkBox:
    """A link annotation's target and the rectangle it covers."""

    uri: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class OutlineEntry:
    """One bookmark. ``level`` is 1-based."""

    title: str
    level: int
    page: int


@dataclass
class Page:
    number: int
    width: float
    height: float
    runs: list[TextRun] = field(default_factory=list)
    images: list[ImageBox] = field(default_factory=list)
    links: list[LinkBox] = field(default_factory=list)

    @property
    def scanned(self) -> bool:
        """No usable text, but a picture covering most of the page."""
        if sum(len(run.text.strip()) for run in self.runs) >= SCANNED_MAX_CHARS:
            return False
        area = self.width * self.height
        if not area:
            return False
        return any(
            (image.width * image.height) / area >= SCANNED_MIN_COVERAGE for image in self.images
        )


@dataclass
class Document:
    pages: list[Page] = field(default_factory=list)
    outline: list[OutlineEntry] = field(default_factory=list)

    @property
    def scanned(self) -> bool:
        """True when the file as a whole has no text worth converting."""
        if not self.pages:
            return False
        share = sum(1 for page in self.pages if page.scanned) / len(self.pages)
        return share >= SCANNED_DOCUMENT_SHARE

    @property
    def scanned_pages(self) -> list[int]:
        return [page.number for page in self.pages if page.scanned]


def read(
    source: Path,
    *,
    password: Optional[str] = None,
    pages: Optional[list[int]] = None,
    include_images: bool = True,
) -> Document:
    """Read ``source`` into positioned runs.

    ``pages`` selects zero-based page indexes; page numbers stay absolute, so a
    later message about page 3 means page 3 of the file, not of the selection.
    """
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover - the CLI reports this
        raise click.ClickException(
            f"pypdf2md needs the pdf2md extra: pip install 'pytoolbox[pdf2md]' ({exc})"
        ) from exc

    try:
        pdf = pypdf.PdfReader(str(source))
        page_count = len(pdf.pages)
    except Exception as exc:
        raise click.ClickException(f"{source} is not a PDF file ({exc}).") from exc

    if pdf.is_encrypted:
        try:
            opened = pdf.decrypt(password or "")
        except Exception:
            opened = 0
        if not opened:
            hint = "wrong password" if password else "try --password"
            raise click.ClickException(f"{source} is password-protected ({hint}).")

    wanted = range(page_count) if pages is None else pages
    document = Document(outline=_outline(pdf))
    for index in wanted:
        if 0 <= index < page_count:
            document.pages.append(_page(pdf.pages[index], index, include_images))
    return document


def _page(source: Any, number: int, include_images: bool) -> Page:
    box = source.mediabox
    page = Page(
        number=number,
        width=float(box.width),
        height=float(box.height),
        links=_links(source),
    )

    def visit_text(text: str, cm: Any, tm: Any, font_dict: Any, font_size: Any) -> None:
        if not text or not text.strip():
            return
        font = str(font_dict.get("/BaseFont", "") or "") if font_dict else ""
        # tm holds the text's own position; cm the enclosing transformation.
        x_scale = float(cm[0]) or 1.0
        y_scale = float(cm[3]) or 1.0
        page.runs.append(
            TextRun(
                text=text,
                x=float(tm[4]) * x_scale + float(cm[4]),
                y=float(tm[5]) * y_scale + float(cm[5]),
                size=float(font_size or 0) * abs(float(tm[3]) or 1.0) * abs(y_scale),
                font=font,
                bold=_is_bold(font),
                italic=_is_italic(font),
            )
        )

    placements: list[tuple[str, list[float]]] = []

    def visit_operand(operator: Any, operands: Any, cm: Any, tm: Any) -> None:
        if operator == b"Do" and operands:
            placements.append((str(operands[0]), [float(value) for value in cm]))

    try:
        source.extract_text(visitor_text=visit_text, visitor_operand_before=visit_operand)
    except Exception as exc:  # pragma: no cover - a damaged page stops that page only
        raise click.ClickException(f"page {number + 1} could not be read ({exc}).") from exc

    page.images = _images(source, placements, include_images)
    return page


def _images(
    source: Any, placements: list[tuple[str, list[float]]], include_data: bool
) -> list[ImageBox]:
    """Every placed image, with its bytes unless they were not asked for.

    Placement is kept even when the bytes are not, because an image covering
    the page is how a scan is recognised.
    """
    embedded: dict[str, Any] = {}
    if include_data:
        try:
            embedded = {_stem(item.name): item for item in source.images}
        except Exception:  # pragma: no cover - a damaged image must not stop the text
            embedded = {}

    found: list[ImageBox] = []
    for name, cm in placements:
        item = embedded.get(_stem(name))
        found.append(
            ImageBox(
                name=item.name if item is not None else f"{_stem(name)}.png",
                data=item.data if item is not None else b"",
                x=cm[4],
                y=cm[5],
                width=abs(cm[0]),
                height=abs(cm[3]),
            )
        )
    return found


def _stem(name: str) -> str:
    """``/I1`` and ``I1.png`` both name the same XObject."""
    return name.lstrip("/").rsplit(".", 1)[0]


def _links(source: Any) -> list[LinkBox]:
    found: list[LinkBox] = []
    try:
        annotations = source.get("/Annots") or []
    except Exception:  # pragma: no cover
        return found
    for annotation in annotations:
        try:
            obj = annotation.get_object()
            if obj.get("/Subtype") != "/Link":
                continue
            action = obj.get("/A") or {}
            uri = action.get("/URI")
            rect = obj.get("/Rect")
            if not uri or not rect:
                continue
            x0, y0, x1, y1 = (float(value) for value in rect)
            found.append(LinkBox(str(uri), min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        except Exception:  # pragma: no cover - one bad annotation is not fatal
            continue
    return found


def _outline(pdf: Any) -> list[OutlineEntry]:
    """Bookmarks flattened to (title, level, page), in document order."""
    entries: list[OutlineEntry] = []

    def walk(items: Any, level: int) -> None:
        for item in items:
            if isinstance(item, list):
                # pypdf nests a child list directly after its parent entry.
                walk(item, level + 1)
                continue
            try:
                title = str(item.title or "").strip()
                page = pdf.get_destination_page_number(item)
            except Exception:  # pragma: no cover - a broken bookmark is skipped
                continue
            if title:
                entries.append(OutlineEntry(title=title, level=level, page=page))

    try:
        walk(pdf.outline, 1)
    except Exception:  # pragma: no cover
        return []
    return entries


def _is_bold(font: str) -> bool:
    lowered = font.lower()
    return "bold" in lowered or "black" in lowered or "heavy" in lowered


def _is_italic(font: str) -> bool:
    lowered = font.lower()
    return "italic" in lowered or "oblique" in lowered
