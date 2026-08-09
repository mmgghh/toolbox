#!/usr/bin/env python3
"""Convert Markdown files to PDF using fpdf2 and DejaVu/Vazir fonts.

Exposes the ``pymd2pdf`` console script (see ``pymd2pdf --help``).

Supports: headings, bold, inline code, code blocks, tables, bullets,
numbered lists, horizontal rules, nested lists, images, and Mermaid
diagrams. Persian/Arabic text is shaped and rendered right-to-left when
Vazir and the RTL extras are present.
"""

import base64
import io
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import click
import requests
from fpdf import FPDF
from fpdf.svg import Percent, SVGObject
from PIL import Image as PILImage

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _HAS_SHAPER = True
except ImportError:
    _HAS_SHAPER = False

# mermaid-cli (`mmdc`), if installed, renders Mermaid diagrams locally and
# offline. Otherwise diagrams fall back to the mermaid.ink web API, and
# finally to showing the raw source as a code block.
_HAS_MMDC = shutil.which("mmdc") is not None
_mermaid_net_warned = False

# ── Font paths (DejaVu ships with most Linux distros) ───────────────
FONT_DIRS = [
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts/TTF"),                 # Arch
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
]

# ── Persian/Arabic font paths (Vazir) ───────────────────────────────
FONT_PERSIAN_DIRS = [
    Path.home() / ".local/share/fonts",
    Path.home() / ".config/Typora/themes/middle-east",
    Path("/usr/share/fonts/truetype/vazir"),
    Path("/usr/share/fonts/TTF"),
    Path("/usr/local/share/fonts"),
]

FONT_SANS = "DejaVu"
FONT_MONO = "DejaVuMono"
FONT_FA   = "Vazir"

# Characters in the Arabic/Persian Unicode blocks (including presentation forms).
_RTL_RE = re.compile(r'[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]')

# A standalone Markdown image line: ![alt](src "optional title")
_IMG_RE = re.compile(r'^!\[([^\]]*)\]\(\s*(\S+?)(?:\s+["\'][^"\']*["\'])?\s*\)\s*$')


def _is_rtl(text):
    return bool(text) and bool(_RTL_RE.search(text))


# Glyphs missing from the base Vazir font. Replaced before shaping when only
# base Vazir is available — Vazirmatn (its successor) covers them natively.
_VAZIR_GLYPH_FALLBACK = {
    '→': '->',   # →
    '←': '<-',   # ←
    '×': 'x',    # ×
    '÷': '/',    # ÷
    '☐': '[ ]',  # ☐ BALLOT BOX
    '☑': '[x]',  # ☑ BALLOT BOX WITH CHECK
    'ˏ': '/',  # ˏ MODIFIER LETTER LOW ACUTE ACCENT, seen as a numeral separator
    # Substitutes for the nested-bullet markers below must stay in the Unicode
    # "neutral" bidi classes (punctuation/symbols), not letters: a marker is
    # folded into RTL text as its first logical word, and unlike a neutral
    # character, a strong-direction letter (e.g. 'o') doesn't take on the
    # surrounding RTL run's position -- it renders on the wrong (left) side.
    '◦': '·',    # ◦ WHITE BULLET (nested list marker)
    '▪': '*',    # ▪ BLACK SMALL SQUARE (nested list marker)
}

# Populated by _find_persian_font when the chosen Persian face needs glyph
# substitution. Empty when the face has full coverage (e.g. Vazirmatn).
_persian_glyph_fallback: dict = {}


def _bidi_display(s):
    """get_display, but only when ``s`` actually has RTL characters.

    Forcing ``base_dir='R'`` (see ``_shape_rtl``'s docstring) is necessary
    for correct ordering whenever RTL text is present, but doing it to a
    string with *no* RTL characters at all backfires: with nothing to anchor
    the forced RTL paragraph level, python-bidi's mirroring pass swaps
    parentheses it shouldn't (`"(SRS)"` -> `"(SRS ("`). Such strings need no
    reordering anyway -- right-alignment at the page-layout level already
    positions them correctly.
    """
    return str(get_display(s, base_dir='R')) if _HAS_SHAPER and _is_rtl(s) else s


def _shape_rtl(text):
    """Reshape Arabic/Persian letters and apply the bidi algorithm.

    ``base_dir='R'`` is required: without it, ``get_display`` auto-detects
    paragraph direction from the first strong-direction character it finds
    (Unicode's P2/P3 rules), so a string that happens to *start* with a run
    of Latin text (e.g. a heading or bold term before any Persian) would get
    treated as an LTR paragraph and come out reordered backwards, even
    though we already know -- the caller checked -- that this text belongs
    in an RTL context. See ``_bidi_display`` for why that's still gated on
    the text actually containing RTL characters.
    """
    if not _HAS_SHAPER or not text:
        return text
    if _persian_glyph_fallback:
        for src, dst in _persian_glyph_fallback.items():
            text = text.replace(src, dst)
    return _bidi_display(arabic_reshaper.reshape(text))


def _shape_rtl_lines(pdf, text, max_width, marker=""):
    """Reshape RTL text and wrap it to max_width, one bidi-reordered line each.

    Reshaping needs the full logical string so Arabic-script letters join
    correctly, but bidi reordering (``get_display``) must happen per rendered
    line: fpdf always draws left-to-right, and ``get_display`` reverses a
    right-to-left string into visual order, so its first characters are
    actually the *end* of the sentence. Reordering the whole paragraph before
    handing it to a greedy left-to-right wrapper (fpdf's multi_cell) puts the
    tail of the paragraph on the first physical line instead of the start.
    Wrapping first (in logical order) and reordering each resulting line
    keeps lines in the right order and each line's glyphs in the right
    direction.

    ``marker``, if given (e.g. a list-item's "1." or "-"), is treated as the
    first logical word rather than appended to the output afterward: bidi
    reordering resolves neutral characters (like a marker's period) based on
    the strong-direction text around them, so splicing a pre-built marker
    string onto already-reordered text puts punctuation on the wrong side.
    Running marker and body through reshape/reorder together as one unit
    gets that resolution right, and naturally budgets the marker's width
    against the first line during wrapping.
    """
    full_text = f"{marker} {text}" if marker else text
    if not _HAS_SHAPER or not text:
        return [full_text]
    if _persian_glyph_fallback:
        for src, dst in _persian_glyph_fallback.items():
            full_text = full_text.replace(src, dst)
    words = arabic_reshaper.reshape(full_text).split(' ')
    lines, current = [], []
    for word in words:
        candidate = ' '.join(current + [word])
        if current and pdf.get_string_width(candidate) > max_width:
            lines.append(_bidi_display(' '.join(current)))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(_bidi_display(' '.join(current)))
    return lines


# ── Colour palette ──────────────────────────────────────────────────
CLR_HEADING       = (20, 60, 120)
CLR_BODY          = (30, 30, 30)
CLR_CODE_BG       = (245, 245, 245)
CLR_CODE_FG       = (40, 40, 40)
CLR_TABLE_HDR_BG  = (30, 70, 130)
CLR_TABLE_HDR_FG  = (255, 255, 255)
CLR_TABLE_ALT     = (235, 240, 250)
CLR_TABLE_BORDER  = (180, 180, 180)
CLR_INLINE_CODE   = (230, 230, 230)
CLR_HR            = (180, 180, 180)
CLR_BOLD          = (0, 0, 0)

# ── Layout constants ────────────────────────────────────────────────
BODY_SIZE   = 10
CODE_SIZE   = 5.5
CODE_LH     = 3.2
TABLE_SIZE  = 7
TABLE_ROW_H = 6
LINE_H_MULT = 1.8     # line-height multiplier for body text
MAX_CODE_COLS = 220    # truncate code lines beyond this


# ═══════════════════════════════════════════════════════════════════
# Font resolution
# ═══════════════════════════════════════════════════════════════════

def _find_font_dir():
    for d in FONT_DIRS:
        if (d / "DejaVuSans.ttf").is_file():
            return d
    print(
        "ERROR: DejaVu fonts not found. Install them:\n"
        "  Debian/Ubuntu : sudo apt-get install fonts-dejavu-core\n"
        "  Fedora/RHEL   : sudo dnf install dejavu-sans-fonts dejavu-sans-mono-fonts\n"
        "  Arch          : sudo pacman -S ttf-dejavu\n"
        "  macOS (brew)  : brew install font-dejavu",
        file=sys.stderr,
    )
    sys.exit(1)


def _find_persian_font():
    """Return (regular_path, bold_path) for the best available Persian face.

    Prefers Vazirmatn (Vazir's successor, broader Unicode coverage) over
    base Vazir. When falling back to base Vazir, populates the
    ``_persian_glyph_fallback`` map so missing glyphs get substituted at
    shape time. Returns (None, None) if neither family is installed.
    """
    global _persian_glyph_fallback
    # (regular, bold, needs_glyph_fallback)
    candidates = (
        ("Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf", False),
        ("Vazirmatn.ttf",         "Vazirmatn-Bold.ttf", False),
        ("Vazir.ttf",              "Vazir-Bold.ttf",     True),
    )
    for d in FONT_PERSIAN_DIRS:
        for reg_name, bold_name, needs_fallback in candidates:
            reg = d / reg_name
            if reg.is_file():
                bold = d / bold_name
                _persian_glyph_fallback = (
                    dict(_VAZIR_GLYPH_FALLBACK) if needs_fallback else {}
                )
                return reg, (bold if bold.is_file() else reg)
    return None, None


# ═══════════════════════════════════════════════════════════════════
# PDF subclass
# ═══════════════════════════════════════════════════════════════════

class PDF(FPDF):
    def __init__(self, title="", **kw):
        super().__init__(**kw)
        self._doc_title = title
        fdir = _find_font_dir()
        self.add_font(FONT_SANS, "",  str(fdir / "DejaVuSans.ttf"))
        self.add_font(FONT_SANS, "B", str(fdir / "DejaVuSans-Bold.ttf"))
        self.add_font(FONT_SANS, "I", str(fdir / "DejaVuSerif.ttf"))
        self.add_font(FONT_MONO, "",  str(fdir / "DejaVuSansMono.ttf"))
        self.add_font(FONT_MONO, "B", str(fdir / "DejaVuSansMono-Bold.ttf"))

        # Set by convert() once the document's text is known; see _use_rtl_layout.
        self.doc_is_rtl = False

        fa_reg, fa_bold = _find_persian_font()
        self.has_persian = fa_reg is not None
        if self.has_persian:
            self.add_font(FONT_FA, "",  str(fa_reg))
            self.add_font(FONT_FA, "B", str(fa_bold))
        if not _HAS_SHAPER:
            print(
                "WARN: arabic-reshaper / python-bidi not installed; Persian text "
                "will not be shaped correctly. Install with:\n"
                "  pip install arabic-reshaper python-bidi",
                file=sys.stderr,
            )
        if not self.has_persian:
            print(
                "WARN: Vazir font not found; Persian text will fall back to DejaVu "
                "(limited Arabic-script coverage). Place Vazir.ttf / Vazir-Bold.ttf "
                "in ~/.local/share/fonts or /usr/share/fonts/truetype/vazir.",
                file=sys.stderr,
            )

    def header(self):
        if self.page_no() > 1 and self._doc_title:
            title = self._doc_title
            self.set_text_color(140, 140, 140)
            if _is_rtl(title) and self.has_persian:
                self.set_font(FONT_FA, "", 8)
                self.cell(0, 6, _shape_rtl(title), align="R")
            else:
                self.set_font(FONT_SANS, "I", 8)
                self.cell(0, 6, title, align="R")
            self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font(FONT_SANS, "I", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")


# ═══════════════════════════════════════════════════════════════════
# Text helpers
# ═══════════════════════════════════════════════════════════════════

def _strip_md(text):
    """Remove markdown bold/italic markers for width calculations."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    return text


def _body_lh(pdf):
    return pdf.font_size * LINE_H_MULT


def _ensure_space(pdf, needed_mm):
    if pdf.get_y() + needed_mm > pdf.h - pdf.b_margin - 5:
        pdf.add_page()


def _render_rich(pdf, text, base_size=BODY_SIZE, base_style=""):
    """Write a line honouring inline `code` and **bold**.

    Uses pdf.write() throughout so segments wrap at the right margin instead of
    overflowing. Inline code is distinguished by the mono font.
    """
    parts = re.split(r'(`[^`]+`|\*\*[^*]+\*\*)', text)
    lh = _body_lh(pdf)
    for part in parts:
        if part.startswith('`') and part.endswith('`'):
            pdf.set_font(FONT_MONO, "", base_size - 1)
            pdf.set_text_color(*CLR_CODE_FG)
            pdf.write(lh, part[1:-1])
            pdf.set_font(FONT_SANS, base_style, base_size)
            pdf.set_text_color(*CLR_BODY)
        elif part.startswith('**') and part.endswith('**'):
            pdf.set_font(FONT_SANS, "B", base_size)
            pdf.set_text_color(*CLR_BOLD)
            pdf.write(lh, part[2:-2])
            pdf.set_font(FONT_SANS, base_style, base_size)
            pdf.set_text_color(*CLR_BODY)
        elif part:
            pdf.write(lh, part)


# ═══════════════════════════════════════════════════════════════════
# Block renderers
# ═══════════════════════════════════════════════════════════════════

def _add_heading(pdf, level, text):
    sizes = {1: 18, 2: 14, 3: 12, 4: 11, 5: 10, 6: 10}
    sz = sizes.get(level, 10)
    pdf.ln(4 if level > 1 else 6)
    pdf.set_text_color(*CLR_HEADING)
    stripped = _strip_md(text)
    if _use_rtl_layout(pdf, stripped):
        pdf.set_font(FONT_FA, "B", sz)
        pdf.multi_cell(
            0, sz * 0.6, _shape_rtl(stripped),
            align="R", new_x="LMARGIN", new_y="NEXT",
        )
    else:
        pdf.set_font(FONT_SANS, "B", sz)
        pdf.multi_cell(0, sz * 0.6, stripped)
    pdf.ln(2)
    pdf.set_font(FONT_SANS, "", BODY_SIZE)
    pdf.set_text_color(*CLR_BODY)


def _add_code_block(pdf, lines):
    pdf.ln(2)
    pdf.set_fill_color(*CLR_CODE_BG)
    pdf.set_text_color(*CLR_CODE_FG)
    pdf.set_font(FONT_MONO, "", CODE_SIZE)
    w = pdf.w - pdf.l_margin - pdf.r_margin
    x0 = pdf.l_margin
    for ln in lines:
        _ensure_space(pdf, CODE_LH)
        pdf.set_fill_color(*CLR_CODE_BG)
        pdf.set_text_color(*CLR_CODE_FG)
        pdf.set_font(FONT_MONO, "", CODE_SIZE)
        display = ln[:MAX_CODE_COLS] if len(ln) > MAX_CODE_COLS else ln
        pdf.set_x(x0)
        pdf.cell(w, CODE_LH, display, fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font(FONT_SANS, "", BODY_SIZE)
    pdf.set_text_color(*CLR_BODY)
    pdf.ln(2)


def _looks_like_svg(data):
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
    if _looks_like_svg(data):
        px_w, px_h = _svg_size(data)
    else:
        px_w, px_h = PILImage.open(io.BytesIO(data)).size
    max_w, max_h = pdf.epw, pdf.eph - 10
    w_mm, h_mm = max_w, max_w * px_h / px_w
    if h_mm > max_h:
        w_mm, h_mm = max_h * px_w / px_h, max_h
    _ensure_space(pdf, h_mm + 8)
    x = pdf.l_margin + (max_w - w_mm) / 2
    pdf.image(data, x=x, w=w_mm, h=h_mm)
    pdf.ln(2)
    if alt:
        pdf.set_font(FONT_FA if _is_rtl(alt) and getattr(pdf, "has_persian", False) else FONT_SANS, "I", 8)
        pdf.set_text_color(120, 120, 120)
        caption = _shape_rtl(alt) if _is_rtl(alt) and getattr(pdf, "has_persian", False) else alt
        pdf.cell(0, 5, caption, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*CLR_BODY)
        pdf.set_font(FONT_SANS, "", BODY_SIZE)
    pdf.ln(3)


def _add_image(pdf, src, alt, base_dir):
    try:
        if re.match(r'^https?://', src):
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
        _add_paragraph(pdf, f"[image: {alt or src}]")


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


def _render_mermaid_ink(source):
    """Render via the mermaid.ink web API. Returns PNG bytes; raises on failure."""
    b64 = base64.urlsafe_b64encode(source.encode("utf-8")).decode("ascii")
    resp = requests.get(f"https://mermaid.ink/img/{b64}?bgColor=white", timeout=15)
    resp.raise_for_status()
    return resp.content


def _render_mermaid(source):
    """Best-effort Mermaid render: local mmdc, then mermaid.ink, then None."""
    global _mermaid_net_warned
    if _HAS_MMDC:
        try:
            data = _render_mermaid_mmdc(source)
            if data:
                return data
        except Exception:
            pass
    try:
        try:
            return _render_mermaid_ink(source)
        except requests.exceptions.RequestException:
            # Transient failures (dropped connections, timeouts) are common
            # enough on this public endpoint to warrant one retry before
            # falling back to showing the raw source.
            return _render_mermaid_ink(source)
    except Exception as exc:
        if not _mermaid_net_warned:
            print(
                "WARN: could not render Mermaid diagram "
                f"({'mmdc failed and ' if _HAS_MMDC else ''}mermaid.ink request "
                f"failed: {exc}); showing raw source instead. Install mermaid-cli "
                "(`npm install -g @mermaid-js/mermaid-cli`) for offline rendering.",
                file=sys.stderr,
            )
            _mermaid_net_warned = True
        return None


def _add_mermaid(pdf, lines):
    source = '\n'.join(lines).strip()
    if not source:
        return
    data = _render_mermaid(source)
    if data:
        try:
            _place_image(pdf, data)
            return
        except Exception as exc:
            print(f"WARN: could not embed rendered Mermaid diagram: {exc}", file=sys.stderr)
    _add_code_block(pdf, lines)


def _parse_table_row(line):
    cells = [c.strip() for c in line.split('|')]
    if cells and cells[0] == '':
        cells = cells[1:]
    if cells and cells[-1] == '':
        cells = cells[:-1]
    return cells


def _strip_code_ticks(text):
    """Strip only inline-code backticks; leave **bold** for fpdf2 markdown."""
    return re.sub(r'`(.+?)`', r'\1', text)


_CELL_BOLD_RE = re.compile(r'^\*\*(.+)\*\*$')


def _add_table(pdf, headers, rows):
    from fpdf.enums import TableCellFillMode
    from fpdf.fonts import FontFace

    pdf.ln(2)
    n = len(headers)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin

    has_persian = getattr(pdf, "has_persian", False) and (
        getattr(pdf, "doc_is_rtl", False)
        or any(_is_rtl(h) for h in headers)
        or any(_is_rtl(c) for row in rows for c in row)
    )
    table_font  = FONT_FA if has_persian else FONT_SANS
    text_align  = "RIGHT" if has_persian else "LEFT"

    if has_persian:
        # Mirror column order: markdown's first (e.g. label) column should
        # land on the right, matching RTL reading order, since fpdf2 always
        # lays table columns out left-to-right regardless of text_align.
        headers = list(reversed(headers))
        rows = [list(reversed(row)) for row in rows]

    def _prep_cell(cell):
        """Cell payload for table.row(): a dict for RTL cells (to carry a
        bold FontFace when the whole cell is **wrapped**, since shaped/
        reordered RTL text can't rely on fpdf2's own markdown bold parsing),
        plain text otherwise (fpdf2's native `markdown=True` handles bold).
        """
        if not has_persian:
            return _strip_code_ticks(cell)
        text = _strip_code_ticks(cell).strip()
        bold_m = _CELL_BOLD_RE.match(text)
        if bold_m:
            text = bold_m.group(1)
        return {
            "text": _shape_rtl(text),
            "style": FontFace(emphasis="BOLD") if bold_m else None,
        }

    # Natural widths (with backticks/markdown stripped, since they don't render).
    pdf.set_font(table_font, "B", TABLE_SIZE)
    natural = [pdf.get_string_width(_strip_md(h)) + 4 for h in headers]
    pdf.set_font(table_font, "", TABLE_SIZE)
    for row in rows:
        for i in range(min(n, len(row))):
            natural[i] = max(natural[i], pdf.get_string_width(_strip_md(row[i])) + 4)

    # Clamp each column to [min_col, max_col]. min_col guarantees at least a few
    # characters fit; max_col forces very long cells to wrap rather than starving
    # narrow columns when totals are scaled down.
    min_col = max(8.0, pdf.get_string_width("MMM") + 2)
    max_col = page_w * 0.28
    col_w = [max(min_col, min(nw, max_col)) for nw in natural]
    total = sum(col_w)

    if total > page_w:
        # Shrink only columns above min_col, proportional to their slack.
        excess = total - page_w
        slack = [w - min_col for w in col_w]
        slack_total = sum(slack)
        if slack_total >= excess:
            col_w = [w - excess * s / slack_total for w, s in zip(col_w, slack)]
        else:
            col_w = [page_w / n] * n
    elif total < page_w:
        # Distribute extra space to columns that were capped (the wide ones).
        leftover = page_w - total
        capped_idx = [i for i, nw in enumerate(natural) if nw > max_col]
        if capped_idx:
            for i in capped_idx:
                col_w[i] += leftover / len(capped_idx)
        else:
            for i in range(n):
                col_w[i] += leftover / n

    headings_style = FontFace(
        emphasis="BOLD",
        color=CLR_TABLE_HDR_FG,
        fill_color=CLR_TABLE_HDR_BG,
    )

    pdf.set_font(table_font, "", TABLE_SIZE)
    pdf.set_draw_color(*CLR_TABLE_BORDER)
    pdf.set_text_color(*CLR_BODY)

    with pdf.table(
        col_widths=tuple(col_w),
        text_align=text_align,
        cell_fill_color=CLR_TABLE_ALT,
        cell_fill_mode=TableCellFillMode.EVEN_ROWS,
        first_row_as_headings=True,
        headings_style=headings_style,
        line_height=TABLE_SIZE * 0.55,
        markdown=not has_persian,
        padding=1,
    ) as table:
        table.row([_prep_cell(h) for h in headers])
        for row in rows:
            cells = [_prep_cell(row[i]) if i < len(row) else "" for i in range(n)]
            table.row(cells)

    pdf.ln(2)


# Cycled by nesting depth, like most markdown editors/viewers, instead of a
# single hyphen at every level.
_BULLET_CHARS = ["•", "◦", "▪"]


def _bullet_char(indent):
    return _BULLET_CHARS[min(indent // 2, len(_BULLET_CHARS) - 1)]


def _use_rtl_layout(pdf, text):
    """Whether a block should use RTL shaping/alignment.

    A document-wide RTL flag (``pdf.doc_is_rtl``) is consulted alongside this
    specific text's own script, so a block with no Persian/Arabic characters
    at all (e.g. an English-only list item inside an otherwise-Persian list)
    still follows the document's base direction instead of snapping to LTR
    and breaking the list's alignment.
    """
    return (getattr(pdf, "doc_is_rtl", False) or _is_rtl(text)) and getattr(pdf, "has_persian", False)


def _add_paragraph(pdf, text):
    pdf.set_text_color(*CLR_BODY)
    if _use_rtl_layout(pdf, text):
        pdf.set_font(FONT_FA, "", BODY_SIZE)
        # multi_cell reserves its own internal c_margin padding on each side,
        # on top of the cell width we pass it -- our wrap width must match
        # that actual usable text width or lines we judge to "just fit" wrap
        # again inside multi_cell.
        width = pdf.w - pdf.l_margin - pdf.r_margin - 2 * pdf.c_margin
        lh = _body_lh(pdf)
        for line in _shape_rtl_lines(pdf, _strip_md(text), width):
            pdf.multi_cell(0, lh, line, align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font(FONT_SANS, "", BODY_SIZE)
        _render_rich(pdf, text)
        pdf.ln(_body_lh(pdf))


def _add_list_item(pdf, prefix, text, indent):
    pdf.set_text_color(*CLR_BODY)
    body = text.strip()
    if _use_rtl_layout(pdf, body):
        pdf.set_font(FONT_FA, "", BODY_SIZE)
        width = pdf.w - pdf.l_margin - pdf.r_margin - indent * 2
        lh = _body_lh(pdf)
        # multi_cell reserves its own internal c_margin padding on each side,
        # on top of the cell width we pass it -- wrap using the actual usable
        # text width or lines we judge to "just fit" wrap again inside multi_cell.
        usable_width = width - 2 * pdf.c_margin
        lines = _shape_rtl_lines(pdf, _strip_md(body), usable_width, marker=prefix.strip())
        for line in lines:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(width, lh, line, align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_x(pdf.l_margin + indent * 2)
        pdf.set_font(FONT_SANS, "", BODY_SIZE)
        pdf.write(_body_lh(pdf), prefix)
        _render_rich(pdf, body)
        pdf.ln(_body_lh(pdf))


def _add_hr(pdf):
    pdf.ln(2)
    pdf.set_draw_color(*CLR_HR)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(4)


# ═══════════════════════════════════════════════════════════════════
# Main converter
# ═══════════════════════════════════════════════════════════════════

def _extract_title(lines):
    """Return the first H1 text, or empty string."""
    for ln in lines:
        m = re.match(r'^#\s+(.*)', ln)
        if m:
            return _strip_md(m.group(1))
    return ""


def convert(md_path, pdf_path):
    md_path = Path(md_path)
    md_text = md_path.read_text(encoding="utf-8")
    lines = md_text.split('\n')
    title = _extract_title(lines)

    pdf = PDF(title=title, orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)
    # Document-wide base direction: a block with no RTL characters of its own
    # (e.g. an English-only list item in an otherwise-Persian list) still
    # follows this instead of snapping to LTR mid-list. See _use_rtl_layout.
    rtl_chars = len(_RTL_RE.findall(md_text))
    latin_chars = len(re.findall(r'[A-Za-z]', md_text))
    pdf.doc_is_rtl = rtl_chars > latin_chars

    # ── Title page ──────────────────────────────────────────────
    if title:
        pdf.add_page()
        pdf.ln(40)
        pdf.set_text_color(*CLR_HEADING)
        title_rtl = _is_rtl(title) and pdf.has_persian
        if title_rtl:
            pdf.set_font(FONT_FA, "B", 24)
            pdf.multi_cell(
                0, 14, _shape_rtl(title),
                align="C", new_x="LMARGIN", new_y="NEXT",
            )
        else:
            pdf.set_font(FONT_SANS, "B", 24)
            # Split long titles across lines
            words = title.split()
            chunk, chunks = [], []
            for w in words:
                chunk.append(w)
                if pdf.get_string_width(" ".join(chunk)) > 140:
                    chunks.append(" ".join(chunk[:-1]))
                    chunk = [w]
            chunks.append(" ".join(chunk))
            for c in chunks:
                pdf.cell(0, 14, c, align="C", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(10)
        pdf.set_font(FONT_SANS, "", 11)
        pdf.set_text_color(100, 100, 100)
        pdf.cell(0, 8, str(Path(md_path).name), align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.add_page()

    # ── Parse & render ──────────────────────────────────────────
    i = 0
    in_code = False
    code_buf = []
    code_lang = ""
    in_table = False
    tbl_hdr = []
    tbl_rows = []

    def _flush_table():
        nonlocal in_table, tbl_hdr, tbl_rows
        if in_table:
            _add_table(pdf, tbl_hdr, tbl_rows)
            in_table, tbl_hdr, tbl_rows = False, [], []

    while i < len(lines):
        line = lines[i]

        # ── code fence ──────────────────────────────────────────
        fence_m = re.match(r'^```\s*(\S*)', line.strip())
        if fence_m:
            if in_code:
                if code_lang == 'mermaid':
                    _add_mermaid(pdf, code_buf)
                else:
                    _add_code_block(pdf, code_buf)
                code_buf, in_code, code_lang = [], False, ""
            else:
                _flush_table()
                in_code = True
                code_lang = fence_m.group(1).lower()
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # ── table ───────────────────────────────────────────────
        if '|' in line and line.strip().startswith('|'):
            cells = _parse_table_row(line)
            if not in_table:
                if i + 1 < len(lines) and re.match(r'^[\s|:-]+$', lines[i + 1]):
                    in_table, tbl_hdr = True, cells
                    i += 2
                    continue
            if in_table:
                if re.match(r'^[\s|:-]+$', line):
                    i += 1
                    continue
                tbl_rows.append(cells)
                if i + 1 >= len(lines) or not lines[i + 1].strip().startswith('|'):
                    _flush_table()
                i += 1
                continue
        _flush_table()

        # ── horizontal rule ─────────────────────────────────────
        if re.match(r'^---+\s*$', line.strip()):
            _add_hr(pdf)
            i += 1
            continue

        # ── heading ─────────────────────────────────────────────
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:
            _add_heading(pdf, len(m.group(1)), m.group(2))
            i += 1
            continue

        # ── numbered list ───────────────────────────────────────
        m = re.match(r'^(\s*)(\d+)\.\s+(.*)', line)
        if m:
            _add_list_item(pdf, f"  {m.group(2)}. ", m.group(3), len(m.group(1)))
            i += 1
            continue

        # ── bullet list ─────────────────────────────────────────
        m = re.match(r'^(\s*)[-*]\s+(.*)', line)
        if m:
            indent = len(m.group(1))
            _add_list_item(pdf, f"  {_bullet_char(indent)} ", m.group(2), indent)
            i += 1
            continue

        # ── image ───────────────────────────────────────────────
        m = _IMG_RE.match(line.strip())
        if m:
            _add_image(pdf, m.group(2), m.group(1), md_path.parent)
            i += 1
            continue

        # ── blank line ──────────────────────────────────────────
        if line.strip() == '':
            pdf.ln(3)
            i += 1
            continue

        # ── paragraph ───────────────────────────────────────────
        _add_paragraph(pdf, line)
        i += 1

    _flush_table()

    pdf.output(str(pdf_path))
    print(f"  {md_path} -> {pdf_path}")


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.argument(
    "files",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
@click.option(
    "-o", "--output",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help="Output PDF path. Only valid with a single input file; "
         "otherwise each <input>.md is written as <input>.pdf.",
)
def pymd2pdf_cli(files: tuple[Path, ...], output: Path | None):
    """Convert Markdown file(s) to PDF.

    \b
    Supports headings, bold, inline code, code blocks, tables, bullets,
    numbered lists, horizontal rules, nested lists, images, and Mermaid
    diagrams. Persian/Arabic text is shaped and rendered right-to-left
    when the optional deps and Vazir font are available.

    \b
    Examples:
      pymd2pdf README.md                     # writes README.pdf
      pymd2pdf doc.md -o report.pdf          # writes report.pdf
      pymd2pdf a.md b.md c.md                # writes a.pdf, b.pdf, c.pdf

    \b
    ── Fonts ──────────────────────────────────────────────────────────
    DejaVu (REQUIRED) — installed at a system font path:
      Debian/Ubuntu : sudo apt-get install fonts-dejavu-core
      Fedora/RHEL   : sudo dnf install dejavu-sans-fonts dejavu-sans-mono-fonts
      Arch          : sudo pacman -S ttf-dejavu
      macOS (brew)  : brew install --cask font-dejavu

    \b
    Vazir (OPTIONAL, for Persian/Arabic) — download Vazir.ttf and
    Vazir-Bold.ttf from https://github.com/rastikerdar/vazir-font and
    drop them in one of:
      ~/.local/share/fonts
      /usr/share/fonts/truetype/vazir
      /usr/share/fonts/TTF
    Run `fc-cache -f` afterwards on Linux.

    \b
    ── Images ─────────────────────────────────────────────────────────
    Standalone ``![alt](path)`` lines are embedded, scaled to fit the
    page. ``path`` may be a local file (relative to the Markdown file)
    or an http(s) URL.

    \b
    ── Mermaid diagrams ────────────────────────────────────────────────
    ```` ```mermaid ```` fenced blocks are rendered to an image using, in
    order: a local mermaid-cli (`mmdc`) install, then the mermaid.ink web
    API. If neither is available the raw diagram source is shown as a
    code block instead. For offline rendering:
      npm install -g @mermaid-js/mermaid-cli

    \b
    ── Python dependencies ────────────────────────────────────────────
    Required : fpdf2, requests, Pillow
    Persian  : arabic-reshaper, python-bidi
                 pip install 'pytoolbox[rtl]'
                 # or: pip install arabic-reshaper python-bidi
    """
    if output and len(files) > 1:
        raise click.UsageError("-o/--output can only be used with a single input file")

    for md_path in files:
        out = output if output else md_path.with_suffix(".pdf")
        convert(md_path, out)


if __name__ == "__main__":
    pymd2pdf_cli()
