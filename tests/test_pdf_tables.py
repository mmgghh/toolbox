"""Finding tables from the cell borders the writer drew."""

import pytest

pytest.importorskip("pypdf")

from pytoolbox.pdf import reader, tables  # noqa: E402
from tests.pdf_fixtures import rule, text, write_pdf  # noqa: E402


def grid_pdf(tmp_path, rows=3, columns=2, cell_width=120.0, cell_height=20.0, left=72.0, top=700.0):
    """A ruled grid with "r0c0"-style text centred in every cell."""
    items = []
    for row in range(rows):
        for column in range(columns):
            x = left + column * cell_width
            y = top - (row + 1) * cell_height
            items.append(rule(x, y, cell_width, cell_height))
            items.append(text(x + 4, y + 6, 10, f"r{row}c{column}"))
    return write_pdf(tmp_path / "grid.pdf", [items])


def test_a_ruled_grid_becomes_a_table(tmp_path):
    page = reader.read(grid_pdf(tmp_path)).pages[0]

    found, outside = tables.find(page)

    assert len(found) == 1
    assert [[" ".join(line.text for line in cell) for cell in row] for row in found[0].rows] == [
        ["r0c0", "r0c1"],
        ["r1c0", "r1c1"],
        ["r2c0", "r2c1"],
    ]
    assert outside == []


def test_text_outside_the_grid_stays_outside(tmp_path):
    path = grid_pdf(tmp_path)
    page = reader.read(path).pages[0]
    page.runs.append(reader.TextRun(text="A paragraph", x=72, y=760, size=10, end=140))

    _, outside = tables.find(page)

    assert [run.text for run in outside] == ["A paragraph"]


def test_an_unruled_page_has_no_table(tmp_path):
    # Guessing one from the gaps would find a table in every two-column page.
    path = write_pdf(
        tmp_path / "plain.pdf",
        [[text(72, 700 - i * 14, 10, f"left{i}") for i in range(4)]
         + [text(330, 700 - i * 14, 10, f"right{i}") for i in range(4)]],
    )

    found, outside = tables.find(reader.read(path).pages[0])

    assert found == []
    assert len(outside) == 8


def test_a_single_box_is_not_a_table(tmp_path):
    path = write_pdf(tmp_path / "box.pdf", [[rule(72, 680, 200, 40), text(76, 690, 10, "Note")]])

    found, _ = tables.find(reader.read(path).pages[0])

    assert found == []


def test_a_band_across_the_whole_page_is_not_part_of_a_table(tmp_path):
    # A running header's background touches the table below it; joining the
    # two would add an empty column at each edge of the page.
    items = [rule(0, 690, 612, 30)]
    for row in range(3):
        for column in range(2):
            x, y = 72 + column * 120, 700 - (row + 1) * 20
            items.append(rule(x, y, 120, 20))
            items.append(text(x + 4, y + 6, 10, f"r{row}c{column}"))
    page = reader.read(write_pdf(tmp_path / "band.pdf", [items])).pages[0]

    found, _ = tables.find(page)

    assert len(found) == 1
    assert len(found[0].rows[0]) == 2


def test_a_right_to_left_table_reads_its_columns_from_the_right():
    # Built by hand rather than as a PDF: the base-14 fixture fonts cannot
    # encode Arabic, and what is under test is the grid, not the encoding.
    page = reader.Page(number=0, width=612.0, height=792.0)
    for row, (left, right) in enumerate([("ﺪﻨﺳ", "ﻡﺎﻧ"), ("ﺦﯾﺭﺎﺗ", "ﻪﺨﺴﻧ")]):
        y = 700 - (row + 1) * 20
        page.rules += [
            reader.RuleBox(72, y, 192, y + 20),
            reader.RuleBox(192, y, 312, y + 20),
        ]
        page.runs += [
            reader.TextRun(text=left, x=76, y=y + 6, size=10, end=140),
            reader.TextRun(text=right, x=196, y=y + 6, size=10, end=260),
        ]

    found, _ = tables.find(page, "R")

    assert found[0].rtl
    # The rightmost column is the first one read.
    assert [cell[0].text for cell in found[0].rows[0]] == ["نام", "سند"]


def test_border_thickness_does_not_become_a_column():
    # A writer that gives its borders a width, and its cells padding inside
    # that, leaves a sliver at every rule. Nothing can be written in one.
    from pytoolbox.pdf.tables import _bands

    bands = _bands([72.0, 76.6, 425.6, 429.9, 434.6, 535.6, 540.0])

    assert [(round(a), round(b)) for a, b in bands] == [(72, 435), (435, 540)]


def test_a_background_behind_each_line_does_not_split_the_cell(tmp_path):
    # Word paints a rectangle behind every line of a cell as well as behind
    # the cell; the edge between two of those looks just like a row boundary.
    items = []
    for row in range(2):
        for column in range(2):
            x, y = 72 + column * 120, 700 - (row + 1) * 40
            items.append(rule(x, y, 120, 40))
            # Two line backgrounds stacked inside the cell.
            items.append(rule(x + 2, y + 2, 116, 18))
            items.append(rule(x + 2, y + 20, 116, 18))
            items.append(text(x + 6, y + 26, 10, f"r{row}c{column}"))

    found, _ = tables.find(reader.read(write_pdf(tmp_path / "word.pdf", [items])).pages[0])

    assert len(found) == 1
    assert (len(found[0].rows), len(found[0].rows[0])) == (2, 2)


def _ruled(page, *, horizontals, verticals):
    """Draw a table's borders as hairline rectangles, the way Word does."""
    for y, x0, x1 in horizontals:
        page.rules.append(reader.RuleBox(x0, y - 0.25, x1, y + 0.25))
    for x, y0, y1 in verticals:
        page.rules.append(reader.RuleBox(x - 0.25, y0, x + 0.25, y1))


def _merged_row_page(rtl=False):
    """Three columns of header, then one row merged across all of them.

    The merged row's text is laid out as separate runs that fall either side
    of the column boundaries the header row draws, which is how a writer sets
    a long line of text in a wide cell.
    """
    page = reader.Page(number=0, width=612.0, height=792.0)
    _ruled(
        page,
        horizontals=[(700, 72, 432), (680, 72, 432), (640, 72, 432)],
        verticals=[(72, 640, 700), (432, 640, 700), (192, 680, 700), (312, 680, 700)],
    )
    header = ("ﺪﻨﺳ", "ﻡﺎﻧ", "ﺦﯾﺭﺎﺗ") if rtl else ("A", "B", "C")
    body = ("ﻪﺨﺴﻧ", "ﻡﺎﻧ", "ﺪﻨﺳ") if rtl else ("alpha", "beta", "gamma")
    for x, word in zip((80, 200, 320), header):
        page.runs.append(reader.TextRun(text=word, x=x, y=686, size=10, end=x + 30))
    # Right to left, the first word sits at the right of the merged cell.
    positions = (330, 200, 80) if rtl else (80, 200, 330)
    for x, word in zip(positions, body):
        page.runs.append(reader.TextRun(text=word, x=x, y=655, size=10, end=x + 40))
    return page


def test_text_in_a_merged_cell_is_one_cell_not_cut_at_the_columns_above():
    found, outside = tables.find(_merged_row_page())

    cells = [[" ".join(line.text for line in cell) for cell in row] for row in found[0].rows]
    assert cells == [["A", "B", "C"], ["alpha beta gamma", "", ""]]
    assert outside == []


def test_a_right_to_left_merged_cell_sits_in_the_column_read_first():
    found, _ = tables.find(_merged_row_page(rtl=True), "R")

    assert found[0].rtl
    merged = found[0].rows[1]
    assert merged[0] and not merged[1] and not merged[2]


def test_a_row_open_at_one_side_keeps_the_columns_of_the_table():
    # Merging is read from a row's own rules, and only a row closed at both
    # ends has any. One whose right edge was never drawn has told us nothing
    # about its inner boundaries, so it is read against the table's columns.
    page = reader.Page(number=0, width=612.0, height=792.0)
    _ruled(
        page,
        horizontals=[(700, 72, 432), (680, 72, 432), (640, 72, 432)],
        verticals=[
            (72, 640, 700), (192, 680, 700), (312, 680, 700), (432, 680, 700),
        ],
    )
    for x, word in zip((80, 200, 320), ("A", "B", "C")):
        page.runs.append(reader.TextRun(text=word, x=x, y=686, size=10, end=x + 30))
    for x, word in zip((80, 200, 320), ("one", "two", "three")):
        page.runs.append(reader.TextRun(text=word, x=x, y=655, size=10, end=x + 30))

    found, _ = tables.find(page)

    cells = [[" ".join(line.text for line in cell) for cell in row] for row in found[0].rows]
    assert cells == [["A", "B", "C"], ["one", "two", "three"]]


def test_an_inner_rule_that_touches_nothing_still_separates_its_cells():
    # Word draws each cell's own borders with a gap between cells, so a row's
    # inner rules can fall into a cluster of their own. They are still that
    # row's rules: reading them only from the cluster that defines the grid
    # would call a perfectly ordinary row merged.
    page = reader.Page(number=0, width=612.0, height=792.0)
    _ruled(
        page,
        horizontals=[(700, 72, 432), (680, 72, 432), (640, 72, 432)],
        verticals=[
            (72, 640, 700), (432, 640, 700),
            (192, 640, 680), (312, 640, 680),  # the bottom row, touching the borders
            (192, 684, 696), (312, 684, 696),  # the top row, stopping short of them
        ],
    )
    for x, word in zip((80, 200, 320), ("A", "B", "C")):
        page.runs.append(reader.TextRun(text=word, x=x, y=686, size=10, end=x + 30))
    for x, word in zip((80, 200, 320), ("one", "two", "three")):
        page.runs.append(reader.TextRun(text=word, x=x, y=655, size=10, end=x + 30))

    found, _ = tables.find(page)

    cells = [[" ".join(line.text for line in cell) for cell in row] for row in found[0].rows]
    assert cells == [["A", "B", "C"], ["one", "two", "three"]]


def test_a_rows_own_rules_decide_its_cells_even_where_other_rows_blur_the_grid():
    # Other rows' edges can lie a few points either side of this row's rule --
    # nested boxes, checkboxes -- close enough that the table's shared columns
    # fold them into one boundary somewhere else. The row's cells come from the
    # row's own rules, not from where the shared columns ended up.
    page = reader.Page(number=0, width=612.0, height=792.0)
    _ruled(
        page,
        horizontals=[(700, 40, 540), (680, 40, 540), (640, 40, 540)],
        verticals=[
            (40, 640, 700), (540, 640, 700),
            # The top row is three cells, split at 207.3 and 283.8 ...
            (207.3, 680, 700), (283.8, 680, 700),
            # ... while the bottom row's edges crowd the same stretch at 211.7,
            # 273.4 and 279.3, which the shared columns fold into 273.4.
            (211.7, 640, 680), (238.7, 640, 680), (273.4, 640, 680),
            (279.3, 640, 680), (332.9, 640, 680),
        ],
    )
    page.runs += [
        reader.TextRun(text="left", x=60, y=686, size=10, end=90),
        reader.TextRun(text="middle", x=225, y=686, size=10, end=260),
        reader.TextRun(text="right", x=300, y=686, size=10, end=330),
    ]

    found, _ = tables.find(page)

    top = [" ".join(line.text for line in cell) for cell in found[0].rows[0] if cell]
    assert top == ["left", "middle", "right"]


def test_the_tables_columns_count_every_rule_inside_it_not_only_the_joined_ones():
    # The same unconnected rule that says a row's cells are separate says the
    # table has that column, and a right-to-left table's cells must still read
    # from the right.
    page = reader.Page(number=0, width=612.0, height=792.0)
    _ruled(
        page,
        horizontals=[(700, 72, 432), (680, 72, 432), (660, 72, 432)],
        verticals=[
            (72, 660, 700), (432, 660, 700), (192, 660, 700), (312, 660, 700),
            # Between 312 and 432, in the top row only, and touching nothing.
            (372, 684, 696),
        ],
    )
    for y, words in ((686, ("ﺪﻨﺳ", "ﻡﺎﻧ", "ﺦﯾﺭﺎﺗ", "ﻪﺨﺴﻧ")), (666, ("ﺪﻨﺳ", "ﻡﺎﻧ", "ﺦﯾﺭﺎﺗ"))):
        for x, word in zip((80, 200, 320, 380), words):
            page.runs.append(reader.TextRun(text=word, x=x, y=y, size=10, end=x + 30))

    found, _ = tables.find(page, "R")

    assert all(len(row) == 4 for row in found[0].rows)
    first_row = [" ".join(line.text for line in cell) for cell in found[0].rows[0]]
    assert first_row == ["نسخه", "تاریخ", "نام", "سند"]
