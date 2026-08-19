#!/usr/bin/env python3
"""Convert Markdown files to document.PDF using fpdf2 and DejaVu/Vazir fonts.

Exposes the ``pymd2pdf`` console script (see ``pymd2pdf --help``).

Supports: headings, bold, italic, strikethrough, links, inline code, code
blocks, tables, bullet/numbered/task lists, blockquotes, horizontal rules,
nested lists, images and Mermaid diagrams. Persian/Arabic text is shaped and
rendered right-to-left when a Vazir/Vazirmatn face and the RTL extras are
present.
"""

from __future__ import annotations

import re
from pathlib import Path

import click

from pytoolbox.core.options import CONTEXT_SETTINGS, version_option
from pytoolbox.mdpdf import document, fonts, media, render, shaping, state, tables

#: Page geometry presets accepted by ``--page-size``.
PAGE_SIZES = ("a3", "a4", "a5", "letter", "legal")




# Text helpers
# ═══════════════════════════════════════════════════════════════════
# Main converter
# ═══════════════════════════════════════════════════════════════════

def _extract_title(lines):
    """Return the first H1 text, or empty string."""
    for ln in lines:
        m = re.match(r'^#\s+(.*)', ln)
        if m:
            return render.strip_md(m.group(1))
    return ""


def convert(
    md_path,
    pdf_path,
    page_size="A4",
    orientation="P",
    margin=20,
    font_size=None,
    title_page=True,
    quiet=False,
):
    """Render one Markdown file to a document.PDF.

    ``font_size`` scales body text by rebinding the module-level ``state.BODY_SIZE``;
    the block renderers read that constant directly, and threading an explicit
    size through every one of them would add a parameter to a dozen functions
    for one rarely-changed knob.
    """
    original_body_size = state.BODY_SIZE
    if font_size:
        state.BODY_SIZE = font_size

    md_path = Path(md_path)
    # The document.PDF is built first because loading its faces is what determines
    # which characters can be drawn, and hence which ones shaping.substitute_glyphs
    # has to replace with text stand-ins.
    pdf = document.PDF(orientation=orientation, unit="mm", format=page_size)
    md_text = shaping.substitute_glyphs(md_path.read_text(encoding="utf-8"))
    lines = md_text.split('\n')
    title = _extract_title(lines) if title_page else ""
    pdf.set_doc_title(title)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=margin)
    pdf.set_margins(margin, margin, margin)
    pdf.set_title(title or md_path.stem)
    # Document-wide base direction: a block with no RTL characters of its own
    # (e.g. an English-only list item in an otherwise-Persian list) still
    # follows this instead of snapping to LTR mid-list. See render.use_rtl_layout.
    rtl_chars = len(shaping.RTL_RE.findall(md_text))
    latin_chars = len(re.findall(r'[A-Za-z]', md_text))
    pdf.doc_is_rtl = rtl_chars > latin_chars

    # ── Title page ──────────────────────────────────────────────
    if title:
        pdf.add_page()
        pdf.ln(40)
        pdf.set_text_color(*document.CLR_HEADING)
        title_rtl = shaping.is_rtl(title) and pdf.has_persian
        if title_rtl:
            pdf.set_font(fonts.FONT_FA, "B", 24)
            pdf.multi_cell(
                0, 14, shaping.shape_rtl(title),
                align="C", new_x="LMARGIN", new_y="NEXT",
            )
        else:
            pdf.set_font(fonts.FONT_SANS, "B", 24)
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
        pdf.set_font(fonts.FONT_SANS, "", 11)
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
            tables.add_table(pdf, tbl_hdr, tbl_rows)
            in_table, tbl_hdr, tbl_rows = False, [], []

    while i < len(lines):
        line = lines[i]

        # ── code fence ──────────────────────────────────────────
        fence_m = re.match(r'^```\s*(\S*)', line.strip())
        if fence_m:
            if in_code:
                if code_lang == 'mermaid':
                    media.add_mermaid(pdf, code_buf)
                else:
                    render.add_code_block(pdf, code_buf)
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
            cells = tables.parse_table_row(line)
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
            render.add_hr(pdf)
            i += 1
            continue

        # ── heading ─────────────────────────────────────────────
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:
            render.add_heading(pdf, len(m.group(1)), m.group(2))
            i += 1
            continue

        # ── numbered list ───────────────────────────────────────
        m = re.match(r'^(\s*)(\d+)\.\s+(.*)', line)
        if m:
            render.add_list_item(pdf, f"  {m.group(2)}. ", m.group(3), len(m.group(1)))
            i += 1
            continue

        # ── blockquote ──────────────────────────────────────────
        if line.lstrip().startswith('>'):
            quote_lines = []
            while i < len(lines) and lines[i].lstrip().startswith('>'):
                quote_lines.append(re.sub(r'^\s*>\s?', '', lines[i]))
                i += 1
            render.add_blockquote(pdf, quote_lines)
            continue

        # ── bullet list (including task lists) ──────────────────
        m = re.match(r'^(\s*)[-*+]\s+(.*)', line)
        if m:
            indent = len(m.group(1))
            body = m.group(2)
            task = render.TASK_RE.match(body)
            if task:
                marker = "  [x] " if task.group(1).lower() == "x" else "  [ ] "
                render.add_list_item(pdf, marker, task.group(2), indent)
            else:
                render.add_list_item(pdf, f"  {render.bullet_char(indent)} ", body, indent)
            i += 1
            continue

        # ── image ───────────────────────────────────────────────
        m = media.IMG_RE.match(line.strip())
        if m:
            media.add_image(pdf, m.group(2), m.group(1), md_path.parent)
            i += 1
            continue

        # ── blank line ──────────────────────────────────────────
        if line.strip() == '':
            pdf.ln(3)
            i += 1
            continue

        # ── paragraph ───────────────────────────────────────────
        render.add_paragraph(pdf, line)
        i += 1

    _flush_table()

    try:
        pdf.output(str(pdf_path))
    finally:
        state.BODY_SIZE = original_body_size
    if not quiet:
        print(f"  {md_path} -> {pdf_path}")


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════

@click.command(context_settings=CONTEXT_SETTINGS)
@click.argument(
    "files",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
@click.option(
    "-o", "--output",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help="Output document.PDF path. Only valid with a single input file; "
         "otherwise each <input>.md is written as <input>.pdf.",
)
@click.option(
    "-d", "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write the PDFs into this directory instead of beside the inputs.",
)
@click.option(
    "--page-size",
    type=click.Choice(PAGE_SIZES, case_sensitive=False),
    default="a4",
    show_default=True,
    help="Paper size.",
)
@click.option(
    "--landscape", is_flag=True, help="Use landscape orientation instead of portrait."
)
@click.option(
    "--margin",
    type=click.FloatRange(5, 60),
    default=20,
    show_default=True,
    help="Page margin in millimetres.",
)
@click.option(
    "--font-size",
    type=click.FloatRange(4, 24),
    default=None,
    help=f"Body text size in points (default: {state.BODY_SIZE}).",
)
@click.option(
    "--fallback-font",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="Extra TTF/OTF to draw glyphs the main fonts lack (repeatable). "
         "Tried before the auto-detected symbol font. Colour-bitmap emoji "
         "fonts (e.g. NotoColorEmoji) cannot be used.",
)
@click.option("--no-title-page", is_flag=True, help="Skip the generated cover page.")
@click.option(
    "--offline",
    is_flag=True,
    help="Never use the network: skip remote images and the mermaid.ink fallback.",
)
@click.option("-q", "--quiet", is_flag=True, help="Do not print the output paths.")
@version_option
def pymd2pdf_cli(
    files: tuple[Path, ...],
    output: Path | None,
    output_dir: Path | None,
    page_size: str,
    landscape: bool,
    margin: float,
    font_size: float | None,
    fallback_font: tuple[Path, ...],
    no_title_page: bool,
    offline: bool,
    quiet: bool,
):
    """Convert Markdown file(s) to document.PDF.

    \b
    Supports headings, bold, italic, strikethrough, links, inline code, code
    blocks, tables, bullet and numbered lists, task lists, blockquotes,
    horizontal rules, nested lists, images and Mermaid diagrams.
    Persian/Arabic text is shaped and rendered right-to-left when the optional
    deps and a Vazir/Vazirmatn font are available.

    \b
    Examples:
      pymd2pdf README.md                     # writes README.pdf
      pymd2pdf doc.md -o report.pdf          # writes report.pdf
      pymd2pdf a.md b.md c.md                # writes a.pdf, b.pdf, c.pdf
      pymd2pdf *.md -d ./pdfs --page-size a5
      pymd2pdf notes.md --no-title-page --font-size 11 --offline

    \b
    ── Fonts ──────────────────────────────────────────────────────────
    DejaVu (REQUIRED) — installed at a system font path:
      Debian/Ubuntu : sudo apt-get install fonts-dejavu-core
      Fedora/RHEL   : sudo dnf install dejavu-sans-fonts dejavu-sans-mono-fonts
      Arch          : sudo pacman -S ttf-dejavu
      Termux        : pkg install fontconfig-utils ttf-dejavu
      macOS (brew)  : brew install --cask font-dejavu

    \b
    Vazirmatn or Vazir (OPTIONAL, for Persian/Arabic) — download from
    https://github.com/rastikerdar/vazirmatn and drop the TTFs in one of:
      ~/.local/share/fonts
      ~/.termux/fonts          (Termux)
      $PREFIX/share/fonts      (Termux)
      /usr/share/fonts/truetype/vazir
    Run `fc-cache -f` afterwards on Linux.

    \b
    ── Missing glyphs ─────────────────────────────────────────────────
    Characters the main face cannot draw (symbols, arrows, emoji in a
    Persian document) are taken from DejaVu and then from a symbol font
    if one is installed — Symbola gives the widest coverage:
      Debian/Ubuntu : sudo apt-get install fonts-symbola
      Fedora/RHEL   : sudo dnf install gdouros-symbola-fonts
      Arch          : sudo pacman -S ttf-symbola
    Point at any other face with --fallback-font FILE (repeatable).
    Colour emoji fonts (NotoColorEmoji and friends) store bitmaps rather
    than outlines and cannot be embedded. Whatever no installed face can
    draw degrades to a text stand-in (✅ -> ✓, → -> ->); colour-coded
    status emoji always become ● ◐ ○, since a one-colour document.PDF would render
    🟢 and 🔴 as the same black disc.

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
        raise click.UsageError("-o/--output can only be used with a single input file.")
    if output and output_dir:
        raise click.UsageError("Use either -o/--output or -d/--output-dir, not both.")

    state.offline = offline
    state.extra_fallback_fonts = fallback_font
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    for md_path in files:
        if output:
            out = output
        elif output_dir:
            out = output_dir / md_path.with_suffix(".pdf").name
        else:
            out = md_path.with_suffix(".pdf")
        convert(
            md_path,
            out,
            page_size=page_size.upper(),
            orientation="L" if landscape else "P",
            margin=margin,
            font_size=font_size,
            title_page=not no_title_page,
            quiet=quiet,
        )


if __name__ == "__main__":  # pragma: no cover
    pymd2pdf_cli()
