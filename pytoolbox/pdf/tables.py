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

#: And a row or column narrower than this holds no text at any size, so it is
#: a border's thickness or a cell's padding rather than a track of its own.
MIN_TRACK = 6.0

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
        segment for rule in _outermost(page.rules) for segment in _segments(rule, page)
    ]
    tables: list[Table] = []
    taken: set[int] = set()

    for group in _clusters(segments):
        columns = _bands([piece.at for piece in group if piece.vertical])
        rows = _bands([piece.at for piece in group if not piece.vertical])
        if len(columns) < MIN_COLUMNS or len(rows) < MIN_ROWS:
            continue
        verticals = _rules_within(segments, rows, columns)
        # The columns are every rule inside the table, joined or not: the one
        # that touches nothing still says there is a column beside it.
        columns = _bands([piece.at for piece in verticals]) or columns
        table = _fill(page, rows, columns, verticals, base, taken)
        if table is not None:
            tables.append(table)

    tables.sort(key=lambda table: -table.top)
    return tables, [run for run in page.runs if id(run) not in taken]


def _rules_within(
    segments: list[Segment],
    rows: list[tuple[float, float]],
    columns: list[tuple[float, float]],
) -> list[Segment]:
    """Every vertical rule drawn inside the table's bounds, joined or not.

    The cluster that defines the grid is the rules that touch. A writer that
    draws each cell's borders on their own, with a gap between cells, leaves a
    row's inner rules in clusters of their own -- still that row's rules, and
    what says whether two of its columns are one cell.
    """
    left, right = columns[0][0] - MIN_TRACK, columns[-1][1] + MIN_TRACK
    low, high = rows[0][0] - EDGE_TOLERANCE, rows[-1][1] + EDGE_TOLERANCE
    return [
        piece
        for piece in segments
        if piece.vertical
        and left <= piece.at <= right
        and piece.high >= low
        and piece.low <= high
    ]


def _outermost(rules: list[RuleBox]) -> list[RuleBox]:
    """Drop rectangles drawn inside another rectangle.

    Some writers paint a background behind every *line* of a cell as well as
    behind the cell itself. Those inner rectangles stack up, and the edge
    between two of them looks exactly like the edge between two rows -- so the
    cell comes back split, one row per line of text it holds. A box drawn
    inside a box says nothing about the grid, so the outer one is kept.

    Only boxes are dropped. A rectangle thin enough to be a drawn line is a
    grid line wherever it sits, and writers that stroke their borders draw them
    right along the edge of the cell they have already filled.
    """
    largest = sorted(rules, key=lambda rule: -(rule.width * rule.height))
    kept: list[RuleBox] = []
    for rule in largest:
        boxed = rule.width >= MIN_SIDE and rule.height >= MIN_SIDE
        if not boxed or not any(_contains(outer, rule) for outer in kept):
            kept.append(rule)
    return kept


def _contains(outer: RuleBox, inner: RuleBox) -> bool:
    return (
        outer.x0 <= inner.x0 + EDGE_TOLERANCE
        and outer.x1 >= inner.x1 - EDGE_TOLERANCE
        and outer.y0 <= inner.y0 + EDGE_TOLERANCE
        and outer.y1 >= inner.y1 - EDGE_TOLERANCE
        and outer.width * outer.height > inner.width * inner.height
    )


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
    """The tracks the distinct edges cut the table into.

    A writer that gives its borders a thickness, and its cells some padding
    inside that, leaves a pair of edges a few points apart at every rule. The
    sliver between them is not a column -- nothing could be written in it -- so
    it is handed to whichever neighbour it abuts, and the row or column it was
    part of gets its own width back.
    """
    distinct: list[float] = []
    for edge in sorted(edges):
        if not distinct or edge - distinct[-1] > EDGE_TOLERANCE:
            distinct.append(edge)

    bands = list(zip(distinct, distinct[1:]))
    while len(bands) > 1:
        narrowest = min(range(len(bands)), key=lambda index: _span(bands[index]))
        if _span(bands[narrowest]) >= MIN_TRACK:
            break
        low, high = bands.pop(narrowest)
        if narrowest == 0:
            bands[0] = (low, bands[0][1])
        elif narrowest == len(bands):
            bands[-1] = (bands[-1][0], high)
        elif _span(bands[narrowest - 1]) >= _span(bands[narrowest]):
            bands[narrowest - 1] = (bands[narrowest - 1][0], high)
        else:
            bands[narrowest] = (low, bands[narrowest][1])
    return bands


def _span(band: tuple[float, float]) -> float:
    return band[1] - band[0]


@dataclass
class _Cell:
    """One cell of one row: where it sits, and which shared columns it covers."""

    low: float
    high: float
    first: int
    last: int


def _fill(
    page: Page,
    rows: list[tuple[float, float]],
    columns: list[tuple[float, float]],
    verticals: list[Segment],
    base: Optional[str],
    taken: set[int],
) -> Optional[Table]:
    """Sort the runs inside the grid into cells, top row first."""
    bottom, top = rows[0][0], rows[-1][1]
    cells_of = [_row_cells(row, columns, verticals) for row in rows]

    # A cell is read as one whole however many columns it spans: text in a
    # merged cell is laid out against the width of the cell, not of the
    # columns the rows above and below it happen to share.
    found: list[dict[int, list[TextRun]]] = [{} for _ in rows]
    inside = False
    for run in page.runs:
        if id(run) in taken:
            continue
        row = _track(rows, run.y)
        column = _track(columns, _middle(run))
        if row is None or column is None:
            continue
        index = _cell_at(cells_of[row], _middle(run))
        # Blank runs are kept: a space between two words is a run of its own
        # in some writers, and dropping it here would close the gap.
        found[row].setdefault(index, []).append(run)
        taken.add(id(run))
        inside = inside or bool(run.text.strip())
    if not inside:
        return None

    lined = [
        {index: to_lines(runs, page.number, base) for index, runs in row.items()} for row in found
    ]
    rtl = any(line.rtl for row in lined for lines in row.values() for line in lines)

    table = Table(page=page.number, top=top, bottom=bottom, rtl=rtl)
    width = len(columns)
    for row, cells in zip(reversed(lined), reversed(cells_of)):  # PDF y grows upwards.
        out: list[list[Line]] = [[] for _ in range(width)]
        # Cells that fall in one column (the shared columns can be coarser than
        # a row's own rules) are read the way the table is: from the right on
        # a right-to-left one.
        for index in reversed(range(len(cells))) if rtl else range(len(cells)):
            cell = cells[index]
            # A merged cell's text goes in the column its reader reaches first:
            # the leftmost, or on a right-to-left table the rightmost.
            out[cell.last if rtl else cell.first].extend(row.get(index, []))
        # The grid was read left to right off the page; a right-to-left table's
        # first column is the rightmost one.
        table.rows.append(list(reversed(out)) if rtl else out)
    return table if any(any(cell for cell in row) for row in table.rows) else None


def _row_cells(
    row: tuple[float, float],
    columns: list[tuple[float, float]],
    verticals: list[Segment],
) -> list[_Cell]:
    """The cells of one row, from the rules that row itself has.

    Rules are drawn row by row, so a merged cell is a row where a rule between
    two columns was simply not drawn -- and which rules a row has is read off
    that row, not off the table's shared columns. Those are a compromise: the
    edges of every row's boxes, folded together wherever they lie too close to
    be told apart, which can leave one row's rule a few points from anywhere.

    A row says something about its cells only once it is closed at both ends.
    One missing an outer edge has said nothing about its inner boundaries and
    keeps the table's columns.
    """
    separate = [_Cell(low, high, index, index) for index, (low, high) in enumerate(columns)]
    middle = (row[0] + row[1]) / 2
    positions = [
        piece.at
        for piece in verticals
        if piece.low - EDGE_TOLERANCE <= middle <= piece.high + EDGE_TOLERANCE
    ]
    bands = _bands(positions) if len(positions) > 1 else []
    left, right = columns[0][0], columns[-1][1]
    if not bands or abs(bands[0][0] - left) > MIN_TRACK or abs(bands[-1][1] - right) > MIN_TRACK:
        return separate

    cells: list[_Cell] = []
    for low, high in bands:
        covered = [
            index
            for index, (start, stop) in enumerate(columns)
            if low <= (start + stop) / 2 <= high
        ]
        if not covered:
            centre = (low + high) / 2
            covered = [
                min(range(len(columns)), key=lambda i: abs((columns[i][0] + columns[i][1]) / 2 - centre))
            ]
        cells.append(_Cell(low, high, min(covered), max(covered)))
    return cells


def _cell_at(cells: list[_Cell], position: float) -> int:
    """Which cell a run's middle falls in, or the nearest when it falls between."""
    for index, cell in enumerate(cells):
        if cell.low - EDGE_TOLERANCE <= position <= cell.high + EDGE_TOLERANCE:
            return index
    return min(range(len(cells)), key=lambda i: abs((cells[i].low + cells[i].high) / 2 - position))


def _middle(run: TextRun) -> float:
    return (run.x + run.end) / 2 if run.end is not None else run.x


def _track(bands: list[tuple[float, float]], position: float) -> Optional[int]:
    """Which band ``position`` falls in, allowing for the edge tolerance."""
    for index, (low, high) in enumerate(bands):
        if low - EDGE_TOLERANCE <= position <= high + EDGE_TOLERANCE:
            return index
    return None
