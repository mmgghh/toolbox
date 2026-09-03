"""Images and Mermaid diagrams: fetching them, sizing them, placing them.

Both are the same problem — turn a reference in the Markdown into bytes, work
out how big those bytes should be on the page, and draw them — and both are
the only parts of the renderer that may touch the network, which is why
``--offline`` is checked here.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

import requests
from fpdf.svg import Percent, SVGObject
from PIL import Image as PILImage

from pytoolbox.core import console, mermaid
from pytoolbox.mdpdf import document, fonts, render, shaping, state

# A standalone Markdown image line: ![alt](src "optional title")
IMG_RE = re.compile(r'^!\[([^\]]*)\]\(\s*(\S+?)(?:\s+["\'][^"\']*["\'])?\s*\)\s*$')


def looks_like_svg(data):
    head = data[:512].lstrip(b'\xef\xbb\xbf').lstrip()
    return head[:5].lower() == b'<?xml' or head[:4].lower() == b'<svg'


def _svg_size(data):
    """Return an SVG's intrinsic (width, height), falling back to a default aspect.

    ``width``/``height`` attributes given as a percentage (e.g. ``width="100%"``)
    are relative to the embedding container, not an absolute size -- the viewBox
    is the only reliable source of aspect ratio in that case.
    """
    svg = SVGObject(data)
    w = h = 0.0
    if svg.viewbox:
        _, _, w, h = svg.viewbox
    if svg.width and not isinstance(svg.width, Percent):
        w = svg.width
    if svg.height and not isinstance(svg.height, Percent):
        h = svg.height
    return (w, h) if w and h else (800.0, 600.0)


def _place_image(pdf, data, alt=""):
    """Embed image bytes, scaled to fit within the page, with an optional caption.

    fpdf2 renders SVGs natively as vector graphics (crisp at any size), but only
    Pillow can report a raster image's pixel size -- so dimension lookup has to
    branch on format, even though the final ``pdf.image()`` call doesn't.
    """
    if looks_like_svg(data):
        px_w, px_h = _svg_size(data)
    else:
        px_w, px_h = PILImage.open(io.BytesIO(data)).size
    max_w, max_h = pdf.epw, pdf.eph - 10
    w_mm, h_mm = max_w, max_w * px_h / px_w
    if h_mm > max_h:
        w_mm, h_mm = max_h * px_w / px_h, max_h
    document.ensure_space(pdf, h_mm + 8)
    x = pdf.l_margin + (max_w - w_mm) / 2
    pdf.image(data, x=x, w=w_mm, h=h_mm)
    pdf.ln(2)
    if alt:
        pdf.set_font(fonts.FONT_FA if shaping.is_rtl(alt) and getattr(pdf, "has_persian", False) else fonts.FONT_SANS, "I", 8)
        pdf.set_text_color(120, 120, 120)
        caption = shaping.shape_rtl(alt) if shaping.is_rtl(alt) and getattr(pdf, "has_persian", False) else alt
        pdf.cell(0, 5, caption, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*document.CLR_BODY)
        pdf.set_font(fonts.FONT_SANS, "", state.BODY_SIZE)
    pdf.ln(3)


def add_image(pdf, src, alt, base_dir):
    try:
        if re.match(r'^https?://', src):
            if state.offline:
                raise RuntimeError("remote images are disabled by --offline")
            resp = requests.get(src, timeout=15)
            resp.raise_for_status()
            data = resp.content
        else:
            path = Path(src)
            if not path.is_absolute():
                path = base_dir / path
            data = path.read_bytes()
        _place_image(pdf, data, alt)
    except Exception as exc:
        console.warn(f"could not load image '{src}': {exc}")
        render.add_paragraph(pdf, f"[image: {alt or src}]")


def render_mermaid(source):
    """Best-effort Mermaid render: local mmdc, then mermaid.ink, then None."""
    return mermaid.render(source, fmt="png", offline=state.offline)


def add_mermaid(pdf, lines):
    source = '\n'.join(lines).strip()
    if not source:
        return
    data = render_mermaid(source)
    if data:
        try:
            _place_image(pdf, data)
            return
        except Exception as exc:
            console.warn(f"could not embed rendered Mermaid diagram: {exc}")
    render.add_code_block(pdf, lines)
