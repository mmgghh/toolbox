"""Finding tables by the lines the writer drew around them.

Position alone cannot tell a table from a two-column page: both are text, a
band of air, then more text, and both keep their edges in line down the page.
The difference is that a table is *drawn*. So the grid is read off the painted
rectangles rather than guessed at from the gaps, which also means an empty
cell, a wrapped cell and a merged cell all fall out for free -- each is a
question about the grid, and the grid is known.

A table nobody drew is left as running text. That loses the columns, which is
a real loss, but it is the same loss as before and it never invents a table in
the middle of a paragraph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pytoolbox.pdf.layout import Line, to_lines
from pytoolbox.pdf.reader import Page, RuleBox, TextRun

#: Sides shorter than this make the rectangle a drawn line rather than a box.
MIN_SIDE = 3.0

#: Two edges this close together are the same edge.
EDGE_TOLERANCE = 2.0

#: A rectangle covering this much of the page is a background, not a table.
MAX_PAGE_SHARE = 0.6

#: Nor is one reaching this far across the page. A table sits inside the
#: margins; a band running edge to edge is a header, a footer or a rule, and
#: letting one join a table it happens to touch adds a column either side.
MAX_SPAN = 0.95

#: A table needs at least this many rows and columns to be worth the name.
MIN_ROWS = 2
MIN_COLUMNS = 2


@dataclass
class Table:
    """A grid of cells, each holding the lines that fall inside it."""

    rows: list[list[list[Line]]] = field(default_factory=list)
    page: int = 0
    top: float = 0.0
    bottom: float = 0.0
    rtl: bool = False


@dataclass
class Segment:
    """One drawn grid line: where it sits, and how far it runs."""

    vertical: bool
    at: float
    low: float
    high: float


def find(page: Page, base: Optional[str] = None) -> tuple[list[Table], list[TextRun]]:
    """Every table drawn on ``page``, and the runs left outside them."""
    segments = [
        segment for rule in page.rules for segment in _segments(rule, page)
    ]
    tables: list[Table] = []
    taken: set[int] = set()

    for group in _clusters(segments):
        columns = _bands([piece.at for piece in group if piece.vertical])
        rows = _bands([piece.at for piece in group if not piece.vertical])
        if len(columns) < MIN_COLUMNS or len(rows) < MIN_ROWS:
            continue
        table = _fill(page, rows, columns, base, taken)
        if table is not None:
            tables.append(table)

    tables.sort(key=lambda table: -table.top)
    return tables, [run for run in page.runs if id(run) not in taken]


def _segments(rule: RuleBox, page: Page) -> list[Segment]:
    """The grid lines a painted rectangle stands for.

    Writers differ: one draws a filled box per cell and lets the boxes' edges
    be the grid, the next draws the borders themselves as rectangles a fraction
    of a point wide. Both are read as the lines they look like on the page.
    """
    area = page.width * page.height
    if area and (rule.width * rule.height) / area >= MAX_PAGE_SHARE:
        return []
    if rule.width >= MAX_SPAN * page.width or rule.height >= MAX_SPAN * page.height:
        return []
    thin_across = rule.width < MIN_SIDE
    thin_down = rule.height < MIN_SIDE
    if thin_across and thin_down:
        return []
    if thin_across:
        return [Segment(True, (rule.x0 + rule.x1) / 2, rule.y0, rule.y1)]
    if thin_down:
        return [Segment(False, (rule.y0 + rule.y1) / 2, rule.x0, rule.x1)]
    return [
        Segment(True, rule.x0, rule.y0, rule.y1),
        Segment(True, rule.x1, rule.y0, rule.y1),
        Segment(False, rule.y0, rule.x0, rule.x1),
        Segment(False, rule.y1, rule.x0, rule.x1),
    ]


def _clusters(segments: list[Segment]) -> list[list[Segment]]:
    """Grid lines grouped into the tables they belong to.

    One table's lines touch, end to end or crossing; two tables on a page do
    not, and a rule under a heading touches nothing at all.
    """
    groups: list[list[Segment]] = []
    for piece in segments:
        joined = [group for group in groups if any(_touching(piece, other) for other in group)]
        merged = [piece]
        for group in joined:
            merged.extend(group)
            groups.remove(group)
        groups.append(merged)
    return groups


def _touching(one: Segment, other: Segment) -> bool:
    gap = EDGE_TOLERANCE
    left, right = _box(one), _box(other)
    return (
        left[0] - gap <= right[2]
        and right[0] - gap <= left[2]
        and left[1] - gap <= right[3]
        and right[1] - gap <= left[3]
    )


def _box(piece: Segment) -> tuple[float, float, float, float]:
    if piece.vertical:
        return piece.at, piece.low, piece.at, piece.high
    return piece.low, piece.at, piece.high, piece.at


def _bands(edges: list[float]) -> list[tuple[float, float]]:
    """Consecutive pairs of the distinct edges, which are the tracks."""
    distinct: list[float] = []
    for edge in sorted(edges):
        if not distinct or edge - distinct[-1] > EDGE_TOLERANCE:
            distinct.append(edge)
    return list(zip(distinct, distinct[1:]))


def _fill(
    page: Page,
    rows: list[tuple[float, float]],
    columns: list[tuple[float, float]],
    base: Optional[str],
    taken: set[int],
) -> Optional[Table]:
    """Sort the runs inside the grid into cells, top row first."""
    bottom, top = rows[0][0], rows[-1][1]

    cells: list[list[list[TextRun]]] = [[[] for _ in columns] for _ in rows]
    inside = False
    for run in page.runs:
        if id(run) in taken:
            continue
        row = _track(rows, run.y)
        column = _track(columns, _middle(run))
        if row is None or column is None:
            continue
        # Blank runs are kept: a space between two words is a run of its own
        # in some writers, and dropping it here would close the gap.
        cells[row][column].append(run)
        taken.add(id(run))
        inside = inside or bool(run.text.strip())
    if not inside:
        return None

    table = Table(page=page.number, top=top, bottom=bottom)
    table.rows = [
        [to_lines(cell, page.number, base) for cell in row]
        for row in reversed(cells)  # PDF y grows upwards; a table reads downwards.
    ]
    table.rtl = any(line.rtl for row in table.rows for cell in row for line in cell)
    if table.rtl:
        # The grid was read left to right off the page; a right-to-left table's
        # first column is the rightmost one.
        table.rows = [list(reversed(row)) for row in table.rows]
    return table if any(any(cell for cell in row) for row in table.rows) else None


def _middle(run: TextRun) -> float:
    return (run.x + run.end) / 2 if run.end is not None else run.x


def _track(bands: list[tuple[float, float]], position: float) -> Optional[int]:
    """Which band ``position`` falls in, allowing for the edge tolerance."""
    for index, (low, high) in enumerate(bands):
        if low - EDGE_TOLERANCE <= position <= high + EDGE_TOLERANCE:
            return index
    return None
