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

#: A heading must be at least this much larger than the body text.
HEADING_RATIO = 1.15

MAX_HEADING = 6

#: Level given to a bold line that is short enough to read as a heading.
BOLD_HEADING_LEVEL = 3

#: A bold line longer than this many characters is a sentence, not a heading.
BOLD_HEADING_MAX_CHARS = 60

#: A paragraph breaks when the gap exceeds this multiple of the median leading.
PARAGRAPH_GAP = 1.5

#: A line ending before this share of the block's right edge ends the paragraph.
PARAGRAPH_SHORT_LINE = 0.85

#: Horizontal shift that starts a new paragraph, in points.
INDENT_STEP = 12.0

#: List indents closer together than this are the same nesting level.
INDENT_TOLERANCE = 4.0

_BULLET = re.compile(r"^[•◦▪·⁃–‐-]\s+")
_ORDERED = re.compile(r"^(\d{1,3}|[a-zA-Z]|[ivxIVX]{1,5})[.)]\s+")
_URL = re.compile(r"https?://[^\s<>()\[\]]+")


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
class PageBreak:
    pass


Block = Union[Paragraph, Heading, ListItem, Image, PageBreak]


def build(
    document: Document,
    per_page: list[list[Line]],
    *,
    include_images: bool = True,
    page_breaks: bool = False,
) -> list[Block]:
    """Turn per-page lines into a flat list of blocks."""
    every_line = [line for lines in per_page for line in lines]
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
        blocks.extend(_page_blocks(lines, page, images, levels, titles))
    return blocks


def _nth(document: Document, index: int) -> Optional[Page]:
    """The page at ``index``, for a page whose lines were all removed."""
    return document.pages[index] if index < len(document.pages) else None


def _page_blocks(
    lines: list[Line],
    page: Optional[Page],
    images: list[ImageBox],
    levels: dict[float, int],
    titles: dict[str, int],
) -> list[Block]:
    gap = leading(lines) if len(lines) > 1 else 12.0
    indents = _list_indents(lines)
    pending: list[Line] = []
    blocks: list[Block] = []
    # Images interleave by height: a picture above a line comes before it.
    remaining = sorted(images, key=lambda item: -(item.y + item.height))

    def flush() -> None:
        if pending:
            blocks.append(_paragraph(pending, page))
            pending.clear()

    for line in lines:
        while remaining and (remaining[0].y + remaining[0].height) > line.y:
            flush()
            blocks.append(Image(name=remaining.pop(0).name))

        level = _heading_of(line, levels, titles)
        if level:
            flush()
            blocks.append(Heading(level=level, runs=_runs(line, page)))
            continue

        item = _list_of(line, indents)
        if item is not None:
            flush()
            blocks.append(item)
            continue

        if pending and _breaks(pending, line, gap):
            flush()
        pending.append(line)

    flush()
    blocks.extend(Image(name=image.name) for image in remaining)
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


def _heading_of(line: Line, levels: dict[float, int], titles: dict[str, int]) -> Optional[int]:
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
    # A bold line that runs on, or ends in punctuation, is an emphasised
    # sentence rather than a title.
    if len(text) > BOLD_HEADING_MAX_CHARS or text.endswith((".", ",", ";", ":", "?", "!")):
        return None
    return BOLD_HEADING_LEVEL


def _list_of(line: Line, indents: list[float]) -> Optional[ListItem]:
    """``line`` as a list item, or None when it does not start with a marker."""
    text = line.text.strip()
    bullet = _BULLET.match(text)
    ordered = None if bullet else _ORDERED.match(text)
    marker = bullet or ordered
    if marker is None:
        return None

    return ListItem(
        level=_indent_level(line.x0, indents),
        ordered=bullet is None,
        runs=_link_runs([Run(text=text[marker.end() :], bold=line.bold, italic=line.italic)]),
    )


def _list_indents(lines: list[Line]) -> list[float]:
    """The distinct left edges list items start at, shallowest first.

    Nesting is read from the indents a document actually uses rather than from
    a fixed step, since one writer indents by 18 points and the next by 36.
    """
    marked = sorted(line.x0 for line in lines if _marker(line.text.strip()))
    clusters: list[float] = []
    for position in marked:
        if not clusters or position - clusters[-1] > INDENT_TOLERANCE:
            clusters.append(position)
    return clusters


def _marker(text: str):
    return _BULLET.match(text) or _ORDERED.match(text)


def _indent_level(x0: float, indents: list[float]) -> int:
    level = 1
    for index, position in enumerate(indents):
        if x0 >= position - INDENT_TOLERANCE:
            level = index + 1
    return min(level, MAX_HEADING)


def _breaks(pending: list[Line], line: Line, gap: float) -> bool:
    """True when ``line`` starts a new paragraph rather than continuing one."""
    previous = pending[-1]
    if line.page != previous.page:
        return False
    if abs(previous.y - line.y) > PARAGRAPH_GAP * gap:
        return True
    # A line that stops well short of the column's edge ended its paragraph.
    right = max(other.x1 for other in pending)
    if previous.x1 < right * PARAGRAPH_SHORT_LINE:
        return True
    return abs(line.x0 - previous.x0) > INDENT_STEP


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
    links = page.links if page is not None else []
    runs = [
        Run(text=text, bold=source.bold, italic=source.italic, link=_link_at(source, links))
        for source, text in _texts(line)
    ]
    return _link_runs(_merge(runs))


def _texts(line: Line) -> list[tuple[TextRun, str]]:
    """Each run paired with its text, including the spacing the line implies.

    The line's own joining decides where spaces go, so the text is re-split
    along the original run boundaries; that way a run's styling still applies
    to exactly its own characters.
    """
    joined = line.text
    if len(line.runs) == 1:
        return [(line.runs[0], joined)]

    pairs: list[tuple[TextRun, str]] = []
    cursor = 0
    for run in line.runs:
        start = joined.find(run.text, cursor)
        if start == -1:
            pairs.append((run, run.text))
            continue
        if start > cursor:
            # The separator the join inserted belongs to the preceding run.
            pairs.append((line.runs[0] if not pairs else pairs[-1][0], joined[cursor:start]))
        pairs.append((run, run.text))
        cursor = start + len(run.text)
    if cursor < len(joined):
        pairs.append((line.runs[-1], joined[cursor:]))
    return pairs


def _link_at(run: TextRun, links: list[LinkBox]) -> Optional[str]:
    for link in links:
        if link.x0 <= run.x <= link.x1 and link.y0 <= run.y <= link.y1:
            return link.uri
    return None


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
