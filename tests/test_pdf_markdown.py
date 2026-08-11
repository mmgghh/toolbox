"""Blocks to Markdown."""

from pytoolbox.pdf.markdown import render
from pytoolbox.pdf.structure import Heading, Image, ListItem, PageBreak, Paragraph, Run


def test_a_heading_gets_its_hashes():
    assert render([Heading(level=2, runs=[Run("Methods")])]) == "## Methods\n"


def test_paragraphs_are_separated_by_a_blank_line():
    blocks = [Paragraph(runs=[Run("One.")]), Paragraph(runs=[Run("Two.")])]

    assert render(blocks) == "One.\n\nTwo.\n"


def test_emphasis_is_applied():
    blocks = [Paragraph(runs=[Run("plain "), Run("bold", bold=True)])]

    assert render(blocks) == "plain **bold**\n"


def test_markdown_syntax_in_the_text_is_escaped():
    blocks = [Paragraph(runs=[Run("a * b _ c")])]

    assert render(blocks) == "a \\* b \\_ c\n"


def test_a_link_is_written_as_markdown():
    blocks = [Paragraph(runs=[Run("the spec", link="https://example.com")])]

    assert render(blocks) == "[the spec](https://example.com)\n"


def test_a_bullet_list_is_indented_by_level():
    blocks = [
        ListItem(level=1, ordered=False, runs=[Run("top")]),
        ListItem(level=2, ordered=False, runs=[Run("nested")]),
    ]

    assert render(blocks) == "- top\n  - nested\n"


def test_an_ordered_list_numbers_itself():
    blocks = [
        ListItem(level=1, ordered=True, runs=[Run("first")]),
        ListItem(level=1, ordered=True, runs=[Run("second")]),
    ]

    assert render(blocks) == "1. first\n2. second\n"


def test_a_nested_ordered_list_numbers_independently():
    blocks = [
        ListItem(level=1, ordered=True, runs=[Run("one")]),
        ListItem(level=2, ordered=True, runs=[Run("one a")]),
        ListItem(level=2, ordered=True, runs=[Run("one b")]),
        ListItem(level=1, ordered=True, runs=[Run("two")]),
    ]

    assert render(blocks) == "1. one\n   1. one a\n   2. one b\n2. two\n"


def test_an_ordered_list_restarts_after_a_paragraph():
    blocks = [
        ListItem(level=1, ordered=True, runs=[Run("first")]),
        Paragraph(runs=[Run("Interrupting text.")]),
        ListItem(level=1, ordered=True, runs=[Run("first again")]),
    ]

    assert render(blocks).endswith("1. first again\n")


def test_an_image_links_into_the_assets_directory():
    blocks = [Image(name="image1.png")]

    assert render(blocks, assets_dir="report.assets") == "![](report.assets/image1.png)\n"


def test_an_image_without_an_assets_directory_is_dropped():
    assert render([Image(name="image1.png")]) == ""


def test_a_page_break_is_a_thematic_break():
    blocks = [Paragraph(runs=[Run("One.")]), PageBreak(), Paragraph(runs=[Run("Two.")])]

    assert render(blocks) == "One.\n\n---\n\nTwo.\n"


def test_an_empty_paragraph_is_dropped():
    blocks = [Paragraph(runs=[Run("   ")]), Paragraph(runs=[Run("Real text.")])]

    assert render(blocks) == "Real text.\n"


def test_an_empty_document_renders_to_nothing():
    assert render([]) == ""


def test_a_heading_is_not_bolded_twice():
    # PDF headings are set bold; "# **Title**" would carry the weight twice.
    blocks = [Heading(level=1, runs=[Run("Quarterly Report", bold=True)])]

    assert render(blocks) == "# Quarterly Report\n"


def test_a_heading_keeps_its_italics():
    blocks = [Heading(level=2, runs=[Run("Nota bene", bold=True, italic=True)])]

    assert render(blocks) == "## *Nota bene*\n"
