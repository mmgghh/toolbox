"""Reading a PDF into positioned runs."""

import click
import pytest

pytest.importorskip("pypdf")

from pytoolbox.pdf import reader  # noqa: E402
from tests.pdf_fixtures import image, link, text, write_pdf  # noqa: E402


def test_reads_text_with_position_and_size(tmp_path):
    path = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 24, "Quarterly Report")]])

    document = reader.read(path)

    assert len(document.pages) == 1
    run = document.pages[0].runs[0]
    assert run.text == "Quarterly Report"
    assert run.x == pytest.approx(72, abs=1)
    assert run.y == pytest.approx(700, abs=1)
    assert run.size == pytest.approx(24, abs=0.5)


def test_blank_runs_are_dropped(tmp_path):
    # pypdf's visitor fires for the empty strings between operators too.
    path = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 12, "hello")]])

    document = reader.read(path)

    assert [run.text for run in document.pages[0].runs] == ["hello"]


def test_bold_and_italic_come_from_the_font_name(tmp_path):
    path = write_pdf(
        tmp_path / "a.pdf",
        [[text(72, 700, 12, "b", "-Bold"), text(72, 680, 12, "i", "-Oblique")]],
    )

    runs = {run.text: run for run in reader.read(path).pages[0].runs}

    assert runs["b"].bold and not runs["b"].italic
    assert runs["i"].italic and not runs["i"].bold


def test_page_geometry_is_reported(tmp_path):
    path = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 12, "x")]])

    page = reader.read(path).pages[0]

    assert (page.width, page.height) == (612.0, 792.0)


def test_pages_can_be_selected(tmp_path):
    path = write_pdf(
        tmp_path / "a.pdf",
        [
            [text(72, 700, 12, "one")],
            [text(72, 700, 12, "two")],
            [text(72, 700, 12, "three")],
        ],
    )

    document = reader.read(path, pages=[0, 2])

    assert [page.runs[0].text for page in document.pages] == ["one", "three"]
    # Page numbers stay absolute, so a message about page 3 means page 3.
    assert [page.number for page in document.pages] == [0, 2]


def test_links_carry_their_rectangle_and_uri(tmp_path):
    path = write_pdf(
        tmp_path / "a.pdf",
        [[text(72, 700, 12, "the spec"), link(70, 695, 140, 712, "https://example.com/spec")]],
    )

    found = reader.read(path).pages[0].links[0]

    assert found.uri == "https://example.com/spec"
    assert found.y0 == pytest.approx(695, abs=1)


def test_images_carry_placement_and_bytes(tmp_path):
    path = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 12, "x"), image(100, 400, 200, 150)]])

    found = reader.read(path).pages[0].images[0]

    assert found.x == pytest.approx(100, abs=1)
    assert found.y == pytest.approx(400, abs=1)
    assert found.width == pytest.approx(200, abs=1)
    assert found.data


def test_images_are_skipped_when_not_wanted(tmp_path):
    path = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 12, "x"), image(100, 400, 200, 150)]])

    assert not any(box.data for box in reader.read(path, include_images=False).pages[0].images)


def test_outline_levels_come_from_nesting(tmp_path):
    path = write_pdf(
        tmp_path / "a.pdf",
        [[text(72, 700, 24, "Quarterly Report")], [text(72, 700, 18, "Appendix")]],
        outline=[("Quarterly Report", 1, 0), ("Appendix", 2, 1)],
    )

    entries = reader.read(path).outline

    assert [(entry.title, entry.level) for entry in entries] == [
        ("Quarterly Report", 1),
        ("Appendix", 2),
    ]


def test_outline_entries_know_their_page(tmp_path):
    path = write_pdf(
        tmp_path / "a.pdf",
        [[text(72, 700, 24, "One")], [text(72, 700, 24, "Two")]],
        outline=[("One", 1, 0), ("Two", 1, 1)],
    )

    assert [entry.page for entry in reader.read(path).outline] == [0, 1]


def test_a_page_of_image_and_no_text_is_scanned(tmp_path):
    path = write_pdf(tmp_path / "scan.pdf", [[image(0, 0, 612, 792)]])

    document = reader.read(path)

    assert document.pages[0].scanned
    assert document.scanned


def test_a_page_with_text_is_not_scanned(tmp_path):
    path = write_pdf(
        tmp_path / "a.pdf",
        [[text(72, 700, 12, "There is plenty of real text on this page here.")]],
    )

    assert not reader.read(path).pages[0].scanned


def test_a_small_image_on_a_bare_page_is_not_a_scan(tmp_path):
    # A logo on a near-empty title page must not condemn the whole document.
    path = write_pdf(tmp_path / "a.pdf", [[image(100, 700, 60, 40)]])

    assert not reader.read(path).pages[0].scanned


def test_a_file_that_is_not_a_pdf_is_rejected(tmp_path):
    path = tmp_path / "a.pdf"
    path.write_text("this is not a pdf at all")

    with pytest.raises(click.ClickException) as excinfo:
        reader.read(path)

    assert "not a PDF" in str(excinfo.value)


def test_each_run_reports_where_it_stops(tmp_path):
    path = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 12, "Quarterly")]])

    run = reader.read(path).pages[0].runs[0]

    assert run.end is not None
    assert run.end > run.x


def test_runs_on_one_line_report_their_own_positions(tmp_path):
    # Everything geometric downstream needs this. pypdf's own extraction
    # reports the line's origin for every run drawn after the first.
    path = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 12, "Left"), text(300, 700, 12, "Right")]])

    runs = {run.text: run for run in reader.read(path).pages[0].runs}

    assert runs["Left"].x == pytest.approx(72, abs=1)
    assert runs["Right"].x == pytest.approx(300, abs=1)


def test_painted_rectangles_are_reported(tmp_path):
    from tests.pdf_fixtures import rule

    path = write_pdf(tmp_path / "a.pdf", [[rule(100, 200, 50, 20), text(72, 700, 12, "x")]])

    rules = reader.read(path).pages[0].rules

    assert len(rules) == 1
    assert (rules[0].x0, rules[0].y0) == pytest.approx((100, 200))
    assert (rules[0].width, rules[0].height) == pytest.approx((50, 20))


def test_character_spacing_does_not_leak_past_its_own_q_block(tmp_path):
    # Tc belongs to the graphics state, so Q puts back what it was. A writer
    # squeezing one justified line must not shrink every line after it.
    from tests.pdf_fixtures import kerned

    plain = write_pdf(tmp_path / "plain.pdf", [[text(72, 700, 12, "Measured")]])
    after = write_pdf(
        tmp_path / "after.pdf",
        [[kerned(72, 720, 12, "Squeezed", -3), text(72, 700, 12, "Measured")]],
    )

    def width(path):
        run = next(r for r in reader.read(path).pages[0].runs if r.text == "Measured")
        return run.end - run.x

    assert width(after) == pytest.approx(width(plain), abs=0.01)


def test_character_spacing_lands_between_glyphs_not_after_the_last(tmp_path):
    # Tc moves the pen after every glyph, not once per string -- Word squeezes
    # and stretches whole lines with it, so a run's end is wrong by a glyph
    # per character if it is counted once. But the last glyph's spacing is
    # air, not ink: a run ends where its last glyph does, which is where the
    # next word starts when Word has packed the line this way.
    from tests.pdf_fixtures import kerned

    path = write_pdf(
        tmp_path / "tc.pdf",
        [[text(72, 720, 12, "Squeezed"), kerned(72, 700, 12, "Squeezed", -3)]],
    )

    runs = sorted(reader.read(path).pages[0].runs, key=lambda run: -run.y)
    natural, squeezed = (run.end - run.x for run in runs)

    assert squeezed == pytest.approx(natural - 3 * (len("Squeezed") - 1), abs=0.01)


def test_a_space_squeezed_to_nothing_is_not_a_space(tmp_path):
    # Word cancels a space's width with a negative character spacing and moves
    # the pen to the next word by hand. The glyph is in the string but takes no
    # room on the page, so it is not a gap -- reading it as one splits a word
    # such as "تهیه" in half.
    from tests.pdf_fixtures import kerned

    # Helvetica's space is 278/1000 of the size: 3.336 points at 12.
    path = write_pdf(
        tmp_path / "squeezed.pdf",
        [[kerned(72, 720, 12, "ab cd", -3.336), text(72, 700, 12, "ab cd")]],
    )

    runs = {round(run.y): run.text for run in reader.read(path).pages[0].runs}

    assert runs[720] == "abcd"
    assert runs[700] == "ab cd"


def test_two_squeezed_spaces_that_add_up_to_a_gap_are_one_space(tmp_path):
    # The same writer sets a real word gap as two space glyphs and squeezes
    # each so that together they take about one space. That is a gap, and one
    # of them -- not two, and not none.
    from tests.pdf_fixtures import kerned

    path = write_pdf(
        tmp_path / "pair.pdf",
        [[kerned(72, 700, 12, "ab  cd", -1.668)]],
    )

    assert [run.text for run in reader.read(path).pages[0].runs] == ["ab cd"]


def test_a_squeezed_space_standing_alone_still_marks_a_word_break(tmp_path):
    # A justified line squeezes its word gaps below what geometry alone would
    # call a space. The space glyph set on its own, taking no room, is then the
    # only evidence there is a break -- inside a word it is noise, but between
    # two runs it is the break, so it stays as a space that has no width.
    from tests.pdf_fixtures import kerned

    path = write_pdf(tmp_path / "alone.pdf", [[kerned(72, 700, 12, "  ", -3.336)]])

    (run,) = reader.read(path).pages[0].runs

    assert run.text == " "
    assert run.end == pytest.approx(run.x, abs=0.01)


def test_a_multi_letter_glyph_is_turned_to_face_paint_order():
    # One glyph, two letters: the file gives them in reading order while
    # everything around them is in paint order, so they have to be turned.
    class _Font:
        character_map = {"\x01": "لا", "\x02": "ﬁ"}

    class _Shown:
        _decoded_value = "\x01"
        font = _Font()
        text = "لا"

    assert reader._letters(_Shown()) == ("ال", "\x01\x01")

    # A Latin ligature sits in a line that is never reversed, so it stands.
    _Shown._decoded_value = "\x02"
    _Shown.text = "ﬁ"
    assert reader._letters(_Shown()) == ("ﬁ", "\x02")


def test_a_font_that_disagrees_with_itself_about_digits_is_repaired():
    # Digit glyphs come as a block of ten and a font draws one set. Eight
    # Persian and two ASCII is two wrong entries, not a font that mixes them.
    class _Font:
        character_map = dict(zip(map(chr, range(10)), "01۲۳۴۵۶۷۸۹"))

    assert reader._digits(_Font()) == {ord("0") + n: chr(0x06F0 + n) for n in range(10)}


def test_a_font_that_really_holds_both_digit_sets_is_left_alone():
    class _Font:
        character_map = dict(zip(map(chr, range(20)), "0123456789۰۱۲۳۴۵۶۷۸۹"))

    assert reader._digits(_Font()) == {}


def test_a_font_with_no_eastern_digits_is_left_alone():
    class _Font:
        character_map = dict(zip(map(chr, range(10)), "0123456789"))

    assert reader._digits(_Font()) == {}
