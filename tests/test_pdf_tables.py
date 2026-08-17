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
