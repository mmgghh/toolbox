"""Tests for pymd2html: the Markdown parser, the page, and the CLI."""

from __future__ import annotations

import pytest

from pytoolbox.pymd2html import (
    DEFAULT_CSS,
    convert,
    extract_title,
    is_rtl,
    md2html_cli,
    render_body,
    render_document,
    render_inline,
)


def body(markdown: str) -> str:
    return render_body(markdown)


# ── inline ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("**bold**", "<strong>bold</strong>"),
        ("__bold__", "<strong>bold</strong>"),
        ("*italic*", "<em>italic</em>"),
        ("_italic_", "<em>italic</em>"),
        ("***both***", "<strong><em>both</em></strong>"),
        ("~~gone~~", "<del>gone</del>"),
        ("`a * b`", "<code>a * b</code>"),
        ("``a ` b``", "<code>a ` b</code>"),
        ("[t](https://x.dev)", '<a href="https://x.dev">t</a>'),
        ("![a](i.png)", '<img src="i.png" alt="a">'),
        ("<https://x.dev>", '<a href="https://x.dev">https://x.dev</a>'),
        ("go to https://x.dev.", 'go to <a href="https://x.dev">https://x.dev</a>.'),
        ("a\\*b\\*c", "a*b*c"),
    ],
)
def test_inline_constructs(markdown, expected):
    assert render_inline(markdown) == expected


def test_underscores_inside_a_word_are_left_alone():
    """snake_case_names must survive a Markdown pass unchanged."""
    assert render_inline("call some_long_name(x)") == "call some_long_name(x)"


def test_text_that_looks_like_a_tag_is_escaped():
    assert render_inline("use 5 < 6 & 7 > 2") == "use 5 &lt; 6 &amp; 7 &gt; 2"


def test_emphasis_reaches_across_a_link():
    assert render_inline("**see [x](y)**") == '<strong>see <a href="y">x</a></strong>'


def test_markdown_inside_a_code_span_stays_literal():
    assert render_inline("`**not bold**`") == "<code>**not bold**</code>"


def test_a_link_that_would_run_code_becomes_text():
    """A Markdown file you did not write is untrusted input."""
    rendered = render_inline("[click](javascript:alert(1))")
    assert "javascript:" not in rendered
    assert rendered == "click"


def test_two_trailing_spaces_are_a_line_break():
    assert "<br>" in body("one  \ntwo")


# ── blocks ──────────────────────────────────────────────────────────


def test_headings_get_anchors():
    assert body("## Shell completion") == '<h2 id="shell-completion">Shell completion</h2>'


def test_repeated_headings_get_distinct_anchors():
    rendered = body("# Notes\n\n# Notes")
    assert 'id="notes"' in rendered and 'id="notes-2"' in rendered


def test_paragraphs_and_rules():
    assert body("one\n\ntwo") == "<p>one</p>\n<p>two</p>"
    assert body("---") == "<hr>"


def test_fenced_code_keeps_its_language_and_stays_literal():
    rendered = body("```python\nif a < b:\n    pass\n```")
    assert rendered == '<pre><code class="language-python">if a &lt; b:\n    pass</code></pre>'


def test_unclosed_fence_still_closes_the_document():
    assert body("```\nx\n") == "<pre><code>x</code></pre>"


def test_mermaid_fence_becomes_an_embedded_svg_image(monkeypatch):
    monkeypatch.setattr("pytoolbox.pymd2html.render_mermaid", lambda source, offline=False: b"<svg></svg>")
    rendered = body("```mermaid\ngraph TD\nA-->B\n```")
    assert rendered == (
        '<img class="mermaid" alt="Mermaid diagram" '
        'src="data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=">'
    )


def test_mermaid_fence_falls_back_to_a_code_block_when_rendering_fails(monkeypatch):
    monkeypatch.setattr("pytoolbox.pymd2html.render_mermaid", lambda source, offline=False: None)
    rendered = body("```mermaid\ngraph TD\nA-->B\n```")
    assert rendered == '<pre><code class="language-mermaid">graph TD\nA--&gt;B</code></pre>'


def test_nested_lists():
    rendered = body("- one\n- two\n  - deep\n")
    assert rendered.count("<ul>") == 2
    assert "<li>deep</li>" in rendered


def test_ordered_list_keeps_its_starting_number():
    assert '<ol start="3">' in body("3. three\n4. four")


def test_task_list_becomes_checkboxes():
    rendered = body("- [x] done\n- [ ] todo")
    assert '<input type="checkbox" disabled checked>done' in rendered
    assert '<input type="checkbox" disabled>todo' in rendered


def test_loose_list_items_are_paragraphs():
    """A blank line between items is the author asking for air."""
    tight = body("- one\n- two")
    loose = body("- one\n\n- two")
    assert "<p>" not in tight
    assert "<li><p>one</p></li>" in loose


def test_blockquote_holds_blocks():
    rendered = body("> ## title\n> and text")
    assert rendered.startswith("<blockquote>")
    assert '<h2 id="title">title</h2>' in rendered


def test_table_alignment_and_short_rows():
    rendered = body("| a | b |\n|:--|--:|\n| 1 | 2 |\n| 3 |\n")
    assert '<th style="text-align:left">a</th>' in rendered
    assert '<td style="text-align:right">2</td>' in rendered
    # The header decides the column count; a short row is padded, not dropped.
    assert rendered.count("<tr>") == 3
    assert '<td style="text-align:right"></td>' in rendered


def test_a_pipe_without_a_divider_row_is_not_a_table():
    assert "<table>" not in body("a | b\nc | d")


def test_escaped_pipe_stays_inside_its_cell():
    rendered = body("| a | b |\n|---|---|\n| x \\| y | z |")
    assert "<td>x | y</td>" in rendered


def test_escaped_pipe_inside_a_code_span_is_a_pipe():
    """`\\|` is the only way to write one in a cell, backticks or not."""
    rendered = body("| op | means |\n|---|---|\n| `\\|` | or |")
    assert "<td><code>|</code></td>" in rendered


def test_raw_html_passes_through_but_can_be_escaped():
    assert body('<div class="x">hi</div>') == '<div class="x">hi</div>'
    assert "&lt;div" in render_body('<div class="x">hi</div>', escape_html=True)


# ── document ────────────────────────────────────────────────────────


def test_document_is_self_contained():
    """One file that opens anywhere: no stylesheet fetch, no font request."""
    page = render_document("# Hi\n\ntext")
    assert page.startswith("<!DOCTYPE html>")
    assert DEFAULT_CSS in page
    assert "http://" not in page and "https://" not in page


def test_title_comes_from_the_first_heading():
    assert extract_title("# The **real** title\n\n# later") == "The real title"
    assert "<title>The real title</title>" in render_document("# The **real** title")


def test_persian_documents_get_a_right_to_left_page():
    assert is_rtl("سلام دنیا") is True
    assert is_rtl("hello world") is False
    assert '<html dir="rtl">' in render_document("# سلام\n\nمتن فارسی")
    assert '<html lang="fa" dir="rtl">' in render_document("متن", lang="fa", rtl=True)
    assert 'dir="rtl"' not in render_document("متن فارسی", rtl=False)


# ── CLI ─────────────────────────────────────────────────────────────


def test_cli_writes_a_page_beside_the_input(runner, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes\n\nbody", encoding="utf-8")
    result = runner.invoke(md2html_cli, [str(source)])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "notes.html").read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_cli_writes_to_stdout(runner, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes", encoding="utf-8")
    result = runner.invoke(md2html_cli, [str(source), "-o", "-"])
    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("<!DOCTYPE html>")


def test_cli_fragment_has_no_page_chrome(runner, tmp_path):
    source = tmp_path / "notes.md"
    source.write_text("# Notes", encoding="utf-8")
    result = runner.invoke(md2html_cli, [str(source), "--fragment", "-o", "-"])
    assert result.stdout.strip() == '<h1 id="notes">Notes</h1>'


def test_cli_converts_a_batch_into_one_directory(runner, tmp_path):
    for name in ("a", "b"):
        (tmp_path / f"{name}.md").write_text(f"# {name}", encoding="utf-8")
    out = tmp_path / "site"
    result = runner.invoke(md2html_cli, [str(tmp_path / "a.md"), str(tmp_path / "b.md"), "-d", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "a.html").exists() and (out / "b.html").exists()


def test_cli_custom_stylesheet(runner, tmp_path):
    source = tmp_path / "n.md"
    source.write_text("# n", encoding="utf-8")
    css = tmp_path / "mine.css"
    css.write_text("body { color: red; }\n", encoding="utf-8")

    styled = runner.invoke(md2html_cli, [str(source), "--css", str(css), "-o", "-"])
    assert "body { color: red; }" in styled.stdout
    assert DEFAULT_CSS not in styled.stdout

    bare = runner.invoke(md2html_cli, [str(source), "--no-css", "-o", "-"])
    assert "<style>" not in bare.stdout


def test_cli_rejects_contradictory_options(runner, tmp_path):
    source = tmp_path / "n.md"
    source.write_text("# n", encoding="utf-8")
    assert runner.invoke(md2html_cli, [str(source), "--rtl", "--ltr"]).exit_code != 0
    assert runner.invoke(md2html_cli, [str(source), "-o", "x.html", "-d", "out"]).exit_code != 0


def test_convert_returns_the_path_it_wrote(tmp_path):
    source = tmp_path / "n.md"
    source.write_text("# n", encoding="utf-8")
    assert convert(source, tmp_path / "out" / "n.html").exists()
