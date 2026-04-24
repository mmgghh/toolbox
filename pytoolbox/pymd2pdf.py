#!/usr/bin/env python3
"""Convert Markdown files to PDF using fpdf2 and DejaVu/Vazir fonts.

Exposes the ``pymd2pdf`` console script (see ``pymd2pdf --help``).

Supports: headings, bold, inline code, code blocks, tables, bullets,
numbered lists, horizontal rules, and nested lists. Persian/Arabic text is
shaped and rendered right-to-left when Vazir and the RTL extras are present.
"""

import re
import sys
from pathlib import Path

import click
from fpdf import FPDF

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _HAS_SHAPER = True
except ImportError:
    _HAS_SHAPER = False

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


def _is_rtl(text):
    return bool(text) and bool(_RTL_RE.search(text))


def _shape_rtl(text):
    """Reshape Arabic/Persian letters and apply the bidi algorithm."""
    if not _HAS_SHAPER or not text:
        return text
    return get_display(arabic_reshaper.reshape(text))

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
    """Return (regular_path, bold_path) for Vazir, or (None, None) if absent."""
    for d in FONT_PERSIAN_DIRS:
        reg = d / "Vazir.ttf"
        if reg.is_file():
            bold = d / "Vazir-Bold.ttf"
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
            self.set_font(FONT_SANS, "I", 8)
            self.set_text_color(140, 140, 140)
            self.cell(0, 6, self._doc_title, align="R")
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
    sizes = {1: 18, 2: 14, 3: 12, 4: 11}
    sz = sizes.get(level, 11)
    pdf.ln(4 if level > 1 else 6)
    pdf.set_text_color(*CLR_HEADING)
    stripped = _strip_md(text)
    if _is_rtl(stripped) and getattr(pdf, "has_persian", False):
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


def _add_table(pdf, headers, rows):
    from fpdf.enums import TableCellFillMode
    from fpdf.fonts import FontFace

    pdf.ln(2)
    n = len(headers)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin

    has_persian = getattr(pdf, "has_persian", False) and (
        any(_is_rtl(h) for h in headers)
        or any(_is_rtl(c) for row in rows for c in row)
    )
    table_font  = FONT_FA if has_persian else FONT_SANS
    text_align  = "RIGHT" if has_persian else "LEFT"

    def _prep(cell):
        return _shape_rtl(_strip_code_ticks(cell)) if has_persian else _strip_code_ticks(cell)

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
        table.row([_prep(h) for h in headers])
        for row in rows:
            cells = [_prep(row[i]) if i < len(row) else "" for i in range(n)]
            table.row(cells)

    pdf.ln(2)


def _add_paragraph(pdf, text):
    pdf.set_text_color(*CLR_BODY)
    if _is_rtl(text) and getattr(pdf, "has_persian", False):
        pdf.set_font(FONT_FA, "", BODY_SIZE)
        pdf.multi_cell(
            0, _body_lh(pdf), _shape_rtl(_strip_md(text)),
            align="R", new_x="LMARGIN", new_y="NEXT",
        )
    else:
        pdf.set_font(FONT_SANS, "", BODY_SIZE)
        _render_rich(pdf, text)
        pdf.ln(_body_lh(pdf))


def _add_list_item(pdf, prefix, text, indent):
    pdf.set_text_color(*CLR_BODY)
    body = text.strip()
    if _is_rtl(body) and getattr(pdf, "has_persian", False):
        pdf.set_font(FONT_FA, "", BODY_SIZE)
        # In RTL, bullet/number marker belongs on the right edge.
        line = _shape_rtl(_strip_md(body)) + "  " + prefix.strip()
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(
            pdf.w - pdf.l_margin - pdf.r_margin - indent * 2,
            _body_lh(pdf), line,
            align="R", new_x="LMARGIN", new_y="NEXT",
        )
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
    md_text = Path(md_path).read_text(encoding="utf-8")
    lines = md_text.split('\n')
    title = _extract_title(lines)

    pdf = PDF(title=title, orientation="P", unit="mm", format="A4")
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

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
        if line.strip().startswith('```'):
            if in_code:
                _add_code_block(pdf, code_buf)
                code_buf, in_code = [], False
            else:
                _flush_table()
                in_code = True
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
        m = re.match(r'^(#{1,4})\s+(.*)', line)
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
            _add_list_item(pdf, "  - ", m.group(2), len(m.group(1)))
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
    numbered lists, horizontal rules, and nested lists. Persian/Arabic
    text is shaped and rendered right-to-left when the optional deps and
    Vazir font are available.

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
    ── Python dependencies ────────────────────────────────────────────
    Required : fpdf2
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
