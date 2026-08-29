"""Markdown tables: measuring the columns, then drawing the grid.

A table is the one block whose height cannot be known before it is laid out,
so this measures every cell, decides the column widths and row heights, and
only then draws — which is also why it needs its own control over link
annotations while it is measuring.
"""

from __future__ import annotations

import re
from contextlib import contextmanager

from pytoolbox.mdpdf import document, fonts, render, shaping


def parse_table_row(line):
    cells = [c.strip() for c in line.split('|')]
    if cells and cells[0] == '':
        cells = cells[1:]
    if cells and cells[-1] == '':
        cells = cells[:-1]
    return cells


def _strip_code_ticks(text):
    """Strip only inline-code backticks; leave **bold** for fpdf2 markdown."""
    return re.sub(r'`(.+?)`', r'\1', text)


#: ``<br>`` is how Markdown forces a line break inside a table cell, since a
#: literal newline would end the row. fpdf2 does not parse HTML -- unlike its
#: own markdown for ``**bold**`` -- so left alone it prints the tag as text.
_BR_RE = re.compile(r'<br\s*/?>', re.I)


def _normalize_cell(text):
    """Cell source with code ticks stripped and ``<br>`` turned into a real
    line break that fpdf2's own line wrapping (``multi_cell``) will honour.
    """
    return _BR_RE.sub('\n', _strip_code_ticks(text)).strip()


def _cell_width(pdf, text):
    """The width a cell wants: its widest line, not its whole (possibly
    ``<br>``-broken) length -- an explicit break should not inflate the column.
    """
    return max(pdf.get_string_width(line) for line in text.split('\n'))


#: A cell that is nothing but a single link, which can therefore become a real
#: document.PDF link annotation on the whole cell rather than plain label text.
_CELL_LINK_RE = re.compile(r'^\[([^\]]+)\]\(([^)\s]+)\)$')


@contextmanager
def _isolated_annotations(pdf):
    """Keep fpdf2's cell-measuring pass from leaking link annotations.

    Before drawing a table, fpdf2 renders every cell once with output
    disabled to learn how tall it is, and restores the page's annotation
    list afterwards -- but it restores the very list object it captured, so
    whatever the measuring pass appended survives whenever the page already
    had an annotation on it. Each linked cell then leaves an extra invisible
    click target at the measuring cursor's position, usually somewhere in
    the table's first row.

    Starting the table with an *empty* list dodges the aliasing (fpdf2 keeps
    a fresh list of its own in that case, and drops it along with the
    measuring pass's additions); the page's real annotations are appended
    back afterwards, in order.
    """
    page = pdf.pages[pdf.page]
    saved = page.annots
    if not saved:  # nothing to alias: fpdf2 already discards the additions
        yield
        return
    page.annots = saved.__class__()
    try:
        yield
    finally:
        # ``page`` is the page the table started on; rows pushed onto later
        # pages keep their own (initially empty, so unaffected) lists.
        drawn = page.annots or []
        page.annots = saved
        saved.extend(drawn)


def add_table(pdf, headers, rows):
    from fpdf.enums import TableCellFillMode
    from fpdf.fonts import FontFace

    pdf.ln(2)
    n = len(headers)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    CELL_PADDING = 1

    has_persian = getattr(pdf, "has_persian", False) and (
        getattr(pdf, "doc_is_rtl", False)
        or any(shaping.is_rtl(h) for h in headers)
        or any(shaping.is_rtl(c) for row in rows for c in row)
    )
    table_font  = fonts.FONT_FA if has_persian else fonts.FONT_SANS
    text_align  = "RIGHT" if has_persian else "LEFT"

    if has_persian:
        # Mirror column order: markdown's first (e.g. label) column should
        # land on the right, matching RTL reading order, since fpdf2 always
        # lays table columns out left-to-right regardless of text_align.
        headers = list(reversed(headers))
        rows = [list(reversed(row)) for row in rows]

    def _prep_cell(cell, col_width, is_header=False):
        """Cell payload for table.row(): plain text, or a dict when the cell
        needs more than fpdf2's own markdown parsing gives us.

        That parser handles ``**bold**`` but knows nothing about links, so a
        cell left as-is would print its raw ``[label](url)`` source. A cell
        that is *only* a link becomes the label plus a real link annotation;
        a link mixed into other text keeps just its label.

        RTL cells always need the dict form: their text is shaped and bidi
        reordered before fpdf2 sees it, so its markdown markers can no longer
        be parsed and any bold has to travel as an explicit FontFace.
        """
        text = _normalize_cell(cell)
        bold_m = render.WHOLE_BOLD_RE.match(text)
        inner = bold_m.group(1).strip() if bold_m else text
        link_m = _CELL_LINK_RE.match(inner)
        link = link_m.group(2) if link_m else None

        if link is None and not has_persian:
            # Nothing fpdf2 cannot handle itself: leave the ** markers in
            # place for its markdown parser, having dropped the link syntax
            # it does not understand.
            return render.strip_links(text)

        emphasis = ("B" if bold_m else "") + ("U" if link else "")
        # Only an RTL cell reaches here without a link, and its text is shaped
        # and bidi reordered before fpdf2 sees it -- markdown markers around
        # part of a cell can no longer be parsed, and would print literally.
        # Whole-cell bold survives as the FontFace above; the rest is dropped.
        body = link_m.group(1) if link_m else render.strip_md(inner)
        if has_persian:
            # Reordering the *whole* cell into visual order and letting fpdf2's
            # plain left-to-right wrapper break it into lines would scatter the
            # paragraph's tail onto the first physical line (see
            # shaping.shape_rtl_lines's docstring). Wrap here instead, in logical
            # order, one bidi-reordered line per line -- using the same style
            # (bold for headings) fpdf2 will actually render the cell in, so
            # our line breaks match its usable width and it doesn't re-wrap
            # (and re-scramble) any of them.
            pdf.set_font(table_font, "B" if (bold_m or is_header) else "", document.TABLE_SIZE)
            usable_width = col_width - 2 * CELL_PADDING
            # Wrap each ``<br>``-separated paragraph on its own: shape_rtl_lines
            # only knows how to wrap on spaces, so an embedded newline would
            # otherwise get shaped as part of whichever word it lands next to.
            shaped = "\n".join(
                line
                for paragraph in body.split("\n")
                for line in shaping.shape_rtl_lines(pdf, paragraph, usable_width)
            )
        else:
            shaped = body
        return {
            "text": shaped,
            "link": link,
            "style": FontFace(
                emphasis=emphasis or None,
                color=document.CLR_LINK if link else None,
            ) if emphasis else None,
        }

    # Natural widths (with backticks/markdown stripped and <br> resolved to its
    # own line, since none of that renders as the raw source's width implies).
    pdf.set_font(table_font, "B", document.TABLE_SIZE)
    natural = [_cell_width(pdf, render.strip_md(_normalize_cell(h))) + 4 for h in headers]
    pdf.set_font(table_font, "", document.TABLE_SIZE)
    for row in rows:
        for i in range(min(n, len(row))):
            width = _cell_width(pdf, render.strip_md(_normalize_cell(row[i]))) + 4
            natural[i] = max(natural[i], width)

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
        color=document.CLR_TABLE_HDR_FG,
        fill_color=document.CLR_TABLE_HDR_BG,
    )

    pdf.set_font(table_font, "", document.TABLE_SIZE)
    pdf.set_draw_color(*document.CLR_TABLE_BORDER)
    pdf.set_text_color(*document.CLR_BODY)

    with _isolated_annotations(pdf), pdf.table(
        col_widths=tuple(col_w),
        text_align=text_align,
        cell_fill_color=document.CLR_TABLE_ALT,
        cell_fill_mode=TableCellFillMode.EVEN_ROWS,
        first_row_as_headings=True,
        headings_style=headings_style,
        line_height=document.TABLE_SIZE * 0.55,
        markdown=not has_persian,
        padding=CELL_PADDING,
    ) as table:
        table.row([_prep_cell(h, col_w[i], is_header=True) for i, h in enumerate(headers)])
        for row in rows:
            cells = [
                _prep_cell(row[i], col_w[i]) if i < len(row) else ""
                for i in range(n)
            ]
            table.row(cells)

    pdf.ln(2)


# Cycled by nesting depth, like most markdown editors/viewers, instead of a
