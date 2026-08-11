"""Grouping positioned runs into lines, and reading columns in order."""

from pytoolbox.pdf.layout import Line, drop_furniture, page_lines, to_lines
from pytoolbox.pdf.reader import Page, TextRun


def run(text, x, y, size=10.0, **kwargs):
    return TextRun(text=text, x=x, y=y, size=size, **kwargs)


def test_runs_on_one_baseline_become_one_line():
    lines = to_lines([run("world", 120, 700), run("hello", 72, 700)])

    assert [line.text for line in lines] == ["hello world"]


def test_lines_are_ordered_top_to_bottom():
    # PDF y grows upwards, so the larger y is the earlier line.
    lines = to_lines([run("second", 72, 680), run("first", 72, 700)])

    assert [line.text for line in lines] == ["first", "second"]


def test_a_small_baseline_difference_is_still_one_line():
    # Subscripts and font switches shift the baseline slightly.
    lines = to_lines([run("H", 72, 700), run("2", 80, 698), run("O", 86, 700)])

    assert len(lines) == 1


def test_adjacent_runs_are_joined_without_a_space():
    # Kerning splits a word into several show operations.
    lines = to_lines([run("Quar", 72, 700), run("terly", 92, 700)])

    assert lines[0].text == "Quarterly"


def test_a_wide_gap_becomes_a_space():
    lines = to_lines([run("Name", 72, 700), run("Status", 300, 700)])

    assert lines[0].text == "Name Status"


def test_line_geometry_covers_all_its_runs():
    line = to_lines([run("ab", 72, 700), run("cd", 100, 700, size=14)])[0]

    assert line.x0 == 72
    assert line.size == 14  # the largest run decides the line's size
    assert line.y == 700


def test_a_line_is_bold_only_when_every_run_is():
    mixed = to_lines([run("a", 72, 700, bold=True), run("b", 90, 700)])[0]
    whole = to_lines([run("a", 72, 700, bold=True), run("b", 90, 700, bold=True)])[0]

    assert not mixed.bold
    assert whole.bold


def _page_of(runs, width=612.0):
    return Page(number=0, width=width, height=792.0, runs=list(runs))


def _two_column_runs():
    # Both columns share their baselines, as a real paper's do. This is why
    # columns have to be found before runs are grouped into lines.
    runs = []
    for i in range(6):
        runs.append(run(f"left{i}", 72, 700 - i * 12))
        runs.append(run(f"right{i}", 330, 700 - i * 12))
    return runs


def test_two_columns_are_read_one_after_the_other():
    lines = page_lines(_page_of(_two_column_runs()))

    assert [line.text for line in lines] == [
        "left0",
        "left1",
        "left2",
        "left3",
        "left4",
        "left5",
        "right0",
        "right1",
        "right2",
        "right3",
        "right4",
        "right5",
    ]


def test_single_column_mode_reads_straight_down_the_page():
    lines = page_lines(_page_of(_two_column_runs()), single_column=True)

    assert lines[0].text == "left0 right0"


def test_a_single_column_page_is_left_alone():
    runs = [run(f"line{i}", 72, 700 - i * 12) for i in range(6)]

    lines = page_lines(_page_of(runs))

    assert [line.text for line in lines] == [f"line{i}" for i in range(6)]


def test_a_full_width_heading_does_not_defeat_column_detection():
    # A title spanning both columns is common; it must stay first.
    runs = [run("A Title Right Across The Whole Of This Page", 72, 740, size=18)]
    runs += _two_column_runs()

    lines = page_lines(_page_of(runs))

    assert lines[0].text == "A Title Right Across The Whole Of This Page"
    assert [line.text for line in lines[1:7]] == [f"left{i}" for i in range(6)]


def test_a_page_whose_lines_all_cross_the_middle_is_not_split():
    # No gutter exists, so guessing one would interleave real sentences.
    runs = [run("a line of text that reaches across the middle", 72, 700 - i * 12) for i in range(6)]

    lines = page_lines(_page_of(runs))

    assert [line.text for line in lines] == [line.runs[0].text for line in lines]
    assert len(lines) == 6


def test_columns_need_a_real_gutter():
    # Two blocks of text that nearly touch are one column, not two.
    runs = [run("x" * 40, 72, 700 - i * 12) for i in range(6)]
    runs += [run("y" * 40, 280, 700 - i * 12) for i in range(6)]

    lines = page_lines(_page_of(runs))

    # Same baselines, so each pair joins into one line rather than splitting.
    assert len(lines) == 6


def test_a_heading_beside_a_logo_is_not_two_columns():
    # One line on each side of the middle is not evidence of a column layout.
    runs = [run("Annual Report", 72, 740), run("ACME", 400, 740)]

    lines = page_lines(_page_of(runs))

    assert [line.text for line in lines] == ["Annual Report ACME"]


# ── Running headers, footers and page numbers ────────────────────────


def _page(number):
    return Page(number=number, width=612, height=792)


def _body(page, *texts):
    return [Line(runs=[run(text, 72, 600 - i * 12)], page=page) for i, text in enumerate(texts)]


def test_a_running_header_is_dropped():
    pages, per_page = [], []
    for number in range(4):
        pages.append(_page(number))
        header = Line(runs=[run("Quarterly Report", 72, 760)], page=number)
        per_page.append([header, *_body(number, f"body {number}")])

    cleaned = drop_furniture(per_page, pages)

    assert [[line.text for line in page] for page in cleaned] == [
        ["body 0"],
        ["body 1"],
        ["body 2"],
        ["body 3"],
    ]


def test_page_numbers_are_dropped_even_though_they_differ():
    pages, per_page = [], []
    for number in range(4):
        pages.append(_page(number))
        footer = Line(runs=[run(f"Page {number + 1} of 4", 290, 40)], page=number)
        per_page.append([*_body(number, f"body {number}"), footer])

    cleaned = drop_furniture(per_page, pages)

    assert all(len(page) == 1 for page in cleaned)


def test_a_heading_high_on_the_page_survives():
    # It sits in the header band but says something different every time.
    pages, per_page = [], []
    for number in range(4):
        pages.append(_page(number))
        heading = Line(runs=[run(f"Chapter {number}: A Distinct Title", 72, 760)], page=number)
        per_page.append([heading, *_body(number, f"body {number}")])

    cleaned = drop_furniture(per_page, pages)

    assert all(len(page) == 2 for page in cleaned)


def test_a_short_document_keeps_everything():
    # Two pages is not enough evidence that a repeated line is furniture.
    pages, per_page = [], []
    for number in range(2):
        pages.append(_page(number))
        header = Line(runs=[run("Report", 72, 760)], page=number)
        per_page.append([header, *_body(number, "body")])

    cleaned = drop_furniture(per_page, pages)

    assert all(len(page) == 2 for page in cleaned)


def test_body_text_repeated_by_chance_is_kept():
    # The same words, but in the middle of the page, so not furniture.
    pages, per_page = [], []
    for number in range(4):
        pages.append(_page(number))
        per_page.append(_body(number, "See the appendix for details."))

    cleaned = drop_furniture(per_page, pages)

    assert all(len(page) == 1 for page in cleaned)


def test_a_header_that_drifts_down_the_page_is_kept():
    # Same text, but at a different height each time: not a running header.
    pages, per_page = [], []
    for number in range(4):
        pages.append(_page(number))
        drifting = Line(runs=[run("Notes", 72, 760 - number * 20)], page=number)
        per_page.append([drifting, *_body(number, "body")])

    cleaned = drop_furniture(per_page, pages)

    assert all(len(page) == 2 for page in cleaned)
