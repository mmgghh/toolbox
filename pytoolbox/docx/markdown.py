"""Blocks and comments in, Markdown out.

Nothing here touches XML. The writer's whole input is the plain objects from
``document`` and ``comments``, which is what makes its behaviour testable one
rule at a time.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field, replace
from typing import Optional

from pytoolbox.core.markdown import emphasis
from pytoolbox.core.markdown import escape as _escape
from pytoolbox.docx.comments import Comment
from pytoolbox.docx.document import Block, Heading, ListItem, Table, items_of
from pytoolbox.docx.inline import CommentMark, FootnoteMark, ImageRef, Item, Math, Run

#: Paragraphs of inline items, keyed by footnote id.
Notes = dict[str, list[list[Item]]]


@dataclass
class RenderOptions:
    """What to include in the output."""

    #: Render comments. Off means the markers disappear too.
    comments: bool = True
    #: Directory name that image links point into. None omits images entirely.
    assets_dir: Optional[str] = None
    #: Heading anchor for each bookmark name, filled in by ``render``. An
    #: internal link to a bookmark that marks no heading has nowhere to go.
    anchors: dict[str, str] = field(default_factory=dict)


def render(
    blocks: list[Block],
    comments: dict[str, Comment],
    notes: Optional[Notes] = None,
    options: Optional[RenderOptions] = None,
) -> str:
    """Render a document to Markdown, ending with exactly one newline."""
    options = options or RenderOptions()
    notes = notes or {}
    numbers = _number_comments(blocks, comments) if options.comments else {}
    options = replace(options, anchors=_anchors(blocks, numbers, options))

    chunks: list[str] = []
    lists = _ListState()

    for block in blocks:
        text = _block(block, numbers, options, lists)
        if text:
            # Items of one list are consecutive lines, not separate blocks.
            if isinstance(block, ListItem) and chunks and not lists.started_run:
                chunks[-1] += "\n" + text
            else:
                chunks.append(text)
        bodies = _comment_bodies(block, comments, numbers, options)
        if bodies:
            lists.reset()
            chunks.append(bodies)

    footnotes = _footnote_definitions(blocks, notes, numbers, options)
    if footnotes:
        chunks.append(footnotes)

    return "\n\n".join(chunks).strip("\n") + "\n" if chunks else ""


# ─────────────────────────────────────────────────────────────────────
# Comment numbering
# ─────────────────────────────────────────────────────────────────────


def _number_comments(blocks: list[Block], comments: dict[str, Comment]) -> dict[str, str]:
    """Assign display numbers: ``1``, ``2`` for comments, ``1.1`` for replies.

    Numbering follows document order, not Word's internal ids, which are
    arbitrary. Replies are numbered under their parent and never take a
    top-level number, so the marker in the text always names a thread.
    """
    order = [cid for cid in _comment_ids(blocks) if cid in comments]
    position = {cid: index for index, cid in enumerate(order)}

    def is_reply(cid: str) -> bool:
        parent = comments[cid].parent_id
        return parent is not None and parent in comments

    numbers: dict[str, str] = {}
    for cid in order:
        if not is_reply(cid):
            numbers[cid] = str(len(numbers) - sum("." in n for n in numbers.values()) + 1)

    for parent_id in list(numbers):
        replies = [c.id for c in comments.values() if c.parent_id == parent_id]
        # Anchored replies keep document order; unanchored ones trail behind,
        # ordered by their Word id so the output stays deterministic.
        replies.sort(key=lambda cid: (position.get(cid, len(order)), cid))
        for index, reply_id in enumerate(replies, start=1):
            numbers[reply_id] = f"{numbers[parent_id]}.{index}"

    return numbers


def _comment_ids(blocks: list[Block]) -> list[str]:
    """Every comment id in document order, including inside table cells."""
    found: list[str] = []
    for block in blocks:
        for item in items_of(block):
            if isinstance(item, CommentMark) and item.comment_id not in found:
                found.append(item.comment_id)
    return found


# ─────────────────────────────────────────────────────────────────────
# Internal links
# ─────────────────────────────────────────────────────────────────────


def _anchors(blocks: list[Block], numbers: dict[str, str], options: RenderOptions) -> dict[str, str]:
    """The anchor a renderer will give each heading, by the bookmark naming it.

    Word links a table of contents entry to a hidden bookmark such as
    ``_Toc229560004``. Markdown has nothing of the kind, but every heading has
    an anchor made from its text, so that is where such a link is pointed.
    """
    used: dict[str, int] = {}
    anchors: dict[str, str] = {}
    plain = replace(options, anchors={})
    for block in blocks:
        if not isinstance(block, Heading):
            continue
        text = _inline(block.items, numbers, plain)
        if not text:
            continue  # a heading with no text is not written at all
        anchor = _slug(text, used)
        for name in block.bookmarks:
            anchors.setdefault(name, anchor)
    return anchors


def _slug(text: str, used: dict[str, int]) -> str:
    """A heading anchor the way ``pymd2html`` and GitHub make one."""
    base = re.sub(r"[`*_~\[\]()]", "", text).strip().lower()
    base = re.sub(r"[^\w\- ]", "", base, flags=re.UNICODE)
    base = re.sub(r"[\s-]+", "-", base).strip("-") or "section"
    used[base] = used.get(base, 0) + 1
    return base if used[base] == 1 else f"{base}-{used[base]}"


# ─────────────────────────────────────────────────────────────────────
# Blocks
# ─────────────────────────────────────────────────────────────────────


class _ListState:
    """Counters for ordered lists, and where one list run ends and the next starts."""

    def __init__(self) -> None:
        self.counters: dict[int, int] = {}
        self.base_ordered: Optional[bool] = None
        #: True when the item just rendered began a new list rather than
        #: continuing the previous one. The caller uses it to decide spacing.
        self.started_run = False

    def reset(self) -> None:
        self.counters.clear()
        self.base_ordered = None
        self.started_run = False

    def marker(self, item: ListItem) -> str:
        # Bullets and numbers at the top level are different lists, so flipping
        # between them starts a new run instead of silently merging.
        flipped = (
            item.level == 0 and self.base_ordered is not None and self.base_ordered != item.ordered
        )
        self.started_run = flipped or self.base_ordered is None
        if flipped:
            self.counters.clear()
        if item.level == 0:
            self.base_ordered = item.ordered

        if not item.ordered:
            return "-"
        self.counters[item.level] = self.counters.get(item.level, 0) + 1
        for deeper in [level for level in self.counters if level > item.level]:
            del self.counters[deeper]
        return f"{self.counters[item.level]}."


def _block(block: Block, numbers: dict[str, str], options: RenderOptions, lists: _ListState) -> str:
    if isinstance(block, Heading):
        lists.reset()
        text = _inline(block.items, numbers, options)
        return f"{'#' * block.level} {text}".rstrip() if text else ""

    if isinstance(block, Table):
        lists.reset()
        return _table(block, numbers, options)

    if isinstance(block, ListItem):
        return _list_item(block, numbers, options, lists)

    lists.reset()
    return _inline(block.items, numbers, options).rstrip()


def _list_item(
    item: ListItem, numbers: dict[str, str], options: RenderOptions, lists: _ListState
) -> str:
    """Render one list item. ``lists.started_run`` says whether it opens a list."""
    text = _inline(item.items, numbers, options).rstrip()
    marker = lists.marker(item)
    return f"{'  ' * item.level}{marker} {text}".rstrip()


def _table(table: Table, numbers: dict[str, str], options: RenderOptions) -> str:
    if not table.rows:
        return ""
    width = max(len(row) for row in table.rows)
    rendered = [
        [_cell(row[index], numbers, options) if index < len(row) else "" for index in range(width)]
        for row in table.rows
    ]
    header, *body = rendered
    lines = [_row(header), _row(["---"] * width)]
    lines.extend(_row(row) for row in body)
    return "\n".join(lines)


def _row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _cell(cell, numbers: dict[str, str], options: RenderOptions) -> str:
    """Cell content on one line: Markdown tables cannot hold block structure."""
    parts: list[str] = []
    counters: dict[int, int] = {}
    for block in cell:
        text = _inline(block.items, numbers, options).strip()
        if isinstance(block, ListItem):
            # A pipe table cannot hold a list, but it can hold a list's text:
            # each item keeps its marker and a line of its own.
            for deeper in [level for level in counters if level > block.level]:
                del counters[deeper]
            if block.ordered:
                counters[block.level] = counters.get(block.level, 0) + 1
                text = f"{counters[block.level]}. {text}"
            else:
                text = f"- {text}"
        else:
            counters.clear()
        parts.append(text)
    joined = "<br>".join(part for part in parts if part).replace("|", "\\|")
    # A line break of any kind would end the row early, so every one of them
    # becomes the break that Markdown allows inside a cell.
    return joined.replace("\n", "<br>")


# ─────────────────────────────────────────────────────────────────────
# Inline
# ─────────────────────────────────────────────────────────────────────


def _styled_stretches(items: list[Item]) -> list[Item]:
    """Gather neighbouring runs that share a style around a link into one.

    ``**see (**`` + ``[**x**](u)`` + ``**)**`` is one bold stretch with a link
    in it, and written as three it is fragmented asterisks. A stretch is
    marked with ``Stretch``, which is rendered once, markers outside.
    """
    out: list[Item] = []
    index = 0
    while index < len(items):
        item = items[index]
        if not isinstance(item, Run) or item.code:
            out.append(item)
            index += 1
            continue
        style = (item.bold, item.italic, item.strike)
        stop = index
        while (
            stop < len(items)
            and isinstance(items[stop], Run)
            and not items[stop].code
            and (items[stop].bold, items[stop].italic, items[stop].strike) == style
        ):
            stop += 1
        group = items[index:stop]
        if len(group) > 1 and any(run.link for run in group) and any(style):
            out.append(Stretch(runs=list(group)))
        else:
            out.extend(group)
        index = stop
    return out


@dataclass
class Stretch:
    """Runs of one style with a link among them, written under one set of markers."""

    runs: list[Run]


def _stretch(stretch: Stretch, options: RenderOptions) -> str:
    first = stretch.runs[0]
    pieces = []
    for run in stretch.runs:
        pieces.append(_run(replace(run, bold=False, italic=False, strike=False), options))
    inner = "".join(pieces)
    lead = inner[: len(inner) - len(inner.lstrip())]
    trail = inner[len(inner.rstrip()) :]
    body = inner.strip()
    if first.strike:
        body = f"~~{body}~~"
    if first.bold:
        body = f"**{body}**"
    if first.italic:
        body = f"*{body}*"
    return f"{lead}{body}{trail}"


def _inline(items: list[Item], numbers: dict[str, str], options: RenderOptions) -> str:
    out: list[str] = []
    for item in _styled_stretches(items):
        if isinstance(item, Stretch):
            out.append(_stretch(item, options))
        elif isinstance(item, Run):
            out.append(_run(item, options))
        elif isinstance(item, CommentMark):
            number = numbers.get(item.comment_id)
            # Replies carry a dotted number and are shown nested in the body,
            # so only thread starters get a marker in the text.
            if number and "." not in number:
                out.append(f" **[{number}]**")
        elif isinstance(item, FootnoteMark):
            out.append(f"[^{item.note_id}]")
        elif isinstance(item, Math):
            # Fenced $$ on lines of its own is what every Markdown renderer
            # that does maths recognises as a displayed equation.
            out.append(f"\n$$\n{item.latex}\n$$\n" if item.display else f"${item.latex}$")
        elif isinstance(item, ImageRef) and options.assets_dir:
            name = posixpath.basename(item.part_name)
            out.append(f"![{_escape(item.alt)}]({options.assets_dir}/{name})")
    return "".join(out).strip()


def _run(run: Run, options: RenderOptions) -> str:
    link = run.link
    if link and link.startswith("#"):
        link = f"#{options.anchors[link[1:]]}" if link[1:] in options.anchors else None
    return emphasis(
        run.text,
        bold=run.bold,
        italic=run.italic,
        strike=run.strike,
        code=run.code,
        link=link,
    )


# ─────────────────────────────────────────────────────────────────────
# Comment bodies and footnotes
# ─────────────────────────────────────────────────────────────────────


def _comment_bodies(
    block: Block, comments: dict[str, Comment], numbers: dict[str, str], options: RenderOptions
) -> str:
    """The blockquote that follows a block, holding every thread anchored in it."""
    if not options.comments:
        return ""

    threads = [
        cid
        for cid in dict.fromkeys(
            item.comment_id for item in items_of(block) if isinstance(item, CommentMark)
        )
        if cid in numbers and "." not in numbers[cid]
    ]
    if not threads:
        return ""

    lines: list[str] = []
    for thread_id in threads:
        if lines:
            lines.append(">")
        lines.extend(_comment_lines(comments[thread_id], numbers[thread_id], depth=1))
        replies = sorted(
            (c for c in comments.values() if c.parent_id == thread_id),
            key=lambda c: numbers.get(c.id, ""),
        )
        for reply in replies:
            lines.append(">")
            lines.extend(_comment_lines(reply, numbers.get(reply.id, ""), depth=2))
    return "\n".join(lines)


def _comment_lines(comment: Comment, number: str, depth: int) -> list[str]:
    prefix = "> " * depth
    status = " (resolved)" if comment.resolved else ""
    head = f"{prefix}**[{number}]** {comment.author}"
    if comment.date:
        head += f" · {comment.date}"
    lines = [head + status]
    for paragraph in comment.paragraphs:
        text = _inline(paragraph, {}, RenderOptions(comments=False))
        if text:
            lines.extend(f"{prefix}{line}" for line in text.split("\n"))
    return lines


def _footnote_definitions(
    blocks: list[Block], notes: Notes, numbers: dict[str, str], options: RenderOptions
) -> str:
    """Definitions for every footnote actually referenced, in order of use."""
    used: list[str] = []
    for block in blocks:
        for item in items_of(block):
            if isinstance(item, FootnoteMark) and item.note_id not in used:
                used.append(item.note_id)

    lines = []
    for note_id in used:
        paragraphs = notes.get(note_id)
        if not paragraphs:
            continue
        text = " ".join(
            filter(None, (_inline(p, numbers, options) for p in paragraphs))
        )
        lines.append(f"[^{note_id}]: {text}")
    return "\n".join(lines)
