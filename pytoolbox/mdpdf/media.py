"""Images and Mermaid diagrams: fetching them, sizing them, placing them.

Both are the same problem — turn a reference in the Markdown into bytes, work
out how big those bytes should be on the page, and draw them — and both are
the only parts of the renderer that may touch the network, which is why
``--offline`` is checked here.
"""

from __future__ import annotations

import base64
import io
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import requests
from fpdf.svg import Percent, SVGObject
from PIL import Image as PILImage

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
        print(f"WARN: could not load image '{src}': {exc}", file=sys.stderr)
        render.add_paragraph(pdf, f"[image: {alt or src}]")


def _render_mermaid_mmdc(source):
    """Render via a local mermaid-cli install. Returns PNG bytes or None."""
    with tempfile.TemporaryDirectory() as tmp:
        in_path = Path(tmp) / "diagram.mmd"
        out_path = Path(tmp) / "diagram.png"
        in_path.write_text(source, encoding="utf-8")
        result = subprocess.run(
            ["mmdc", "-i", str(in_path), "-o", str(out_path), "-b", "white", "-s", "2"],
            capture_output=True, timeout=30, check=False,
        )
        if result.returncode == 0 and out_path.is_file():
            return out_path.read_bytes()
    return None


def render_mermaid_ink(source):
    """Render via the mermaid.ink web API. Returns PNG bytes; raises on failure."""
    b64 = base64.urlsafe_b64encode(source.encode("utf-8")).decode("ascii")
    resp = requests.get(f"https://mermaid.ink/img/{b64}?bgColor=white", timeout=15)
    resp.raise_for_status()
    return resp.content


def render_mermaid(source):
    """Best-effort Mermaid render: local mmdc, then mermaid.ink, then None."""
    if state.HAS_MMDC:
        try:
            data = _render_mermaid_mmdc(source)
            if data:
                return data
        except Exception:
            pass
    if state.offline:
        if not state.mermaid_net_warned:
            print(
                "WARN: --offline is set and mermaid-cli is unavailable; showing raw "
                "diagram source. Install it with `npm install -g @mermaid-js/mermaid-cli`.",
                file=sys.stderr,
            )
            state.mermaid_net_warned = True
        return None
    try:
        try:
            return render_mermaid_ink(source)
        except requests.exceptions.RequestException:
            # Transient failures (dropped connections, timeouts) are common
            # enough on this public endpoint to warrant one retry before
            # falling back to showing the raw source.
            return render_mermaid_ink(source)
    except Exception as exc:
        if not state.mermaid_net_warned:
            print(
                "WARN: could not render Mermaid diagram "
                f"({'mmdc failed and ' if state.HAS_MMDC else ''}mermaid.ink request "
                f"failed: {exc}); showing raw source instead. Install mermaid-cli "
                "(`npm install -g @mermaid-js/mermaid-cli`) for offline rendering.",
                file=sys.stderr,
            )
            state.mermaid_net_warned = True
        return None


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
            print(f"WARN: could not embed rendered Mermaid diagram: {exc}", file=sys.stderr)
    render.add_code_block(pdf, lines)
