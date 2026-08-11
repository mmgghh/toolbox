"""Runs to lines, and lines into reading order.

The rules here are geometric and deliberately conservative: a page that does
not split cleanly into columns is left in plain reading order, because
half-detected columns interleave real sentences, which is worse than not
detecting them at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from pytoolbox.pdf.reader import Page, TextRun

#: Baselines within this fraction of the font size are the same line.
BASELINE_TOLERANCE = 0.4

#: A horizontal gap wider than this fraction of the font size is a space.
SPACE_GAP = 0.25

#: Average glyph width as a fraction of the font size, for estimating extents.
GLYPH_WIDTH = 0.5

#: A gutter must be at least this fraction of the page wide.
MIN_GUTTER = 0.02

#: And at least this share of lines must sit cleanly on one side of it.
MIN_COLUMN_SHARE = 0.6

#: Fraction of the page height at the top and bottom where furniture lives.
FURNITURE_BAND = 0.08

#: A repeated line must appear on at least this share of pages ...
FURNITURE_SHARE = 0.5

#: ... and the document must have at least this many pages for that to count.
FURNITURE_MIN_PAGES = 3

#: Vertical wobble tolerated between one page's header and the next's.
FURNITURE_Y_TOLERANCE = 4.0

#: Digits are only normalised on lines this short. "Page 3" and "Page 4" are
#: the same piece of furniture; "Chapter 3: Methods" and "Chapter 4: Results"
#: are two headings that happen to carry a number, and must not be collapsed.
FURNITURE_NUMBERED_MAX_CHARS = 20

_DIGITS = re.compile(r"\d+")


@dataclass
class Line:
    """Runs sharing a baseline, in reading order."""

    runs: list[TextRun] = field(default_factory=list)
    page: int = 0

    @property
    def text(self) -> str:
        return _join(self.runs)

    @property
    def x0(self) -> float:
        return min(run.x for run in self.runs)

    @property
    def x1(self) -> float:
        # A run carries no width, so estimate it from the glyph count.
        return max(run.x + GLYPH_WIDTH * run.size * len(run.text) for run in self.runs)

    @property
    def y(self) -> float:
        return max(run.y for run in self.runs)

    @property
    def size(self) -> float:
        return max(run.size for run in self.runs)

    @property
    def bold(self) -> bool:
        visible = [run for run in self.runs if run.text.strip()]
        return bool(visible) and all(run.bold for run in visible)

    @property
    def italic(self) -> bool:
        visible = [run for run in self.runs if run.text.strip()]
        return bool(visible) and all(run.italic for run in visible)


def _join(runs: list[TextRun]) -> str:
    """Concatenate runs, inserting a space only where the gap implies one."""
    if not runs:
        return ""
    parts = [runs[0].text]
    for previous, current in zip(runs, runs[1:]):
        end = previous.x + GLYPH_WIDTH * previous.size * len(previous.text)
        gap = current.x - end
        if gap > SPACE_GAP * max(previous.size, current.size) and not parts[-1].endswith(" "):
            parts.append(" ")
        parts.append(current.text)
    return "".join(parts).strip()


def to_lines(runs: list[TextRun], page: int = 0) -> list[Line]:
    """Group runs sharing a baseline, ordered top to bottom then left to right."""
    lines: list[Line] = []
    for run in sorted(runs, key=lambda item: (-item.y, item.x)):
        target: Optional[Line] = None
        for line in lines:
            tolerance = BASELINE_TOLERANCE * max(line.size, run.size, 1.0)
            if abs(line.runs[0].y - run.y) <= tolerance:
                target = line
                break
        if target is None:
            lines.append(Line(runs=[run], page=page))
        else:
            target.runs.append(run)

    for line in lines:
        line.runs.sort(key=lambda item: item.x)
    lines.sort(key=lambda line: (-line.y, line.x0))
    return lines


def order_by_columns(lines: list[Line], width: float) -> list[Line]:
    """Reorder ``lines`` column by column, or return them unchanged."""
    split = _gutter(lines, width)
    if split is None:
        return list(lines)

    left, right, spanning = [], [], []
    for line in lines:
        if line.x1 <= split:
            left.append(line)
        elif line.x0 >= split:
            right.append(line)
        else:
            spanning.append(line)

    # A title or figure crossing the gutter keeps its place above the columns.
    ordered = sorted(spanning, key=lambda item: -item.y)
    ordered.extend(sorted(left, key=lambda item: -item.y))
    ordered.extend(sorted(right, key=lambda item: -item.y))
    return ordered


def _gutter(lines: list[Line], width: float) -> Optional[float]:
    """The x of a vertical whitespace gutter, if the page has a clean one."""
    body = [line for line in lines if line.runs]
    if len(body) < 4 or width <= 0:
        return None

    # Only the middle is considered: multi-column layouts are symmetric in
    # practice, and searching every x finds "gutters" inside indented text.
    candidate = width / 2
    left = [line for line in body if line.x1 <= candidate]
    right = [line for line in body if line.x0 >= candidate]
    if not left or not right:
        return None
    if (len(left) + len(right)) / len(body) < MIN_COLUMN_SHARE:
        return None

    gap = min(line.x0 for line in right) - max(line.x1 for line in left)
    if gap < MIN_GUTTER * width:
        return None
    return candidate


def page_lines(page: Page, single_column: bool = False) -> list[Line]:
    """Every line on ``page``, in reading order."""
    lines = to_lines(page.runs, page.number)
    if single_column:
        return lines
    return order_by_columns(lines, page.width)


def leading(lines: list[Line]) -> float:
    """Median vertical distance between consecutive lines."""
    gaps = [
        abs(previous.y - current.y)
        for previous, current in zip(lines, lines[1:])
        if previous.page == current.page and abs(previous.y - current.y) > 0.1
    ]
    return median(gaps) if gaps else 12.0


def drop_furniture(per_page: list[list[Line]], pages: list[Page]) -> list[list[Line]]:
    """Remove running headers, footers and page numbers.

    A line counts as furniture when it sits in the top or bottom band of the
    page and its text -- with digits normalised, so "Page 3" and "Page 4" match
    -- recurs at about the same height on most pages.
    """
    if len(pages) < FURNITURE_MIN_PAGES:
        return [list(lines) for lines in per_page]

    heights = {page.number: page.height for page in pages}
    seen: dict[str, list[float]] = {}
    for lines in per_page:
        for line in lines:
            key = _furniture_key(line, heights.get(line.page, 792.0))
            if key is not None:
                seen.setdefault(key, []).append(line.y)

    threshold = max(FURNITURE_MIN_PAGES, len(pages) * FURNITURE_SHARE)
    repeated = {key for key, ys in seen.items() if len(ys) >= threshold and _consistent(ys)}

    return [
        [
            line
            for line in lines
            if _furniture_key(line, heights.get(line.page, 792.0)) not in repeated
        ]
        for lines in per_page
    ]


def _furniture_key(line: Line, height: float) -> Optional[str]:
    """A page-independent identity for a line in the header or footer band."""
    band = FURNITURE_BAND * height
    if band < line.y < height - band:
        return None
    text = line.text.strip()
    if not text:
        return None
    normalised = _DIGITS.sub("#", text)
    if len(normalised) <= FURNITURE_NUMBERED_MAX_CHARS:
        text = normalised
    where = "top" if line.y >= height / 2 else "bottom"
    return f"{where}:{text}"


def _consistent(ys: list[float]) -> bool:
    """True when every occurrence sits at about the same height."""
    return max(ys) - min(ys) <= FURNITURE_Y_TOLERANCE
