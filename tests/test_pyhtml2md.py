"""End-to-end and unit tests for the pyhtml2md command."""

from __future__ import annotations

from pytoolbox.pyhtml2md import html2md_cli, html_to_markdown


def test_writes_a_markdown_file_beside_the_input(runner, tmp_path):
    source = tmp_path / "page.html"
    source.write_text("<h1>Title</h1><p>Body.</p>", encoding="utf-8")

    result = runner.invoke(html2md_cli, [str(source)])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "page.md").read_text(encoding="utf-8") == "# Title\n\nBody.\n"


def test_output_option_writes_to_a_specific_path(runner, tmp_path):
    source = tmp_path / "page.html"
    source.write_text("<p>Hi</p>", encoding="utf-8")

    result = runner.invoke(html2md_cli, [str(source), "-o", str(tmp_path / "notes.md")])

    assert result.exit_code == 0, result.output
    assert (tmp_path / "notes.md").read_text(encoding="utf-8") == "Hi\n"


def test_output_option_rejects_multiple_inputs(runner, tmp_path):
    a = tmp_path / "a.html"
    b = tmp_path / "b.html"
    a.write_text("<p>a</p>", encoding="utf-8")
    b.write_text("<p>b</p>", encoding="utf-8")

    result = runner.invoke(html2md_cli, [str(a), str(b), "-o", str(tmp_path / "out.md")])

    assert result.exit_code != 0
    assert "single input file" in result.output


def test_output_dir_writes_one_file_per_input(runner, tmp_path):
    a = tmp_path / "a.html"
    b = tmp_path / "b.html"
    a.write_text("<p>a</p>", encoding="utf-8")
    b.write_text("<p>b</p>", encoding="utf-8")
    out = tmp_path / "md"

    result = runner.invoke(html2md_cli, [str(a), str(b), "-d", str(out)])

    assert result.exit_code == 0, result.output
    assert (out / "a.md").read_text(encoding="utf-8") == "a\n"
    assert (out / "b.md").read_text(encoding="utf-8") == "b\n"


def test_quiet_suppresses_progress_output(runner, tmp_path):
    source = tmp_path / "page.html"
    source.write_text("<p>Hi</p>", encoding="utf-8")

    result = runner.invoke(html2md_cli, [str(source), "-q"])

    assert result.exit_code == 0, result.output
    assert result.output == ""


# ── Headings, emphasis, links, images ──────────────────────────────


def test_headings():
    assert html_to_markdown("<h1>One</h1><h2>Two</h2><h6>Six</h6>") == "# One\n\n## Two\n\n###### Six\n"


def test_strong_and_em_and_code():
    md = html_to_markdown("<p><strong>bold</strong> <em>italic</em> <code>x = 1</code></p>")
    assert md == "**bold** *italic* `x = 1`\n"


def test_strikethrough():
    assert html_to_markdown("<p><del>old</del></p>") == "~~old~~\n"


def test_code_span_with_backticks_gets_a_wider_fence():
    md = html_to_markdown("<p><code>a `b` c</code></p>")
    assert md == "``a `b` c``\n"


def test_link_with_title():
    md = html_to_markdown('<p><a href="https://x.test" title="X">go</a></p>')
    assert md == '[go](https://x.test "X")\n'


def test_link_without_href_falls_back_to_raw_html():
    md = html_to_markdown('<p><a name="anchor">text</a></p>')
    assert '<a name="anchor">text</a>' in md


def test_image_with_alt():
    md = html_to_markdown('<img src="pic.png" alt="A picture">')
    assert md == "![A picture](pic.png)\n"


# ── Lists ───────────────────────────────────────────────────────────


def test_unordered_list():
    md = html_to_markdown("<ul><li>one</li><li>two</li></ul>")
    assert md == "- one\n- two\n"


def test_ordered_list():
    md = html_to_markdown("<ol><li>one</li><li>two</li></ol>")
    assert md == "1. one\n2. two\n"


def test_task_list():
    md = html_to_markdown(
        '<ul><li><input type="checkbox" checked>done</li><li><input type="checkbox">todo</li></ul>'
    )
    assert md == "- [x] done\n- [ ] todo\n"


def test_nested_list_is_indented():
    md = html_to_markdown("<ul><li>one<ul><li>nested</li></ul></li></ul>")
    assert md == "- one\n  - nested\n"


def test_top_level_list_keeps_a_blank_line_after_a_paragraph():
    md = html_to_markdown("<p>Intro.</p><ul><li>one</li></ul>")
    assert md == "Intro.\n\n- one\n"


# ── Blockquote, code block, rule ───────────────────────────────────


def test_blockquote():
    assert html_to_markdown("<blockquote><p>Quoted.</p></blockquote>") == "> Quoted.\n"


def test_pre_code_block_with_language():
    md = html_to_markdown('<pre><code class="language-python">x = 1\ny = 2</code></pre>')
    assert md == "```python\nx = 1\ny = 2\n```\n"


def test_horizontal_rule():
    assert html_to_markdown("<p>a</p><hr><p>b</p>") == "a\n\n---\n\nb\n"


# ── Tables ──────────────────────────────────────────────────────────


def test_table_with_header():
    md = html_to_markdown(
        "<table><tr><th>A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>"
    )
    assert md == "| A | B |\n| --- | --- |\n| 1 | 2 |\n"


def test_table_cell_alignment():
    md = html_to_markdown(
        '<table><tr><th style="text-align:right">A</th></tr><tr><td>1</td></tr></table>'
    )
    assert md == "| A |\n| ---: |\n| 1 |\n"


def test_table_cell_pipe_is_escaped():
    md = html_to_markdown("<table><tr><td>a | b</td></tr></table>")
    assert "a \\| b" in md


# ── Content-preservation guarantees ────────────────────────────────


def test_unknown_block_tag_with_no_markdown_equivalent_passes_through_as_raw_html():
    # <fieldset> has no Markdown mapping and holds real form content, so it
    # is one of the tags that must stay as raw HTML rather than be unwrapped.
    md = html_to_markdown('<fieldset><p>Careful.</p></fieldset>')
    assert "<fieldset>" in md
    assert "Careful." in md
    assert "</fieldset>" in md


def test_unknown_inline_tag_passes_through_as_raw_html():
    md = html_to_markdown("<p>Water is H<sub>2</sub>O.</p>")
    assert "<sub>2</sub>" in md


def test_iframe_src_is_preserved():
    md = html_to_markdown('<iframe src="https://example.test/embed"></iframe>')
    assert 'src="https://example.test/embed"' in md


def test_block_level_raw_tag_separates_content_with_blank_lines():
    # The open/close tags must sit alone on their own line: a raw HTML block
    # runs verbatim to the next blank line and nothing inside it is
    # reprocessed as Markdown, so text glued to the tags would either show up
    # as literal "**bold**" or (worse) corrupt whatever line the closing tag
    # lands on, such as a table's last row.
    md = html_to_markdown('<fieldset>Some <strong>bold</strong> text.</fieldset>')
    assert md == "<fieldset>\n\nSome **bold** text.\n\n</fieldset>\n"


def test_block_level_raw_tag_does_not_corrupt_a_trailing_table():
    md = html_to_markdown('<fieldset><table><tr><td>a</td></tr></table></fieldset>')
    assert "| --- |\n\n</fieldset>" in md


def test_empty_block_level_raw_tag_stays_on_one_line():
    md = html_to_markdown('<fieldset></fieldset>')
    assert md == "<fieldset></fieldset>\n"


# ── Layout wrappers are unwrapped, not passed through ──────────────


def test_div_is_unwrapped_keeping_only_its_content():
    md = html_to_markdown('<div class="note"><p>Careful.</p></div>')
    assert md == "Careful.\n"


def test_span_is_unwrapped_inline():
    md = html_to_markdown('<p>Water is <span class="chem">H2O</span>.</p>')
    assert md == "Water is H2O.\n"


def test_sectioning_tags_are_unwrapped():
    md = html_to_markdown(
        "<header class='top'><h1>Title</h1></header>"
        "<main><article><section><p>Body.</p></section></article></main>"
        "<footer><p>Footer.</p></footer>"
    )
    assert md == "# Title\n\nBody.\n\nFooter.\n"
    assert "<" not in md


def test_html_comment_round_trips():
    md = html_to_markdown("<p>a</p><!-- note --><p>b</p>")
    assert "<!-- note -->" in md


def test_script_and_style_content_is_dropped():
    md = html_to_markdown("<style>body{color:red}</style><script>alert(1)</script><p>Text.</p>")
    assert "color:red" not in md
    assert "alert(1)" not in md
    assert md == "Text.\n"


def test_head_metadata_is_dropped_but_body_survives():
    md = html_to_markdown(
        "<html><head><title>Ignored</title><meta charset='utf-8'></head>"
        "<body><p>Kept.</p></body></html>"
    )
    assert "Ignored" not in md
    assert md == "Kept.\n"


def test_literal_markdown_syntax_characters_are_escaped():
    md = html_to_markdown("<p>1 * 2 = 2, and [x] or _y_ or `z`</p>")
    assert md == "1 \\* 2 = 2, and \\[x\\] or \\_y\\_ or \\`z\\`\n"


def test_leading_marker_lookalike_is_escaped():
    md = html_to_markdown("<p># not a heading</p>")
    assert md == "\\# not a heading\n"


def test_malformed_unclosed_tags_do_not_lose_trailing_content():
    md = html_to_markdown("<p>one<p>two<p>three")
    assert "one" in md and "two" in md and "three" in md
