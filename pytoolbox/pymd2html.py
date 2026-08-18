#!/usr/bin/env python3
"""Convert Markdown files to standalone HTML.

Exposes the ``pymd2html`` console script, also available as ``toolbox md2html``.

Needs no optional dependency: the parser is written here rather than pulled
from a Markdown library, which keeps a bare install (and Termux) able to run
it. It covers the same ground as ``pymd2pdf`` -- headings, emphasis, code,
tables, lists, task lists, blockquotes, rules, images, links -- plus raw HTML
passthrough, which a PDF cannot have.

The output is one self-contained file: the stylesheet is embedded, so the page
survives being mailed, copied to a phone or opened from a USB stick with no
network and no sibling files. ``--fragment`` emits just the body for pasting
into a page that already has its own chrome.
"""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Optional

import click

from pytoolbox.core.options import CONTEXT_SETTINGS, quiet_option, version_option

# ═══════════════════════════════════════════════════════════════════
# Patterns
# ═══════════════════════════════════════════════════════════════════

_HEADING = re.compile(r"^ {0,3}(#{1,6})\s+(.*?)(?:\s+#+)?\s*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(\S*)")
_RULE = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
_QUOTE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
_BULLET = re.compile(r"^(\s*)([-*+])[ \t]+(.*)$")
_ORDERED = re.compile(r"^(\s*)(\d{1,9})([.)])[ \t]+(.*)$")
_TASK = re.compile(r"^\[([ xX])\][ \t]+(.*)$")
#: The `|---|:--:|` line is what makes the row above it a table header.
_TABLE_RULE = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(\|[ \t]*:?-+:?[ \t]*)+\|?[ \t]*$")
#: Tags that open a block of raw HTML, which then runs to the next blank line.
_HTML_BLOCK = re.compile(
    r"^ {0,3}</?(?:address|article|aside|blockquote|details|div|dl|fieldset|figcaption|figure|"
    r"footer|form|h[1-6]|header|hr|iframe|main|nav|ol|p|pre|section|summary|table|ul|video)\b",
    re.I,
)
_HTML_INLINE = re.compile(r"</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*)?/?>|<!--.*?-->", re.S)
_AUTOLINK = re.compile(r"<((?:https?|ftp|mailto):[^\s<>]+)>|<([^\s<>@]+@[^\s<>@]+\.[^\s<>@]+)>")
_BARE_URL = re.compile(r"https?://[^\s<>\[\]()\"']+")
#: The destination may hold balanced parentheses -- Wikipedia URLs are full of
#: them -- so one level of nesting is matched rather than stopping at the first.
_LINK = re.compile(
    r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]"
    r"\(\s*(<[^>]*>|(?:[^\s()\\]|\\.|\([^\s()]*\))*)(?:\s+\"([^\"]*)\")?\s*\)"
)
_IMAGE = re.compile(r"!" + _LINK.pattern)
_CODE_SPAN = re.compile(r"(`+)(.+?)\1", re.S)
_RTL_CHARS = re.compile(r"[\u0590-\u08FF\uFB1D-\uFDFF\uFE70-\uFEFF]")
_LATIN_CHARS = re.compile(r"[A-Za-z]")

#: Backslash escapes recognised in inline text, as Markdown defines them.
_PUNCTUATION = set("\\`*_{}[]()#+-.!|<>~\"'$%&,/:;=?@^")

#: Emphasis, applied to text that no other rule claimed. Underscore forms are
#: fenced off from word characters so that snake_case survives intact.
_EMPHASIS = (
    (re.compile(r"\*\*\*(?=\S)(.+?)(?<=\S)\*\*\*", re.S), "<strong><em>{}</em></strong>"),
    (re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.S), "<strong>{}</strong>"),
    (re.compile(r"(?<!\w)___(?=\S)(.+?)(?<=\S)___(?!\w)", re.S), "<strong><em>{}</em></strong>"),
    (re.compile(r"(?<!\w)__(?=\S)(.+?)(?<=\S)__(?!\w)", re.S), "<strong>{}</strong>"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.S), "<del>{}</del>"),
    (re.compile(r"\*(?=\S)(.+?)(?<=\S)\*", re.S), "<em>{}</em>"),
    (re.compile(r"(?<!\w)_(?=\S)(.+?)(?<=\S)_(?!\w)", re.S), "<em>{}</em>"),
)

#: URL schemes that would run code if someone clicked the link. A Markdown
#: file you did not write is untrusted input; these become plain text.
_UNSAFE_SCHEME = re.compile(r"^\s*(?:javascript|vbscript|file):", re.I)

#: Placeholders keep finished HTML out of the way of the emphasis pass. NUL
#: cannot appear in the input (it is stripped first), so nothing collides.
_MARK = "\x00"
_MARK_RE = re.compile(r"\x00(\d+)\x00")

#: Embedded stylesheet. Deliberately small and free of any external request:
#: system fonts, both colour schemes, and the same layout in either direction.
DEFAULT_CSS = """\
:root {
  color-scheme: light dark;
  --bg: #ffffff;
  --fg: #1f2328;
  --muted: #59636e;
  --border: #d1d9e0;
  --accent: #0969da;
  --code-bg: #f6f8fa;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d1117;
    --fg: #e6edf3;
    --muted: #9198a1;
    --border: #3d444d;
    --accent: #4493f8;
    --code-bg: #161b22;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0 auto;
  padding: 2.5rem 1.25rem 4rem;
  max-width: 46rem;
  background: var(--bg);
  color: var(--fg);
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Noto Sans", sans-serif;
  font-size: 16px;
  line-height: 1.65;
  word-wrap: break-word;
}
h1, h2, h3, h4, h5, h6 { margin: 2rem 0 1rem; line-height: 1.25; font-weight: 600; }
h1 { font-size: 2rem; }
h2 { font-size: 1.5rem; padding-bottom: .3rem; border-bottom: 1px solid var(--border); }
h3 { font-size: 1.25rem; }
h4 { font-size: 1rem; }
h5, h6 { font-size: .9rem; color: var(--muted); }
p, ul, ol, table, pre, blockquote { margin: 0 0 1rem; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code, kbd, samp, pre {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: .875em;
}
code { padding: .2em .4em; background: var(--code-bg); border-radius: 6px; }
pre {
  padding: 1rem;
  overflow-x: auto;
  background: var(--code-bg);
  border-radius: 6px;
  direction: ltr;
  text-align: left;
}
pre code { padding: 0; background: none; }
blockquote {
  padding: 0 1rem;
  color: var(--muted);
  border-inline-start: .25rem solid var(--border);
}
blockquote > :last-child { margin-bottom: 0; }
ul, ol { padding-inline-start: 2rem; }
li { margin: .25rem 0; }
li.task { list-style: none; margin-inline-start: -1.4rem; }
li.task input { margin-inline-end: .4rem; }
table { border-collapse: collapse; display: block; overflow-x: auto; }
th, td { padding: .4rem .75rem; border: 1px solid var(--border); }
th { background: var(--code-bg); font-weight: 600; }
img { max-width: 100%; height: auto; }
hr { height: 1px; margin: 2rem 0; background: var(--border); border: 0; }
"""


# ═══════════════════════════════════════════════════════════════════
# Inline rendering
# ═══════════════════════════════════════════════════════════════════


def _stash(store: list[str], value: str) -> str:
    """Park finished HTML and return the placeholder standing in for it."""
    store.append(value)
    return f"{_MARK}{len(store) - 1}{_MARK}"


def _safe_url(url: str) -> Optional[str]:
    """The URL as an attribute value, or ``None`` when it would run code."""
    url = url.strip()
    if url.startswith("<") and url.endswith(">"):
        url = url[1:-1]
    if _UNSAFE_SCHEME.match(url):
        return None
    return html.escape(url, quote=True)


def _title_attribute(title: Optional[str]) -> str:
    return f' title="{html.escape(title, quote=True)}"' if title else ""


def _image(match: re.Match[str]) -> str:
    src = _safe_url(match.group(2))
    alt = html.escape(re.sub(r"[*_`~]", "", match.group(1)), quote=True)
    if src is None:
        return html.escape(match.group(0))
    return f'<img src="{src}" alt="{alt}"{_title_attribute(match.group(3))}>'


def _link(match: re.Match[str], escape_html: bool) -> str:
    href = _safe_url(match.group(2))
    label = render_inline(match.group(1), escape_html=escape_html)
    if href is None:
        return label
    return f'<a href="{href}"{_title_attribute(match.group(3))}>{label}</a>'


def render_inline(text: str, escape_html: bool = False) -> str:
    """Render one run of inline Markdown to HTML.

    Anything with its own syntax -- code, links, images, raw HTML -- is turned
    into final HTML first and replaced by a placeholder, so the emphasis pass
    that follows cannot reach inside a URL or a code span. What is left is
    escaped before emphasis is applied, which is what keeps a stray ``<`` in
    prose from becoming a tag.
    """
    store: list[str] = []
    out: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]

        if char == "\\" and index + 1 < len(text) and text[index + 1] in _PUNCTUATION:
            out.append(_stash(store, html.escape(text[index + 1])))
            index += 2
            continue

        if char == "`":
            match = _CODE_SPAN.match(text, index)
            if match:
                # One space either side is padding that lets a span hold a
                # backtick of its own (`` ` ``); the rest is content.
                body = match.group(2)
                if body.startswith(" ") and body.endswith(" ") and body.strip():
                    body = body[1:-1]
                out.append(_stash(store, f"<code>{html.escape(body)}</code>"))
                index = match.end()
                continue

        if char == "!":
            match = _IMAGE.match(text, index)
            if match:
                out.append(_stash(store, _image(match)))
                index = match.end()
                continue

        if char == "[":
            match = _LINK.match(text, index)
            if match:
                out.append(_stash(store, _link(match, escape_html)))
                index = match.end()
                continue

        if char == "<":
            match = _AUTOLINK.match(text, index)
            if match:
                target = match.group(1) or f"mailto:{match.group(2)}"
                label = html.escape(match.group(1) or match.group(2))
                href = _safe_url(target)
                out.append(_stash(store, f'<a href="{href}">{label}</a>' if href else label))
                index = match.end()
                continue
            match = None if escape_html else _HTML_INLINE.match(text, index)
            if match:
                out.append(_stash(store, match.group(0)))
                index = match.end()
                continue

        if char in "hH":
            match = _BARE_URL.match(text, index)
            if match:
                # Sentence punctuation is not part of the address.
                url = match.group(0).rstrip(".,;:!?")
                href = _safe_url(url)
                out.append(_stash(store, f'<a href="{href}">{html.escape(url)}</a>' if href else url))
                index += len(url)
                continue

        out.append(char)
        index += 1

    rendered = html.escape("".join(out), quote=False)
    for pattern, template in _EMPHASIS:
        rendered = pattern.sub(lambda m, t=template: t.format(m.group(1)), rendered)
    # A trailing backslash or two trailing spaces is Markdown for "break here".
    rendered = re.sub(r"(?: {2,}|\\)\n", "<br>\n", rendered)
    return _MARK_RE.sub(lambda m: store[int(m.group(1))], rendered)


# ═══════════════════════════════════════════════════════════════════
# Block rendering
# ═══════════════════════════════════════════════════════════════════


def _slug(text: str, used: dict[str, int]) -> str:
    """A heading anchor: lowercase, punctuation dropped, never a duplicate."""
    base = re.sub(r"[`*_~\[\]()]", "", text).strip().lower()
    base = re.sub(r"[^\w\- ]", "", base, flags=re.UNICODE)
    base = re.sub(r"[\s-]+", "-", base).strip("-") or "section"
    used[base] = used.get(base, 0) + 1
    return base if used[base] == 1 else f"{base}-{used[base]}"


def _is_block_start(line: str) -> bool:
    """Whether ``line`` begins a block, i.e. ends the paragraph before it."""
    return bool(
        not line.strip()
        or _HEADING.match(line)
        or _FENCE.match(line)
        or _RULE.match(line)
        or _QUOTE.match(line)
        or _BULLET.match(line)
        or _ORDERED.match(line)
        or _HTML_BLOCK.match(line)
    )


def _list_marker(line: str):
    """``(indent, ordered, content, start)`` when ``line`` opens a list item."""
    match = _BULLET.match(line)
    if match:
        return len(match.group(1)), False, match.group(3), None
    match = _ORDERED.match(line)
    if match:
        return len(match.group(1)), True, match.group(4), int(match.group(2))
    return None


def _alignments(rule: str) -> list[str]:
    """Per-column alignment read off the `:---:` row."""
    columns = []
    for cell in _split_row(rule):
        left, right = cell.startswith(":"), cell.endswith(":")
        columns.append("center" if left and right else "right" if right else "left" if left else "")
    return columns


def _split_row(line: str) -> list[str]:
    """Split a table row on its unescaped pipes."""
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells: list[str] = []
    buffer: list[str] = []
    index = 0
    while index < len(line):
        char = line[index]
        if char == "\\" and index + 1 < len(line):
            buffer.append(char)
            buffer.append(line[index + 1])
            index += 2
            continue
        if char == "|":
            cells.append("".join(buffer).strip())
            buffer = []
        else:
            buffer.append(char)
        index += 1
    cells.append("".join(buffer).strip())
    return cells


class _Renderer:
    """Turns a list of Markdown lines into HTML blocks.

    Kept as a class only to carry the two pieces of state a block needs from
    the document around it: the anchors already handed out, and whether raw
    HTML is trusted.
    """

    def __init__(self, escape_html: bool = False) -> None:
        self.escape_html = escape_html
        self.anchors: dict[str, int] = {}

    # ── entry point ─────────────────────────────────────────────

    def blocks(self, lines: list[str], tight: bool = False) -> str:
        """Render ``lines`` as a sequence of blocks."""
        out: list[str] = []
        index = 0
        while index < len(lines):
            line = lines[index]
            if not line.strip():
                index += 1
                continue

            fence = _FENCE.match(line)
            if fence:
                index = self._code(lines, index, fence, out)
                continue

            heading = _HEADING.match(line)
            if heading:
                index = self._heading(heading, index, out)
                continue

            if _RULE.match(line):
                out.append("<hr>")
                index += 1
                continue

            if _QUOTE.match(line):
                index = self._quote(lines, index, out)
                continue

            if _list_marker(line):
                index = self._list(lines, index, out)
                continue

            if self._is_table(lines, index):
                index = self._table(lines, index, out)
                continue

            if not self.escape_html and _HTML_BLOCK.match(line):
                index = self._raw_html(lines, index, out)
                continue

            index = self._paragraph(lines, index, out, tight)
        return "\n".join(out)

    # ── one block per method ────────────────────────────────────

    def _code(self, lines: list[str], index: int, fence: re.Match[str], out: list[str]) -> int:
        marker, language = fence.group(1), fence.group(2)
        body: list[str] = []
        index += 1
        while index < len(lines) and not re.match(rf"^ {{0,3}}{marker[0]}{{{len(marker)},}}\s*$", lines[index]):
            body.append(lines[index])
            index += 1
        attribute = f' class="language-{html.escape(language, quote=True)}"' if language else ""
        out.append(f"<pre><code{attribute}>{html.escape(chr(10).join(body))}</code></pre>")
        # The closing fence, or the end of the document when there is none.
        return index + 1

    def _heading(self, heading: re.Match[str], index: int, out: list[str]) -> int:
        level = len(heading.group(1))
        text = heading.group(2)
        anchor = _slug(text, self.anchors)
        out.append(f'<h{level} id="{anchor}">{render_inline(text, self.escape_html)}</h{level}>')
        return index + 1

    def _quote(self, lines: list[str], index: int, out: list[str]) -> int:
        body: list[str] = []
        while index < len(lines):
            match = _QUOTE.match(lines[index])
            if match:
                body.append(match.group(1))
            elif lines[index].strip() and not _is_block_start(lines[index]):
                # Lazy continuation: an unmarked line still belongs to the
                # quote it is wrapped from.
                body.append(lines[index])
            else:
                break
            index += 1
        out.append(f"<blockquote>\n{self.blocks(body)}\n</blockquote>")
        return index

    def _list(self, lines: list[str], index: int, out: list[str]) -> int:
        indent, ordered, _, start = _list_marker(lines[index])
        items: list[list[str]] = []
        loose = False
        pending_blank = False

        while index < len(lines):
            line = lines[index]
            if not line.strip():
                # A blank line ends the list unless more of it follows.
                nxt = index + 1
                if nxt >= len(lines) or not lines[nxt].strip():
                    break
                following = _list_marker(lines[nxt])
                if not following and len(lines[nxt]) - len(lines[nxt].lstrip()) <= indent:
                    break
                pending_blank = True
                index += 1
                continue

            marker = _list_marker(line)
            current_indent = len(line) - len(line.lstrip())
            if marker and marker[0] <= indent:
                if marker[1] != ordered:
                    break  # A different marker type starts a different list.
                if pending_blank:
                    loose = True
                items.append([marker[2]])
                pending_blank = False
                index += 1
                continue

            if not items:
                break
            if current_indent > indent or marker:
                # Nested content: strip the parent's indent and let the
                # recursive pass see it as a block of its own.
                if pending_blank:
                    items[-1].append("")
                    loose = True
                items[-1].append(line[min(current_indent, indent + 2) :])
                pending_blank = False
                index += 1
                continue

            if pending_blank:
                break
            items[-1].append(line.strip())  # Lazy paragraph continuation.
            index += 1

        rendered = [self._item(item, loose) for item in items]
        tag = "ol" if ordered else "ul"
        opening = f'<ol start="{start}">' if ordered and start not in (None, 1) else f"<{tag}>"
        out.append("\n".join([opening, *rendered, f"</{tag}>"]))
        return index

    def _item(self, body: list[str], loose: bool) -> str:
        """One ``<li>``, with a checkbox when the item is a task."""
        attribute = ""
        task = _TASK.match(body[0]) if body else None
        if task:
            checked = " checked" if task.group(1).lower() == "x" else ""
            body = [task.group(2), *body[1:]]
            attribute = ' class="task"'
            box = f'<input type="checkbox" disabled{checked}>'
        else:
            box = ""
        content = self.blocks(body, tight=not loose)
        return f"<li{attribute}>{box}{content}</li>"

    def _is_table(self, lines: list[str], index: int) -> bool:
        return (
            "|" in lines[index]
            and index + 1 < len(lines)
            and _TABLE_RULE.match(lines[index + 1]) is not None
            and len(_split_row(lines[index])) == len(_split_row(lines[index + 1]))
        )

    def _table(self, lines: list[str], index: int, out: list[str]) -> int:
        headers = _split_row(lines[index])
        aligns = _alignments(lines[index + 1])
        index += 2
        rows: list[list[str]] = []
        while index < len(lines) and lines[index].strip() and "|" in lines[index]:
            rows.append(_split_row(lines[index]))
            index += 1

        def cell(tag: str, text: str, column: int) -> str:
            align = aligns[column] if column < len(aligns) else ""
            style = f' style="text-align:{align}"' if align else ""
            return f"<{tag}{style}>{render_inline(text, self.escape_html)}</{tag}>"

        parts = ["<table>", "<thead>", "<tr>"]
        parts += [cell("th", text, column) for column, text in enumerate(headers)]
        parts += ["</tr>", "</thead>", "<tbody>"]
        for row in rows:
            # A short row is padded rather than dropped: the header decides
            # how many columns the table has.
            padded = (row + [""] * len(headers))[: len(headers)]
            parts.append("<tr>" + "".join(cell("td", text, column) for column, text in enumerate(padded)) + "</tr>")
        parts += ["</tbody>", "</table>"]
        out.append("\n".join(parts))
        return index

    def _raw_html(self, lines: list[str], index: int, out: list[str]) -> int:
        body: list[str] = []
        while index < len(lines) and lines[index].strip():
            body.append(lines[index])
            index += 1
        out.append("\n".join(body))
        return index

    def _paragraph(self, lines: list[str], index: int, out: list[str], tight: bool) -> int:
        body: list[str] = []
        while index < len(lines) and lines[index].strip():
            if body and _is_block_start(lines[index]):
                break
            if body and self._is_table(lines, index):
                break
            body.append(lines[index])
            index += 1
        text = render_inline("\n".join(body), self.escape_html)
        # A tight list item holds its text directly: <li>one</li>, not
        # <li><p>one</p></li>, which browsers render with a blank line.
        out.append(text if tight and not out else f"<p>{text}</p>")
        return index


# ═══════════════════════════════════════════════════════════════════
# Document
# ═══════════════════════════════════════════════════════════════════


def render_body(text: str, escape_html: bool = False) -> str:
    """Render Markdown to an HTML fragment: no ``<html>``, no stylesheet."""
    # NUL is the placeholder sentinel, and a control character no document
    # needs; dropping it up front is what makes the placeholders unforgeable.
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
    lines = text.split("\n")
    if lines and lines[-1] == "":
        # The empty string after a file's final newline is not a blank line,
        # and inside an unclosed code fence it would be printed as one.
        lines.pop()
    return _Renderer(escape_html=escape_html).blocks(lines)


def is_rtl(text: str) -> bool:
    """Whether the document reads right-to-left, by weight of its letters."""
    return len(_RTL_CHARS.findall(text)) > len(_LATIN_CHARS.findall(text))


def extract_title(text: str) -> str:
    """The first level-1 heading, with its Markdown stripped."""
    for line in text.split("\n"):
        match = _HEADING.match(line)
        if match and len(match.group(1)) == 1:
            return re.sub(r"[`*_~]|\[|\]\([^)]*\)", "", match.group(2)).strip()
    return ""


def render_document(
    text: str,
    title: Optional[str] = None,
    css: Optional[str] = DEFAULT_CSS,
    lang: Optional[str] = None,
    rtl: Optional[bool] = None,
    escape_html: bool = False,
) -> str:
    """Render Markdown to one self-contained HTML page."""
    body = render_body(text, escape_html=escape_html)
    heading = title if title is not None else extract_title(text)
    direction = is_rtl(text) if rtl is None else rtl
    attributes = f' lang="{html.escape(lang, quote=True)}"' if lang else ""
    if direction:
        attributes += ' dir="rtl"'
    style = f"<style>\n{css}</style>\n" if css else ""
    return (
        "<!DOCTYPE html>\n"
        f"<html{attributes}>\n"
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(heading or 'Document')}</title>\n"
        f"{style}"
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def convert(
    md_path,
    html_path,
    fragment: bool = False,
    title: Optional[str] = None,
    css: Optional[str] = DEFAULT_CSS,
    lang: Optional[str] = None,
    rtl: Optional[bool] = None,
    escape_html: bool = False,
) -> Path:
    """Convert one Markdown file, returning the path written."""
    md_path = Path(md_path)
    text = md_path.read_text(encoding="utf-8")
    if fragment:
        output = render_body(text, escape_html=escape_html) + "\n"
    else:
        output = render_document(
            text,
            title=title if title is not None else (extract_title(text) or md_path.stem),
            css=css,
            lang=lang,
            rtl=rtl,
            escape_html=escape_html,
        )

    html_path = Path(html_path)
    if str(html_path) == "-":
        click.echo(output, nl=False)
        return html_path
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_text(output, encoding="utf-8")
    return html_path


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


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
    type=click.Path(dir_okay=False, writable=True, allow_dash=True, path_type=Path),
    help="Output HTML path, or '-' for stdout. Only valid with a single input "
    "file; otherwise each <input>.md is written as <input>.html.",
)
@click.option(
    "-d",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write the HTML into this directory instead of beside the inputs.",
)
@click.option("--fragment", is_flag=True, help="Emit the body only: no <html>, <head> or stylesheet.")
@click.option(
    "--css",
    "css_file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="Embed this stylesheet instead of the built-in one.",
)
@click.option("--no-css", is_flag=True, help="Embed no stylesheet at all.")
@click.option("--title", default=None, help="Page title (default: the first H1, else the file name).")
@click.option("--lang", default=None, help="Value for the <html lang> attribute, e.g. 'en' or 'fa'.")
@click.option("--rtl", is_flag=True, help="Force right-to-left layout.")
@click.option("--ltr", is_flag=True, help="Force left-to-right layout.")
@click.option(
    "--escape-html",
    is_flag=True,
    help="Show raw HTML in the Markdown as text instead of passing it through. "
    "Use it for documents you did not write.",
)
@quiet_option
@version_option
def md2html_cli(
    files: tuple[Path, ...],
    output: Optional[Path],
    output_dir: Optional[Path],
    fragment: bool,
    css_file: Optional[Path],
    no_css: bool,
    title: Optional[str],
    lang: Optional[str],
    rtl: bool,
    ltr: bool,
    escape_html: bool,
    quiet: bool,
) -> None:
    """Convert Markdown file(s) to HTML.

    \b
    Writes one self-contained page per input: the stylesheet is embedded and
    nothing is fetched from the network, so the file works offline, on a phone
    and from an email attachment. Supports headings (with anchors), bold,
    italic, strikethrough, inline code, code blocks, tables, bullet, numbered
    and task lists, blockquotes, rules, images, links and raw HTML.
    Persian/Arabic/Hebrew documents get a right-to-left page automatically.

    \b
    Examples:
      pymd2html README.md                    # writes README.html
      pymd2html notes.md -o - | less         # to stdout
      pymd2html *.md -d ./site
      pymd2html post.md --fragment           # body only, for a template
      pymd2html doc.md --css mine.css --lang fa
    """
    if output and len(files) > 1:
        raise click.UsageError("-o/--output can only be used with a single input file.")
    if output and output_dir:
        raise click.UsageError("Use either -o/--output or -d/--output-dir, not both.")
    if rtl and ltr:
        raise click.UsageError("Use either --rtl or --ltr, not both.")
    if css_file and no_css:
        raise click.UsageError("Use either --css or --no-css, not both.")

    css = None if no_css else (css_file.read_text(encoding="utf-8") if css_file else DEFAULT_CSS)
    direction = True if rtl else False if ltr else None
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    for md_path in files:
        if output:
            destination = output
        elif output_dir:
            destination = output_dir / md_path.with_suffix(".html").name
        else:
            destination = md_path.with_suffix(".html")
        written = convert(
            md_path,
            destination,
            fragment=fragment,
            title=title,
            css=css,
            lang=lang,
            rtl=direction,
            escape_html=escape_html,
        )
        if not quiet and str(written) != "-":
            click.echo(f"  {md_path} -> {written}", err=True)


if __name__ == "__main__":  # pragma: no cover
    md2html_cli()
