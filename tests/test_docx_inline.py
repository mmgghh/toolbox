"""Tests for parsing the inline content of a paragraph."""

from __future__ import annotations

from pytoolbox.docx.inline import CommentMark, FootnoteMark, ImageRef, Math, Run, parse_inline
from pytoolbox.docx.package import open_docx
from tests.docx_fixtures import build_docx, commented, mrun, omath, para, run


def inline_of(tmp_path, paragraph_xml, parts=None):
    """Parse the first paragraph's inline stream, through a real package."""
    pkg = open_docx(build_docx(tmp_path / "a.docx", paragraph_xml, parts=parts))
    body = list(pkg.document)[0]
    return parse_inline(list(body)[0], pkg)


def texts(items):
    return [i.text for i in items if isinstance(i, Run)]


def test_plain_text_becomes_one_run(tmp_path):
    items = inline_of(tmp_path, para("hello"))
    assert texts(items) == ["hello"]


def test_an_equation_becomes_a_maths_item(tmp_path):
    items = inline_of(tmp_path, para(runs=omath(mrun("E = mc"))))
    assert [i.latex for i in items if isinstance(i, Math)] == ["E = mc"]


def test_character_formatting_is_captured(tmp_path):
    items = inline_of(tmp_path, para(runs=run("bold", bold=True) + run("it", italic=True)))
    assert [(i.text, i.bold, i.italic) for i in items] == [("bold", True, False), ("it", False, True)]


def test_strikethrough_is_captured(tmp_path):
    items = inline_of(tmp_path, para(runs=run("gone", strike=True)))
    assert items[0].strike is True


def test_a_comment_range_end_becomes_a_mark_after_the_text(tmp_path):
    items = inline_of(tmp_path, para(runs=commented("3", run("offline mode"))))
    assert texts(items) == ["offline mode"]
    marks = [i for i in items if isinstance(i, CommentMark)]
    assert [m.comment_id for m in marks] == ["3"]
    assert items.index(marks[0]) > 0, "the marker must follow the text it annotates"


def test_deleted_text_is_dropped(tmp_path):
    body = run("kept ") + '<w:del w:id="1" w:author="a"><w:r><w:delText>gone</w:delText></w:r></w:del>'
    items = inline_of(tmp_path, para(runs=body))
    assert texts(items) == ["kept "]


def test_inserted_text_is_kept(tmp_path):
    body = run("a ") + f'<w:ins w:id="1" w:author="a">{run("new")}</w:ins>'
    items = inline_of(tmp_path, para(runs=body))
    assert texts(items) == ["a new"]


def test_moved_text_is_kept_once(tmp_path):
    body = f'<w:moveFrom w:id="1">{run("old spot")}</w:moveFrom><w:moveTo w:id="2">{run("new spot")}</w:moveTo>'
    items = inline_of(tmp_path, para(runs=body))
    assert texts(items) == ["new spot"]


def test_a_hyperlink_carries_its_target(tmp_path):
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId9" Target="https://example.com" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"/>'
        "</Relationships>"
    )
    body = f'<w:hyperlink r:id="rId9">{run("the site")}</w:hyperlink>'
    items = inline_of(tmp_path, para(runs=body), parts={"word/_rels/document.xml.rels": rels})
    assert items[0].text == "the site"
    assert items[0].link == "https://example.com"


def test_a_footnote_reference_becomes_a_mark(tmp_path):
    body = run("claim") + '<w:r><w:footnoteReference w:id="2"/></w:r>'
    items = inline_of(tmp_path, para(runs=body))
    assert [i.note_id for i in items if isinstance(i, FootnoteMark)] == ["2"]


def test_a_drawing_becomes_an_image_reference(tmp_path):
    rels = (
        '<?xml version="1.0"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId5" Target="media/image1.png" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/>'
        "</Relationships>"
    )
    body = (
        "<w:r><w:drawing><wp:inline>"
        '<wp:docPr id="1" name="Picture 1" descr="a chart"/>'
        '<a:graphic><a:graphicData><pic:pic><pic:blipFill><a:blip r:embed="rId5"/>'
        "</pic:blipFill></pic:pic></a:graphicData></a:graphic>"
        "</wp:inline></w:drawing></w:r>"
    )
    items = inline_of(tmp_path, para(runs=body), parts={"word/_rels/document.xml.rels": rels})
    images = [i for i in items if isinstance(i, ImageRef)]
    assert len(images) == 1
    assert images[0].alt == "a chart"
    assert images[0].part_name == "word/media/image1.png"


def test_a_tab_becomes_a_space(tmp_path):
    body = run("a") + "<w:r><w:tab/></w:r>" + run("b")
    items = inline_of(tmp_path, para(runs=body))
    assert "".join(texts(items)) == "a b"


def test_a_line_break_becomes_a_newline(tmp_path):
    body = run("a") + "<w:r><w:br/></w:r>" + run("b")
    items = inline_of(tmp_path, para(runs=body))
    assert "".join(texts(items)) == "a\nb"


def test_a_wingdings_tick_becomes_a_ticked_box(tmp_path):
    body = run("done ") + '<w:r><w:sym w:font="Wingdings" w:char="F0FE"/></w:r>'
    items = inline_of(tmp_path, para(runs=body))
    assert "".join(texts(items)) == "done ☑"


def test_a_symbol_font_character_becomes_its_greek_letter(tmp_path):
    body = '<w:r><w:sym w:font="Symbol" w:char="F062"/></w:r>'
    items = inline_of(tmp_path, para(runs=body))
    assert "".join(texts(items)) == "β"


def test_a_symbol_keeps_the_formatting_of_its_run(tmp_path):
    body = '<w:r><w:rPr><w:b/></w:rPr><w:sym w:font="Wingdings" w:char="F0FC"/></w:r>'
    items = inline_of(tmp_path, para(runs=body))
    assert [(i.text, i.bold) for i in items] == [("✓", True)]


def test_an_unknown_symbol_font_falls_back_to_the_plain_character(tmp_path):
    body = '<w:r><w:sym w:font="Marlett" w:char="F061"/></w:r>'
    items = inline_of(tmp_path, para(runs=body))
    assert "".join(texts(items)) == "a"


def test_runs_word_split_are_joined_back_into_one(tmp_path):
    body = run("one ") + run("sentence, ") + run("three runs")
    items = inline_of(tmp_path, para(runs=body))
    assert texts(items) == ["one sentence, three runs"]


def test_runs_are_only_joined_when_the_formatting_matches(tmp_path):
    body = run("plain ") + run("bold", bold=True) + run("more", bold=True) + run(" plain")
    items = inline_of(tmp_path, para(runs=body))
    assert texts(items) == ["plain ", "boldmore", " plain"]


def test_an_empty_run_is_dropped(tmp_path):
    body = run("a") + '<w:r><w:t></w:t></w:r>' + run("b")
    items = inline_of(tmp_path, para(runs=body))
    assert texts(items) == ["ab"]


def test_a_mark_between_runs_keeps_them_apart(tmp_path):
    body = commented("7", run("first")) + run("second")
    items = inline_of(tmp_path, para(runs=body))
    assert texts(items) == ["first", "second"]


def test_a_hyperlink_split_across_runs_becomes_one_link(tmp_path):
    """Word shreds a table of contents entry into a run per word."""
    inner = run("Chapter") + run(" 1 ") + run("Aims")
    body = f'<w:hyperlink w:anchor="_Toc1">{inner}</w:hyperlink>'
    items = inline_of(tmp_path, para(runs=body))
    assert [(i.text, i.link) for i in items] == [("Chapter 1 Aims", "#_Toc1")]
