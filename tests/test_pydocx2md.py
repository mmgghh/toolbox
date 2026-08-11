"""End-to-end tests for the pydocx2md command."""

from __future__ import annotations

import zipfile

from pytoolbox.pydocx2md import docx2md_cli
from tests.docx_fixtures import build_docx, comment, commented, comments, para, run

W15 = 'xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml"'
W14 = 'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml"'
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

IMAGE_RELS = (
    '<?xml version="1.0"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId5" Target="media/image1.png" '
    'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"/>'
    "</Relationships>"
)
DRAWING = (
    "<w:r><w:drawing><wp:inline>"
    '<wp:docPr id="1" name="Picture 1" descr="a chart"/>'
    '<a:graphic><a:graphicData><pic:pic><pic:blipFill><a:blip r:embed="rId5"/>'
    "</pic:blipFill></pic:pic></a:graphicData></a:graphic>"
    "</wp:inline></w:drawing></w:r>"
)
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def test_writes_a_markdown_file_beside_the_input(runner, tmp_path):
    source = build_docx(tmp_path / "report.docx", para("Title", style="Heading1") + para("Body."))
    result = runner.invoke(docx2md_cli, [str(source)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "# Title\n\nBody.\n"


def test_comments_are_anchored_and_quoted(runner, tmp_path):
    body = para(runs=run("The system supports ") + commented("1", run("offline mode")))
    source = build_docx(
        tmp_path / "spec.docx",
        body,
        parts={"word/comments.xml": comments(comment("1", "Sara Ahmadi", text="In scope for v1?"))},
    )
    result = runner.invoke(docx2md_cli, [str(source)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "spec.md").read_text(encoding="utf-8") == (
        "The system supports offline mode **[1]**\n"
        "\n"
        "> **[1]** Sara Ahmadi · 2026-03-14\n"
        "> In scope for v1?\n"
    )


def test_a_comment_inside_a_table_cell_lands_after_the_table(runner, tmp_path):
    cell = para(runs=commented("1", run("Offline")))
    body = (
        "<w:tbl>"
        f"<w:tr><w:tc>{para('Feature')}</w:tc></w:tr>"
        f"<w:tr><w:tc>{cell}</w:tc></w:tr>"
        "</w:tbl>"
    )
    source = build_docx(
        tmp_path / "t.docx",
        body,
        parts={"word/comments.xml": comments(comment("1", "Ali", text="scope?"))},
    )
    result = runner.invoke(docx2md_cli, [str(source)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "t.md").read_text(encoding="utf-8") == (
        "| Feature |\n"
        "| --- |\n"
        "| Offline **[1]** |\n"
        "\n"
        "> **[1]** Ali · 2026-03-14\n"
        "> scope?\n"
    )


def test_no_comments_flag_drops_markers_and_bodies(runner, tmp_path):
    body = para(runs=commented("1", run("offline mode")))
    source = build_docx(
        tmp_path / "spec.docx",
        body,
        parts={"word/comments.xml": comments(comment("1", "Sara", text="note"))},
    )
    result = runner.invoke(docx2md_cli, [str(source), "--no-comments"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "spec.md").read_text(encoding="utf-8") == "offline mode\n"


def test_images_are_extracted_next_to_the_output(runner, tmp_path):
    source = build_docx(
        tmp_path / "doc.docx",
        para(runs=DRAWING),
        parts={"word/_rels/document.xml.rels": IMAGE_RELS, "word/media/image1.png": PNG},
    )
    result = runner.invoke(docx2md_cli, [str(source)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "doc.md").read_text(encoding="utf-8") == "![a chart](doc.assets/image1.png)\n"
    assert (tmp_path / "doc.assets" / "image1.png").read_bytes() == PNG


def test_no_images_flag_writes_no_assets_directory(runner, tmp_path):
    source = build_docx(
        tmp_path / "doc.docx",
        para(runs=DRAWING),
        parts={"word/_rels/document.xml.rels": IMAGE_RELS, "word/media/image1.png": PNG},
    )
    result = runner.invoke(docx2md_cli, [str(source), "--no-images"])
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "doc.assets").exists()


def test_deleted_text_does_not_reach_the_output(runner, tmp_path):
    body = para(
        runs=run("keep ") + '<w:del w:id="1" w:author="a"><w:r><w:delText>drop</w:delText></w:r></w:del>'
    )
    source = build_docx(tmp_path / "rev.docx", body)
    result = runner.invoke(docx2md_cli, [str(source)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "rev.md").read_text(encoding="utf-8") == "keep\n"


def test_output_path_can_be_named(runner, tmp_path):
    source = build_docx(tmp_path / "in.docx", para("x"))
    result = runner.invoke(docx2md_cli, [str(source), "-o", str(tmp_path / "out.md")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out.md").exists()


def test_naming_an_output_for_several_inputs_is_refused(runner, tmp_path):
    a = build_docx(tmp_path / "a.docx", para("a"))
    b = build_docx(tmp_path / "b.docx", para("b"))
    result = runner.invoke(docx2md_cli, [str(a), str(b), "-o", str(tmp_path / "out.md")])
    assert result.exit_code != 0
    assert "single input" in result.stderr


def test_several_inputs_write_one_file_each(runner, tmp_path):
    a = build_docx(tmp_path / "a.docx", para("first"))
    b = build_docx(tmp_path / "b.docx", para("second"))
    result = runner.invoke(docx2md_cli, [str(a), str(b), "-d", str(tmp_path / "out")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "out" / "a.md").read_text(encoding="utf-8") == "first\n"
    assert (tmp_path / "out" / "b.md").read_text(encoding="utf-8") == "second\n"


def test_quiet_prints_nothing_on_success(runner, tmp_path):
    source = build_docx(tmp_path / "a.docx", para("x"))
    result = runner.invoke(docx2md_cli, [str(source), "-q"])
    assert result.exit_code == 0
    assert result.stdout == ""


def test_the_output_path_is_reported_by_default(runner, tmp_path):
    source = build_docx(tmp_path / "a.docx", para("x"))
    result = runner.invoke(docx2md_cli, [str(source)])
    assert "a.md" in result.stdout


def test_a_file_that_is_not_a_docx_reports_why(runner, tmp_path):
    bogus = tmp_path / "notes.docx"
    bogus.write_text("plain text", encoding="utf-8")
    result = runner.invoke(docx2md_cli, [str(bogus)])
    assert result.exit_code != 0
    assert "not a Word" in result.stderr


def test_a_legacy_doc_suggests_converting(runner, tmp_path):
    old = tmp_path / "old.docx"
    old.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    result = runner.invoke(docx2md_cli, [str(old)])
    assert result.exit_code != 0
    assert "Word 97" in result.stderr


def test_one_bad_file_does_not_stop_the_others(runner, tmp_path):
    good = build_docx(tmp_path / "good.docx", para("fine"))
    bad = tmp_path / "bad.docx"
    bad.write_text("nope", encoding="utf-8")
    result = runner.invoke(docx2md_cli, [str(bad), str(good)])
    assert result.exit_code != 0, "a failure must still be reported in the exit code"
    assert (tmp_path / "good.md").exists(), "the readable file should still convert"


def test_persian_text_survives_unchanged(runner, tmp_path):
    persian = "سلام دنیا"
    source = build_docx(tmp_path / "fa.docx", para(persian))
    result = runner.invoke(docx2md_cli, [str(source)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "fa.md").read_text(encoding="utf-8") == f"{persian}\n"


def test_a_threaded_reply_is_nested_under_its_parent(runner, tmp_path):
    def with_para_id(cid, author, para_id, text):
        return (
            f'<w:comment w:id="{cid}" w:author="{author}" w:date="2026-03-14T10:00:00Z">'
            f'<w:p {W14} w14:paraId="{para_id}"><w:r><w:t>{text}</w:t></w:r></w:p></w:comment>'
        )

    body = para(runs=commented("1", run("offline")) + commented("2", run(" mode")))
    extended = (
        f'<?xml version="1.0"?><w:commentsEx {W} {W15}>'
        '<w15:commentEx w15:paraId="AAAA1111" w15:done="0"/>'
        '<w15:commentEx w15:paraId="BBBB2222" w15:paraIdParent="AAAA1111" w15:done="0"/>'
        "</w:commentsEx>"
    )
    source = build_docx(
        tmp_path / "thread.docx",
        body,
        parts={
            "word/comments.xml": comments(
                with_para_id("1", "Sara", "AAAA1111", "In scope?"),
                with_para_id("2", "Mohammad", "BBBB2222", "Yes."),
            ),
            "word/commentsExtended.xml": extended,
        },
    )
    result = runner.invoke(docx2md_cli, [str(source)])
    assert result.exit_code == 0, result.output
    out = (tmp_path / "thread.md").read_text(encoding="utf-8")
    assert "> **[1]** Sara · 2026-03-14" in out
    assert "> > **[1.1]** Mohammad · 2026-03-14" in out
    assert "**[2]**" not in out


def test_an_existing_output_file_is_overwritten(runner, tmp_path):
    source = build_docx(tmp_path / "a.docx", para("new"))
    (tmp_path / "a.md").write_text("stale", encoding="utf-8")
    result = runner.invoke(docx2md_cli, [str(source)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "a.md").read_text(encoding="utf-8") == "new\n"


def test_an_encrypted_document_says_so(runner, tmp_path):
    path = tmp_path / "locked.docx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("EncryptedPackage", b"\x00")
    result = runner.invoke(docx2md_cli, [str(path)])
    assert result.exit_code != 0
    assert "password" in result.stderr
