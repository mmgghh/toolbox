"""Turning one Markdown block into marks on the page.

Each ``add_*`` takes the :class:`~pytoolbox.mdpdf.document.PDF` being built
and one parsed block — a heading, a paragraph, a list item, a quote — and
draws it. ``render_rich`` is the shared inner loop that handles the inline
markers (bold, italic, code, links) any of those blocks may contain.
"""

from __future__ import annotations

import re

from pytoolbox.mdpdf import document, fonts, shaping, state

#: An inline link or image: the label is drawn, the target is not.
_MD_LINK_RE = re.compile(r'!?\[([^\]]*)\]\(([^)]*)\)')


def strip_links(text):
    """Replace ``[label](url)`` (and ``![alt](src)``) with just its label."""
    return _MD_LINK_RE.sub(r'\1', text)


def strip_md(text):
    """Remove inline markdown markers, leaving the text that will be drawn."""
    text = strip_links(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'~~(.+?)~~', r'\1', text)
    text = re.sub(r'\*(.+?)\*', r'\1', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    return text


#: A block whose *entire* text is wrapped in ``**bold**``. RTL blocks are
#: shaped and bidi-reordered before fpdf2 sees them, so its own markdown
#: parser can no longer find markers inside the text -- this is the one case
#: (whole-block, not partial/inline) still worth honouring for RTL text.

# Inline spans, longest markers first so ``**`` wins over ``*`` and ``__``
# over ``_``. Links come first because their label may itself contain markers.
WHOLE_BOLD_RE = re.compile(r'^\*\*(.+)\*\*$')

INLINE_RE = re.compile(
    r'(\[[^\]]+\]\([^)\s]+\)'
    r'|`[^`]+`'
    r'|\*\*[^*]+\*\*'
    r'|__[^_]+__'
    r'|~~[^~]+~~'
    r'|\*[^*\s][^*]*\*'
    r')'
)

_LINK_RE = re.compile(r'^\[([^\]]+)\]\(([^)\s]+)\)$')


def render_rich(pdf, text, base_size=None, base_style=""):
    """Write a line honouring inline code, bold, italic, strikethrough and links.

    Uses pdf.write() throughout so segments wrap at the right margin instead of
    overflowing. Inline code is distinguished by the mono font; links are drawn
    underlined and carry a real document.PDF link annotation.

    ``base_size`` defaults to state.BODY_SIZE at *call* time, not import time:
    ``convert`` rebinds state.BODY_SIZE for --font-size, and a default evaluated at
    import would pin every styled run to the original 10pt while the plain
    text around it scaled.
    """
    if base_size is None:
        base_size = state.BODY_SIZE
    lh = document.body_lh(pdf)

    def reset():
        pdf.set_font(fonts.FONT_SANS, base_style, base_size)
        pdf.set_text_color(*document.CLR_BODY)

    for part in INLINE_RE.split(text):
        if not part:
            continue
        link_match = _LINK_RE.match(part)
        if link_match:
            label, url = link_match.groups()
            pdf.set_font(fonts.FONT_SANS, base_style + "U" if "U" not in base_style else base_style, base_size)
            pdf.set_text_color(*document.CLR_LINK)
            pdf.write(lh, strip_md(label), link=url)
            reset()
        elif part.startswith('`') and part.endswith('`'):
            pdf.set_font(fonts.FONT_MONO, "", base_size - 1)
            pdf.set_text_color(*document.CLR_CODE_FG)
            pdf.write(lh, part[1:-1])
            reset()
        elif (part.startswith('**') and part.endswith('**')) or (
            part.startswith('__') and part.endswith('__')
        ):
            pdf.set_font(fonts.FONT_SANS, "B", base_size)
            pdf.set_text_color(*document.CLR_BOLD)
            pdf.write(lh, part[2:-2])
            reset()
        elif part.startswith('~~') and part.endswith('~~'):
            # fpdf2 has no strikethrough style; grey text reads as "struck out"
            # well enough without drawing manual lines under wrapped runs.
            pdf.set_text_color(*document.CLR_STRIKE)
            pdf.write(lh, part[2:-2])
            reset()
        elif part.startswith('*') and part.endswith('*'):
            pdf.set_font(fonts.FONT_SANS, "I", base_size)
            pdf.write(lh, part[1:-1])
            reset()
        else:
            pdf.write(lh, part)



def add_heading(pdf, level, text):
    sizes = {1: 18, 2: 14, 3: 12, 4: 11, 5: 10, 6: 10}
    sz = sizes.get(level, 10)
    pdf.ln(4 if level > 1 else 6)
    pdf.set_text_color(*document.CLR_HEADING)
    stripped = strip_md(text)
    if use_rtl_layout(pdf, stripped):
        pdf.set_font(fonts.FONT_FA, "B", sz)
        pdf.multi_cell(
            0, sz * 0.6, shaping.shape_rtl(stripped),
            align="R", new_x="LMARGIN", new_y="NEXT",
        )
    else:
        pdf.set_font(fonts.FONT_SANS, "B", sz)
        pdf.multi_cell(0, sz * 0.6, stripped)
    pdf.ln(2)
    pdf.set_font(fonts.FONT_SANS, "", state.BODY_SIZE)
    pdf.set_text_color(*document.CLR_BODY)


def code_block_is_rtl(pdf, lines, language=""):
    """Whether a fenced block should be laid out right-to-left.

    Unlike prose, a code fence does not follow the document's base direction:
    an ASCII snippet in a Persian document is still code, and right-aligning
    it would be wrong. A fence with an explicit language (```json, ```python,
    ...) is always left-aligned -- a Persian string literal inside it does
    not make the language itself RTL. Only an untagged fence looks at its own
    characters to decide.
    """
    if language:
        return False
    return shaping.is_rtl("\n".join(lines)) and getattr(pdf, "has_persian", False)


#: A maximal run of RTL (incl. shaped presentation-form) characters, captured
#: so ``re.split`` hands back the runs alongside the plain text between them.
_RTL_RUN_RE = re.compile(f"({shaping.RTL_RE.pattern}+)")


def _code_runs(line, has_persian, align_rtl):
    """The (text, face) segments to draw for one code line, in order.

    Shaping is decided per line, independent of the block's own alignment: a
    Persian run needs the Persian face to render at all -- the mono face has
    no Arabic-script glyphs -- whether or not the block around it is
    left-aligned.

    A right-aligned block reorders the whole line as one RTL paragraph and
    draws it as a single Persian-face run, indent moved to the far side so it
    still reads as indentation. A left-aligned block (e.g. a Persian string
    literal inside tagged code) instead reorders only the RTL run in place,
    leaving the ASCII syntax around it -- keys, punctuation, indentation --
    exactly where it is; drawn as separate mono/Persian runs rather than one
    Persian-face run, since Vazir is a proportional face and drawing ASCII
    indentation with it would throw off alignment against the plain-mono
    lines around it.
    """
    if not has_persian or not shaping.is_rtl(line):
        return [(line, fonts.FONT_MONO)]
    if align_rtl:
        body = line.lstrip(' ')
        indent = ' ' * (len(line) - len(body))
        return [(shaping.shape_rtl(body) + indent, fonts.FONT_FA)]
    shaped = shaping.shape_rtl(line, base_dir='L')
    return [
        (part, fonts.FONT_FA if shaping.is_rtl(part) else fonts.FONT_MONO)
        for part in _RTL_RUN_RE.split(shaped) if part
    ]


def add_code_block(pdf, lines, language=""):
    """Draw a fenced block as a padded, rounded-corner box, Typora-style.

    Drawn in page-sized chunks rather than one rect for the whole block: a
    block long enough to cross a page boundary gets one box per page instead
    of a single rect that would either overflow the page or clip the code
    drawn on top of it.
    """
    pdf.ln(2)
    rtl = code_block_is_rtl(pdf, lines, language)
    has_persian = getattr(pdf, "has_persian", False)
    align = "R" if rtl else "L"
    w = pdf.w - pdf.l_margin - pdf.r_margin
    inner_w = w - 2 * document.CODE_PAD_X
    x0 = pdf.l_margin
    bottom = pdf.h - pdf.b_margin - 5
    # fpdf2's default cell padding is meant for isolated cells; back-to-back
    # runs on one line would each carry it, opening a visible gap between a
    # Persian run and the ASCII quote right next to it.
    original_c_margin = pdf.c_margin
    pdf.c_margin = 0
    try:
        index = 0
        while index < len(lines):
            if bottom - pdf.get_y() < document.CODE_LH + 2 * document.CODE_PAD_Y:
                pdf.add_page()
            room = bottom - pdf.get_y() - 2 * document.CODE_PAD_Y
            fit = max(1, int(room // document.CODE_LH))
            chunk = lines[index : index + fit]

            y0 = pdf.get_y()
            box_h = len(chunk) * document.CODE_LH + 2 * document.CODE_PAD_Y
            pdf.set_fill_color(*document.CLR_CODE_BG)
            pdf.rect(x0, y0, w, box_h, style="F", round_corners=True, corner_radius=document.CODE_RADIUS)

            pdf.set_text_color(*document.CLR_CODE_FG)
            y = y0 + document.CODE_PAD_Y
            for ln in chunk:
                display = ln[:document.MAX_CODE_COLS] if len(ln) > document.MAX_CODE_COLS else ln
                runs = _code_runs(display, has_persian, rtl)
                if rtl:
                    text, family = runs[0]
                    pdf.set_font(family, "", document.CODE_SIZE)
                    pdf.set_xy(x0 + document.CODE_PAD_X, y)
                    pdf.cell(inner_w, document.CODE_LH, text, align=align, new_x="LMARGIN", new_y="TOP")
                else:
                    pdf.set_xy(x0 + document.CODE_PAD_X, y)
                    for text, family in runs:
                        pdf.set_font(family, "", document.CODE_SIZE)
                        # Auto width, default new_x=RIGHT/new_y=TOP: each run
                        # starts right where the last one ended, same line.
                        pdf.cell(None, document.CODE_LH, text, align=align)
                y += document.CODE_LH
            pdf.set_y(y0 + box_h)
            index += fit
    finally:
        pdf.c_margin = original_c_margin

    pdf.set_font(fonts.FONT_SANS, "", state.BODY_SIZE)
    pdf.set_text_color(*document.CLR_BODY)
    pdf.ln(2)



_BULLET_CHARS = ["•", "◦", "▪"]


def bullet_char(indent):
    return _BULLET_CHARS[min(indent // 2, len(_BULLET_CHARS) - 1)]


def use_rtl_layout(pdf, text):
    """Whether a block should use RTL shaping/alignment.

    A document-wide RTL flag (``pdf.doc_is_rtl``) is consulted alongside this
    specific text's own script, so a block with no Persian/Arabic characters
    at all (e.g. an English-only list item inside an otherwise-Persian list)
    still follows the document's base direction instead of snapping to LTR
    and breaking the list's alignment.
    """
    return (getattr(pdf, "doc_is_rtl", False) or shaping.is_rtl(text)) and getattr(pdf, "has_persian", False)


def add_paragraph(pdf, text):
    pdf.set_text_color(*document.CLR_BODY)
    if use_rtl_layout(pdf, text):
        bold_m = WHOLE_BOLD_RE.match(text.strip())
        pdf.set_font(fonts.FONT_FA, "B" if bold_m else "", state.BODY_SIZE)
        # multi_cell reserves its own internal c_margin padding on each side,
        # on top of the cell width we pass it -- our wrap width must match
        # that actual usable text width or lines we judge to "just fit" wrap
        # again inside multi_cell.
        width = pdf.w - pdf.l_margin - pdf.r_margin - 2 * pdf.c_margin
        lh = document.body_lh(pdf)
        body = bold_m.group(1) if bold_m else text
        for line in shaping.shape_rtl_lines(pdf, strip_md(body), width):
            pdf.multi_cell(0, lh, line, align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font(fonts.FONT_SANS, "", state.BODY_SIZE)
        render_rich(pdf, text)
        pdf.ln(document.body_lh(pdf))


def add_list_item(pdf, prefix, text, indent):
    pdf.set_text_color(*document.CLR_BODY)
    body = text.strip()
    if use_rtl_layout(pdf, body):
        bold_m = WHOLE_BOLD_RE.match(body)
        pdf.set_font(fonts.FONT_FA, "B" if bold_m else "", state.BODY_SIZE)
        width = pdf.w - pdf.l_margin - pdf.r_margin - indent * 2
        lh = document.body_lh(pdf)
        # multi_cell reserves its own internal c_margin padding on each side,
        # on top of the cell width we pass it -- wrap using the actual usable
        # text width or lines we judge to "just fit" wrap again inside multi_cell.
        usable_width = width - 2 * pdf.c_margin
        inner = bold_m.group(1) if bold_m else body
        lines = shaping.shape_rtl_lines(pdf, strip_md(inner), usable_width, marker=prefix.strip())
        for line in lines:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(width, lh, line, align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_x(pdf.l_margin + indent * 2)
        pdf.set_font(fonts.FONT_SANS, "", state.BODY_SIZE)
        pdf.write(document.body_lh(pdf), prefix)
        render_rich(pdf, body)
        pdf.ln(document.body_lh(pdf))


def add_blockquote(pdf, lines):
    """Render consecutive ``> `` lines as an indented, bar-marked quote."""
    if not lines:
        return
    text = " ".join(line.strip() for line in lines if line.strip())
    if not text:
        return
    pdf.ln(1)
    indent = 6
    start = (pdf.page, pdf.get_y())
    pdf.set_text_color(*document.CLR_QUOTE_FG)

    if use_rtl_layout(pdf, text):
        bold_m = WHOLE_BOLD_RE.match(text.strip())
        pdf.set_font(fonts.FONT_FA, "B" if bold_m else "", state.BODY_SIZE)
        # multi_cell reserves its own internal c_margin padding on each side,
        # on top of the cell width we pass it -- our wrap width must match
        # that actual usable text width or lines we judge to "just fit" wrap
        # again inside multi_cell (see add_paragraph's docstring).
        width = pdf.w - pdf.l_margin - pdf.r_margin - indent
        usable_width = width - 2 * pdf.c_margin
        body = bold_m.group(1) if bold_m else text
        for line in shaping.shape_rtl_lines(pdf, strip_md(body), usable_width):
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(width, document.body_lh(pdf), line, align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font(fonts.FONT_SANS, "I", state.BODY_SIZE)
        pdf.set_left_margin(pdf.l_margin + indent)
        pdf.set_x(pdf.l_margin)
        render_rich(pdf, text, base_style="I")
        pdf.ln(document.body_lh(pdf))
        pdf.set_left_margin(pdf.l_margin - indent)

    # Draw the bar last, once the quote's height is known -- and, if it spilled
    # onto further pages, once each page's share of that height is known too.
    bar_x = pdf.w - pdf.r_margin - 1 if use_rtl_layout(pdf, text) else pdf.l_margin + 1
    document.draw_vertical_rule(
        pdf, bar_x, start, (pdf.page, pdf.get_y() - 1),
        document.CLR_QUOTE_BAR, 0.8,
    )
    pdf.set_font(fonts.FONT_SANS, "", state.BODY_SIZE)
    pdf.set_text_color(*document.CLR_BODY)
    pdf.ln(2)


#: ``- [ ] todo`` / ``- [x] done`` list items.
TASK_RE = re.compile(r'^\[([ xX])\]\s+(.*)$')


def add_hr(pdf):
    pdf.ln(2)
    pdf.set_draw_color(*document.CLR_HR)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.w - pdf.r_margin, y)
    pdf.ln(4)
