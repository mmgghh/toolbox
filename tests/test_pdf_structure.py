"""Inferring headings, paragraphs and lists from line geometry."""

from pytoolbox.pdf.layout import Line
from pytoolbox.pdf.reader import Document, ImageBox, LinkBox, OutlineEntry, Page, TextRun
from pytoolbox.pdf.structure import Heading, Image, ListItem, Paragraph, build


def line(text, y, size=10.0, x=72.0, page=0, **kwargs):
    return Line(runs=[TextRun(text=text, x=x, y=y, size=size, **kwargs)], page=page)


def one_page(*, images=None, links=None):
    return Page(
        number=0,
        width=612.0,
        height=792.0,
        images=list(images or []),
        links=list(links or []),
    )


def document(*pages):
    return Document(pages=list(pages))


def test_body_text_becomes_a_paragraph():
    blocks = build(document(one_page()), [[line("Revenue grew.", 700)]])

    assert isinstance(blocks[0], Paragraph)
    assert blocks[0].runs[0].text == "Revenue grew."


def test_a_larger_line_becomes_a_heading():
    lines = [line("Quarterly Report", 740, size=24), line("Revenue grew.", 700)]

    blocks = build(document(one_page()), [lines])

    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 1
    assert blocks[0].runs[0].text == "Quarterly Report"
    assert isinstance(blocks[1], Paragraph)


def test_distinct_heading_sizes_become_distinct_levels():
    lines = [
        line("Title", 740, size=24),
        line("Section", 720, size=16),
        line("Body text here.", 700),
    ]

    blocks = build(document(one_page()), [lines])

    assert [block.level for block in blocks if isinstance(block, Heading)] == [1, 2]


def test_the_outline_overrides_size_guessing():
    # The same size as the body, but the author bookmarked it.
    lines = [line("Introduction", 740), line("Revenue grew.", 700)]
    doc = document(one_page())
    doc.outline = [OutlineEntry(title="Introduction", level=1, page=0)]

    blocks = build(doc, [lines])

    assert isinstance(blocks[0], Heading)
    assert blocks[0].level == 1


def test_a_bold_short_line_becomes_a_heading():
    lines = [line("Methods", 740, bold=True), line("We measured everything.", 720)]

    blocks = build(document(one_page()), [lines])

    assert isinstance(blocks[0], Heading)


def test_a_bold_sentence_ending_in_a_period_stays_a_paragraph():
    lines = [line("This whole sentence is bold and ends properly.", 740, bold=True)]

    blocks = build(document(one_page()), [lines])

    assert isinstance(blocks[0], Paragraph)


def test_consecutive_body_lines_join_into_one_paragraph():
    lines = [line("Revenue grew twelve percent", 700), line("in the third quarter.", 688)]

    blocks = build(document(one_page()), [lines])

    assert len(blocks) == 1
    assert blocks[0].runs[0].text == "Revenue grew twelve percent in the third quarter."


def test_a_hyphen_at_a_line_break_is_healed():
    lines = [line("Revenue grew in the third quar-", 700), line("ter of the year.", 688)]

    blocks = build(document(one_page()), [lines])

    assert blocks[0].runs[0].text == "Revenue grew in the third quarter of the year."


def test_a_hyphenated_compound_keeps_its_hyphen():
    # Uppercase after the break means a real hyphen, not a split word.
    lines = [line("the Anglo-", 700), line("French treaty", 688)]

    blocks = build(document(one_page()), [lines])

    assert blocks[0].runs[0].text == "the Anglo-French treaty"


def test_a_wide_vertical_gap_starts_a_new_paragraph():
    lines = [
        line("First paragraph here.", 700),
        line("Still the first paragraph.", 688),
        line("Second paragraph here.", 620),
    ]

    blocks = build(document(one_page()), [lines])

    assert len(blocks) == 2


def test_a_short_last_line_ends_a_paragraph():
    lines = [
        line("A long line of text that runs the full width of the text column here", 700),
        line("and stops.", 688),
        line("A new paragraph starts on the next line of the page here as well.", 676),
    ]

    blocks = build(document(one_page()), [lines])

    assert len(blocks) == 2


def test_bulleted_lines_become_list_items():
    lines = [line("• offline mode", 700), line("• SQLite store", 688)]

    blocks = build(document(one_page()), [lines])

    assert all(isinstance(block, ListItem) for block in blocks)
    assert [block.runs[0].text for block in blocks] == ["offline mode", "SQLite store"]
    assert not blocks[0].ordered


def test_numbered_lines_become_an_ordered_list():
    lines = [line("1. first", 700), line("2. second", 688)]

    blocks = build(document(one_page()), [lines])

    assert all(isinstance(block, ListItem) and block.ordered for block in blocks)


def test_an_indented_bullet_nests():
    lines = [line("• top", 700), line("• nested", 688, x=108)]

    blocks = build(document(one_page()), [lines])

    assert [block.level for block in blocks] == [1, 2]


def test_a_sentence_ending_in_a_number_is_not_a_list():
    # "1." only starts a list at the beginning of a line.
    lines = [line("We shipped it in 2026. Everyone was pleased.", 700)]

    blocks = build(document(one_page()), [lines])

    assert isinstance(blocks[0], Paragraph)


def test_a_link_annotation_marks_the_run_it_covers():
    page = one_page(links=[LinkBox("https://example.com", 70, 695, 140, 712)])

    blocks = build(document(page), [[line("the spec", 700)]])

    assert blocks[0].runs[0].link == "https://example.com"


def test_bare_urls_are_linked():
    blocks = build(document(one_page()), [[line("See https://example.com for more", 700)]])

    links = [run.link for run in blocks[0].runs if run.link]
    assert links == ["https://example.com"]


def test_an_image_is_placed_by_its_height_on_the_page():
    page = one_page(images=[ImageBox("I1.png", b"x", 100, 500, 200, 150)])
    lines = [line("Above the picture.", 700), line("Below the picture.", 400)]

    blocks = build(document(page), [lines])

    assert [type(block).__name__ for block in blocks] == ["Paragraph", "Image", "Paragraph"]


def test_images_are_skipped_when_not_wanted():
    page = one_page(images=[ImageBox("I1.png", b"x", 100, 500, 200, 150)])

    blocks = build(document(page), [[line("Text.", 700)]], include_images=False)

    assert not any(isinstance(block, Image) for block in blocks)


def test_page_breaks_are_optional():
    doc = Document(
        pages=[
            Page(number=0, width=612, height=792),
            Page(number=1, width=612, height=792),
        ]
    )
    per_page = [[line("one", 700)], [line("two", 700, page=1)]]

    without = build(doc, per_page)
    with_breaks = build(doc, per_page, page_breaks=True)

    assert len(with_breaks) == len(without) + 1


def test_an_empty_page_produces_nothing():
    assert build(document(one_page()), [[]]) == []
