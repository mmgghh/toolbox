"""Tests for turning blocks into Markdown.

These build blocks by hand rather than parsing a ``.docx``. That is the point
of the seam: the writer's behaviour is stated directly, with no XML in the way.
"""

from __future__ import annotations

from pytoolbox.docx.comments import Comment
from pytoolbox.docx.document import Heading, ListItem, Paragraph, Table
from pytoolbox.docx.inline import CommentMark, FootnoteMark, ImageRef, Math, Run
from pytoolbox.docx.markdown import RenderOptions, render


def para(*items):
    return Paragraph(items=list(items))


def comment(cid, author="Sara", date="2026-03-14", text="a note", parent=None, resolved=False):
    return Comment(
        id=cid,
        author=author,
        date=date,
        paragraphs=[[Run(text=text)]],
        parent_id=parent,
        resolved=resolved,
    )


def test_a_heading_uses_hashes_for_its_level():
    assert render([Heading(level=3, items=[Run("Scope")])], {}) == "### Scope\n"


def test_emphasis_wraps_the_run():
    items = [Run("plain "), Run("bold", bold=True), Run(" and "), Run("it", italic=True)]
    assert render([para(*items)], {}) == "plain **bold** and *it*\n"


def test_an_inline_equation_is_wrapped_in_single_dollars():
    items = [Run("where "), Math(latex="x_{i}"), Run(" is the input")]
    assert render([para(*items)], {}) == "where $x_{i}$ is the input\n"


def test_a_display_equation_stands_alone_in_double_dollars():
    assert render([para(Math(latex=r"\frac{a}{b}", display=True))], {}) == "$$\n\\frac{a}{b}\n$$\n"


def test_a_cell_never_breaks_its_row_across_lines():
    """A newline inside a cell would end the row and wreck the table."""
    table = Table(rows=[[[para(Run("a\nb"))], [para(Math(latex="x", display=True))]]])
    assert render([table], {}).splitlines()[0] == "| a<br>b | $$<br>x<br>$$ |"


def test_strikethrough_and_code_are_marked():
    items = [Run("gone", strike=True), Run(" "), Run("x = 1", code=True)]
    assert render([para(*items)], {}) == "~~gone~~ `x = 1`\n"


def test_a_link_becomes_markdown():
    assert render([para(Run("the site", link="https://example.com"))], {}) == (
        "[the site](https://example.com)\n"
    )


def test_bullets_and_numbers_reflect_the_list_kind():
    blocks = [
        ListItem(level=0, ordered=False, items=[Run("one")]),
        ListItem(level=0, ordered=True, items=[Run("two")]),
    ]
    assert render(blocks, {}) == "- one\n\n1. two\n"


def test_nested_list_items_are_indented():
    blocks = [
        ListItem(level=0, ordered=False, items=[Run("top")]),
        ListItem(level=1, ordered=False, items=[Run("nested")]),
    ]
    assert render(blocks, {}) == "- top\n  - nested\n"


def test_an_ordered_list_counts_up():
    blocks = [ListItem(level=0, ordered=True, items=[Run(t)]) for t in ("a", "b", "c")]
    assert render(blocks, {}) == "1. a\n2. b\n3. c\n"


def test_a_table_becomes_a_pipe_table():
    table = Table(
        rows=[
            [[para(Run("Feature"))], [para(Run("Status"))]],
            [[para(Run("Offline"))], [para(Run("Planned"))]],
        ],
        header_rows=1,
    )
    assert render([table], {}) == (
        "| Feature | Status |\n| --- | --- |\n| Offline | Planned |\n"
    )


def test_a_pipe_inside_a_cell_is_escaped():
    table = Table(rows=[[[para(Run("a|b"))]], [[para(Run("c"))]]], header_rows=1)
    assert "a\\|b" in render([table], {})


def test_a_multi_paragraph_cell_joins_with_a_break():
    table = Table(rows=[[[para(Run("one")), para(Run("two"))]], [[para(Run("x"))]]], header_rows=1)
    assert "one<br>two" in render([table], {})


def test_a_comment_marker_follows_the_text_and_the_body_follows_the_block():
    blocks = [para(Run("offline mode"), CommentMark("7"))]
    out = render(blocks, {"7": comment("7", text="Is this in scope?")})
    assert out == (
        "offline mode **[1]**\n"
        "\n"
        "> **[1]** Sara · 2026-03-14\n"
        "> Is this in scope?\n"
    )


def test_comments_are_numbered_in_document_order_not_by_word_id():
    blocks = [para(Run("a"), CommentMark("42")), para(Run("b"), CommentMark("7"))]
    out = render(blocks, {"42": comment("42", text="first"), "7": comment("7", text="second")})
    assert "a **[1]**" in out
    assert "b **[2]**" in out


def test_a_reply_nests_under_its_parent():
    blocks = [para(Run("x"), CommentMark("1"), CommentMark("2"))]
    found = {"1": comment("1", text="parent?"), "2": comment("2", author="Ali", text="yes", parent="1")}
    out = render(blocks, found)
    assert "> **[1]** Sara · 2026-03-14" in out
    assert "> > **[1.1]** Ali · 2026-03-14" in out
    assert "**[2]**" not in out, "a reply must not take a top-level number"


def test_a_resolved_comment_says_so():
    out = render([para(Run("x"), CommentMark("1"))], {"1": comment("1", resolved=True)})
    assert "(resolved)" in out


def test_comments_on_a_table_land_after_the_table():
    table = Table(
        rows=[[[para(Run("Feature"))]], [[para(Run("Offline"), CommentMark("9"))]]],
        header_rows=1,
    )
    out = render([table], {"9": comment("9", text="scope?")})
    lines = out.splitlines()
    assert lines[-2].startswith("> **[1]**")
    assert "| Offline **[1]** |" in out


def test_comments_can_be_turned_off():
    blocks = [para(Run("offline mode"), CommentMark("7"))]
    out = render(blocks, {"7": comment("7")}, options=RenderOptions(comments=False))
    assert out == "offline mode\n"


def test_a_marker_for_an_unknown_comment_id_is_dropped():
    out = render([para(Run("text"), CommentMark("nope"))], {})
    assert out == "text\n"


def test_an_image_links_into_the_assets_directory():
    blocks = [para(ImageRef(part_name="word/media/image1.png", alt="a chart"))]
    out = render(blocks, {}, options=RenderOptions(assets_dir="report.assets"))
    assert out == "![a chart](report.assets/image1.png)\n"


def test_images_are_omitted_entirely_when_turned_off():
    blocks = [para(Run("before "), ImageRef(part_name="word/media/image1.png", alt="x"))]
    out = render(blocks, {}, options=RenderOptions(assets_dir=None))
    assert out == "before\n"


def test_a_footnote_reference_and_its_definition():
    blocks = [para(Run("claim"), FootnoteMark("2"))]
    out = render(blocks, {}, notes={"2": [[Run("the source")]]})
    assert "claim[^2]" in out
    assert out.rstrip().endswith("[^2]: the source")


def test_markdown_special_characters_in_text_are_escaped():
    out = render([para(Run("a * b _ c [d]"))], {})
    assert out == "a \\* b \\_ c \\[d\\]\n"


def test_an_empty_paragraph_separates_blocks_without_emitting_junk():
    out = render([para(Run("a")), para(), para(Run("b"))], {})
    assert out == "a\n\nb\n"
