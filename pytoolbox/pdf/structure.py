"""Lines to blocks: the inference layer.

Every rule here is a heuristic, and each is written to fail towards plain text.
A line that cannot be classified confidently stays a paragraph, which is the
outcome that loses the least.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Union

from pytoolbox.pdf.layout import Line, leading
from pytoolbox.pdf.reader import Document, ImageBox, LinkBox, Page, TextRun
from pytoolbox.pdf.tables import Table as Grid

#: A heading must be at least this much larger than the body text.
HEADING_RATIO = 1.15

MAX_HEADING = 6

#: Level given to a bold line that is short enough to read as a heading.
BOLD_HEADING_LEVEL = 3

#: A bold line longer than this many characters is a sentence, not a heading.
BOLD_HEADING_MAX_CHARS = 60

#: A paragraph breaks when the gap exceeds this multiple of the median leading.
PARAGRAPH_GAP = 1.5

#: A list item takes another line only when it follows at the leading itself.
#: The looser paragraph rule is wrong here: a bullet is usually the last line
#: before a gap, so anything it is allowed to swallow it will swallow.
LIST_GAP = 1.2

#: A heading is set larger than the body, so its own lines sit further apart
#: than the page's leading. Its second line is allowed this much of its size.
HEADING_GAP = 1.5

#: A line ending before this share of the block's right edge ends the paragraph.
PARAGRAPH_SHORT_LINE = 0.85

#: Horizontal shift that starts a new paragraph, in points.
INDENT_STEP = 12.0

#: List indents closer together than this are the same nesting level.
INDENT_TOLERANCE = 4.0

_BULLET = re.compile(r"^[•◦▪·⁃–‐-]\s+")
_ORDERED = re.compile(r"^(\d{1,3}|[a-zA-Z]|[ivxIVX]{1,5})[.)]\s+")
_URL = re.compile(r"https?://[^\s<>()\[\]]+")

#: Schemes a link annotation may use. A PDF's annotations are attacker-supplied
#: like the rest of the file, and "javascript:" or "data:text/html" targets stay
#: live once the Markdown is rendered, so anything else keeps its text and loses
#: its link.
SAFE_SCHEMES = ("http://", "https://", "mailto:", "ftp://", "ftps://")


@dataclass
class Run:
    text: str
    bold: bool = False
    italic: bool = False
    link: Optional[str] = None


@dataclass
class Paragraph:
    runs: list[Run] = field(default_factory=list)


@dataclass
class Heading:
    level: int
    runs: list[Run] = field(default_factory=list)


@dataclass
class ListItem:
    level: int
    ordered: bool
    runs: list[Run] = field(default_factory=list)


@dataclass
class Image:
    name: str


@dataclass
class Cell:
    blocks: list[Block] = field(default_factory=list)


@dataclass
class Table:
    rows: list[list[Cell]] = field(default_factory=list)
    rtl: bool = False


@dataclass
class PageBreak:
    pass


Block = Union[Paragraph, Heading, ListItem, Image, Table, PageBreak]


def build(
    document: Document,
    per_page: list[list[Line]],
    *,
    grids: Optional[list[list[Grid]]] = None,
    include_images: bool = True,
    page_breaks: bool = False,
) -> list[Block]:
    """Turn per-page lines into a flat list of blocks."""
    every_line = [line for lines in per_page for line in lines]
    every_line += [
        line
        for page_grids in (grids or [])
        for grid in page_grids
        for row in grid.rows
        for cell in row
        for line in cell
    ]
    body = _body_size(every_line)
    levels = _heading_levels(every_line, body)
    titles = _outline_titles(document)
    pages = {page.number: page for page in document.pages}

    blocks: list[Block] = []
    for index, lines in enumerate(per_page):
        if page_breaks and index:
            blocks.append(PageBreak())
        page = pages.get(lines[0].page) if lines else _nth(document, index)
        images = list(page.images) if (page is not None and include_images) else []
        found = list(grids[index]) if grids and index < len(grids) else []
        blocks.extend(_page_blocks(lines, page, images, found, levels, titles))
    return _rejoin(blocks)


def _rejoin(blocks: list[Block]) -> list[Block]:
    """Sew a table back together where a page break cut it in two.

    A table running over a page is drawn twice, and its header is repeated at
    the top of the second half so the reader can still tell what the columns
    are. That repeated header is the signal: two tables in a row, the second
    opening with the first's header, were one table before it was printed.
    """
    out: list[Block] = []
    for block in blocks:
        previous = out[-1] if out else None
        if (
            isinstance(block, Table)
            and isinstance(previous, Table)
            and previous.rows
            and block.rows
            and previous.rtl == block.rtl
            and len(block.rows[0]) == len(previous.rows[0])
            and _row_text(block.rows[0]) == _row_text(previous.rows[0])
        ):
            previous.rows.extend(block.rows[1:])
            continue
        out.append(block)
    return out


def _row_text(row: list[Cell]) -> list[str]:
    return [
        " ".join(
            run.text
            for block in cell.blocks
            for run in getattr(block, "runs", [])
        ).strip()
        for cell in row
    ]


def _table(grid: Grid, page: Optional[Page]) -> Table:
    """A detected grid as blocks, one cell at a time.

    A cell is read with the paragraph and list rules but not the heading ones.
    A header cell is bold and short, which is exactly the shape of a heading,
    so a table of one-word bold cells would otherwise come out as a table of
    headings -- and each wrapped line of one as a heading of its own.
    """
    return Table(
        rtl=grid.rtl,
        rows=[
            [Cell(blocks=_page_blocks(cell, page, [], [], {}, {}, headings=False)) for cell in row]
            for row in grid.rows
        ],
    )


def _at(lines: list[Line], index: int) -> Optional[Line]:
    return lines[index] if 0 <= index < len(lines) else None


def _nth(document: Document, index: int) -> Optional[Page]:
    """The page at ``index``, for a page whose lines were all removed."""
    return document.pages[index] if index < len(document.pages) else None


def _page_blocks(
    lines: list[Line],
    page: Optional[Page],
    images: list[ImageBox],
    grids: list[Grid],
    levels: dict[float, int],
    titles: dict[str, int],
    headings: bool = True,
) -> list[Block]:
    gap = leading(lines) if len(lines) > 1 else 12.0
    indents = _list_indents(lines)
    pending: list[Line] = []
    blocks: list[Block] = []
    # Images and tables interleave by height: whatever sits above a line comes
    # before it, which is the only ordering the page itself records.
    remaining = sorted(images, key=lambda item: -(item.y + item.height))
    grids = sorted(grids, key=lambda item: -item.top)

    def flush() -> None:
        if pending:
            blocks.append(_paragraph(pending, page))
            pending.clear()

    def catch_up(above: float) -> None:
        while remaining and (remaining[0].y + remaining[0].height) > above:
            flush()
            blocks.append(Image(name=remaining.pop(0).name))
        while grids and grids[0].top > above:
            flush()
            blocks.append(_table(grids.pop(0), page))

    # The line a list item or heading was last continued from, while it can
    # still take another.
    open_item: Optional[tuple[ListItem, Line]] = None
    open_heading: Optional[tuple[Heading, Line]] = None

    for index, line in enumerate(lines):
        catch_up(line.y)

        alone = line.bold and not any(
            other.bold and other.page == line.page
            for other in (lines[index - 1] if index else None, _at(lines, index + 1))
            if other is not None
        )
        level = _heading_of(line, levels, titles, alone) if headings else None
        if level:
            flush()
            open_item = None
            # A heading too long for one line is still one heading.
            if (
                open_heading is not None
                and open_heading[0].level == level
                and _continues(open_heading[1], line, _heading_reach(line, gap))
            ):
                _append(open_heading[0].runs, _runs(line, page))
                open_heading[0].runs[:] = _merge(open_heading[0].runs)
                open_heading = (open_heading[0], line)
                continue
            heading = Heading(level=level, runs=_runs(line, page))
            blocks.append(heading)
            open_heading = (heading, line)
            continue
        open_heading = None

        item = _list_of(line, indents)
        if item is not None:
            flush()
            blocks.append(item)
            open_item = (item, line)
            continue

        # A wrapped list item runs straight on at the leading, with no marker
        # of its own. Reading it as a new paragraph splits the item's sentence
        # in half, which is what the second line of every long bullet is.
        if open_item is not None and not pending and _continues(open_item[1], line, LIST_GAP * gap):
            _append(open_item[0].runs, _runs(line, page))
            open_item[0].runs[:] = _merge(open_item[0].runs)
            open_item = (open_item[0], line)
            continue

        open_item = None
        if pending and _breaks(pending, line, gap):
            flush()
        pending.append(line)

    flush()
    catch_up(float("-inf"))
    return blocks


def _body_size(lines: list[Line]) -> float:
    """The size most lines are set in; ties go to the smaller size.

    Counting lines rather than characters matters on short documents, where a
    title can easily carry more characters than the two paragraphs under it.
    Headings are few lines almost by definition, whatever their length.
    """
    counts: Counter = Counter(round(line.size, 1) for line in lines if line.text.strip())
    if not counts:
        return 10.0
    most = max(counts.values())
    return min(size for size, count in counts.items() if count == most)


def _heading_levels(lines: list[Line], body: float) -> dict[float, int]:
    """Map each size above the body size to a heading level, largest first."""
    sizes = sorted(
        {round(line.size, 1) for line in lines if line.size >= body * HEADING_RATIO},
        reverse=True,
    )
    return {size: min(index + 1, MAX_HEADING) for index, size in enumerate(sizes)}


def _outline_titles(document: Document) -> dict[str, int]:
    return {_normalise(entry.title): entry.level for entry in document.outline}


def _normalise(text: str) -> str:
    return " ".join(text.split()).strip().lower()


def _heading_of(
    line: Line,
    levels: dict[float, int],
    titles: dict[str, int],
    alone: bool = False,
) -> Optional[int]:
    """The heading level for ``line``, or None if it is not a heading."""
    # The author's own bookmark beats any guess geometry could support.
    level = titles.get(_normalise(line.text))
    if level:
        return min(level, MAX_HEADING)

    size_level = levels.get(round(line.size, 1))
    if size_level:
        return size_level

    text = line.text.strip()
    if not text or not line.bold:
        return None
    # A bold line ending in punctuation is an emphasised sentence, not a title.
    if text.endswith((".", ",", ";", ":", "?", "!")):
        return None
    # One that runs on is one too, unless it stands by itself: a deep heading
    # is often set at the body size and told apart only by its weight, and a
    # bold line with plain text above and below it is not mid-sentence.
    return BOLD_HEADING_LEVEL if alone or len(text) <= BOLD_HEADING_MAX_CHARS else None


def _list_of(line: Line, indents: list[float]) -> Optional[ListItem]:
    """``line`` as a list item, or None when it does not start with a marker."""
    text = line.text.strip()
    bullet = _BULLET.match(text)
    ordered = None if bullet else _ORDERED.match(text)
    marker = bullet or ordered
    if marker is None:
        return None

    return ListItem(
        level=_indent_level(_inset(line), indents),
        ordered=bullet is None,
        runs=_link_runs([Run(text=text[marker.end() :], bold=line.bold, italic=line.italic)]),
    )


def _list_indents(lines: list[Line]) -> list[float]:
    """The distinct edges list items start at, shallowest first.

    Nesting is read from the indents a document actually uses rather than from
    a fixed step, since one writer indents by 18 points and the next by 36.
    """
    marked = sorted(_inset(line) for line in lines if _marker(line.text.strip()))
    clusters: list[float] = []
    for position in marked:
        if not clusters or position - clusters[-1] > INDENT_TOLERANCE:
            clusters.append(position)
    return clusters


def _inset(line: Line) -> float:
    """How far the line is pushed in, counted from the margin it starts at.

    Measuring from the start rather than from the left is what lets one rule
    serve both directions: a Persian list nests towards the left, and its
    deeper items have the *smaller* right edge.
    """
    return -line.start if line.rtl else line.start


def _marker(text: str):
    return _BULLET.match(text) or _ORDERED.match(text)


def _indent_level(inset: float, indents: list[float]) -> int:
    level = 1
    for index, position in enumerate(indents):
        if inset >= position - INDENT_TOLERANCE:
            level = index + 1
    return min(level, MAX_HEADING)


def _heading_reach(line: Line, gap: float) -> float:
    """How far below a heading its own second line may sit."""
    return max(LIST_GAP * gap, HEADING_GAP * line.size)


def _continues(previous: Line, line: Line, limit: float) -> bool:
    """True when ``line`` is the rest of what ``previous`` started."""
    if line.page != previous.page:
        return False
    if abs(previous.y - line.y) > limit:
        return False
    # A wrapped line starts under the item's own text, never further out: an
    # item that has moved back towards the margin belongs to something else.
    return _inset(line) >= _inset(previous) - INDENT_TOLERANCE


def _breaks(pending: list[Line], line: Line, gap: float) -> bool:
    """True when ``line`` starts a new paragraph rather than continuing one."""
    previous = pending[-1]
    if line.page != previous.page:
        return False
    if abs(previous.y - line.y) > PARAGRAPH_GAP * gap:
        return True
    # A line that stops well short of the column's far edge ended its
    # paragraph. Which edge is "far" depends on which way the text runs.
    left = min(other.x0 for other in pending)
    right = max(other.x1 for other in pending)
    span = right - left
    filled = (right - previous.x0) if previous.rtl else (previous.x1 - left)
    if span > 0 and filled < span * PARAGRAPH_SHORT_LINE:
        return True
    return abs(line.start - previous.start) > INDENT_STEP


def _paragraph(lines: list[Line], page: Optional[Page]) -> Paragraph:
    runs: list[Run] = []
    for index, line in enumerate(lines):
        line_runs = _runs(line, page)
        if index:
            _append(runs, line_runs)
        else:
            runs.extend(line_runs)
    return Paragraph(runs=_merge(runs))


def _append(runs: list[Run], line_runs: list[Run]) -> None:
    """Join a continuation line, healing a word split across the break."""
    if not runs or not line_runs:
        runs.extend(line_runs)
        return
    previous, following = runs[-1], line_runs[0]
    if previous.text.endswith("-"):
        if following.text[:1].islower():
            # A word split across lines: drop the hyphen, add no space.
            previous.text = previous.text[:-1]
        # Otherwise a real compound: keep the hyphen, still no space.
    elif not previous.text.endswith(" "):
        previous.text += " "
    runs.extend(line_runs)


def _merge(runs: list[Run]) -> list[Run]:
    """Collapse neighbouring runs that share their styling."""
    merged: list[Run] = []
    for run in runs:
        if (
            merged
            and merged[-1].bold == run.bold
            and merged[-1].italic == run.italic
            and merged[-1].link == run.link
        ):
            merged[-1].text += run.text
        else:
            merged.append(run)
    return [run for run in merged if run.text]


def _runs(line: Line, page: Optional[Page]) -> list[Run]:
    """The line's text as styled runs, in reading order.

    The line has already paired each stretch of text with the run that drew
    it, which is what keeps bold, italic and links on exactly their own
    characters after a right-to-left line has been put back in order.
    """
    links = page.links if page is not None else []
    runs = [
        Run(text=text, bold=source.bold, italic=source.italic, link=_link_at(source, links))
        for source, text in line.parts
    ]
    return _link_runs(_merge(runs))


def _link_at(run: TextRun, links: list[LinkBox]) -> Optional[str]:
    for link in links:
        if link.x0 <= run.x <= link.x1 and link.y0 <= run.y <= link.y1:
            return link.uri if _is_safe(link.uri) else None
    return None


def _is_safe(uri: str) -> bool:
    """True for a scheme that cannot execute when the Markdown is rendered."""
    return uri.strip().lower().startswith(SAFE_SCHEMES)


def _link_runs(runs: list[Run]) -> list[Run]:
    """Split out bare URLs so they render as links."""
    result: list[Run] = []
    for run in runs:
        if run.link or not _URL.search(run.text):
            result.append(run)
            continue
        cursor = 0
        for match in _URL.finditer(run.text):
            if match.start() > cursor:
                result.append(Run(run.text[cursor : match.start()], run.bold, run.italic))
            result.append(Run(match.group(), run.bold, run.italic, match.group()))
            cursor = match.end()
        if cursor < len(run.text):
            result.append(Run(run.text[cursor:], run.bold, run.italic))
    return result
