"""Tests for reading comments and their reply threading."""

from __future__ import annotations

from pytoolbox.docx.comments import load_comments
from pytoolbox.docx.package import open_docx
from tests.docx_fixtures import build_docx, comment, comments, para

W15 = 'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"'
W14 = 'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def comment_with_para_id(cid, author, para_id, text):
    """A comment whose last paragraph carries the w14:paraId threading uses."""
    return (
        f'<w:comment w:id="{cid}" w:author="{author}" w:date="2026-03-14T10:00:00Z">'
        f'<w:p {W14} w14:paraId="{para_id}"><w:r><w:t>{text}</w:t></w:r></w:p>'
        f"</w:comment>"
    )


def comments_ex(*entries):
    return f'<?xml version="1.0"?><w:commentsEx {W} {W15}>{"".join(entries)}</w:commentsEx>'


def comment_ex(para_id, parent=None, done="0"):
    parent_attr = f' w15:paraIdParent="{parent}"' if parent else ""
    return f'<w15:commentEx w15:paraId="{para_id}"{parent_attr} w15:done="{done}"/>'


def load(tmp_path, *parts_pairs):
    parts = dict(parts_pairs)
    return load_comments(open_docx(build_docx(tmp_path / "a.docx", para("x"), parts=parts)))


def test_a_document_without_comments_yields_none(tmp_path):
    assert load(tmp_path) == {}


def test_author_date_and_text_are_read(tmp_path):
    found = load(
        tmp_path,
        ("word/comments.xml", comments(comment("1", "Sara Ahmadi", text="Is this in scope?"))),
    )
    assert list(found) == ["1"]
    entry = found["1"]
    assert entry.author == "Sara Ahmadi"
    assert entry.date == "2026-03-14"
    assert entry.plain_text() == "Is this in scope?"


def test_a_malformed_date_is_kept_verbatim(tmp_path):
    found = load(tmp_path, ("word/comments.xml", comments(comment("1", "A", date="soon", text="x"))))
    assert found["1"].date == "soon"


def test_comments_are_flat_without_the_extended_part(tmp_path):
    found = load(
        tmp_path,
        ("word/comments.xml", comments(comment("1", "A", text="one"), comment("2", "B", text="two"))),
    )
    assert [c.parent_id for c in found.values()] == [None, None]


def test_a_reply_points_at_its_parent(tmp_path):
    found = load(
        tmp_path,
        (
            "word/comments.xml",
            comments(
                comment_with_para_id("1", "Sara", "AAAA1111", "Is this in scope?"),
                comment_with_para_id("2", "Mohammad", "BBBB2222", "Yes, required."),
            ),
        ),
        ("word/commentsExtended.xml", comments_ex(comment_ex("AAAA1111"), comment_ex("BBBB2222", parent="AAAA1111"))),
    )
    assert found["1"].parent_id is None
    assert found["2"].parent_id == "1"


def test_a_resolved_comment_is_marked(tmp_path):
    found = load(
        tmp_path,
        ("word/comments.xml", comments(comment_with_para_id("1", "Sara", "AAAA1111", "done?"))),
        ("word/commentsExtended.xml", comments_ex(comment_ex("AAAA1111", done="1"))),
    )
    assert found["1"].resolved is True


def test_an_unresolved_comment_is_not_marked(tmp_path):
    found = load(
        tmp_path,
        ("word/comments.xml", comments(comment_with_para_id("1", "Sara", "AAAA1111", "open"))),
        ("word/commentsExtended.xml", comments_ex(comment_ex("AAAA1111", done="0"))),
    )
    assert found["1"].resolved is False


def test_a_parent_pointing_nowhere_is_treated_as_top_level(tmp_path):
    found = load(
        tmp_path,
        ("word/comments.xml", comments(comment_with_para_id("1", "Sara", "AAAA1111", "hi"))),
        ("word/commentsExtended.xml", comments_ex(comment_ex("AAAA1111", parent="MISSING9"))),
    )
    assert found["1"].parent_id is None


def test_a_multi_paragraph_comment_keeps_both_paragraphs(tmp_path):
    body = (
        '<w:comment w:id="1" w:author="A" w:date="2026-03-14T10:00:00Z">'
        "<w:p><w:r><w:t>first</w:t></w:r></w:p><w:p><w:r><w:t>second</w:t></w:r></w:p>"
        "</w:comment>"
    )
    found = load(tmp_path, ("word/comments.xml", comments(body)))
    assert found["1"].plain_text() == "first\nsecond"


def test_an_author_with_no_name_falls_back(tmp_path):
    body = '<w:comment w:id="1" w:date="2026-03-14T10:00:00Z"><w:p><w:r><w:t>x</w:t></w:r></w:p></w:comment>'
    found = load(tmp_path, ("word/comments.xml", comments(body)))
    assert found["1"].author == "Unknown"
