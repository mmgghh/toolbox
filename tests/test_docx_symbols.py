"""Tests for reading the glyphs Word stores as ``w:sym``."""

from __future__ import annotations

import pytest

from pytoolbox.docx.symbols import text_of


@pytest.mark.parametrize(
    ("font", "char", "expected"),
    [
        ("Wingdings", "F0FE", "☑"),
        ("Wingdings", "F0FD", "☒"),
        ("Wingdings", "F0FC", "✓"),
        ("Wingdings", "F0FB", "✗"),
        ("Wingdings", "F06C", "●"),
        ("Wingdings 2", "F050", "✓"),
        ("Symbol", "F062", "β"),
        ("Symbol", "F0D6", "√"),
        ("Symbol", "F0B7", "•"),
    ],
)
def test_known_fonts_map_to_their_unicode_glyph(font, char, expected):
    assert text_of(font, char) == expected


def test_the_font_name_is_matched_regardless_of_case_and_padding(tmp_path):
    assert text_of(" WINGDINGS ", "F0FE") == "☑"


def test_a_code_point_written_without_the_private_use_shift_still_maps():
    """Either way ``w:char`` is a code point in the font, not in Unicode."""
    assert text_of("Symbol", "00B5") == text_of("Symbol", "F0B5") == "∝"


def test_an_unmapped_glyph_falls_back_to_the_plain_character():
    assert text_of("Wingdings", "F041") == "A"


def test_an_unknown_font_falls_back_to_the_plain_character():
    assert text_of("Marlett", "F061") == "a"


def test_an_unreadable_code_point_yields_nothing():
    assert text_of("Wingdings", "") == ""
    assert text_of("Wingdings", "not-hex") == ""


def test_a_control_code_point_yields_nothing():
    assert text_of("Nowhere", "F001") == ""
