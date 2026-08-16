"""Tests for the Markdown to PDF converter.

The rendering tests need DejaVu installed; they skip cleanly when it is not.
"""

from __future__ import annotations

import pytest

from pytoolbox import pymd2pdf
from pytoolbox.core import paths
from pytoolbox.pymd2pdf import pymd2pdf_cli

has_fonts = paths.find_font("DejaVuSans.ttf") is not None
needs_fonts = pytest.mark.skipif(not has_fonts, reason="DejaVu fonts are not installed")

SAMPLE = """# Title

A paragraph with **bold**, *italic*, `code` and a [link](https://example.com).

> quoted line

- [ ] todo
- [x] done
- plain
  - nested

1. one
2. two

| A | B |
| --- | --- |
| 1 | 2 |

```python
print("hi")
```

---

## بخش فارسی

این یک پاراگراف است.
"""


# ── pure helpers ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("**bold**", "bold"),
        ("*italic*", "italic"),
        ("~~struck~~", "struck"),
        ("`code`", "code"),
        ("[label](https://example.com)", "label"),
        ("![alt](img.png)", "alt"),
        ("mix **a** and `b`", "mix a and b"),
    ],
)
def test_strip_md(text, expected):
    assert pymd2pdf._strip_md(text) == expected


def test_is_rtl():
    assert pymd2pdf._is_rtl("سلام")
    assert not pymd2pdf._is_rtl("hello")
    assert not pymd2pdf._is_rtl("")
    assert pymd2pdf._is_rtl("hello سلام")


def test_strip_links_keeps_the_label():
    assert pymd2pdf._strip_links("see [Braintrust](https://b.example/) here") == (
        "see Braintrust here"
    )
    assert pymd2pdf._strip_links("![alt](img.png)") == "alt"
    assert pymd2pdf._strip_links("no link here") == "no link here"


def test_extract_title():
    assert pymd2pdf._extract_title(["intro", "# The **Title**", "more"]) == "The Title"
    assert pymd2pdf._extract_title(["no heading"]) == ""


def test_bullet_char_cycles_by_depth():
    assert pymd2pdf._bullet_char(0) == "•"
    assert pymd2pdf._bullet_char(2) == "◦"
    assert pymd2pdf._bullet_char(4) == "▪"
    assert pymd2pdf._bullet_char(99) == "▪"


def test_parse_table_row():
    assert pymd2pdf._parse_table_row("| a | b |") == ["a", "b"]
    assert pymd2pdf._parse_table_row("a | b") == ["a", "b"]


def test_task_regex():
    assert pymd2pdf._TASK_RE.match("[ ] todo").groups() == (" ", "todo")
    assert pymd2pdf._TASK_RE.match("[x] done").groups() == ("x", "done")
    assert pymd2pdf._TASK_RE.match("not a task") is None


def test_inline_split_keeps_spans_intact():
    parts = [p for p in pymd2pdf._INLINE_RE.split("a **b** c `d`") if p]
    assert "**b**" in parts
    assert "`d`" in parts


def test_looks_like_svg():
    assert pymd2pdf._looks_like_svg(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    assert pymd2pdf._looks_like_svg(b'<?xml version="1.0"?><svg></svg>')
    assert not pymd2pdf._looks_like_svg(b"\x89PNG\r\n")


def test_glyph_translation_only_substitutes_what_no_font_draws():
    covered = {ord("→"), ord("✅")}
    table = pymd2pdf._build_glyph_translation(covered)
    assert ord("→") not in table          # DejaVu can draw it: keep the arrow
    assert ord("✅") not in table
    assert table[ord("←")] == "<-"        # nothing can: fall back to text
    assert table[0xFE0F] == ""            # variation selector: dropped


def test_colour_status_emoji_are_always_substituted():
    # Even when the symbol font covers them: a one-colour PDF would draw
    # 🟢 and 🔴 as the same black disc.
    table = pymd2pdf._build_glyph_translation({ord("🟢"), ord("🔴"), ord("🟡")})
    assert table[ord("🟢")] == "●"
    assert table[ord("🟡")] == "◐"
    assert table[ord("🔴")] == "○"


def test_substitute_glyphs_uses_the_built_table(monkeypatch):
    monkeypatch.setattr(
        pymd2pdf, "_glyph_translation", pymd2pdf._build_glyph_translation(set())
    )
    assert pymd2pdf._substitute_glyphs("done ✅️ and 🔴") == "done ✓ and ○"


def test_substitute_glyphs_is_a_no_op_before_fonts_are_loaded(monkeypatch):
    monkeypatch.setattr(pymd2pdf, "_glyph_translation", {})
    assert pymd2pdf._substitute_glyphs("✅") == "✅"


def test_colour_emoji_fonts_are_not_offered_as_fallbacks(tmp_path):
    fake = tmp_path / "NotAFont.ttf"
    fake.write_bytes(b"not a font at all")
    assert not pymd2pdf._has_outlines(fake)


@needs_fonts
def test_dejavu_is_registered_as_a_fallback_face():
    pdf = pymd2pdf.PDF()
    assert any(
        key.startswith(pymd2pdf.FONT_SANS.lower()) for key in pdf._fallback_font_ids
    )


def test_font_dirs_include_termux_paths_when_on_termux(monkeypatch, tmp_path):
    prefix = tmp_path / "termux" / "usr"
    prefix.mkdir(parents=True)
    monkeypatch.setattr(paths, "termux_prefix", lambda: prefix)
    dirs = paths.font_dirs()
    assert prefix / "share/fonts" in dirs


# ── conversion ──────────────────────────────────────────────────────

@needs_fonts
def test_convert_produces_a_pdf(tmp_path):
    source = tmp_path / "doc.md"
    source.write_text(SAMPLE, encoding="utf-8")
    target = tmp_path / "doc.pdf"
    pymd2pdf.convert(source, target, quiet=True)
    assert target.exists()
    assert target.read_bytes().startswith(b"%PDF")


@needs_fonts
def test_convert_respects_page_and_font_options(tmp_path):
    source = tmp_path / "doc.md"
    source.write_text(SAMPLE, encoding="utf-8")
    a4 = tmp_path / "a4.pdf"
    a5 = tmp_path / "a5.pdf"
    pymd2pdf.convert(source, a4, page_size="A4", quiet=True)
    pymd2pdf.convert(source, a5, page_size="A5", quiet=True)
    assert a4.exists() and a5.exists()
    # The module-level body size must be restored after each conversion.
    pymd2pdf.convert(source, a4, font_size=14, quiet=True)
    assert pymd2pdf.BODY_SIZE == 10


TABLE_LINKS = """# Links

A [body link](https://example.com/body) first, so the page already has an
annotation when the table is drawn.

| Ref | Note |
| --- | --- |
| [Whole](https://example.com/whole) | cell is only a link |
| see [Inline](https://example.com/inline) here | link inside text |

## جدول

| مرجع | توضیح |
| --- | --- |
| [Farsi](https://example.com/farsi) | سلول فارسی |
"""


@needs_fonts
@pytest.mark.parametrize(
    ("url", "count"),
    [
        ("https://example.com/whole", 1),   # a whole-cell link stays clickable
        ("https://example.com/farsi", 1),   # ... in an RTL table too
        ("https://example.com/inline", 0),  # a link inside other text: label only
    ],
)
def test_table_cell_links_become_annotations(tmp_path, url, count):
    source = tmp_path / "doc.md"
    source.write_text(TABLE_LINKS, encoding="utf-8")
    target = tmp_path / "doc.pdf"
    pymd2pdf.convert(source, target, quiet=True)
    data = target.read_bytes()
    # Annotation dictionaries are written uncompressed. Counting them also
    # guards against fpdf2's cell-measuring pass leaking a second, misplaced
    # copy -- see _isolated_annotations.
    assert data.count(f"/URI ({url})".encode()) == count


@needs_fonts
def test_table_cells_show_the_label_not_the_link_markup(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    source = tmp_path / "doc.md"
    source.write_text(TABLE_LINKS, encoding="utf-8")
    target = tmp_path / "doc.pdf"
    pymd2pdf.convert(source, target, quiet=True)
    text = "".join(page.extract_text() for page in pypdf.PdfReader(target).pages)
    assert "Whole" in text and "Inline" in text
    assert "](" not in text
    assert "example.com" not in text


LONG_RTL_TABLE_CELL = """# Test

| شرح | مقدار |
| --- | --- |
| MARKSTART این یک متن بسیار طولانی فارسی است که باید در چند خط داخل جدول بپیچد تا مشکل ترتیب خطوط را نشان دهد و این جمله همچنان ادامه دارد تا برسد به کلمه MARKEND |
"""


@needs_fonts
def test_long_rtl_table_cell_wraps_lines_in_reading_order(tmp_path):
    """A cell that wraps must keep the start of the sentence on the first
    physical line -- reordering the whole cell into visual order before
    fpdf2 wraps it (instead of wrapping first, like paragraphs/list items
    do) put the tail of the sentence on the first line instead.
    """
    pypdf = pytest.importorskip("pypdf")
    source = tmp_path / "doc.md"
    source.write_text(LONG_RTL_TABLE_CELL, encoding="utf-8")
    target = tmp_path / "doc.pdf"
    pymd2pdf.convert(source, target, quiet=True)

    positions = {}

    def visitor(text, cm, tm, font_dict, font_size):
        if "MARKSTART" in text:
            positions["start"] = tm[5]
        elif "MARKEND" in text:
            positions["end"] = tm[5]

    reader = pypdf.PdfReader(target)
    reader.pages[-1].extract_text(visitor_text=visitor)
    assert positions.keys() == {"start", "end"}
    # PDF y-coordinates increase upward, so the first (higher) physical line
    # has the larger y.
    assert positions["start"] > positions["end"]


@needs_fonts
def test_cli_writes_next_to_the_input(runner, tmp_path):
    source = tmp_path / "doc.md"
    source.write_text("# Hi\n\ntext\n", encoding="utf-8")
    result = runner.invoke(pymd2pdf_cli, [str(source), "--offline", "-q"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "doc.pdf").exists()


@needs_fonts
def test_cli_output_dir(runner, tmp_path):
    source = tmp_path / "doc.md"
    source.write_text("# Hi\n", encoding="utf-8")
    out_dir = tmp_path / "pdfs"
    result = runner.invoke(pymd2pdf_cli, [str(source), "-d", str(out_dir), "-q", "--offline"])
    assert result.exit_code == 0, result.output
    assert (out_dir / "doc.pdf").exists()


def test_cli_rejects_output_with_multiple_inputs(runner, tmp_path):
    first = tmp_path / "a.md"
    second = tmp_path / "b.md"
    first.write_text("# a\n", encoding="utf-8")
    second.write_text("# b\n", encoding="utf-8")
    result = runner.invoke(pymd2pdf_cli, [str(first), str(second), "-o", str(tmp_path / "x.pdf")])
    assert result.exit_code != 0
    assert "single input file" in result.stderr


def test_cli_rejects_output_and_output_dir_together(runner, tmp_path):
    source = tmp_path / "a.md"
    source.write_text("# a\n", encoding="utf-8")
    result = runner.invoke(
        pymd2pdf_cli, [str(source), "-o", str(tmp_path / "x.pdf"), "-d", str(tmp_path)]
    )
    assert result.exit_code != 0


def test_offline_blocks_the_mermaid_web_fallback(monkeypatch):
    monkeypatch.setattr(pymd2pdf, "_offline", True)
    monkeypatch.setattr(pymd2pdf, "_HAS_MMDC", False)
    monkeypatch.setattr(pymd2pdf, "_mermaid_net_warned", False)

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the network was used despite --offline")

    monkeypatch.setattr(pymd2pdf, "_render_mermaid_ink", explode)
    assert pymd2pdf._render_mermaid("graph TD; A-->B;") is None
