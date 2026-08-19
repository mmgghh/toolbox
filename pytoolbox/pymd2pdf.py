#!/usr/bin/env python3
"""Convert Markdown files to PDF using fpdf2 and DejaVu/Vazir fonts.

Exposes the ``pymd2pdf`` console script (see ``pymd2pdf --help``).

Supports: headings, bold, italic, strikethrough, links, inline code, code
blocks, tables, bullet/numbered/task lists, blockquotes, horizontal rules,
nested lists, images and Mermaid diagrams. Persian/Arabic text is shaped and
rendered right-to-left when a Vazir/Vazirmatn face and the RTL extras are
present.
"""

from __future__ import annotations

import base64
import io
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

import click
import requests
from fpdf import FPDF
from fpdf.svg import Percent, SVGObject
from PIL import Image as PILImage

from pytoolbox.core import paths
from pytoolbox.core.options import CONTEXT_SETTINGS, version_option

try:
    import arabic_reshaper
    from bidi.algorithm import get_display
    _HAS_SHAPER = True
    # The default configuration deletes Harakat (تشکیل/اعراب: فتحه، کسره، ضمه،
    # تنوین, ...) before shaping, so e.g. "نُه" would reshape as "نه". Keep
    # them; get_display (below) positions combining marks correctly on its own.
    _reshaper = arabic_reshaper.ArabicReshaper(configuration={"delete_harakat": False})
except ImportError:
    _HAS_SHAPER = False

# mermaid-cli (`mmdc`), if installed, renders Mermaid diagrams locally and
# offline. Otherwise diagrams fall back to the mermaid.ink web API, and
# finally to showing the raw source as a code block.
_HAS_MMDC = shutil.which("mmdc") is not None
_mermaid_net_warned = False

#: Set by the CLI: when true, nothing reaches out to the network (no remote
#: images, no mermaid.ink). Sensible default for offline/metered devices.
_offline = False

# ── Font search paths ───────────────────────────────────────────────
# paths.font_dirs() covers Linux, macOS, Windows and Termux ($PREFIX/share/fonts
# and ~/.termux/fonts, which have no /usr/share equivalent on Android).
FONT_DIRS = paths.font_dirs()

FONT_PERSIAN_DIRS = [
    Path.home() / ".config/Typora/themes/middle-east",
    Path("/usr/share/fonts/truetype/vazir"),
    *FONT_DIRS,
]

#: Page geometry presets accepted by ``--page-size``.
PAGE_SIZES = ("a3", "a4", "a5", "letter", "legal")

FONT_SANS = "DejaVu"
FONT_MONO = "DejaVuMono"
FONT_FA   = "Vazir"
#: Family prefix for the extra faces registered as per-glyph fallbacks.
FONT_FALLBACK = "Fallback"

#: Symbol/emoji faces tried, in order, as a last-resort fallback for glyphs
#: neither DejaVu nor the Persian face can draw (✅ ❌ 💻 …). Monochrome
#: outline fonts only: fpdf2 draws ``glyf``/``CFF`` outlines, so a colour
#: emoji font (NotoColorEmoji's CBDT bitmaps, Apple's sbix) would contribute
#: a cmap entry and then render nothing -- see _has_outlines.
_SYMBOL_FONTS = (
    "Symbola.ttf",
    "Symbola_hint.ttf",
    "NotoSansSymbols2-Regular.ttf",
    "NotoSansSymbols-Regular.ttf",
    "NotoEmoji-Regular.ttf",
    "OpenSansEmoji.ttf",
    "seguisym.ttf",        # Windows: Segoe UI Symbol
    "Apple Symbols.ttf",   # macOS
)

#: Set by the CLI from ``--fallback-font``: extra faces to try before the
#: built-in symbol candidates.
_extra_fallback_fonts: tuple[Path, ...] = ()

# Characters in the Arabic/Persian Unicode blocks (including presentation forms).
_RTL_RE = re.compile(r'[؀-ۿݐ-ݿࢠ-ࣿﭐ-﷿ﹰ-﻿]')

# A standalone Markdown image line: ![alt](src "optional title")
_IMG_RE = re.compile(r'^!\[([^\]]*)\]\(\s*(\S+?)(?:\s+["\'][^"\']*["\'])?\s*\)\s*$')


def _is_rtl(text):
    return bool(text) and bool(_RTL_RE.search(text))


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
# _build_glyph_translation): with DejaVu and a symbol font registered as
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

#: str.translate table built by PDF.__init__ once the loaded faces (and hence
#: the set of drawable code points) are known.
_glyph_translation: dict[int, str] = {}


def _build_glyph_translation(covered) -> dict[int, str]:
    """Map code points to stand-ins, given the code points the fonts can draw."""
    table: dict[int, str] = {ord(c): '' for c in _VARIATION_SELECTORS}
    table.update({ord(src): dst for src, dst in _COLOUR_STATUS_SUBSTITUTES.items()})
    table.update({
        ord(src): dst
        for src, dst in _GLYPH_SUBSTITUTES.items()
        if ord(src) not in covered
    })
    return table


def _substitute_glyphs(text):
    """Replace undrawable characters with stand-ins the loaded fonts cover.

    Applied once to the whole document, before parsing, so every renderer
    (RTL and LTR, headings, tables, code blocks) sees the same text. Safe to
    apply twice: substitutes are themselves never keys of the table.
    """
    return text.translate(_glyph_translation) if _glyph_translation else text


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
    return _bidi_display(_reshaper.reshape(text))


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
CLR_LINK          = (20, 80, 180)
CLR_STRIKE        = (140, 140, 140)
CLR_QUOTE_BAR     = (170, 190, 220)
CLR_QUOTE_FG      = (90, 90, 90)

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

#: The five faces the renderer needs, and the DejaVu file that provides each.
_REQUIRED_FACES = (
    "DejaVuSans.ttf",
    "DejaVuSans-Bold.ttf",
    "DejaVuSerif.ttf",
    "DejaVuSansMono.ttf",
    "DejaVuSansMono-Bold.ttf",
)


def _find_dejavu_faces() -> dict[str, Path]:
    """Locate the DejaVu faces, searching each font directory one level deep.

    Returns a name -> path map. Faces may legitimately come from different
    directories (Termux, for instance, splits the mono and sans packages).
    """
    found: dict[str, Path] = {}
    for name in _REQUIRED_FACES:
        match = paths.find_font(name)
        if match is not None:
            found[name] = match
    if "DejaVuSans.ttf" not in found:
        print(
            "ERROR: DejaVu fonts not found. Install them:\n"
            "  Debian/Ubuntu : sudo apt-get install fonts-dejavu-core\n"
            "  Fedora/RHEL   : sudo dnf install dejavu-sans-fonts dejavu-sans-mono-fonts\n"
            "  Arch          : sudo pacman -S ttf-dejavu\n"
            "  Termux        : pkg install fontconfig-utils ttf-dejavu\n"
            "  macOS (brew)  : brew install --cask font-dejavu\n"
            "Or point pymd2pdf at a font directory with --font-dir.",
            file=sys.stderr,
        )
        sys.exit(1)
    # Fall back to the regular face for any variant that is missing, so a
    # partial install degrades to plain text instead of crashing.
    for name in _REQUIRED_FACES:
        found.setdefault(name, found["DejaVuSans.ttf"])
    return found


def _find_persian_font():
    """Return (regular_path, bold_path) for the best available Persian face.

    Prefers Vazirmatn (Vazir's successor, broader Unicode coverage) over
    base Vazir. Whatever is found only has to cover the Arabic script:
    symbols and Latin it lacks come from the fallback faces registered by
    ``PDF.__init__``. Returns (None, None) if no family is installed.
    """
    candidates = (
        ("Vazirmatn-Regular.ttf", "Vazirmatn-Bold.ttf"),
        ("Vazirmatn.ttf",         "Vazirmatn-Bold.ttf"),
        ("Vazir.ttf",             "Vazir-Bold.ttf"),
        # Noto ships in most distro font packages and on Termux, so it is a
        # reasonable last resort when no Vazir family is installed.
        ("NotoNaskhArabic-Regular.ttf", "NotoNaskhArabic-Bold.ttf"),
    )
    for d in FONT_PERSIAN_DIRS:
        for reg_name, bold_name in candidates:
            reg = d / reg_name
            if reg.is_file():
                bold = d / bold_name
                return reg, (bold if bold.is_file() else reg)

    for reg_name, bold_name in candidates:
        reg = paths.find_font(reg_name)
        if reg is not None:
            bold = paths.find_font(bold_name)
            return reg, (bold if bold is not None else reg)
    return None, None


def _has_outlines(path):
    """Whether a font file carries drawable outlines (``glyf`` or ``CFF``).

    Colour emoji fonts store their artwork as embedded bitmaps (``CBDT``) or
    Apple ``sbix`` tables, which fpdf2 cannot draw: registering one would
    claim coverage of every emoji and then render blanks, which is worse than
    substituting text. fontTools is already an fpdf2 dependency.
    """
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(str(path), fontNumber=0, lazy=True)
        try:
            return "glyf" in font or "CFF " in font
        finally:
            font.close()
    except Exception:
        return False


def _find_fallback_fonts():
    """Paths of the extra faces to register as per-glyph fallbacks.

    ``--fallback-font`` entries come first (an explicit choice wins), then the
    first usable built-in symbol candidate. Unusable files are reported rather
    than silently ignored, since the user asked for them by name.
    """
    found = []
    for path in _extra_fallback_fonts:
        if not _has_outlines(path):
            print(
                f"WARN: ignoring --fallback-font '{path}': not a font with drawable "
                "outlines (colour-bitmap emoji fonts are not supported).",
                file=sys.stderr,
            )
            continue
        found.append(path)
    for name in _SYMBOL_FONTS:
        match = paths.find_font(name)
        if match is not None and _has_outlines(match):
            found.append(match)
            break
    # An explicitly named font may also be the one auto-detected; loading the
    # same file twice costs a parse and buys nothing.
    seen, unique = set(), []
    for path in found:
        try:
            key = path.resolve()
        except OSError:  # pragma: no cover - unresolvable symlink
            key = path
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


# ═══════════════════════════════════════════════════════════════════
# PDF subclass
# ═══════════════════════════════════════════════════════════════════

class PDF(FPDF):
    def __init__(self, title="", **kw):
        super().__init__(**kw)
        self._doc_title = title
        faces = _find_dejavu_faces()
        self.add_font(FONT_SANS, "",  str(faces["DejaVuSans.ttf"]))
        self.add_font(FONT_SANS, "B", str(faces["DejaVuSans-Bold.ttf"]))
        self.add_font(FONT_SANS, "I", str(faces["DejaVuSerif.ttf"]))
        self.add_font(FONT_MONO, "",  str(faces["DejaVuSansMono.ttf"]))
        self.add_font(FONT_MONO, "B", str(faces["DejaVuSansMono-Bold.ttf"]))

        # Set by convert() once the document's text is known; see _use_rtl_layout.
        self.doc_is_rtl = False

        fa_reg, fa_bold = _find_persian_font()
        self.has_persian = fa_reg is not None
        if self.has_persian:
            self.add_font(FONT_FA, "",  str(fa_reg))
            self.add_font(FONT_FA, "B", str(fa_bold))

        self._register_fallback_fonts()

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

    def set_doc_title(self, title):
        """Set the running-header title, known only after the text is parsed."""
        self._doc_title = title

    def _register_fallback_fonts(self):
        """Register per-glyph fallbacks and rebuild the substitution table.

        fpdf2 draws each character with the current face and, for characters
        that face has no glyph for, looks through the fallback list instead of
        dropping them. DejaVu comes first -- it covers the arrows, maths and
        geometric shapes Vazir lacks in a matching text style -- then a symbol
        face for the pictographs (✅ ❌ 💻) DejaVu itself lacks.
        ``exact_match=False`` lets bold text fall back to a regular-weight
        symbol face rather than losing the glyph over a weight mismatch.

        Whatever the loaded faces still cannot draw is handled as text by
        ``_substitute_glyphs``, so the module-level translation table is
        rebuilt here from their combined coverage.
        """
        global _glyph_translation
        fallbacks = [FONT_SANS]
        for idx, path in enumerate(_find_fallback_fonts()):
            family = f"{FONT_FALLBACK}{idx}"
            try:
                self.add_font(family, "", str(path))
            except Exception as exc:  # pragma: no cover - malformed font file
                print(f"WARN: could not load fallback font '{path}': {exc}", file=sys.stderr)
                continue
            fallbacks.append(family)
        self.set_fallback_fonts(fallbacks, exact_match=False)

        covered: set[int] = set()
        for font in self.fonts.values():
            covered.update(getattr(font, "cmap", ()) or ())
        _glyph_translation = _build_glyph_translation(covered)

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

#: An inline link or image: the label is drawn, the target is not.
_MD_LINK_RE = re.compile(r'!?\[([^\]]*)\]\(([^)]*)\)')


def _strip_links(text):
    """Replace ``[label](url)`` (and ``![alt](src)``) with just its label."""
    return _MD_LINK_RE.sub(r'\1', text)


def _strip_md(text):
    """Remove inline markdown markers, leaving the text that will be drawn."""
    text = _strip_links(text)
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
_WHOLE_BOLD_RE = re.compile(r'^\*\*(.+)\*\*$')


def _body_lh(pdf):
    return pdf.font_size * LINE_H_MULT


def _ensure_space(pdf, needed_mm):
    if pdf.get_y() + needed_mm > pdf.h - pdf.b_margin - 5:
        pdf.add_page()


# Inline spans, longest markers first so ``**`` wins over ``*`` and ``__``
# over ``_``. Links come first because their label may itself contain markers.
_INLINE_RE = re.compile(
    r'(\[[^\]]+\]\([^)\s]+\)'
    r'|`[^`]+`'
    r'|\*\*[^*]+\*\*'
    r'|__[^_]+__'
    r'|~~[^~]+~~'
    r'|\*[^*\s][^*]*\*'
    r')'
)

_LINK_RE = re.compile(r'^\[([^\]]+)\]\(([^)\s]+)\)$')


def _render_rich(pdf, text, base_size=None, base_style=""):
    """Write a line honouring inline code, bold, italic, strikethrough and links.

    Uses pdf.write() throughout so segments wrap at the right margin instead of
    overflowing. Inline code is distinguished by the mono font; links are drawn
    underlined and carry a real PDF link annotation.

    ``base_size`` defaults to BODY_SIZE at *call* time, not import time:
    ``convert`` rebinds BODY_SIZE for --font-size, and a default evaluated at
    import would pin every styled run to the original 10pt while the plain
    text around it scaled.
    """
    if base_size is None:
        base_size = BODY_SIZE
    lh = _body_lh(pdf)

    def reset():
        pdf.set_font(FONT_SANS, base_style, base_size)
        pdf.set_text_color(*CLR_BODY)

    for part in _INLINE_RE.split(text):
        if not part:
            continue
        link_match = _LINK_RE.match(part)
        if link_match:
            label, url = link_match.groups()
            pdf.set_font(FONT_SANS, base_style + "U" if "U" not in base_style else base_style, base_size)
            pdf.set_text_color(*CLR_LINK)
            pdf.write(lh, _strip_md(label), link=url)
            reset()
        elif part.startswith('`') and part.endswith('`'):
            pdf.set_font(FONT_MONO, "", base_size - 1)
            pdf.set_text_color(*CLR_CODE_FG)
            pdf.write(lh, part[1:-1])
            reset()
        elif (part.startswith('**') and part.endswith('**')) or (
            part.startswith('__') and part.endswith('__')
        ):
            pdf.set_font(FONT_SANS, "B", base_size)
            pdf.set_text_color(*CLR_BOLD)
            pdf.write(lh, part[2:-2])
            reset()
        elif part.startswith('~~') and part.endswith('~~'):
            # fpdf2 has no strikethrough style; grey text reads as "struck out"
            # well enough without drawing manual lines under wrapped runs.
            pdf.set_text_color(*CLR_STRIKE)
            pdf.write(lh, part[2:-2])
            reset()
        elif part.startswith('*') and part.endswith('*'):
            pdf.set_font(FONT_SANS, "I", base_size)
            pdf.write(lh, part[1:-1])
            reset()
        else:
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
            if _offline:
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
    if _offline:
        if not _mermaid_net_warned:
            print(
                "WARN: --offline is set and mermaid-cli is unavailable; showing raw "
                "diagram source. Install it with `npm install -g @mermaid-js/mermaid-cli`.",
                file=sys.stderr,
            )
            _mermaid_net_warned = True
        return None
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


#: A cell that is nothing but a single link, which can therefore become a real
#: PDF link annotation on the whole cell rather than plain label text.
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


def _add_table(pdf, headers, rows):
    from fpdf.enums import TableCellFillMode
    from fpdf.fonts import FontFace

    pdf.ln(2)
    n = len(headers)
    page_w = pdf.w - pdf.l_margin - pdf.r_margin
    CELL_PADDING = 1

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
        text = _strip_code_ticks(cell).strip()
        bold_m = _WHOLE_BOLD_RE.match(text)
        inner = bold_m.group(1).strip() if bold_m else text
        link_m = _CELL_LINK_RE.match(inner)
        link = link_m.group(2) if link_m else None

        if link is None and not has_persian:
            # Nothing fpdf2 cannot handle itself: leave the ** markers in
            # place for its markdown parser, having dropped the link syntax
            # it does not understand.
            return _strip_links(text)

        emphasis = ("B" if bold_m else "") + ("U" if link else "")
        # Only an RTL cell reaches here without a link, and its text is shaped
        # and bidi reordered before fpdf2 sees it -- markdown markers around
        # part of a cell can no longer be parsed, and would print literally.
        # Whole-cell bold survives as the FontFace above; the rest is dropped.
        body = link_m.group(1) if link_m else _strip_md(inner)
        if has_persian:
            # Reordering the *whole* cell into visual order and letting fpdf2's
            # plain left-to-right wrapper break it into lines would scatter the
            # paragraph's tail onto the first physical line (see
            # _shape_rtl_lines's docstring). Wrap here instead, in logical
            # order, one bidi-reordered line per line -- using the same style
            # (bold for headings) fpdf2 will actually render the cell in, so
            # our line breaks match its usable width and it doesn't re-wrap
            # (and re-scramble) any of them.
            pdf.set_font(table_font, "B" if (bold_m or is_header) else "", TABLE_SIZE)
            usable_width = col_width - 2 * CELL_PADDING
            shaped = "\n".join(_shape_rtl_lines(pdf, body, usable_width))
        else:
            shaped = body
        return {
            "text": shaped,
            "link": link,
            "style": FontFace(
                emphasis=emphasis or None,
                color=CLR_LINK if link else None,
            ) if emphasis else None,
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

    with _isolated_annotations(pdf), pdf.table(
        col_widths=tuple(col_w),
        text_align=text_align,
        cell_fill_color=CLR_TABLE_ALT,
        cell_fill_mode=TableCellFillMode.EVEN_ROWS,
        first_row_as_headings=True,
        headings_style=headings_style,
        line_height=TABLE_SIZE * 0.55,
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
        bold_m = _WHOLE_BOLD_RE.match(text.strip())
        pdf.set_font(FONT_FA, "B" if bold_m else "", BODY_SIZE)
        # multi_cell reserves its own internal c_margin padding on each side,
        # on top of the cell width we pass it -- our wrap width must match
        # that actual usable text width or lines we judge to "just fit" wrap
        # again inside multi_cell.
        width = pdf.w - pdf.l_margin - pdf.r_margin - 2 * pdf.c_margin
        lh = _body_lh(pdf)
        body = bold_m.group(1) if bold_m else text
        for line in _shape_rtl_lines(pdf, _strip_md(body), width):
            pdf.multi_cell(0, lh, line, align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font(FONT_SANS, "", BODY_SIZE)
        _render_rich(pdf, text)
        pdf.ln(_body_lh(pdf))


def _add_list_item(pdf, prefix, text, indent):
    pdf.set_text_color(*CLR_BODY)
    body = text.strip()
    if _use_rtl_layout(pdf, body):
        bold_m = _WHOLE_BOLD_RE.match(body)
        pdf.set_font(FONT_FA, "B" if bold_m else "", BODY_SIZE)
        width = pdf.w - pdf.l_margin - pdf.r_margin - indent * 2
        lh = _body_lh(pdf)
        # multi_cell reserves its own internal c_margin padding on each side,
        # on top of the cell width we pass it -- wrap using the actual usable
        # text width or lines we judge to "just fit" wrap again inside multi_cell.
        usable_width = width - 2 * pdf.c_margin
        inner = bold_m.group(1) if bold_m else body
        lines = _shape_rtl_lines(pdf, _strip_md(inner), usable_width, marker=prefix.strip())
        for line in lines:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(width, lh, line, align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_x(pdf.l_margin + indent * 2)
        pdf.set_font(FONT_SANS, "", BODY_SIZE)
        pdf.write(_body_lh(pdf), prefix)
        _render_rich(pdf, body)
        pdf.ln(_body_lh(pdf))


def _add_blockquote(pdf, lines):
    """Render consecutive ``> `` lines as an indented, bar-marked quote."""
    if not lines:
        return
    text = " ".join(line.strip() for line in lines if line.strip())
    if not text:
        return
    pdf.ln(1)
    indent = 6
    start_y = pdf.get_y()
    pdf.set_text_color(*CLR_QUOTE_FG)

    if _use_rtl_layout(pdf, text):
        bold_m = _WHOLE_BOLD_RE.match(text.strip())
        pdf.set_font(FONT_FA, "B" if bold_m else "", BODY_SIZE)
        # multi_cell reserves its own internal c_margin padding on each side,
        # on top of the cell width we pass it -- our wrap width must match
        # that actual usable text width or lines we judge to "just fit" wrap
        # again inside multi_cell (see _add_paragraph's docstring).
        width = pdf.w - pdf.l_margin - pdf.r_margin - indent
        usable_width = width - 2 * pdf.c_margin
        body = bold_m.group(1) if bold_m else text
        for line in _shape_rtl_lines(pdf, _strip_md(body), usable_width):
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(width, _body_lh(pdf), line, align="R", new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.set_font(FONT_SANS, "I", BODY_SIZE)
        pdf.set_left_margin(pdf.l_margin + indent)
        pdf.set_x(pdf.l_margin)
        _render_rich(pdf, text, base_style="I")
        pdf.ln(_body_lh(pdf))
        pdf.set_left_margin(pdf.l_margin - indent)

    # Draw the bar last, once the quote's height is known.
    end_y = pdf.get_y()
    pdf.set_draw_color(*CLR_QUOTE_BAR)
    pdf.set_line_width(0.8)
    bar_x = pdf.w - pdf.r_margin - 1 if _use_rtl_layout(pdf, text) else pdf.l_margin + 1
    pdf.line(bar_x, start_y, bar_x, end_y - 1)
    pdf.set_line_width(0.2)
    pdf.set_font(FONT_SANS, "", BODY_SIZE)
    pdf.set_text_color(*CLR_BODY)
    pdf.ln(2)


#: ``- [ ] todo`` / ``- [x] done`` list items.
_TASK_RE = re.compile(r'^\[([ xX])\]\s+(.*)$')


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
    """Render one Markdown file to a PDF.

    ``font_size`` scales body text by rebinding the module-level ``BODY_SIZE``;
    the block renderers read that constant directly, and threading an explicit
    size through every one of them would add a parameter to a dozen functions
    for one rarely-changed knob.
    """
    global BODY_SIZE
    original_body_size = BODY_SIZE
    if font_size:
        BODY_SIZE = font_size

    md_path = Path(md_path)
    # The PDF is built first because loading its faces is what determines
    # which characters can be drawn, and hence which ones _substitute_glyphs
    # has to replace with text stand-ins.
    pdf = PDF(orientation=orientation, unit="mm", format=page_size)
    md_text = _substitute_glyphs(md_path.read_text(encoding="utf-8"))
    lines = md_text.split('\n')
    title = _extract_title(lines) if title_page else ""
    pdf.set_doc_title(title)
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=margin)
    pdf.set_margins(margin, margin, margin)
    pdf.set_title(title or md_path.stem)
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

        # ── blockquote ──────────────────────────────────────────
        if line.lstrip().startswith('>'):
            quote_lines = []
            while i < len(lines) and lines[i].lstrip().startswith('>'):
                quote_lines.append(re.sub(r'^\s*>\s?', '', lines[i]))
                i += 1
            _add_blockquote(pdf, quote_lines)
            continue

        # ── bullet list (including task lists) ──────────────────
        m = re.match(r'^(\s*)[-*+]\s+(.*)', line)
        if m:
            indent = len(m.group(1))
            body = m.group(2)
            task = _TASK_RE.match(body)
            if task:
                marker = "  [x] " if task.group(1).lower() == "x" else "  [ ] "
                _add_list_item(pdf, marker, task.group(2), indent)
            else:
                _add_list_item(pdf, f"  {_bullet_char(indent)} ", body, indent)
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

    try:
        pdf.output(str(pdf_path))
    finally:
        BODY_SIZE = original_body_size
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
    help="Output PDF path. Only valid with a single input file; "
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
    help=f"Body text size in points (default: {BODY_SIZE}).",
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
    """Convert Markdown file(s) to PDF.

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
    status emoji always become ● ◐ ○, since a one-colour PDF would render
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
    global _offline, _extra_fallback_fonts

    if output and len(files) > 1:
        raise click.UsageError("-o/--output can only be used with a single input file.")
    if output and output_dir:
        raise click.UsageError("Use either -o/--output or -d/--output-dir, not both.")

    _offline = offline
    _extra_fallback_fonts = fallback_font
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
