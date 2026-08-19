"""The page itself: the fpdf2 subclass, its palette and its metrics.

Everything that draws a Markdown block draws into a :class:`PDF`, and reads
the colours and sizes here to do it, so they live together rather than being
scattered across the renderers that use them.
"""

from __future__ import annotations

import sys

from fpdf import FPDF

from pytoolbox.mdpdf import fonts, shaping, state

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
CODE_SIZE   = 5.5
CODE_LH     = 3.2
TABLE_SIZE  = 7
TABLE_ROW_H = 6
LINE_H_MULT = 1.8     # line-height multiplier for body text
MAX_CODE_COLS = 220    # truncate code lines beyond this


class PDF(FPDF):
    def __init__(self, title="", **kw):
        super().__init__(**kw)
        self._doc_title = title
        faces = fonts.find_dejavu_faces()
        self.add_font(fonts.FONT_SANS, "",  str(faces["DejaVuSans.ttf"]))
        self.add_font(fonts.FONT_SANS, "B", str(faces["DejaVuSans-Bold.ttf"]))
        self.add_font(fonts.FONT_SANS, "I", str(faces["DejaVuSerif.ttf"]))
        self.add_font(fonts.FONT_MONO, "",  str(faces["DejaVuSansMono.ttf"]))
        self.add_font(fonts.FONT_MONO, "B", str(faces["DejaVuSansMono-Bold.ttf"]))

        # Set by convert() once the document's text is known; see _use_rtl_layout.
        self.doc_is_rtl = False

        fa_reg, fa_bold = fonts.find_persian_font()
        self.has_persian = fa_reg is not None
        if self.has_persian:
            self.add_font(fonts.FONT_FA, "",  str(fa_reg))
            self.add_font(fonts.FONT_FA, "B", str(fa_bold))

        self._register_fallback_fonts()

        if not shaping.HAS_SHAPER:
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
        ``shaping.substitute_glyphs``, so the module-level translation table is
        rebuilt here from their combined coverage.
        """
        fallbacks = [fonts.FONT_SANS]
        for idx, path in enumerate(fonts.find_fallback_fonts()):
            family = f"{fonts.FONT_FALLBACK}{idx}"
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
        state.glyph_translation = shaping.build_glyph_translation(covered)

    def header(self):
        if self.page_no() > 1 and self._doc_title:
            title = self._doc_title
            self.set_text_color(140, 140, 140)
            if shaping.is_rtl(title) and self.has_persian:
                self.set_font(fonts.FONT_FA, "", 8)
                self.cell(0, 6, shaping.shape_rtl(title), align="R")
            else:
                self.set_font(fonts.FONT_SANS, "I", 8)
                self.cell(0, 6, title, align="R")
            self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font(fonts.FONT_SANS, "I", 8)
        self.set_text_color(140, 140, 140)
        self.cell(0, 10, f"Page {self.page_no()}/{{nb}}", align="C")




def body_lh(pdf):
    return pdf.font_size * LINE_H_MULT


def ensure_space(pdf, needed_mm):
    if pdf.get_y() + needed_mm > pdf.h - pdf.b_margin - 5:
        pdf.add_page()
