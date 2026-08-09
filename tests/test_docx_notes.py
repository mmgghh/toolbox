"""Tests for reading footnotes and endnotes."""

from __future__ import annotations

from pytoolbox.docx.inline import Run
from pytoolbox.docx.notes import load_notes
from pytoolbox.docx.package import open_docx
from tests.docx_fixtures import build_docx, para

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def footnotes_part(*entries):
    return f'<?xml version="1.0"?><w:footnotes {W}>{"".join(entries)}</w:footnotes>'


def footnote(nid, text, note_type=None):
    type_attr = f' w:type="{note_type}"' if note_type else ""
    return (
        f'<w:footnote w:id="{nid}"{type_attr}>'
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:footnote>"
    )


def load(tmp_path, parts=None):
    return load_notes(open_docx(build_docx(tmp_path / "a.docx", para("x"), parts=parts)))


def plain(paragraphs):
    return "".join(item.text for p in paragraphs for item in p if isinstance(item, Run))


def test_a_document_without_notes_yields_none(tmp_path):
    assert load(tmp_path) == {}


def test_a_footnote_is_read_by_id(tmp_path):
    notes = load(tmp_path, {"word/footnotes.xml": footnotes_part(footnote("2", "the source"))})
    assert plain(notes["2"]) == "the source"


def test_the_separator_pseudo_notes_are_skipped(tmp_path):
    part = footnotes_part(
        footnote("-1", "sep", note_type="separator"),
        footnote("0", "cont", note_type="continuationSeparator"),
        footnote("1", "real"),
    )
    notes = load(tmp_path, {"word/footnotes.xml": part})
    assert list(notes) == ["1"]


def test_endnotes_are_read_too(tmp_path):
    endnote = '<w:endnote w:id="3"><w:p><w:r><w:t>at the end</w:t></w:r></w:p></w:endnote>'
    part = f'<?xml version="1.0"?><w:endnotes {W}>{endnote}</w:endnotes>'
    notes = load(tmp_path, {"word/endnotes.xml": part})
    assert plain(notes["3"]) == "at the end"
