"""Tests for walking document.xml into blocks."""

from __future__ import annotations

from pytoolbox.docx.document import Heading, ListItem, Paragraph, Table, parse_document
from pytoolbox.docx.inline import Run
from pytoolbox.docx.numbering import load_numbering
from pytoolbox.docx.package import open_docx
from pytoolbox.docx.styles import load_styles
from tests.docx_fixtures import build_docx, num_pr, para, style_def, styles_part

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def blocks_of(tmp_path, body, parts=None):
    pkg = open_docx(build_docx(tmp_path / "a.docx", body, parts=parts))
    return parse_document(pkg, load_numbering(pkg), load_styles(pkg))


def text_of(block):
    return "".join(item.text for item in block.items if isinstance(item, Run))


def numbering_part(fmt="bullet", num_id="1"):
    return (
        f'<?xml version="1.0"?><w:numbering {W}>'
        f'<w:abstractNum w:abstractNumId="0">'
        f'<w:lvl w:ilvl="0"><w:numFmt w:val="{fmt}"/></w:lvl>'
        f'<w:lvl w:ilvl="1"><w:numFmt w:val="{fmt}"/></w:lvl>'
        f"</w:abstractNum>"
        f'<w:num w:numId="{num_id}"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    )


def list_para(text, ilvl="0", num_id="1"):
    return (
        f"<w:p><w:pPr><w:numPr>"
        f'<w:ilvl w:val="{ilvl}"/><w:numId w:val="{num_id}"/>'
        f"</w:numPr></w:pPr><w:r><w:t>{text}</w:t></w:r></w:p>"
    )


def test_a_plain_paragraph_becomes_a_paragraph(tmp_path):
    blocks = blocks_of(tmp_path, para("hello"))
    assert isinstance(blocks[0], Paragraph)
    assert text_of(blocks[0]) == "hello"


def test_a_heading_style_sets_the_level(tmp_path):
    blocks = blocks_of(tmp_path, para("Title", style="Heading1") + para("Sub", style="Heading3"))
    assert [(type(b), b.level) for b in blocks] == [(Heading, 1), (Heading, 3)]


def test_heading_levels_stop_at_six(tmp_path):
    blocks = blocks_of(tmp_path, para("deep", style="Heading9"))
    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 6


def test_a_style_based_on_a_heading_is_a_heading(tmp_path):
    """House styles are usually built on a built-in heading rather than reused."""
    parts = {"word/styles.xml": styles_part(style_def("ChapterTitle", based_on="Heading2"))}
    blocks = blocks_of(tmp_path, para("Scope", style="ChapterTitle"), parts)
    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 2


def test_a_localised_style_name_is_not_mistaken_for_a_heading(tmp_path):
    """Only the English style *id* is a heading; ids are language-independent."""
    blocks = blocks_of(tmp_path, para("Titulo", style="Ttulo1"))
    assert isinstance(blocks[0], Paragraph)


def test_an_outline_level_makes_a_heading_without_a_heading_style(tmp_path):
    body = '<w:p><w:pPr><w:outlineLvl w:val="1"/></w:pPr><w:r><w:t>Outlined</w:t></w:r></w:p>'
    blocks = blocks_of(tmp_path, body)
    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 2


def test_a_digit_lookalike_outline_level_is_ignored_not_crashed(tmp_path):
    """A superscript passes isdigit() but int() cannot parse it; a malformed
    w:val must be ignored, not crash the conversion."""
    body = '<w:p><w:pPr><w:outlineLvl w:val="²"/></w:pPr><w:r><w:t>Odd</w:t></w:r></w:p>'
    blocks = blocks_of(tmp_path, body)
    assert isinstance(blocks[0], Paragraph)


def test_a_bulleted_paragraph_becomes_an_unordered_list_item(tmp_path):
    blocks = blocks_of(tmp_path, list_para("one"), {"word/numbering.xml": numbering_part("bullet")})
    assert isinstance(blocks[0], ListItem)
    assert blocks[0].ordered is False
    assert blocks[0].level == 0


def test_a_numbered_paragraph_becomes_an_ordered_list_item(tmp_path):
    blocks = blocks_of(tmp_path, list_para("one"), {"word/numbering.xml": numbering_part("decimal")})
    assert blocks[0].ordered is True


def test_a_digit_lookalike_ilvl_falls_back_to_level_zero(tmp_path):
    """A superscript passes isdigit() but int() cannot parse it; a malformed
    w:ilvl must fall back to level 0, not crash the conversion."""
    blocks = blocks_of(
        tmp_path, list_para("one", ilvl="²"), {"word/numbering.xml": numbering_part("bullet")}
    )
    assert isinstance(blocks[0], ListItem)
    assert blocks[0].level == 0


def test_list_nesting_depth_is_kept(tmp_path):
    body = list_para("top") + list_para("nested", ilvl="1")
    blocks = blocks_of(tmp_path, body, {"word/numbering.xml": numbering_part("bullet")})
    assert [b.level for b in blocks] == [0, 1]


def test_a_paragraph_styled_as_a_list_becomes_a_list_item(tmp_path):
    """Word omits numPr from the paragraph when its style already carries one."""
    parts = {
        "word/numbering.xml": numbering_part("bullet"),
        "word/styles.xml": styles_part(style_def("ListBullet", num_pr(num_id="1"))),
    }
    blocks = blocks_of(tmp_path, para("one", style="ListBullet"), parts)
    assert isinstance(blocks[0], ListItem)
    assert blocks[0].ordered is False
    assert blocks[0].level == 0


def test_a_digit_lookalike_style_ilvl_falls_back_to_level_zero(tmp_path):
    """A superscript passes isdigit() but int() cannot parse it; a malformed
    style-level w:ilvl must fall back to level 0, not crash the conversion."""
    parts = {
        "word/numbering.xml": numbering_part("bullet"),
        "word/styles.xml": styles_part(style_def("ListBullet", num_pr(num_id="1", ilvl="²"))),
    }
    blocks = blocks_of(tmp_path, para("one", style="ListBullet"), parts)
    assert isinstance(blocks[0], ListItem)
    assert blocks[0].level == 0


def test_a_paragraph_can_opt_out_of_its_style_numbering(tmp_path):
    """``numId="0"`` is how Word says "not a list item after all"."""
    parts = {
        "word/numbering.xml": numbering_part("bullet"),
        "word/styles.xml": styles_part(style_def("ListBullet", num_pr(num_id="1"))),
    }
    body = f'<w:p><w:pPr><w:pStyle w:val="ListBullet"/>{num_pr(num_id="0")}</w:pPr>'
    body += "<w:r><w:t>plain</w:t></w:r></w:p>"
    blocks = blocks_of(tmp_path, body, parts)
    assert isinstance(blocks[0], Paragraph)


def test_a_style_chain_carries_the_numbering_down(tmp_path):
    """A style based on a list style is a list style too."""
    parts = {
        "word/numbering.xml": numbering_part("decimal"),
        "word/styles.xml": styles_part(
            style_def("ListNumber", num_pr(num_id="1")),
            style_def("MyList", based_on="ListNumber"),
        ),
    }
    blocks = blocks_of(tmp_path, para("one", style="MyList"), parts)
    assert isinstance(blocks[0], ListItem)
    assert blocks[0].ordered is True


def test_a_paragraph_outranks_its_style_for_nesting_depth(tmp_path):
    parts = {
        "word/numbering.xml": numbering_part("bullet"),
        "word/styles.xml": styles_part(style_def("ListBullet", num_pr(num_id="1", ilvl="0"))),
    }
    body = f'<w:p><w:pPr><w:pStyle w:val="ListBullet"/>{num_pr(ilvl="1")}</w:pPr>'
    body += "<w:r><w:t>nested</w:t></w:r></w:p>"
    blocks = blocks_of(tmp_path, body, parts)
    assert blocks[0].level == 1


def test_a_table_becomes_rows_of_cells(tmp_path):
    body = (
        "<w:tbl>"
        f"<w:tr><w:tc>{para('Feature')}</w:tc><w:tc>{para('Status')}</w:tc></w:tr>"
        f"<w:tr><w:tc>{para('Offline')}</w:tc><w:tc>{para('Planned')}</w:tc></w:tr>"
        "</w:tbl>"
    )
    blocks = blocks_of(tmp_path, body)
    assert isinstance(blocks[0], Table)
    assert len(blocks[0].rows) == 2
    assert text_of(blocks[0].rows[0][0][0]) == "Feature"
    assert text_of(blocks[0].rows[1][1][0]) == "Planned"


def test_a_cell_can_hold_several_paragraphs(tmp_path):
    body = f"<w:tbl><w:tr><w:tc>{para('one')}{para('two')}</w:tc></w:tr></w:tbl>"
    blocks = blocks_of(tmp_path, body)
    assert [text_of(b) for b in blocks[0].rows[0][0]] == ["one", "two"]


def test_a_header_row_is_flagged(tmp_path):
    body = (
        "<w:tbl>"
        f"<w:tr><w:trPr><w:tblHeader/></w:trPr><w:tc>{para('H')}</w:tc></w:tr>"
        f"<w:tr><w:tc>{para('d')}</w:tc></w:tr>"
        "</w:tbl>"
    )
    blocks = blocks_of(tmp_path, body)
    assert blocks[0].header_rows == 1


def test_a_table_without_a_declared_header_still_reports_one(tmp_path):
    body = f"<w:tbl><w:tr><w:tc>{para('a')}</w:tc></w:tr><w:tr><w:tc>{para('b')}</w:tc></w:tr></w:tbl>"
    blocks = blocks_of(tmp_path, body)
    assert blocks[0].header_rows == 1


def test_list_properties_without_a_num_id_do_not_crash(tmp_path):
    """Word writes a bare numPr when a style supplies the numbering."""
    body = '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/></w:numPr></w:pPr><w:r><w:t>x</w:t></w:r></w:p>'
    blocks = blocks_of(tmp_path, body)
    assert isinstance(blocks[0], ListItem)
    assert blocks[0].ordered is False


def test_blocks_come_out_in_document_order(tmp_path):
    body = para("first", style="Heading1") + para("second") + f"<w:tbl><w:tr><w:tc>{para('c')}</w:tc></w:tr></w:tbl>"
    blocks = blocks_of(tmp_path, body)
    assert [type(b) for b in blocks] == [Heading, Paragraph, Table]


def test_an_empty_paragraph_is_kept_as_a_break(tmp_path):
    blocks = blocks_of(tmp_path, para("a") + "<w:p/>" + para("b"))
    assert len(blocks) == 3
    assert text_of(blocks[1]) == ""


def test_a_style_that_sets_an_outline_level_makes_a_heading(tmp_path):
    """A house style names its own level rather than being based on Heading1."""
    parts = {"word/styles.xml": styles_part(style_def("a0", '<w:outlineLvl w:val="1"/>'))}
    blocks = blocks_of(tmp_path, para("فصل", style="a0"), parts=parts)
    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 2


def test_a_digit_lookalike_style_outline_level_is_ignored_not_crashed(tmp_path):
    """A superscript passes isdigit() but int() cannot parse it; a malformed
    style-level w:val must be ignored, not crash the conversion."""
    parts = {"word/styles.xml": styles_part(style_def("a0", '<w:outlineLvl w:val="²"/>'))}
    blocks = blocks_of(tmp_path, para("x", style="a0"), parts=parts)
    assert isinstance(blocks[0], Paragraph)


def test_an_outline_level_is_inherited_through_basedOn(tmp_path):
    parts = {
        "word/styles.xml": styles_part(
            style_def("Chapter", '<w:outlineLvl w:val="0"/>'),
            style_def("ChapterAlt", based_on="Chapter"),
        )
    }
    blocks = blocks_of(tmp_path, para("t", style="ChapterAlt"), parts=parts)
    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 1


def test_outline_level_nine_is_body_text_not_a_heading(tmp_path):
    """Word writes 9 for "no outline level"; it must not become a heading."""
    parts = {"word/styles.xml": styles_part(style_def("Quote", '<w:outlineLvl w:val="9"/>'))}
    blocks = blocks_of(tmp_path, para("body", style="Quote"), parts=parts)
    assert isinstance(blocks[0], Paragraph)


def test_a_paragraph_outline_level_beats_the_style(tmp_path):
    parts = {"word/styles.xml": styles_part(style_def("Chapter", '<w:outlineLvl w:val="0"/>'))}
    body = (
        '<w:p><w:pPr><w:pStyle w:val="Chapter"/><w:outlineLvl w:val="2"/></w:pPr>'
        "<w:r><w:t>t</w:t></w:r></w:p>"
    )
    blocks = blocks_of(tmp_path, body, parts=parts)
    assert blocks[0].level == 3


def test_a_numbered_heading_is_a_heading_not_a_list_item(tmp_path):
    """Word numbers chapter headings through the style; they stay headings."""
    parts = {
        "word/numbering.xml": numbering_part(fmt="decimal"),
        "word/styles.xml": styles_part(
            style_def("Chapter", num_pr(num_id="1", ilvl="0") + '<w:outlineLvl w:val="0"/>')
        ),
    }
    blocks = blocks_of(tmp_path, para("فصل", style="Chapter"), parts=parts)
    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 1


def test_a_numbered_paragraph_without_an_outline_level_is_still_a_list(tmp_path):
    parts = {
        "word/numbering.xml": numbering_part(),
        "word/styles.xml": styles_part(style_def("ListBullet", num_pr(num_id="1"))),
    }
    blocks = blocks_of(tmp_path, para("point", style="ListBullet"), parts=parts)
    assert isinstance(blocks[0], ListItem)


def _drawing(kind):
    wp = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
    a = "http://schemas.openxmlformats.org/drawingml/2006/main"
    r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    shell = f'<wp:{kind} xmlns:wp="{wp}"><wp:docPr id="1" name="Picture 1"/>'
    blip = f'<a:graphic xmlns:a="{a}"><a:graphicData><a:blip xmlns:r="{r}" r:embed="rId5"/></a:graphicData></a:graphic>'
    return f"<w:drawing>{shell}{blip}</wp:{kind}></w:drawing>"


_RELS = (
    '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId5" Target="media/line.png" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/></Relationships>'
)


def test_a_floating_picture_in_a_heading_is_not_part_of_the_heading(tmp_path):
    # Word designers lay a flourish under a heading by anchoring a picture in
    # it. It decorates the heading and says nothing in it, and left in, it
    # becomes part of the heading's text -- and of its anchor.
    from pytoolbox.docx.inline import ImageRef

    body = (
        f'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r>{_drawing("anchor")}</w:r>'
        "<w:r><w:t>Scope</w:t></w:r></w:p>"
        f'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r>{_drawing("inline")}</w:r>'
        "<w:r><w:t>Icon</w:t></w:r></w:p>"
        f"<w:p><w:r>{_drawing('anchor')}</w:r></w:p>"
    )
    blocks = blocks_of(tmp_path, body, parts={"word/_rels/document.xml.rels": _RELS})

    kinds = [[type(item).__name__ for item in block.items] for block in blocks]
    assert kinds == [["Run"], ["ImageRef", "Run"], ["ImageRef"]]
    assert isinstance(blocks[1].items[0], ImageRef)


# ─────────────────────────────────────────────────────────────────────
# Headings a document never styled
# ─────────────────────────────────────────────────────────────────────


def sized(text, size, bold=True):
    props = ("<w:b/>" if bold else "") + (f'<w:sz w:val="{size}"/>' if size else "")
    return f"<w:p><w:r><w:rPr>{props}</w:rPr><w:t>{text}</w:t></w:r></w:p>"


BODY = "".join(sized("An ordinary sentence of the document body that runs on for a good while. " * 3, 24, bold=False) for _ in range(3))


def kinds(blocks):
    return [(type(block).__name__, getattr(block, "level", None)) for block in blocks]


def test_a_bold_line_set_larger_than_the_body_is_a_heading_by_its_size(tmp_path):
    # A document written without heading styles still shows its structure the
    # one way left: the title is bigger. One level per distinct size, largest
    # first, as for a PDF.
    body = sized("The Title", 40) + sized("A Subtitle", 28) + BODY
    blocks = blocks_of(tmp_path, body)

    assert kinds(blocks) == [("Heading", 1), ("Heading", 2)] + [("Paragraph", None)] * 3


def test_larger_text_that_is_not_bold_is_not_a_heading(tmp_path):
    blocks = blocks_of(tmp_path, sized("Big but plain", 40, bold=False) + BODY)

    assert kinds(blocks)[0] == ("Paragraph", None)


def test_bold_text_at_the_body_size_is_not_a_heading(tmp_path):
    blocks = blocks_of(tmp_path, sized("Chapter four", 24) + BODY)

    assert kinds(blocks)[0] == ("Paragraph", None)


def test_a_long_bold_paragraph_is_a_paragraph_however_large(tmp_path):
    blocks = blocks_of(tmp_path, sized("word " * 40, 40) + BODY)

    assert kinds(blocks)[0] == ("Paragraph", None)


def test_larger_bold_text_in_a_table_cell_is_not_a_heading(tmp_path):
    cell = f"<w:tbl><w:tr><w:tc>{sized('Header', 40)}</w:tc></w:tr></w:tbl>"
    blocks = blocks_of(tmp_path, cell + BODY)

    assert isinstance(blocks[0], Table)
    assert isinstance(blocks[0].rows[0][0][0], Paragraph)


def test_the_body_size_is_the_documents_default_when_runs_do_not_name_one(tmp_path):
    # 26 half-points is 18% over Word's 11pt fallback, but only 8% over this
    # document's own 12pt default -- a bold line at that size is just emphasis.
    defaults = (
        f'<?xml version="1.0"?><w:styles {W}><w:docDefaults><w:rPrDefault><w:rPr>'
        '<w:sz w:val="24"/></w:rPr></w:rPrDefault></w:docDefaults></w:styles>'
    )
    plain = "".join(sized("An ordinary sentence of the body that runs on a while. " * 3, 0, bold=False) for _ in range(3))
    blocks = blocks_of(tmp_path, sized("Emphasis", 26) + sized("Real title", 40) + plain, parts={"word/styles.xml": defaults})

    assert kinds(blocks) == [("Paragraph", None), ("Heading", 1)] + [("Paragraph", None)] * 3


def test_a_size_set_by_the_paragraph_style_counts_as_the_runs_size(tmp_path):
    # Word records size in the style far more often than on the run, so a
    # reader that looks only at the run sees a body of 11pt text everywhere.
    big = '<w:style w:type="paragraph" w:styleId="Big"><w:rPr><w:sz w:val="44"/></w:rPr></w:style>'
    titled = f'<w:p><w:pPr><w:pStyle w:val="Big"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>Annual report</w:t></w:r></w:p>'
    blocks = blocks_of(tmp_path, titled + BODY, parts={"word/styles.xml": styles_part(big)})

    assert kinds(blocks)[0] == ("Heading", 1)


def test_a_title_promoted_by_its_size_does_not_repeat_its_boldness(tmp_path):
    # Bold throughout is what made the line a candidate. Kept on the runs it
    # would come out as "# **The Title**", saying twice what the # says.
    blocks = blocks_of(tmp_path, sized("The Title", 40) + BODY)

    assert isinstance(blocks[0], Heading)
    assert [item.bold for item in blocks[0].items] == [False]
