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
