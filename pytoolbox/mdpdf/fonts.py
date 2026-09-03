"""Finding the font files the renderer draws with.

fpdf2 needs a real file on disk for every face, and where those live differs
per platform — so the DejaVu family, a Persian face, and the symbol fonts used
as per-glyph fallbacks are each searched for here rather than assumed.
"""

from __future__ import annotations

from pathlib import Path

from pytoolbox.core import console, paths
from pytoolbox.mdpdf import state

# ── Font search paths ───────────────────────────────────────────────
# paths.font_dirs() covers Linux, macOS, Windows and Termux ($PREFIX/share/fonts
# and ~/.termux/fonts, which have no /usr/share equivalent on Android).
FONT_DIRS = paths.font_dirs()

FONT_PERSIAN_DIRS = [
    Path.home() / ".config/Typora/themes/middle-east",
    Path("/usr/share/fonts/truetype/vazir"),
    *FONT_DIRS,
]

FONT_SANS = "DejaVu"
FONT_MONO = "DejaVuMono"
FONT_FA   = "Vazir"
#: Family prefix for the extra faces registered as per-glyph fallbacks.
FONT_FALLBACK = "Fallback"

#: Symbol/emoji faces tried, in order, as a last-resort fallback for glyphs
#: neither DejaVu nor the Persian face can draw (✅ ❌ 💻 …). Monochrome
#: outline fonts only: fpdf2 draws ``glyf``/``CFF`` outlines, so a colour
#: emoji font (NotoColorEmoji's CBDT bitmaps, Apple's sbix) would contribute
#: a cmap entry and then render nothing -- see has_outlines.
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


#: The five faces the renderer needs, and the DejaVu file that provides each.
_REQUIRED_FACES = (
    "DejaVuSans.ttf",
    "DejaVuSans-Bold.ttf",
    "DejaVuSerif.ttf",
    "DejaVuSansMono.ttf",
    "DejaVuSansMono-Bold.ttf",
)


def find_dejavu_faces() -> dict[str, Path]:
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
        raise console.fail(
            "DejaVu fonts not found. Install them:\n"
            "  Debian/Ubuntu : sudo apt-get install fonts-dejavu-core\n"
            "  Fedora/RHEL   : sudo dnf install dejavu-sans-fonts dejavu-sans-mono-fonts\n"
            "  Arch          : sudo pacman -S ttf-dejavu\n"
            "  Termux        : pkg install fontconfig-utils ttf-dejavu\n"
            "  macOS (brew)  : brew install --cask font-dejavu\n"
            "Or point pymd2pdf at a font directory with --font-dir."
        )
    # Fall back to the regular face for any variant that is missing, so a
    # partial install degrades to plain text instead of crashing.
    for name in _REQUIRED_FACES:
        found.setdefault(name, found["DejaVuSans.ttf"])
    return found


def find_persian_font():
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


def has_outlines(path):
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


def find_fallback_fonts():
    """Paths of the extra faces to register as per-glyph fallbacks.

    ``--fallback-font`` entries come first (an explicit choice wins), then the
    first usable built-in symbol candidate. Unusable files are reported rather
    than silently ignored, since the user asked for them by name.
    """
    found = []
    for path in state.extra_fallback_fonts:
        if not has_outlines(path):
            console.warn(
                f"ignoring --fallback-font '{path}': not a font with drawable "
                "outlines (colour-bitmap emoji fonts are not supported)."
            )
            continue
        found.append(path)
    for name in _SYMBOL_FONTS:
        match = paths.find_font(name)
        if match is not None and has_outlines(match):
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
