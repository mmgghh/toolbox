"""Tests for pydocx2pdf.

The engines are exercised separately: LibreOffice is stubbed out everywhere
except one end-to-end test that skips when it is not installed, and the
Markdown pipeline needs DejaVu and skips without it.
"""

from __future__ import annotations

import pytest

from pytoolbox import pydocx2pdf
from pytoolbox.core import paths
from pytoolbox.pydocx2pdf import convert, docx2pdf_cli, find_soffice
from tests.docx_fixtures import build_docx, para

has_fonts = paths.find_font("DejaVuSans.ttf") is not None
needs_fonts = pytest.mark.skipif(not has_fonts, reason="DejaVu fonts are not installed")
needs_soffice = pytest.mark.skipif(find_soffice() is None, reason="LibreOffice is not installed")

BODY = para("Report", style="Heading1") + para("A paragraph.") + para("Another one.")


@pytest.fixture
def document(tmp_path):
    return build_docx(tmp_path / "report.docx", BODY)


@pytest.fixture
def no_soffice(monkeypatch):
    """A machine with no LibreOffice on it -- a phone, say."""
    monkeypatch.setattr(pydocx2pdf.shutil, "which", lambda name: None)
    monkeypatch.setattr(pydocx2pdf, "SOFFICE_PATHS", ())


@pytest.fixture
def fake_soffice(monkeypatch, tmp_path):
    """LibreOffice, stubbed: records what it was asked to convert."""
    calls = []

    def fake(source, destination, binary=None, timeout=None):
        calls.append((source, destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"%PDF-1.4 stub\n")
        return destination

    monkeypatch.setattr(pydocx2pdf, "find_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(pydocx2pdf, "convert_with_libreoffice", fake)
    return calls


# ── engine choice ───────────────────────────────────────────────────


def test_auto_prefers_libreoffice(document, tmp_path, fake_soffice):
    assert convert(document, tmp_path / "out.pdf") == "libreoffice"
    assert fake_soffice == [(document, tmp_path / "out.pdf")]


@needs_fonts
def test_auto_falls_back_to_markdown_without_libreoffice(document, tmp_path, no_soffice):
    assert convert(document, tmp_path / "out.pdf") == "markdown"
    assert (tmp_path / "out.pdf").read_bytes().startswith(b"%PDF")


@needs_fonts
def test_auto_falls_back_when_libreoffice_fails(document, tmp_path, monkeypatch):
    """A PDF of the content beats no PDF at all."""
    import click

    monkeypatch.setattr(pydocx2pdf, "find_soffice", lambda: "/usr/bin/soffice")
    monkeypatch.setattr(
        pydocx2pdf,
        "convert_with_libreoffice",
        lambda *a, **k: (_ for _ in ()).throw(click.ClickException("boom")),
    )
    assert convert(document, tmp_path / "out.pdf") == "markdown"
    assert (tmp_path / "out.pdf").exists()


def test_asking_for_libreoffice_without_it_says_what_to_do(document, tmp_path, no_soffice, runner):
    result = runner.invoke(docx2pdf_cli, [str(document), "--engine", "libreoffice"])
    assert result.exit_code != 0
    assert "--engine markdown" in result.stderr


def test_markdown_engine_never_calls_libreoffice(document, tmp_path, fake_soffice, runner):
    result = runner.invoke(docx2pdf_cli, [str(document), "--engine", "markdown", "-o", str(tmp_path / "x.pdf")])
    assert result.exit_code == 0, result.output
    assert fake_soffice == []


# ── the Markdown pipeline ───────────────────────────────────────────


@needs_fonts
def test_keep_md_leaves_the_intermediate_markdown(document, tmp_path, no_soffice, runner):
    result = runner.invoke(docx2pdf_cli, [str(document), "--keep-md"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "report.pdf").exists()
    assert (tmp_path / "report.md").read_text(encoding="utf-8").startswith("# Report")


@needs_fonts
def test_the_intermediate_markdown_is_cleaned_up_by_default(document, tmp_path, no_soffice, runner):
    result = runner.invoke(docx2pdf_cli, [str(document)])
    assert result.exit_code == 0, result.output
    assert list(tmp_path.glob("*.md")) == []


@needs_fonts
def test_comments_are_left_out_unless_asked_for(tmp_path, no_soffice):
    """A PDF is usually the shareable copy, not the review copy."""
    from tests.docx_fixtures import comment, commented, comments, run

    source = build_docx(
        tmp_path / "reviewed.docx",
        para(runs=commented("1", run("Body text."))),
        {"word/comments.xml": comments(comment("1", "Ann", text="Fix this."))},
    )
    convert(source, tmp_path / "plain.pdf", keep_md=tmp_path / "plain.md")
    assert "Fix this." not in (tmp_path / "plain.md").read_text(encoding="utf-8")

    convert(source, tmp_path / "reviewed.pdf", comments=True, keep_md=tmp_path / "reviewed.md")
    assert "Fix this." in (tmp_path / "reviewed.md").read_text(encoding="utf-8")


# ── CLI plumbing ────────────────────────────────────────────────────


def test_batch_writes_one_pdf_per_input(tmp_path, fake_soffice, runner):
    sources = [build_docx(tmp_path / f"{name}.docx", BODY) for name in ("a", "b")]
    out = tmp_path / "pdfs"
    result = runner.invoke(docx2pdf_cli, [*(str(s) for s in sources), "-d", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "a.pdf").exists() and (out / "b.pdf").exists()


def test_output_option_rejects_a_batch(tmp_path, fake_soffice, runner):
    sources = [build_docx(tmp_path / f"{name}.docx", BODY) for name in ("a", "b")]
    result = runner.invoke(docx2pdf_cli, [*(str(s) for s in sources), "-o", str(tmp_path / "one.pdf")])
    assert result.exit_code != 0


def test_one_bad_file_does_not_abandon_the_batch(tmp_path, fake_soffice, runner):
    good = build_docx(tmp_path / "good.docx", BODY)
    broken = tmp_path / "broken.docx"
    broken.write_bytes(b"not a zip")
    result = runner.invoke(docx2pdf_cli, [str(broken), str(good), "--engine", "markdown"])
    assert result.exit_code == 1
    assert (tmp_path / "good.pdf").exists()


# ── the real thing ──────────────────────────────────────────────────


#: LibreOffice refuses a package whose main part is not declared, which the
#: shared fixture leaves out because pytoolbox's own reader does not need it.
CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml"
    ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""


@needs_soffice
def test_libreoffice_really_converts_a_document(tmp_path):
    source = build_docx(tmp_path / "real.docx", BODY, {"[Content_Types].xml": CONTENT_TYPES})
    assert convert(source, tmp_path / "real.pdf", engine="libreoffice") == "libreoffice"
    assert (tmp_path / "real.pdf").read_bytes().startswith(b"%PDF")
