"""Minimal PDFs built by hand, for exact control over glyph positions.

A PDF-writing library would be a second dependency and would not let a test say
"put this word at exactly x=300", which is what the layout rules have to be
tested against. Everything here uses base-14 fonts and uncompressed content
streams, which pypdf reads directly.
"""

from __future__ import annotations

import io
import zlib
from typing import Optional

#: One drawing instruction on a page. Built by the helpers below.
Item = tuple

LETTER = (612.0, 792.0)

#: Suffix -> base-14 font name. The suffix is what a test passes.
FONTS = {"": "Helvetica", "-Bold": "Helvetica-Bold", "-Oblique": "Helvetica-Oblique"}


def text(x: float, y: float, size: float, body: str, font: str = "") -> Item:
    """Draw ``body`` with its baseline origin at (x, y)."""
    return ("text", x, y, size, body, font)


def image(x: float, y: float, width: float, height: float) -> Item:
    """Place an image with its lower-left corner at (x, y)."""
    return ("image", x, y, width, height, None)


def link(x0: float, y0: float, x1: float, y1: float, uri: str) -> Item:
    """A link annotation over the given rectangle."""
    return ("link", x0, y0, x1, y1, uri)


def rule(x: float, y: float, width: float, height: float) -> Item:
    """Stroke a rectangle, the way a table's cell borders are drawn."""
    return ("rule", x, y, width, height, None)


#: Raw RGB samples for a 4x4 image -- the smallest thing worth extracting.
_PIXELS = zlib.compress(bytes([200, 30, 30] * 16))


class _Builder:
    """Accumulates numbered PDF objects and serialises them."""

    def __init__(self) -> None:
        self.objects: dict[int, bytes] = {}
        self._next = 1

    def reserve(self) -> int:
        number = self._next
        self._next += 1
        return number

    def put(self, number: int, body: bytes) -> None:
        self.objects[number] = body

    def add(self, body: bytes) -> int:
        number = self.reserve()
        self.put(number, body)
        return number

    def serialise(self, root: int) -> bytes:
        out = io.BytesIO()
        out.write(b"%PDF-1.4\n")
        offsets = {}
        for number in sorted(self.objects):
            offsets[number] = out.tell()
            out.write(b"%d 0 obj\n" % number + self.objects[number] + b"\nendobj\n")
        start = out.tell()
        top = max(self.objects) + 1
        out.write(b"xref\n0 %d\n" % top)
        out.write(b"0000000000 65535 f \n")
        for number in range(1, top):
            out.write(b"%010d 00000 n \n" % offsets.get(number, 0))
        out.write(b"trailer\n<< /Size %d /Root %d 0 R >>\n" % (top, root))
        out.write(b"startxref\n%d\n%%%%EOF\n" % start)
        return out.getvalue()


def build_pdf(
    pages: list[list[Item]],
    size: tuple[float, float] = LETTER,
    outline: Optional[list[tuple[str, int, int]]] = None,
) -> bytes:
    """Build a PDF.

    ``outline`` items are ``(title, level, page_index)`` with a 1-based level;
    entries must be given in document order, each nested under the closest
    preceding entry one level shallower.
    """
    width, height = size
    builder = _Builder()

    catalog = builder.reserve()
    pages_node = builder.reserve()
    font_numbers = {suffix: builder.reserve() for suffix in FONTS}
    for suffix, number in font_numbers.items():
        builder.put(
            number,
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{FONTS[suffix]} >>".encode(),
        )

    page_numbers = []
    for items in pages:
        page_numbers.append(_page(builder, items, width, height, font_numbers))

    kids = " ".join(f"{number} 0 R" for number in page_numbers)
    builder.put(
        pages_node,
        f"<< /Type /Pages /Count {len(pages)} /Kids [{kids}] >>".encode(),
    )

    outline_ref = ""
    if outline:
        root = _outline(builder, outline, page_numbers)
        outline_ref = f" /Outlines {root} 0 R"
    builder.put(
        catalog,
        f"<< /Type /Catalog /Pages {pages_node} 0 R{outline_ref} >>".encode(),
    )
    return builder.serialise(catalog)


def _page(
    builder: _Builder,
    items: list[Item],
    width: float,
    height: float,
    font_numbers: dict[str, int],
) -> int:
    page_number = builder.reserve()
    parts: list[str] = []
    xobjects: list[tuple[str, int]] = []
    annotations: list[int] = []

    for item in items:
        kind = item[0]
        if kind == "text":
            _, x, y, size, body, suffix = item
            escaped = body.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
            name = f"F{list(FONTS).index(suffix)}"
            parts.append(f"BT /{name} {size} Tf 1 0 0 1 {x} {y} Tm ({escaped}) Tj ET")
        elif kind == "image":
            _, x, y, item_width, item_height, _ = item
            name = f"I{len(xobjects)}"
            number = builder.add(
                b"<< /Type /XObject /Subtype /Image /Width 4 /Height 4 "
                b"/ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode "
                b"/Length %d >>\nstream\n" % len(_PIXELS) + _PIXELS + b"\nendstream"
            )
            xobjects.append((name, number))
            parts.append(f"q {item_width} 0 0 {item_height} {x} {y} cm /{name} Do Q")
        elif kind == "rule":
            _, x, y, item_width, item_height, _ = item
            parts.append(f"{x} {y} {item_width} {item_height} re S")
        elif kind == "link":
            _, x0, y0, x1, y1, uri = item
            annotations.append(
                builder.add(
                    (
                        f"<< /Type /Annot /Subtype /Link /Rect [{x0} {y0} {x1} {y1}] "
                        f"/Border [0 0 0] /A << /S /URI /URI ({uri}) >> >>"
                    ).encode()
                )
            )

    stream = "\n".join(parts).encode("latin-1")
    contents = builder.add(
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    )

    fonts = " ".join(f"/F{index} {font_numbers[suffix]} 0 R" for index, suffix in enumerate(FONTS))
    images = " ".join(f"/{name} {number} 0 R" for name, number in xobjects)
    annots = " ".join(f"{number} 0 R" for number in annotations)
    builder.put(
        page_number,
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /Font << {fonts} >> /XObject << {images} >> >> "
            f"/Annots [{annots}] /Contents {contents} 0 R >>"
        ).encode(),
    )
    return page_number


def _outline(
    builder: _Builder, entries: list[tuple[str, int, int]], page_numbers: list[int]
) -> int:
    """Build the outline tree, returning the root object number."""
    root = builder.reserve()
    numbers = [builder.reserve() for _ in entries]

    # Each entry's parent is the closest preceding entry one level shallower.
    parents: list[int] = []
    open_at: dict[int, int] = {}
    for number, (_, level, _) in zip(numbers, entries):
        parents.append(open_at.get(level - 1, root))
        open_at[level] = number
        for deeper in [key for key in open_at if key > level]:
            del open_at[deeper]

    children: dict[int, list[int]] = {}
    for number, parent in zip(numbers, parents):
        children.setdefault(parent, []).append(number)

    for index, (number, (title, _, page_index)) in enumerate(zip(numbers, entries)):
        siblings = children[parents[index]]
        position = siblings.index(number)
        fields = [
            f"/Title ({title})",
            f"/Parent {parents[index]} 0 R",
            f"/Dest [{page_numbers[page_index]} 0 R /XYZ 0 700 0]",
        ]
        if position:
            fields.append(f"/Prev {siblings[position - 1]} 0 R")
        if position + 1 < len(siblings):
            fields.append(f"/Next {siblings[position + 1]} 0 R")
        own = children.get(number)
        if own:
            fields.append(f"/First {own[0]} 0 R")
            fields.append(f"/Last {own[-1]} 0 R")
            fields.append(f"/Count {len(own)}")
        builder.put(number, ("<< " + " ".join(fields) + " >>").encode())

    top = children.get(root, [])
    builder.put(
        root,
        (
            f"<< /Type /Outlines /Count {len(entries)} "
            f"/First {top[0]} 0 R /Last {top[-1]} 0 R >>"
        ).encode(),
    )
    return root


def write_pdf(path, pages, **kwargs):
    """Build a PDF and write it to ``path``, returning the path."""
    path.write_bytes(build_pdf(pages, **kwargs))
    return path
