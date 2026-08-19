"""Persian/Arabic shaping and bidirectional reordering.

fpdf2 draws glyphs left to right in the order it is given them, so an
Arabic-script run has to be reshaped into its contextual forms and reordered
into visual order before it is handed over. Everything that has to happen to
a string *because* it is right-to-left lives here.
"""

from __future__ import annotations

import re

from pytoolbox.mdpdf import state

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    HAS_SHAPER = True
    # The default configuration deletes Harakat (تشکیل/اعراب: فتحه، کسره، ضمه،
    # تنوین, ...) before shaping, so e.g. "نُه" would reshape as "نه". Keep
    # them; get_display (below) positions combining marks correctly on its own.
    _reshaper = arabic_reshaper.ArabicReshaper(configuration={"delete_harakat": False})
except ImportError:
    HAS_SHAPER = False

# Characters in the Arabic/Persian Unicode blocks (including presentation forms).
RTL_RE = re.compile(r'[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]')


def is_rtl(text):
    return bool(text) and bool(RTL_RE.search(text))


# Variation selectors: zero-width characters that only pick a glyph *variant*
# (text vs emoji presentation). No face draws them, so each is reported as a
# missing glyph; dropping them is lossless. ZWNJ (U+200C) is deliberately not
# in this set -- it is meaningful in Persian orthography.
_VARIATION_SELECTORS = "\ufe0e\ufe0f"

# Colour-coded status emoji, substituted even when a symbol face *can* draw
# them: the PDF draws text in one colour, so 🔴 and 🟢 would come out as two
# identical black discs, losing exactly the distinction they encode. The
# stand-ins follow the usual full/half/empty "harvey ball" reading.
_COLOUR_STATUS_SUBSTITUTES = {
    '🟢': '●', '🟩': '●',
    '🟡': '◐', '🟨': '◐', '🟠': '◐', '🟧': '◐',
    '🔴': '○', '🟥': '○',
}

# Text stand-ins used only for characters *no* loaded face can draw (see
# build_glyph_translation): with DejaVu and a symbol font registered as
# fallbacks, most of these now render as their real glyph instead.
# Every substitute must itself be covered by DejaVu.
_GLYPH_SUBSTITUTES = {
    '→': '->',
    '←': '<-',
    '↔': '<->',
    '⇢': '-->',
    '×': 'x',
    '÷': '/',
    '☐': '[ ]',  # ☐ BALLOT BOX
    '☑': '[x]',  # ☑ BALLOT BOX WITH CHECK
    '☒': '[x]',  # ☒ BALLOT BOX WITH X
    '✅': '✓',
    '✔': '✓',
    '❌': '✗',
    '✖': '✗',
    '⚠': '(!)',
    'ˏ': '/',  # ˏ MODIFIER LETTER LOW ACUTE ACCENT, seen as a numeral separator
    '⬛': '●', '⚫': '●',
    '⬜': '○', '⚪': '○',
    # Substitutes for the nested-bullet markers below must stay in the Unicode
    # "neutral" bidi classes (punctuation/symbols), not letters: a marker is
    # folded into RTL text as its first logical word, and unlike a neutral
    # character, a strong-direction letter (e.g. 'o') doesn't take on the
    # surrounding RTL run's position -- it renders on the wrong (left) side.
    '◦': '·',    # ◦ WHITE BULLET (nested list marker)
    '▪': '*',    # ▪ BLACK SMALL SQUARE (nested list marker)
}


def build_glyph_translation(covered) -> dict[int, str]:
    """Map code points to stand-ins, given the code points the fonts can draw."""
    table: dict[int, str] = {ord(c): '' for c in _VARIATION_SELECTORS}
    table.update({ord(src): dst for src, dst in _COLOUR_STATUS_SUBSTITUTES.items()})
    table.update({
        ord(src): dst
        for src, dst in _GLYPH_SUBSTITUTES.items()
        if ord(src) not in covered
    })
    return table


def substitute_glyphs(text):
    """Replace undrawable characters with stand-ins the loaded fonts cover.

    Applied once to the whole document, before parsing, so every renderer
    (RTL and LTR, headings, tables, code blocks) sees the same text. Safe to
    apply twice: substitutes are themselves never keys of the table.
    """
    return text.translate(state.glyph_translation) if state.glyph_translation else text


def _bidi_display(s):
    """get_display, but only when ``s`` actually has RTL characters.

    Forcing ``base_dir='R'`` (see ``shape_rtl``'s docstring) is necessary
    for correct ordering whenever RTL text is present, but doing it to a
    string with *no* RTL characters at all backfires: with nothing to anchor
    the forced RTL paragraph level, python-bidi's mirroring pass swaps
    parentheses it shouldn't (`"(SRS)"` -> `"(SRS ("`). Such strings need no
    reordering anyway -- right-alignment at the page-layout level already
    positions them correctly.
    """
    return str(get_display(s, base_dir='R')) if HAS_SHAPER and is_rtl(s) else s


def shape_rtl(text):
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
    if not HAS_SHAPER or not text:
        return text
    return _bidi_display(_reshaper.reshape(text))


def shape_rtl_lines(pdf, text, max_width, marker=""):
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
    if not HAS_SHAPER or not text:
        return [full_text]
    words = _reshaper.reshape(full_text).split(' ')
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
