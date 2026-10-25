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


def test_a_number_followed_by_a_dash_is_a_list_marker():
    # "1- text" is how a lot of Persian documents number their items.
    lines = [line("1- first", 700), line("2- second", 688)]

    blocks = build(document(one_page()), [lines])

    assert all(isinstance(block, ListItem) and block.ordered for block in blocks)
    assert [block.runs[0].text for block in blocks] == ["first", "second"]


def test_a_list_item_remembers_the_number_it_was_given():
    # A list that carries on over a page, or sits one item to a table row,
    # starts at 14: renumbering it from 1 would change what the page said.
    blocks = build(document(one_page()), [[line("14- fourteenth", 700), line("b) lettered", 688)]])

    assert [block.number for block in blocks] == [14, None]


def test_a_range_is_not_a_list_marker():
    lines = [line("10-12 people came to the meeting that was held in the afternoon.", 700)]

    blocks = build(document(one_page()), [lines])

    assert isinstance(blocks[0], Paragraph)


def test_a_symbol_font_bullet_is_a_bullet():
    # Word draws its bullets in the Symbol and Wingdings fonts, whose glyphs
    # reach the text as private-use characters -- one per kind of bullet.
    lines = [
        line("\uf0b7 round", 700),
        line("\uf0a7 square", 688),
        line("\uf0d8 arrow", 676),
        line("\uf0fc tick", 664),
    ]

    blocks = build(document(one_page()), [lines])

    assert [isinstance(block, ListItem) and not block.ordered for block in blocks] == [True] * 4
    assert [block.runs[0].text for block in blocks] == ["round", "square", "arrow", "tick"]


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


def test_a_javascript_link_keeps_its_text_but_loses_the_link():
    # A PDF's annotations are attacker-supplied; a live javascript: target
    # would survive into whatever renders the Markdown.
    page = one_page(links=[LinkBox("javascript:alert(1)", 70, 695, 140, 712)])

    blocks = build(document(page), [[line("click me", 700)]])

    assert blocks[0].runs[0].text == "click me"
    assert blocks[0].runs[0].link is None


def test_a_data_uri_link_is_dropped_too():
    page = one_page(links=[LinkBox("data:text/html;base64,PHNjcmlwdD4=", 70, 695, 140, 712)])

    blocks = build(document(page), [[line("click me", 700)]])

    assert blocks[0].runs[0].link is None


def test_a_mailto_link_is_kept():
    page = one_page(links=[LinkBox("mailto:someone@example.com", 70, 695, 140, 712)])

    blocks = build(document(page), [[line("email us", 700)]])

    assert blocks[0].runs[0].link == "mailto:someone@example.com"


def rtl_line(text, y, size=10.0, x=72.0, x1=None, page=0, **kwargs):
    run = TextRun(text=text, x=x, y=y, size=size, end=x1 if x1 is not None else x + 200, **kwargs)
    return Line(runs=[run], page=page, base="R", flow="R")


def test_a_wrapped_list_item_stays_one_item():
    lines = [
        line("- The first point, which runs on past the end of its line and", 700),
        line("carries on here.", 688),
        line("- The second point.", 676),
    ]

    blocks = build(document(one_page()), [lines])

    assert [type(block) for block in blocks] == [ListItem, ListItem]
    assert blocks[0].runs[-1].text.endswith("carries on here.")


def test_a_list_item_does_not_swallow_the_paragraph_after_a_gap():
    lines = [line(f"Body line {i}.", 760 - i * 12) for i in range(5)]
    lines += [
        line("- The only point.", 700),
        line("A new paragraph, well clear of the list above it.", 660),
    ]

    blocks = build(document(one_page()), [lines])

    assert [type(block) for block in blocks] == [Paragraph, ListItem, Paragraph]


def test_a_heading_too_long_for_one_line_stays_one_heading():
    lines = [
        line("A Title That Did Not Fit On", 740, size=24),
        line("One Single Line", 716, size=24),
    ]
    lines += [line(f"Body line {i}.", 690 - i * 12) for i in range(4)]

    blocks = build(document(one_page()), [lines])

    assert [type(block) for block in blocks] == [Heading, Paragraph]
    assert blocks[0].runs[-1].text.endswith("One Single Line")


def test_a_right_to_left_paragraph_breaks_on_its_own_ragged_edge():
    # The short line is short at the left, which is where a Persian
    # paragraph's last line stops.
    lines = [
        rtl_line("first line", 700, x=72, x1=500),
        rtl_line("short last", 688, x=400, x1=500),
        rtl_line("next paragraph", 676, x=72, x1=500),
    ]

    blocks = build(document(one_page()), [lines])

    assert len(blocks) == 2


def test_a_blank_line_is_never_a_heading():
    # A run of spaces set at a heading's size has no text to be a heading of.
    lines = [line("Revenue grew.", 700), line("   ", 640, size=24), line("Costs fell.", 580)]

    blocks = build(document(one_page()), [lines])

    assert [type(block) for block in blocks] == [Paragraph, Paragraph]


def test_each_line_of_a_table_of_contents_is_its_own_paragraph_without_the_dots():
    lines = [
        line("Introduction ........................ 3", 700),
        line("Methods ............................. 5", 688),
        line("Results ............................. 9", 676),
    ]

    blocks = build(document(one_page()), [lines])

    assert [block.runs[0].text for block in blocks] == [
        "Introduction 3",
        "Methods 5",
        "Results 9",
    ]


def test_a_sentence_with_an_ellipsis_is_not_a_contents_line():
    lines = [line("Well... that is one way to put it, and it is a fair one at that.", 700)]

    blocks = build(document(one_page()), [lines])

    assert blocks[0].runs[0].text.startswith("Well...")


def test_an_o_set_in_a_monospace_font_is_a_sub_bullet():
    # Word's second-level bullet is a letter o in Courier New, which says
    # nothing as text and everything as a typeface.
    marker = TextRun(text="o", x=90.0, y=700, size=10.0, font="CourierNewPSMT", end=96.0)
    body = TextRun(text="nested point", x=110.0, y=700, size=10.0, font="Arial", end=170.0)
    plain = [line("• top level", 712)]

    blocks = build(document(one_page()), [plain + [Line(runs=[marker, body], page=0)]])

    assert [(block.level, block.runs[0].text) for block in blocks] == [
        (1, "top level"),
        (2, "nested point"),
    ]


def test_the_sub_bullet_of_a_right_to_left_line_is_read_from_its_right_edge():
    # The marker leads the line, which on a Persian page is its right-hand end.
    marker = TextRun(text="o", x=470.0, y=700, size=10.0, font="CourierNewPSMT", end=476.0)
    space = TextRun(text=" ", x=466.0, y=700, size=10.0, font="Arial", end=469.0)
    body = TextRun(text="ﻡﺎﻧ", x=420.0, y=700, size=10.0, font="Nazanin", end=465.0)
    row = Line(runs=[body, space, marker], page=0, base="R", flow="R")

    blocks = build(document(one_page()), [[line("ﻡﺎﻧ", 712, x=420.0, end=465.0), row]])

    assert isinstance(blocks[1], ListItem)
    assert blocks[1].runs[0].text == "نام"


def test_a_lone_o_in_running_text_is_just_a_word():
    lines = [line("o what a lovely day it is to be out walking in the park", 700)]

    blocks = build(document(one_page()), [lines])

    assert isinstance(blocks[0], Paragraph)


# ─────────────────────────────────────────────────────────────────────
# Tables
# ─────────────────────────────────────────────────────────────────────


def grid(*rows, top=700.0, rtl=False, page=0):
    """A detected grid whose cells hold one line of the given text each."""
    from pytoolbox.pdf.tables import Table as Grid

    return Grid(
        rows=[
            [[line(text, top - 12 * index, page=page)] if text else [] for text in row]
            for index, row in enumerate(rows)
        ],
        page=page,
        top=top,
        bottom=top - 12 * len(rows),
        rtl=rtl,
    )


def table_text(table):
    return [
        [" ".join(run.text for block in cell.blocks for run in getattr(block, "runs", []))
         for cell in row]
        for row in table.rows
    ]


def tables_of(*pages_of_grids):
    """The tables ``build`` makes from one list of grids per page."""
    pages = [one_page() for _ in pages_of_grids]
    for number, page in enumerate(pages):
        page.number = number
    blocks = build(document(*pages), [[] for _ in pages], grids=[list(g) for g in pages_of_grids])
    return [block for block in blocks if hasattr(block, "rows")]


def test_a_column_empty_in_every_row_is_a_spacer_and_is_dropped():
    (table,) = tables_of([grid(("a", "", "b"), ("c", "", "d"))])

    assert table_text(table) == [["a", "b"], ["c", "d"]]


def test_a_column_with_a_header_is_kept_even_when_its_body_is_empty():
    (table,) = tables_of([grid(("Name", "Notes"), ("a", ""), ("b", ""))])

    assert table_text(table) == [["Name", "Notes"], ["a", ""], ["b", ""]]


def test_blank_rows_at_the_top_are_dropped_so_the_header_is_a_header():
    (table,) = tables_of([grid(("", ""), ("Name", "Role"), ("a", "b"))])

    assert table_text(table) == [["Name", "Role"], ["a", "b"]]


def test_a_table_that_carries_on_across_a_page_is_one_table():
    first = grid(("Name", "Role"), ("a", "b"))
    carried = grid(("c", "d"), ("e", "f"), page=1)

    (table,) = tables_of([first], [carried])

    assert table_text(table) == [["Name", "Role"], ["a", "b"], ["c", "d"], ["e", "f"]]


def test_a_repeated_header_is_not_repeated_in_the_sewn_table():
    first = grid(("Name", "Role"), ("a", "b"))
    carried = grid(("Name", "Role"), ("c", "d"), page=1)

    (table,) = tables_of([first], [carried])

    assert table_text(table) == [["Name", "Role"], ["a", "b"], ["c", "d"]]


def test_tables_of_different_widths_stay_apart():
    (one, two) = tables_of([grid(("a", "b"))], [grid(("c", "d", "e"), page=1)])

    assert (len(one.rows[0]), len(two.rows[0])) == (2, 3)
