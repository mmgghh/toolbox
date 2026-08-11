"""The pypdf2md command."""

import click
import pytest

pytest.importorskip("pypdf")

from pytoolbox.pypdf2md import parse_pages, pdf2md_cli  # noqa: E402
from tests.pdf_fixtures import image, text, write_pdf  # noqa: E402


def test_parse_pages_accepts_ranges_and_singles():
    assert parse_pages("1-3,7") == [0, 1, 2, 6]


def test_parse_pages_sorts_and_deduplicates():
    assert parse_pages("3,1,3") == [0, 2]


def test_parse_pages_rejects_nonsense():
    with pytest.raises(click.UsageError):
        parse_pages("banana")


def test_parse_pages_rejects_a_backwards_range():
    with pytest.raises(click.UsageError):
        parse_pages("9-2")


def test_parse_pages_rejects_page_zero():
    # Pages are numbered as a reader would name them, from one.
    with pytest.raises(click.UsageError):
        parse_pages("0")


def test_converting_writes_a_markdown_file_beside_the_input(runner, tmp_path):
    source = write_pdf(
        tmp_path / "report.pdf",
        [
            [
                text(72, 740, 24, "Quarterly Report"),
                text(72, 700, 10, "Revenue grew twelve percent."),
            ]
        ],
    )

    result = runner.invoke(pdf2md_cli, [str(source)])

    assert result.exit_code == 0, result.output
    written = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "# Quarterly Report" in written
    assert "Revenue grew twelve percent." in written


def test_output_path_can_be_given(runner, tmp_path):
    source = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 10, "Hello there world.")]])

    result = runner.invoke(pdf2md_cli, [str(source), "-o", str(tmp_path / "out.md")])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "out.md").exists()


def test_output_path_rejects_several_inputs(runner, tmp_path):
    first = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 10, "One page here.")]])
    second = write_pdf(tmp_path / "b.pdf", [[text(72, 700, 10, "Two pages here.")]])

    result = runner.invoke(pdf2md_cli, [str(first), str(second), "-o", str(tmp_path / "x.md")])

    assert result.exit_code != 0
    assert "single input" in result.stderr


def test_several_inputs_each_get_their_own_file(runner, tmp_path):
    first = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 10, "First document text.")]])
    second = write_pdf(tmp_path / "b.pdf", [[text(72, 700, 10, "Second document text.")]])
    out = tmp_path / "md"

    result = runner.invoke(pdf2md_cli, [str(first), str(second), "-d", str(out)])

    assert result.exit_code == 0, result.output
    assert (out / "a.md").exists() and (out / "b.md").exists()


def test_one_bad_file_does_not_stop_the_others(runner, tmp_path):
    good = write_pdf(tmp_path / "good.pdf", [[text(72, 700, 10, "Good document here.")]])
    bad = tmp_path / "bad.pdf"
    bad.write_text("not a pdf")

    result = runner.invoke(pdf2md_cli, [str(bad), str(good)])

    assert result.exit_code == 1
    assert (tmp_path / "good.md").exists()
    assert "not a PDF" in result.stderr


def test_images_are_extracted_beside_the_markdown(runner, tmp_path):
    source = write_pdf(
        tmp_path / "r.pdf",
        [[text(72, 700, 10, "Text above the picture."), image(100, 300, 200, 150)]],
    )

    result = runner.invoke(pdf2md_cli, [str(source)])

    assert result.exit_code == 0, result.output
    assets = tmp_path / "r.assets"
    assert assets.is_dir() and any(assets.iterdir())
    assert "r.assets/" in (tmp_path / "r.md").read_text(encoding="utf-8")


def test_no_images_leaves_no_assets_directory(runner, tmp_path):
    source = write_pdf(
        tmp_path / "r.pdf",
        [[text(72, 700, 10, "Text above the picture."), image(100, 300, 200, 150)]],
    )

    runner.invoke(pdf2md_cli, [str(source), "--no-images"])

    assert not (tmp_path / "r.assets").exists()


def test_pages_can_be_selected(runner, tmp_path):
    source = write_pdf(
        tmp_path / "b.pdf",
        [[text(72, 700, 10, "Page one content.")], [text(72, 700, 10, "Page two content.")]],
    )

    runner.invoke(pdf2md_cli, [str(source), "--pages", "2"])

    written = (tmp_path / "b.md").read_text(encoding="utf-8")
    assert "Page two" in written and "Page one" not in written


def test_a_scanned_pdf_says_what_to_do(runner, tmp_path):
    source = write_pdf(tmp_path / "scan.pdf", [[image(0, 0, 612, 792)]])

    result = runner.invoke(pdf2md_cli, [str(source)])

    assert result.exit_code != 0
    assert "scanned" in result.stderr
    assert "ocrmypdf" in result.stderr


def test_quiet_prints_nothing_on_success(runner, tmp_path):
    source = write_pdf(tmp_path / "a.pdf", [[text(72, 700, 10, "Some text here.")]])

    result = runner.invoke(pdf2md_cli, [str(source), "-q"])

    assert result.exit_code == 0
    assert result.stdout.strip() == ""


def test_page_breaks_are_off_by_default(runner, tmp_path):
    source = write_pdf(
        tmp_path / "a.pdf",
        [[text(72, 700, 10, "Page one content.")], [text(72, 700, 10, "Page two content.")]],
    )

    runner.invoke(pdf2md_cli, [str(source)])

    assert "---" not in (tmp_path / "a.md").read_text(encoding="utf-8")


def test_page_breaks_can_be_asked_for(runner, tmp_path):
    source = write_pdf(
        tmp_path / "a.pdf",
        [[text(72, 700, 10, "Page one content.")], [text(72, 700, 10, "Page two content.")]],
    )

    runner.invoke(pdf2md_cli, [str(source), "--page-breaks"])

    assert "---" in (tmp_path / "a.md").read_text(encoding="utf-8")


def test_a_two_column_page_reads_column_by_column(runner, tmp_path):
    left = [text(72, 700 - i * 12, 10, f"left line number {i}") for i in range(6)]
    right = [text(330, 700 - i * 12, 10, f"right line number {i}") for i in range(6)]
    source = write_pdf(tmp_path / "paper.pdf", [left + right])

    runner.invoke(pdf2md_cli, [str(source)])

    written = (tmp_path / "paper.md").read_text(encoding="utf-8")
    assert written.index("left line number 5") < written.index("right line number 0")


def test_single_column_overrides_detection(runner, tmp_path):
    left = [text(72, 700 - i * 12, 10, f"left line number {i}") for i in range(6)]
    right = [text(330, 700 - i * 12, 10, f"right line number {i}") for i in range(6)]
    source = write_pdf(tmp_path / "paper.pdf", [left + right])

    runner.invoke(pdf2md_cli, [str(source), "--single-column"])

    written = (tmp_path / "paper.md").read_text(encoding="utf-8")
    assert written.index("right line number 0") < written.index("left line number 5")
