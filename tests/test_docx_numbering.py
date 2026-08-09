"""Tests for the list-numbering lookup."""

from __future__ import annotations

from pytoolbox.docx.numbering import load_numbering
from pytoolbox.docx.package import open_docx
from tests.docx_fixtures import build_docx, para

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def numbering_part(*abstract_levels: str, num_id: str = "1", abstract_id: str = "0") -> str:
    levels = "".join(abstract_levels)
    return (
        f'<?xml version="1.0"?><w:numbering {W}>'
        f'<w:abstractNum w:abstractNumId="{abstract_id}">{levels}</w:abstractNum>'
        f'<w:num w:numId="{num_id}"><w:abstractNumId w:val="{abstract_id}"/></w:num>'
        f"</w:numbering>"
    )


def level(ilvl: str, fmt: str) -> str:
    return f'<w:lvl w:ilvl="{ilvl}"><w:numFmt w:val="{fmt}"/></w:lvl>'


def load(tmp_path, part=None):
    parts = {"word/numbering.xml": part} if part else None
    return load_numbering(open_docx(build_docx(tmp_path / "a.docx", para("x"), parts=parts)))


def test_a_bullet_level_is_unordered(tmp_path):
    numbering = load(tmp_path, numbering_part(level("0", "bullet")))
    assert numbering.is_ordered("1", 0) is False


def test_a_decimal_level_is_ordered(tmp_path):
    numbering = load(tmp_path, numbering_part(level("0", "decimal")))
    assert numbering.is_ordered("1", 0) is True


def test_letters_and_roman_numerals_are_ordered(tmp_path):
    numbering = load(tmp_path, numbering_part(level("0", "lowerLetter"), level("1", "upperRoman")))
    assert numbering.is_ordered("1", 0) is True
    assert numbering.is_ordered("1", 1) is True


def test_each_level_is_looked_up_independently(tmp_path):
    numbering = load(tmp_path, numbering_part(level("0", "decimal"), level("1", "bullet")))
    assert numbering.is_ordered("1", 0) is True
    assert numbering.is_ordered("1", 1) is False


def test_a_document_without_numbering_defaults_to_bullets(tmp_path):
    numbering = load(tmp_path)
    assert numbering.is_ordered("1", 0) is False


def test_an_unknown_list_id_defaults_to_bullets(tmp_path):
    numbering = load(tmp_path, numbering_part(level("0", "decimal")))
    assert numbering.is_ordered("99", 0) is False


def test_a_level_marked_none_is_not_a_list(tmp_path):
    numbering = load(tmp_path, numbering_part(level("0", "none")))
    assert numbering.is_ordered("1", 0) is False
