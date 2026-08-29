"""Tests for the Markdown to PDF converter.

The rendering tests need DejaVu installed; they skip cleanly when it is not.
"""

from __future__ import annotations

import re

import pytest

from pytoolbox import pymd2pdf
from pytoolbox.core import paths
from pytoolbox.mdpdf import document, fonts, media, render, shaping, state, tables
from pytoolbox.pymd2pdf import pymd2pdf_cli

has_fonts = paths.find_font("DejaVuSans.ttf") is not None
needs_fonts = pytest.mark.skipif(not has_fonts, reason="DejaVu fonts are not installed")
needs_shaper = pytest.mark.skipif(
    not shaping.HAS_SHAPER, reason="arabic-reshaper / python-bidi are not installed"
)

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
    assert render.strip_md(text) == expected


def test_is_rtl():
    assert shaping.is_rtl("سلام")
    assert not shaping.is_rtl("hello")
    assert not shaping.is_rtl("")
    assert shaping.is_rtl("hello سلام")


@needs_shaper
def test_shape_rtl_keeps_harakat():
    # "نُه" (nine) carries a damma on the noon; arabic_reshaper deletes
    # Harakat by default, which would reshape it as "نه" (no) instead.
    assert "ُ" in shaping.shape_rtl("نُه")


def test_strip_links_keeps_the_label():
    assert render.strip_links("see [Braintrust](https://b.example/) here") == (
        "see Braintrust here"
    )
    assert render.strip_links("![alt](img.png)") == "alt"
    assert render.strip_links("no link here") == "no link here"


def test_extract_title():
    assert pymd2pdf._extract_title(["intro", "# The **Title**", "more"]) == "The Title"
    assert pymd2pdf._extract_title(["no heading"]) == ""


def test_bullet_char_cycles_by_depth():
    assert render.bullet_char(0) == "•"
    assert render.bullet_char(2) == "◦"
    assert render.bullet_char(4) == "▪"
    assert render.bullet_char(99) == "▪"


def test_parse_table_row():
    assert tables.parse_table_row("| a | b |") == ["a", "b"]
    assert tables.parse_table_row("a | b") == ["a", "b"]


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        ("one<br>two", "one\ntwo"),
        ("one<br/>two", "one\ntwo"),
        ("one<br />two", "one\ntwo"),
        ("one<BR>two", "one\ntwo"),
        ("plain", "plain"),
    ],
)
def test_normalize_cell_turns_br_into_a_newline(cell, expected):
    assert tables._normalize_cell(cell) == expected


def test_task_regex():
    assert render.TASK_RE.match("[ ] todo").groups() == (" ", "todo")
    assert render.TASK_RE.match("[x] done").groups() == ("x", "done")
    assert render.TASK_RE.match("not a task") is None


def test_inline_split_keeps_spans_intact():
    parts = [p for p in render.INLINE_RE.split("a **b** c `d`") if p]
    assert "**b**" in parts
    assert "`d`" in parts


def test_looks_like_svg():
    assert media.looks_like_svg(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')
    assert media.looks_like_svg(b'<?xml version="1.0"?><svg></svg>')
    assert not media.looks_like_svg(b"\x89PNG\r\n")


def test_glyph_translation_only_substitutes_what_no_font_draws():
    covered = {ord("→"), ord("✅")}
    table = shaping.build_glyph_translation(covered)
    assert ord("→") not in table          # DejaVu can draw it: keep the arrow
    assert ord("✅") not in table
    assert table[ord("←")] == "<-"        # nothing can: fall back to text
    assert table[0xFE0F] == ""            # variation selector: dropped


def test_colour_status_emoji_are_always_substituted():
    # Even when the symbol font covers them: a one-colour PDF would draw
    # 🟢 and 🔴 as the same black disc.
    table = shaping.build_glyph_translation({ord("🟢"), ord("🔴"), ord("🟡")})
    assert table[ord("🟢")] == "●"
    assert table[ord("🟡")] == "◐"
    assert table[ord("🔴")] == "○"


def test_substitute_glyphs_uses_the_built_table(monkeypatch):
    monkeypatch.setattr(state, "glyph_translation", shaping.build_glyph_translation(set()))
    assert shaping.substitute_glyphs("done ✅️ and 🔴") == "done ✓ and ○"


def test_substitute_glyphs_is_a_no_op_before_fonts_are_loaded(monkeypatch):
    monkeypatch.setattr(state, "glyph_translation", {})
    assert shaping.substitute_glyphs("✅") == "✅"


def test_colour_emoji_fonts_are_not_offered_as_fallbacks(tmp_path):
    fake = tmp_path / "NotAFont.ttf"
    fake.write_bytes(b"not a font at all")
    assert not fonts.has_outlines(fake)


@needs_fonts
def test_dejavu_is_registered_as_a_fallback_face():
    pdf = document.PDF()
    assert any(
        key.startswith(fonts.FONT_SANS.lower()) for key in pdf._fallback_font_ids
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
    assert state.BODY_SIZE == 10


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


TABLE_BR = """# Test

| A | B |
| --- | --- |
| MARKSTART<br>MARKEND | x |
"""


@needs_fonts
def test_table_cell_br_becomes_a_real_line_break(tmp_path):
    """A ``<br>`` inside a table cell used to reach fpdf2 as literal text --
    it does not parse HTML the way it parses ``**bold**`` -- so the cell
    printed the tag instead of breaking. It is now turned into a real
    newline before the cell is measured or drawn.
    """
    pypdf = pytest.importorskip("pypdf")
    source = tmp_path / "doc.md"
    source.write_text(TABLE_BR, encoding="utf-8")
    target = tmp_path / "doc.pdf"
    pymd2pdf.convert(source, target, quiet=True)

    positions = {}

    def visitor(text, cm, tm, font_dict, font_size):
        if "MARKSTART" in text:
            positions["start"] = tm[5]
        elif "MARKEND" in text:
            positions["end"] = tm[5]

    reader = pypdf.PdfReader(target)
    text = reader.pages[-1].extract_text(visitor_text=visitor)
    assert "<br>" not in text
    assert positions.keys() == {"start", "end"}
    assert positions["start"] > positions["end"]


LONG_RTL_BLOCKQUOTE = """> پیشنهاد: در MVP، Fit Score یک عدد ۰ تا ۱۰۰ باشد و تنها معیار پذیرشِ آن بر پایه‌ی یک رویداد رفتاری منفرد و بدون‌ابهام باشد — مثلاً «مشتری حداقل یکی از پنج نامزد پیشنهادی را به مرحله‌ی گفت‌وگو دعوت کرد». معیارهای مبتنی بر نتیجه‌ی نهایی (تحویل موفق) در MVP قابل اندازه‌گیری نیستند، چون چرخه‌شان از عمر MVP طولانی‌تر است.
"""


@needs_fonts
def test_long_rtl_blockquote_does_not_orphan_a_word_onto_an_extra_line(tmp_path):
    """A wrapped RTL blockquote line used to double-count multi_cell's
    internal c_margin padding (once for wrapping, once inside multi_cell
    itself), so multi_cell re-wrapped its already bidi-reordered text and
    split a word off the end of the visual string onto its own orphan line.
    """
    pypdf = pytest.importorskip("pypdf")
    source = tmp_path / "doc.md"
    source.write_text(LONG_RTL_BLOCKQUOTE, encoding="utf-8")
    target = tmp_path / "doc.pdf"
    pymd2pdf.convert(source, target, title_page=False, quiet=True)

    line_ys = set()

    def visitor(text, cm, tm, font_dict, font_size):
        if text.strip() and "Page" not in text:
            line_ys.add(round(tm[5], 1))

    reader = pypdf.PdfReader(target)
    reader.pages[-1].extract_text(visitor_text=visitor)
    assert len(line_ys) == 3


WHOLE_BOLD_RTL_BLOCKS = """این پاراگراف پررنگ نیست.

**این پاراگراف باید به طور کامل پررنگ باشد.**

- **این آیتم لیست باید پررنگ باشد.**
- این آیتم لیست پررنگ نیست.

> **این نقل‌قول باید پررنگ باشد.**
"""


@needs_fonts
def test_whole_bold_rtl_blocks_render_in_bold(monkeypatch):
    """A paragraph/list-item/blockquote wrapped *entirely* in ``**bold**``
    used to render as plain text: the RTL branches of these renderers
    stripped markdown markers instead of parsing them (unlike the LTR path,
    which goes through render.render_rich), because RTL text has to be shaped and
    bidi-reordered before fpdf2 sees it, so its own markdown parser can no
    longer find markers inside it.
    """
    styles = []
    orig_set_font = document.PDF.set_font

    def spy(self, family, style="", size=0):
        if family == fonts.FONT_FA and size == state.BODY_SIZE:
            styles.append(style)
        return orig_set_font(self, family, style, size)

    monkeypatch.setattr(document.PDF, "set_font", spy)

    pdf = document.PDF(orientation="P", unit="mm", format="A4")
    if not pdf.has_persian:
        pytest.skip("no Persian font installed")
    pdf.doc_is_rtl = True
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    for line in WHOLE_BOLD_RTL_BLOCKS.splitlines():
        if not line.strip():
            continue
        if line.startswith("> "):
            render.add_blockquote(pdf, [line[2:]])
        elif line.startswith("- "):
            render.add_list_item(pdf, "  • ", line[2:], 0)
        else:
            render.add_paragraph(pdf, line)

    assert styles == ["", "B", "B", "", "B"]


def _vertical_rules(page, page_height_mm):
    """The vertical rules on a page, as ``(y_top_mm, y_bottom_mm)`` pairs.

    fpdf2 writes a rule as ``x y m x y l S`` in points, measured up from the
    bottom of the page; both are converted back to the top-down millimetres
    the renderers work in.
    """
    stream = page.get_contents().get_data().decode("latin-1")
    rules = []
    for m in re.finditer(r"([\d.]+) ([\d.]+) m ([\d.]+) ([\d.]+) l S", stream):
        x1, y1, x2, y2 = (float(g) for g in m.groups())
        if abs(x1 - x2) < 0.01:
            top, bottom = sorted(page_height_mm - y / (72 / 25.4) for y in (y1, y2))
            rules.append((top, bottom))
    return rules


@needs_fonts
def test_blockquote_bar_is_split_across_a_page_break(tmp_path):
    """A quote that spills onto the next page used to have its whole bar drawn
    on that next page: ``pdf.line`` was handed a start y from the page before,
    which it happily read as a y on the page that was current by then, running
    the bar down the side of several sections the quote had nothing to do with.
    """
    pypdf = pytest.importorskip("pypdf")
    pdf = document.PDF(orientation="P", unit="mm", format="A4")
    pdf.set_doc_title("title")
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()
    # Leave room for one line only, so the rest of the quote breaks over.
    pdf.set_y(pdf.h - pdf.b_margin - 12)
    render.add_blockquote(pdf, ["overflowing quote " * 40])
    assert pdf.page_no() == 2, "the quote was meant to break across pages"

    target = tmp_path / "doc.pdf"
    pdf.output(str(target))
    reader = pypdf.PdfReader(target)
    first, second = (_vertical_rules(page, 297) for page in reader.pages[:2])

    assert len(first) == 1 and len(second) == 1
    # The first page's share ends at its bottom margin, the second page's
    # starts below its running header -- not at the top of the page, and not
    # (as before) at a y borrowed from the page before it.
    assert first[0][1] == pytest.approx(297 - pdf.b_margin, abs=0.5)
    assert second[0][0] == pytest.approx(pdf.content_top, abs=0.5)
    assert second[0][1] < 297 / 2


RTL_CODE_FENCE = """# سناریو

```
\u0633\u0646\u0627\u0631\u06cc\u0648\u06cc \u067e\u0627\u06cc\u0647
  \u06f1\u06f5 \u0645\u0634\u062a\u0631\u06cc = \u06f2\u06f2\u06f5 \u06a9\u06cc\u0644\u0648
```

```
plain_ascii = 1
```
"""


@needs_fonts
def test_rtl_code_fence_is_shaped_and_right_aligned(monkeypatch, tmp_path):
    """Persian inside a fence used to be drawn as-is: unjoined letters in
    logical order, laid out left to right. Only the fence's own characters
    decide, so an ASCII snippet in a Persian document stays left-aligned.
    """
    drawn = []
    orig_cell = document.PDF.cell

    def spy(self, w=None, h=None, text="", *args, **kwargs):
        if kwargs.get("fill"):
            # fpdf2 lower-cases the family it records against the page state.
            drawn.append((text, self.font_family, kwargs.get("align")))
        return orig_cell(self, w, h, text, *args, **kwargs)

    monkeypatch.setattr(document.PDF, "cell", spy)

    source = tmp_path / "doc.md"
    source.write_text(RTL_CODE_FENCE, encoding="utf-8")
    pymd2pdf.convert(source, tmp_path / "doc.pdf", title_page=False, quiet=True)
    if not document.PDF(format="A4").has_persian:
        pytest.skip("no Persian font installed")

    persian = [d for d in drawn if shaping.is_rtl(d[0])]
    ascii_only = [d for d in drawn if d[0].strip() == "plain_ascii = 1"]
    assert persian, "no Persian code line was drawn"
    assert all(
        family == fonts.FONT_FA.lower() and align == "R"
        for _, family, align in persian
    )
    assert [d[2] for d in ascii_only] == ["L"]
    # Shaped text is written in presentation forms, not the source code points.
    assert all("\u0633\u0646\u0627" not in text for text, _, _ in persian)


@needs_fonts
def test_ascii_code_fence_in_an_rtl_document_stays_ltr():
    pdf = document.PDF(orientation="P", unit="mm", format="A4")
    if not pdf.has_persian:
        pytest.skip("no Persian font installed")
    pdf.doc_is_rtl = True
    assert not render.code_block_is_rtl(pdf, ["def f():", "    return 1"])
    assert render.code_block_is_rtl(pdf, ["# \u0633\u0644\u0627\u0645"])


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
    monkeypatch.setattr(state, "offline", True)
    monkeypatch.setattr(state, "HAS_MMDC", False)
    monkeypatch.setattr(state, "mermaid_net_warned", False)

    def explode(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("the network was used despite --offline")

    monkeypatch.setattr(media, "render_mermaid_ink", explode)
    assert media.render_mermaid("graph TD; A-->B;") is None


def test_font_size_reaches_styled_spans(tmp_path, monkeypatch):
    """--font-size must scale bold/italic runs, not just plain body text."""
    source = tmp_path / "styled.md"
    source.write_text("Plain with **bold** and *italic* inside.\n", encoding="utf-8")

    seen: list[tuple[str, float]] = []
    original = document.PDF.set_font

    def spy(self, family=None, style="", size=0):
        seen.append((style, size))
        return original(self, family, style, size)

    monkeypatch.setattr(document.PDF, "set_font", spy)
    pymd2pdf.convert(source, tmp_path / "styled.pdf", font_size=20, quiet=True)

    body_sizes = {size for style, size in seen if style in ("", "B", "I") and size}
    assert state.BODY_SIZE not in body_sizes, (
        f"styled runs fell back to the import-time default: {sorted(body_sizes)}"
    )


@needs_fonts
def test_cover_page_shows_only_the_title(tmp_path):
    """The cover used to print the source file's name under the title. It was
    the one line that skipped shaping and bidi reordering, so a Persian name
    came out backwards -- and it told the reader nothing the title did not.
    """
    pypdf = pytest.importorskip("pypdf")

    source = tmp_path / "\u0646\u0642\u0634\u0647 \u0634\u0646\u0627\u062e\u062a.md"
    source.write_text("# The Title\n\nBody text.\n", encoding="utf-8")
    target = tmp_path / "doc.pdf"
    pymd2pdf.convert(source, target, quiet=True)

    cover = pypdf.PdfReader(target).pages[0].extract_text()
    assert "The Title" in cover
    assert ".md" not in cover
    assert source.stem not in cover
