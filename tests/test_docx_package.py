"""Tests for the OOXML package reader."""

from __future__ import annotations

import zipfile

import click
import pytest

from pytoolbox.docx.package import open_docx
from tests.docx_fixtures import build_docx, comments, para


def test_reads_the_main_document_part(tmp_path):
    path = build_docx(tmp_path / "a.docx", para("hello"))
    pkg = open_docx(path)
    assert pkg.document.tag.endswith("}document")


def test_optional_parts_are_absent_not_an_error(tmp_path):
    path = build_docx(tmp_path / "a.docx", para("hello"))
    pkg = open_docx(path)
    assert pkg.part("word/comments.xml") is None


def test_optional_parts_are_parsed_when_present(tmp_path):
    path = build_docx(
        tmp_path / "a.docx",
        para("hello"),
        parts={"word/comments.xml": comments()},
    )
    pkg = open_docx(path)
    assert pkg.part("word/comments.xml") is not None


def test_relationships_resolve_to_targets(tmp_path):
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId7" Target="https://example.com" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"/>'
        "</Relationships>"
    )
    path = build_docx(tmp_path / "a.docx", para("hi"), parts={"word/_rels/document.xml.rels": rels})
    pkg = open_docx(path)
    assert pkg.rel_target("rId7") == "https://example.com"


def test_a_file_that_is_not_a_zip_is_rejected(tmp_path):
    path = tmp_path / "notes.docx"
    path.write_text("this is plain text", encoding="utf-8")
    with pytest.raises(click.ClickException, match="not a Word"):
        open_docx(path)


def test_a_legacy_binary_doc_is_named_in_the_error(tmp_path):
    path = tmp_path / "old.doc"
    # The OLE2 compound-file magic that starts every Word 97-2003 document.
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    with pytest.raises(click.ClickException, match="Word 97"):
        open_docx(path)


def test_an_encrypted_package_says_so(tmp_path):
    path = tmp_path / "locked.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("EncryptedPackage", b"\x00\x01")
        z.writestr("EncryptionInfo", b"\x00\x01")
    with pytest.raises(click.ClickException, match="password"):
        open_docx(path)


def test_a_part_declaring_a_doctype_is_refused(tmp_path):
    """ElementTree expands internal entities, so a doctype is never parsed.

    This is the "billion laughs" shape: ten nested entities, each ten times the
    last. No legitimate Word part declares a doctype, so refusing them closes
    both that hole and XXE without pulling in defusedxml.
    """
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz ['
        '<!ENTITY lol "lol">'
        '<!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        '<!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">'
        "]><w:comments>&lol2;</w:comments>"
    )
    path = build_docx(tmp_path / "bomb.docx", para("hi"), parts={"word/comments.xml": bomb})
    pkg = open_docx(path)
    assert pkg.part("word/comments.xml") is None


def test_a_document_part_declaring_a_doctype_is_rejected_outright(tmp_path):
    path = tmp_path / "bomb.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", '<?xml version="1.0"?><!DOCTYPE d []><w:document/>')
    with pytest.raises(click.ClickException, match="not a Word"):
        open_docx(path)


def test_a_zip_without_a_document_part_is_rejected(tmp_path):
    path = tmp_path / "empty.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("hello.txt", "nothing to see")
    with pytest.raises(click.ClickException, match="not a Word"):
        open_docx(path)
