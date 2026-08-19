"""Turn Word's ``<w:sym>`` glyphs into ordinary Unicode text.

Word stores a character picked from a symbol font as the font name plus the
glyph's own code point, not as text: a Wingdings tick is
``<w:sym w:font="Wingdings" w:char="F0FE"/>``, with the code point shifted into
the private use area. Nothing in the run's ``w:t`` records it, so a reader that
only looks at ``w:t`` loses every tick, cross and bullet in the document.

The tables below cover the two fonts that carry meaning rather than decoration:
Symbol, whose Greek letters and operators are the usual way older documents
write maths inline, and Wingdings, whose ticked and crossed boxes are how a
Word form says yes and no. Anything else falls back to the plain character at
that code point, which is what the glyph would read as with the symbol font
taken off.
"""

from __future__ import annotations

#: Word shifts symbol-font code points into the private use area, F020..F0FF.
_PUA_START = 0xF000
_PUA_END = 0xF0FF

#: Adobe's Symbol encoding: Greek letters, arrows and mathematical operators.
_SYMBOL = {
    0x22: "∀", 0x24: "∃", 0x27: "∋", 0x2A: "∗", 0x2D: "−", 0x40: "≅",
    0x41: "Α", 0x42: "Β", 0x43: "Χ", 0x44: "Δ", 0x45: "Ε", 0x46: "Φ",
    0x47: "Γ", 0x48: "Η", 0x49: "Ι", 0x4A: "ϑ", 0x4B: "Κ", 0x4C: "Λ",
    0x4D: "Μ", 0x4E: "Ν", 0x4F: "Ο", 0x50: "Π", 0x51: "Θ", 0x52: "Ρ",
    0x53: "Σ", 0x54: "Τ", 0x55: "Υ", 0x56: "ς", 0x57: "Ω", 0x58: "Ξ",
    0x59: "Ψ", 0x5A: "Ζ", 0x5C: "∴", 0x5E: "⊥", 0x60: "‾",
    0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε", 0x66: "φ",
    0x67: "γ", 0x68: "η", 0x69: "ι", 0x6A: "ϕ", 0x6B: "κ", 0x6C: "λ",
    0x6D: "μ", 0x6E: "ν", 0x6F: "ο", 0x70: "π", 0x71: "θ", 0x72: "ρ",
    0x73: "σ", 0x74: "τ", 0x75: "υ", 0x76: "ϖ", 0x77: "ω", 0x78: "ξ",
    0x79: "ψ", 0x7A: "ζ", 0x7E: "∼",
    0xA0: "€", 0xA1: "ϒ", 0xA2: "′", 0xA3: "≤", 0xA4: "⁄", 0xA5: "∞",
    0xA6: "ƒ", 0xA7: "♣", 0xA8: "♦", 0xA9: "♥", 0xAA: "♠", 0xAB: "↔",
    0xAC: "←", 0xAD: "↑", 0xAE: "→", 0xAF: "↓",
    0xB0: "°", 0xB1: "±", 0xB2: "″", 0xB3: "≥", 0xB4: "×", 0xB5: "∝",
    0xB6: "∂", 0xB7: "•", 0xB8: "÷", 0xB9: "≠", 0xBA: "≡", 0xBB: "≈",
    0xBC: "…", 0xBF: "↵",
    0xC0: "ℵ", 0xC1: "ℑ", 0xC2: "ℜ", 0xC3: "℘", 0xC4: "⊗", 0xC5: "⊕",
    0xC6: "∅", 0xC7: "∩", 0xC8: "∪", 0xC9: "⊃", 0xCA: "⊇", 0xCB: "⊄",
    0xCC: "⊂", 0xCD: "⊆", 0xCE: "∈", 0xCF: "∉",
    0xD0: "∠", 0xD1: "∇", 0xD2: "®", 0xD3: "©", 0xD4: "™", 0xD5: "∏",
    0xD6: "√", 0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨", 0xDB: "⇔",
    0xDC: "⇐", 0xDD: "⇑", 0xDE: "⇒", 0xDF: "⇓",
    0xE0: "◊", 0xE1: "⟨", 0xE5: "∑", 0xF1: "⟩", 0xF2: "∫",
}

#: Wingdings, limited to the glyphs that carry meaning: ticks, crosses, the
#: faces Word's autocorrect produces, and the shapes used as list bullets.
_WINGDINGS = {
    0x21: "✏", 0x22: "✂",
    0x4A: "☺", 0x4B: "😐", 0x4C: "☹",
    0x6C: "●", 0x6E: "■", 0x6F: "□",
    0xA7: "▪", 0xA8: "▫",
    0xD8: "➔",
    0xFB: "✗", 0xFC: "✓", 0xFD: "☒", 0xFE: "☑",
}

#: Wingdings 2 keeps its ticks and crosses in one block of its own.
_WINGDINGS_2 = {
    0x50: "✓", 0x51: "✔", 0x52: "✗", 0x53: "✘",
    0x54: "☑", 0x56: "☒",
}

_FONTS = {
    "symbol": _SYMBOL,
    "wingdings": _WINGDINGS,
    "wingdings 2": _WINGDINGS_2,
}


def text_of(font: str, char: str) -> str:
    """The Unicode text for one ``w:sym``, given its ``w:font`` and ``w:char``.

    ``char`` is a hexadecimal code point, usually shifted into the private use
    area. An unreadable one yields the empty string; an unmapped glyph yields
    the plain character at its code point.
    """
    try:
        code = int(char, 16)
    except (TypeError, ValueError):
        return ""
    if _PUA_START <= code <= _PUA_END:
        code -= _PUA_START
    table = _FONTS.get((font or "").strip().lower())
    if table is not None and code in table:
        return table[code]
    return chr(code) if code >= 0x20 else ""
