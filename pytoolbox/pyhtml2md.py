"""Convert HTML files to Markdown.

Exposes the ``pyhtml2md`` console script, also available as ``toolbox html2md``.

Needs no optional dependency: parsing uses the standard library's
``html.parser``, so this works on a bare install and on Termux.

Tags with a clean Markdown equivalent -- headings, paragraphs, lists (plain,
ordered and task), blockquotes, code blocks and spans, tables, rules, links,
images, emphasis, strong, strikethrough -- are converted to Markdown syntax.
Everything else (``<div>``, ``<span>``, ``<figure>``, ``<iframe>``, ``<svg>``,
custom elements, HTML comments...) passes through verbatim as raw HTML, the
mirror image of ``pymd2html``'s raw-HTML passthrough. Nothing a browser would
render is discarded -- only ``<script>``, ``<style>`` and ``<head>`` metadata,
which a Markdown reader would never see either.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional, Union

import click

from pytoolbox.core.options import CONTEXT_SETTINGS, quiet_option, version_option

# ═══════════════════════════════════════════════════════════════════
# Parsing: HTML -> a small element tree
# ═══════════════════════════════════════════════════════════════════

#: Elements with no closing tag; a matching handle_endtag never arrives.
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

#: Never rendered by a browser, so dropped entirely rather than passed
#: through -- their content is code, CSS or document metadata, not text.
_DROPPED_TAGS = frozenset({"script", "style", "head", "title"})

#: <meta>, <link>, <base> are void *and* metadata: dropped outright, no node.
_DROPPED_VOID_TAGS = frozenset({"meta", "link", "base"})

#: Transparent wrappers: their children render as if at the parent's level.
_TRANSPARENT_TAGS = frozenset({"html", "body"})


class _Node:
    """One HTML element, as much of it as the renderer needs."""

    __slots__ = ("tag", "attrs", "raw_start", "children")

    def __init__(self, tag: str, attrs: dict, raw_start: str = "") -> None:
        self.tag = tag
        self.attrs = attrs
        self.raw_start = raw_start
        self.children: list[Union[_Node, str, _Comment]] = []


class _Comment:
    """An HTML comment, kept so it round-trips into the Markdown output."""

    __slots__ = ("data",)

    def __init__(self, data: str) -> None:
        self.data = data


class _TreeBuilder(HTMLParser):
    """Turns an HTML document into a tree of :class:`_Node`.

    Unbalanced tags in real-world HTML are common; closing an element pops
    the stack up to (and including) the nearest matching open tag rather
    than requiring an exact match, so a stray unclosed ``<li>`` or ``<p>``
    doesn't derail everything after it.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("", {})
        self._stack: list[_Node] = [self.root]
        self._skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self._open(tag, attrs, self_closing=False)

    def handle_startendtag(self, tag: str, attrs: list) -> None:
        self._open(tag, attrs, self_closing=True)

    def _open(self, tag: str, attrs: list, self_closing: bool) -> None:
        if tag in _DROPPED_VOID_TAGS:
            return
        if tag in _DROPPED_TAGS:
            self._skip_stack.append(tag)
            return
        if self._skip_stack:
            return
        node = _Node(tag, dict(attrs), self.get_starttag_text() or f"<{tag}>")
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS and not self_closing:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        if self._skip_stack:
            if self._skip_stack[-1] == tag:
                self._skip_stack.pop()
            return
        for depth in range(len(self._stack) - 1, 0, -1):
            if self._stack[depth].tag == tag:
                del self._stack[depth:]
                return
        # No matching open tag on the stack: a stray close tag, ignore it.

    def handle_data(self, data: str) -> None:
        if self._skip_stack:
            return
        self._stack[-1].children.append(data)

    def handle_comment(self, data: str) -> None:
        if self._skip_stack:
            return
        self._stack[-1].children.append(_Comment(data))


# ═══════════════════════════════════════════════════════════════════
# Rendering: element tree -> Markdown text
# ═══════════════════════════════════════════════════════════════════

_HEADING_LEVEL = {f"h{n}": n for n in range(1, 7)}
_STRONG_TAGS = frozenset({"strong", "b"})
_EM_TAGS = frozenset({"em", "i", "cite", "dfn"})
_STRIKE_TAGS = frozenset({"del", "s", "strike"})
_LIST_TAGS = frozenset({"ul", "ol"})
_KNOWN_BLOCK_TAGS = frozenset({"p", "blockquote", "pre", "table", "hr", *_HEADING_LEVEL, *_LIST_TAGS})

#: HTML5 elements that are block-level by spec, even with no Markdown syntax
#: of their own -- an unknown one of these still breaks paragraph flow when
#: it is passed through as raw HTML.
_BLOCK_LEVEL_HTML = frozenset(
    {
        "address",
        "article",
        "aside",
        "canvas",
        "details",
        "dialog",
        "dd",
        "div",
        "dl",
        "dt",
        "fieldset",
        "figcaption",
        "figure",
        "footer",
        "form",
        "header",
        "hgroup",
        "iframe",
        "main",
        "menu",
        "nav",
        "noscript",
        "picture",
        "section",
        "summary",
        "svg",
        "video",
        "audio",
    }
)

#: Escaped everywhere inline so source text can't be misread as syntax.
_INLINE_ESCAPE = re.compile(r"[\\`*_\[\]<]")
#: Escaped only at the start of an assembled line/paragraph, where these
#: characters (and not elsewhere) would open a heading, rule, list or quote.
_LEADING_MARKER = re.compile(r"^(\s*)([-+>#]|\d{1,9}[.)])")


def _escape_inline(text: str) -> str:
    return _INLINE_ESCAPE.sub(lambda m: "\\" + m.group(0), text)


def _escape_leading_marker(text: str) -> str:
    match = _LEADING_MARKER.match(text)
    if not match:
        return text
    indent, marker = match.group(1), match.group(2)
    return f"{indent}\\{marker}{text[match.end():]}"


def _text_only(node: _Node) -> str:
    """A node's descendant text, ignoring any markup -- for code content."""
    parts = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif isinstance(child, _Node):
            parts.append(_text_only(child))
    return "".join(parts)


def _flatten_transparent(nodes):
    for node in nodes:
        if isinstance(node, _Node) and node.tag in _TRANSPARENT_TAGS:
            yield from _flatten_transparent(node.children)
        else:
            yield node


def _is_block(node) -> bool:
    if not isinstance(node, _Node):
        return False
    return node.tag in _KNOWN_BLOCK_TAGS or node.tag in _BLOCK_LEVEL_HTML


def _render_raw(node: _Node) -> str:
    """Reserialize an element with no Markdown mapping, verbatim."""
    if node.tag in _VOID_TAGS:
        return node.raw_start
    if node.tag in _BLOCK_LEVEL_HTML:
        inner = _render_block(node.children)
    else:
        inner = _render_inline(node.children)
    return f"{node.raw_start}{inner}</{node.tag}>"


def _render_inline_node(node) -> str:
    if isinstance(node, str):
        return _escape_inline(re.sub(r"\s+", " ", node))
    if isinstance(node, _Comment):
        return f"<!--{node.data}-->"
    tag = node.tag
    if tag == "br":
        return "  \n"
    if tag in _STRONG_TAGS:
        inner = _render_inline(node.children).strip()
        return f"**{inner}**" if inner else ""
    if tag in _EM_TAGS:
        inner = _render_inline(node.children).strip()
        return f"*{inner}*" if inner else ""
    if tag in _STRIKE_TAGS:
        inner = _render_inline(node.children).strip()
        return f"~~{inner}~~" if inner else ""
    if tag == "code":
        return _render_code_span(node)
    if tag == "a":
        return _render_link(node)
    if tag == "img":
        return _render_image(node)
    return _render_raw(node)


def _render_inline(nodes) -> str:
    text = "".join(_render_inline_node(node) for node in nodes)
    return re.sub(r"[ \t]+", " ", text)


def _render_code_span(node: _Node) -> str:
    content = _text_only(node).replace("\n", " ")
    if not content:
        return ""
    runs = re.findall(r"`+", content)
    fence = "`" * (max((len(r) for r in runs), default=0) + 1)
    pad = " " if content.startswith("`") or content.endswith("`") else ""
    return f"{fence}{pad}{content}{pad}{fence}"


def _render_link(node: _Node) -> str:
    href = node.attrs.get("href")
    if not href:
        return _render_raw(node)
    text = _render_inline(node.children).strip() or href
    title = node.attrs.get("title")
    dest = f"<{href}>" if " " in href else href
    title_part = f' "{title}"' if title else ""
    return f"[{text}]({dest}{title_part})"


def _render_image(node: _Node) -> str:
    src = node.attrs.get("src")
    if not src:
        return _render_raw(node)
    alt = node.attrs.get("alt") or ""
    title = node.attrs.get("title")
    dest = f"<{src}>" if " " in src else src
    title_part = f' "{title}"' if title else ""
    return f"![{alt}]({dest}{title_part})"


def _render_block_node(node: _Node) -> str:
    tag = node.tag
    if tag in _HEADING_LEVEL:
        text = _render_inline(node.children).strip()
        return f"{'#' * _HEADING_LEVEL[tag]} {text}" if text else ""
    if tag == "p":
        text = _render_inline(node.children).strip()
        return _escape_leading_marker(text) if text else ""
    if tag == "hr":
        return "---"
    if tag == "blockquote":
        return _render_blockquote(node)
    if tag == "pre":
        return _render_pre(node)
    if tag in _LIST_TAGS:
        return _render_list(node, ordered=(tag == "ol"))
    if tag == "table":
        return _render_table(node)
    return _render_raw(node)


def _split_blocks(nodes) -> list[tuple[str, bool]]:
    """Sibling blocks, each paired with whether it is a list."""
    blocks: list[tuple[str, bool]] = []
    buffer: list = []

    def flush() -> None:
        if buffer:
            text = _render_inline(buffer).strip()
            if text:
                blocks.append((_escape_leading_marker(text), False))
            buffer.clear()

    for node in _flatten_transparent(nodes):
        if _is_block(node):
            flush()
            rendered = _render_block_node(node)
            if rendered.strip():
                is_list = isinstance(node, _Node) and node.tag in _LIST_TAGS
                blocks.append((rendered, is_list))
        else:
            buffer.append(node)
    flush()
    return blocks


def _render_block(nodes) -> str:
    return "\n\n".join(text for text, _is_list in _split_blocks(nodes))


def _render_li_body(nodes) -> str:
    """A list item's content, joined the way a hand-written nested list is:

    a blank line between blocks in general, but none between a block and a
    list that immediately follows it, since that's the conventional, tight
    way to write nesting.
    """
    parts: list[str] = []
    for index, (text, is_list) in enumerate(_split_blocks(nodes)):
        if index == 0:
            parts.append(text)
        else:
            parts.append(("\n" if is_list else "\n\n") + text)
    return "".join(parts)


def _render_blockquote(node: _Node) -> str:
    inner = _render_block(node.children)
    if not inner:
        return ""
    return "\n".join(f"> {line}" if line else ">" for line in inner.split("\n"))


def _render_pre(node: _Node) -> str:
    code_child = None
    if len(node.children) == 1 and isinstance(node.children[0], _Node) and node.children[0].tag == "code":
        code_child = node.children[0]
    content = _text_only(code_child or node).strip("\n")
    lang = ""
    if code_child is not None:
        for cls in (code_child.attrs.get("class") or "").split():
            if cls.startswith("language-"):
                lang = cls[len("language-") :]
                break
    fence = "```"
    while fence in content:
        fence += "`"
    return f"{fence}{lang}\n{content}\n{fence}"


def _task_checkbox(li: _Node) -> Optional[str]:
    for child in li.children:
        if isinstance(child, str) and not child.strip():
            continue
        if isinstance(child, _Node) and child.tag == "input" and (child.attrs.get("type") or "").lower() == "checkbox":
            return "[x]" if "checked" in child.attrs else "[ ]"
        return None
    return None


def _without_leading_checkbox(children) -> list:
    result = list(children)
    for index, child in enumerate(result):
        if isinstance(child, str) and not child.strip():
            continue
        if isinstance(child, _Node) and child.tag == "input":
            del result[index]
        break
    return result


def _render_list(node: _Node, ordered: bool) -> str:
    items = [c for c in node.children if isinstance(c, _Node) and c.tag == "li"]
    lines: list[str] = []
    for index, li in enumerate(items, start=1):
        marker = f"{index}." if ordered else "-"
        checkbox = _task_checkbox(li)
        content_nodes = _without_leading_checkbox(li.children) if checkbox is not None else li.children
        if checkbox is not None:
            marker = f"{marker} {checkbox}"
        body = _render_li_body(content_nodes)
        if not body:
            lines.append(marker)
            continue
        first, _, rest = body.partition("\n")
        indent = " " * (len(marker) + 1)
        item_lines = [f"{marker} {first}"]
        for line in rest.split("\n") if rest else []:
            item_lines.append(f"{indent}{line}" if line else "")
        lines.append("\n".join(item_lines))
    return "\n".join(lines)


def _cell_align(cell: _Node) -> Optional[str]:
    style = (cell.attrs.get("style") or "").lower()
    match = re.search(r"text-align\s*:\s*(left|right|center)", style)
    value = match.group(1) if match else (cell.attrs.get("align") or "").lower()
    return value if value in ("left", "right", "center") else None


def _sep_cell(align: Optional[str]) -> str:
    return {"left": ":---", "right": "---:", "center": ":---:"}.get(align, "---")


def _render_table_cell(cell: _Node) -> str:
    text = _render_inline(cell.children).strip().replace("\n", " ")
    return text.replace("|", "\\|")


def _table_rows(container: _Node) -> list[_Node]:
    found = []
    for child in container.children:
        if not isinstance(child, _Node):
            continue
        if child.tag == "tr":
            found.append(child)
        elif child.tag in ("thead", "tbody", "tfoot"):
            found.extend(_table_rows(child))
    return found


def _render_table(node: _Node) -> str:
    all_rows = _table_rows(node)
    if not all_rows:
        return ""

    def cells_of(tr: _Node) -> list[_Node]:
        return [c for c in tr.children if isinstance(c, _Node) and c.tag in ("th", "td")]

    header: Optional[list[str]] = None
    aligns: list[Optional[str]] = []
    body_rows: list[list[str]] = []
    for tr in all_rows:
        cells = cells_of(tr)
        texts = [_render_table_cell(c) for c in cells]
        if header is None and (any(c.tag == "th" for c in cells) or tr is all_rows[0]):
            header = texts
            aligns = [_cell_align(c) for c in cells]
        else:
            body_rows.append(texts)

    width = max([len(header or [])] + [len(row) for row in body_rows] + [1])
    header = (header or []) + [""] * (width - len(header or []))
    aligns = aligns + [None] * (width - len(aligns))
    body_rows = [row + [""] * (width - len(row)) for row in body_rows]

    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(_sep_cell(a) for a in aligns) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in body_rows)
    return "\n".join(lines)


def html_to_markdown(source: str) -> str:
    """Render an HTML document (or fragment) as Markdown."""
    builder = _TreeBuilder()
    builder.feed(source)
    builder.close()
    text = _render_block(builder.root.children)
    return f"{text}\n" if text else ""


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


def convert(source: Path, destination: Path) -> list[Path]:
    """Convert one HTML file, returning the Markdown path written."""
    html_text = source.read_text(encoding="utf-8", errors="replace")
    markdown = html_to_markdown(html_text)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(markdown, encoding="utf-8")
    return [destination]


def _destination(source: Path, output: Optional[Path], output_dir: Optional[Path]) -> Path:
    if output is not None:
        return output
    if output_dir is not None:
        return output_dir / (source.stem + ".md")
    return source.with_suffix(".md")


@click.command(context_settings=CONTEXT_SETTINGS)
@click.argument(
    "files",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help="Output Markdown path. Only valid with a single input file; "
    "otherwise each <input>.html is written as <input>.md.",
)
@click.option(
    "-d",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write the Markdown into this directory instead of beside the inputs.",
)
@quiet_option
@version_option
def html2md_cli(
    files: tuple[Path, ...],
    output: Optional[Path],
    output_dir: Optional[Path],
    quiet: bool,
) -> None:
    """Convert HTML files to Markdown.

    \b
    Tags with a clean Markdown equivalent -- headings, lists, tables, links,
    images, emphasis and the rest -- convert to it. Anything else (<div>,
    <span>, <iframe>, custom elements, HTML comments) passes through
    verbatim as raw HTML, so nothing reader-visible is ever lost.

    \b
    Examples:
      pyhtml2md page.html                  # writes page.md
      pyhtml2md a.html b.html -d ./md      # one .md per input
      pyhtml2md page.html -o notes.md
    """
    if output is not None and len(files) > 1:
        raise click.UsageError("-o/--output takes a single input file; use -d/--output-dir instead.")

    failures = 0
    for source in files:
        destination = _destination(source, output, output_dir)
        try:
            written = convert(source, destination)
        except click.ClickException as exc:
            # One unreadable file should not abandon the rest of the batch,
            # but it must still show up in the exit code.
            failures += 1
            click.secho(f"error: {exc.format_message()}", fg="red", err=True)
            continue
        if not quiet:
            click.echo(f"  {source} -> {written[0]}")

    if failures:
        raise click.exceptions.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    html2md_cli()
