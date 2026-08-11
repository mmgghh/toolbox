"""Reading a PDF into positioned runs."""

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

    with pytest.raises(Exception) as excinfo:
        reader.read(path)

    assert "not a PDF" in str(excinfo.value)
