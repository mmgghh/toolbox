"""Inline code inside right-to-left blocks, drawn as boxed monospace runs.

RTL paragraphs are reshaped and bidi-reordered as one string, so fpdf2's own
inline markup (and ``render_rich``'s font switching) cannot reach into them.
To get Typora-style ``code`` boxes anyway, each span is swapped for a one-letter
placeholder before shaping, the line is wrapped and reordered with the
placeholder standing in for the box, and the visual line is then split back
into plain and code runs that are drawn at explicit positions.

The placeholders are strong *right-to-left* letters on purpose. A code span is
a unit in the reading flow; were it plain neutral text, bidi would merge the
Latin text on either side of it into one left-to-right run, reversing the
order of ``(U+064C)، `x` (U+0652)`` and misplacing the parentheses.
"""

from __future__ import annotations

import re
import unicodedata

from pytoolbox.mdpdf import document, fonts, shaping

#: A code span as CommonMark has it: opened by a run of N backticks, closed by
#: the next run of exactly N, so ``` `` ` `` ``` holds a literal backtick.
_CODE_RE = re.compile(r'(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)', re.DOTALL)

#: Hebrew letters then N'Ko letters: all bidi class R, none touched by the
#: Arabic reshaper, and absent from any Persian document.
_PLACEHOLDERS = [chr(c) for c in range(0x05D0, 0x05EB)] + [chr(c) for c in range(0x07CA, 0x07EB)]

CODE_PAD_X = 0.8        # mm of padding between a code box's edge and its text
CODE_BOX_HEIGHT = 1.3   # box height as a multiple of the surrounding font size
CODE_BOX_RADIUS = 0.8   # mm
CODE_SIZE_DELTA = 1     # code is drawn this many points smaller than the body

#: A drawn piece of a line: ("text", str) or ("code", str).
Run = tuple[str, str]


def has_code(text: str) -> bool:
    return bool(_CODE_RE.search(text))


def protect_code(text: str) -> tuple[str, list[str]]:
    """Swap each ``code`` span for a placeholder; return the text and the spans.

    Spans beyond the placeholder pool (60 in one block) are left as plain text.
    """
    spans: list[str] = []

    def swap(match):
        code = match.group(2).replace("\n", " ")
        # One space of padding on each side is not part of the code, unless
        # that would leave nothing.
        if len(code) > 1 and code[0] == code[-1] == " " and code.strip(" "):
            code = code[1:-1]
        if len(spans) >= len(_PLACEHOLDERS):
            return code
        spans.append(code)
        return _PLACEHOLDERS[len(spans) - 1]

    return _CODE_RE.sub(swap, text), spans


def _only_marks(text: str) -> bool:
    return bool(text) and all(unicodedata.category(c) == "Mn" for c in text)


def _face(pdf, text: str, bold: bool):
    """Context manager: the font a code span is drawn in.

    A span of lone combining marks stays in the monospace face even though the
    marks are Arabic-script: there they are centred in a full-width cell,
    whereas Persian faces place them for a base letter that is not there.
    """
    if shaping.is_rtl(text) and pdf.has_persian and not _only_marks(text):
        family = fonts.FONT_FA
    else:
        family = fonts.FONT_MONO
    return pdf.local_context(
        font_family=family,
        font_style="B" if bold else "",
        font_size_pt=max(pdf.font_size_pt - CODE_SIZE_DELTA, 1),
    )


def _code_text(text: str) -> str:
    """The span as it is drawn, shaped when it holds Arabic-script letters."""
    if shaping.is_rtl(text) and not _only_marks(text):
        return shaping.shape_rtl(text, base_dir="L")
    return text


def code_width(pdf, text: str, bold: bool = False) -> float:
    with _face(pdf, text, bold):
        inner = pdf.get_string_width(_code_text(text))
        if _only_marks(text):
            # Marks may have no advance of their own; the box is one cell wide.
            inner = max(inner, pdf.get_string_width(" "))
    return inner + 2 * CODE_PAD_X


def _width(pdf, run: Run, bold: bool) -> float:
    kind, text = run
    return code_width(pdf, text, bold) if kind == "code" else pdf.get_string_width(text)


def runs_width(pdf, runs: list[Run], bold: bool = False) -> float:
    return sum(_width(pdf, run, bold) for run in runs)


def layout_rtl(pdf, carrier: str, spans: list[str], max_width: float,
               marker: str = "", bold: bool = False) -> list[list[Run]]:
    """Wrap and reorder ``carrier``; return each visual line as left-to-right runs.

    ``carrier`` is the block text after :func:`protect_code` and markdown
    stripping. The current font is the one plain text is measured in.
    """
    by_placeholder = dict(zip(_PLACEHOLDERS, spans))
    strip_placeholders = str.maketrans("", "", "".join(by_placeholder))

    def measure(candidate: str) -> float:
        boxes = sum(code_width(pdf, by_placeholder[c], bold) for c in candidate if c in by_placeholder)
        return pdf.get_string_width(candidate.translate(strip_placeholders)) + boxes

    lines = shaping.shape_rtl_lines(pdf, carrier, max_width, marker=marker, measure=measure)
    return [_split_runs(line, by_placeholder) for line in lines]


def _split_runs(line: str, by_placeholder: dict[str, str]) -> list[Run]:
    runs: list[Run] = []
    plain = ""
    for char in line:
        if char in by_placeholder:
            if plain:
                runs.append(("text", plain))
                plain = ""
            runs.append(("code", by_placeholder[char]))
        else:
            plain += char
    if plain:
        runs.append(("text", plain))
    return runs


def draw_line(pdf, runs: list[Run], lh: float, left: float, right: float,
              align: str = "R", bold: bool = False) -> None:
    """Draw one laid-out line at the current y, then move to the next line.

    Plain runs use the current font and colour; code runs are boxed. Breaks to
    a new page first if the line would not fit, as ``multi_cell`` would.
    """
    if pdf.will_page_break(lh):
        pdf.add_page()
    y = pdf.get_y()
    total = runs_width(pdf, runs, bold)
    x = {"L": left, "C": (left + right - total) / 2}.get(align, right - total)

    margin = pdf.c_margin
    pdf.c_margin = 0
    try:
        for run in runs:
            width = _width(pdf, run, bold)
            if run[0] == "code":
                _draw_code(pdf, x, y, width, lh, run[1], bold)
            else:
                pdf.set_xy(x, y)
                pdf.cell(width, lh, run[1])
            x += width
    finally:
        pdf.c_margin = margin
    pdf.set_xy(pdf.l_margin, y + lh)


def _draw_code(pdf, x: float, y: float, width: float, lh: float, text: str, bold: bool) -> None:
    box_h = min(pdf.font_size * CODE_BOX_HEIGHT, lh)
    with pdf.local_context(
        fill_color=document.CLR_CODE_BOX_BG,
        draw_color=document.CLR_CODE_BOX_BORDER,
        line_width=0.15,
    ):
        pdf.rect(x, y + (lh - box_h) / 2, width, box_h, style="DF",
                 round_corners=True, corner_radius=CODE_BOX_RADIUS)
    with _face(pdf, text, bold):
        pdf.set_text_color(*document.CLR_CODE_FG)
        pdf.set_xy(x + CODE_PAD_X, y)
        pdf.cell(width - 2 * CODE_PAD_X, lh, _code_text(text), align="C")
