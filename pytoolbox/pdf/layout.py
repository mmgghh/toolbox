"""Runs to lines, and lines into reading order.

The rules here are geometric and deliberately conservative: a page that does
not split cleanly into columns is left in plain reading order, because
half-detected columns interleave real sentences, which is worse than not
detecting them at all.

A line arrives as glyphs in paint order and leaves as text in reading order.
For a left-to-right page those are the same thing; for a Persian or Arabic one
they are not, and :mod:`~pytoolbox.pdf.text` does the reversing. The runs
themselves stay in paint order throughout, because that is the order their
positions are in and every geometric rule below reads positions.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from statistics import median
from typing import Optional

from pytoolbox.pdf import text as bidi_text
from pytoolbox.pdf.reader import Page, TextRun

#: Baselines within this fraction of the font size are the same line.
BASELINE_TOLERANCE = 0.4

#: A horizontal gap wider than this fraction of the font size is a space.
SPACE_GAP = 0.25

#: And one wider than this is not a space at all but a jump to the next cell.
CELL_GAP = 1.2

#: Average glyph width as a fraction of the font size, for estimating extents.
GLYPH_WIDTH = 0.5

#: A run this much of whose width lands on its neighbour was drawn on top of
#: it, and so took no room of its own: a vowel mark, or a non-joiner's space.
DRAWN_OVER = 0.8

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
    """Runs sharing a baseline, kept in paint order.

    ``base`` is the direction this line's own text reads in and decides the
    order its runs are stitched together in. ``flow`` is the direction the
    page runs in and decides which edge is the start of a line, which is not
    the same question: a Latin caption inside a Persian table still hangs off
    the right margin, and reading its own three words left to right does not
    move it.
    """

    runs: list[TextRun] = field(default_factory=list)
    page: int = 0
    base: str = "L"
    flow: str = "L"
    _parts: Optional[list[tuple[TextRun, str]]] = field(
        default=None, repr=False, compare=False
    )

    @property
    def parts(self) -> list[tuple[TextRun, str]]:
        """Each run with the text it contributes, in reading order.

        Pairing text back to its run is what keeps bold, italic and links
        attached to exactly their own characters once the line has been
        reordered.
        """
        if self._parts is None:
            self._parts = _stitch(self.runs, self.base)
        return self._parts

    @property
    def text(self) -> str:
        return "".join(text for _, text in self.parts).strip()

    @property
    def rtl(self) -> bool:
        return self.flow == "R"

    @property
    def x0(self) -> float:
        return min(run.x for run in self.runs)

    @property
    def x1(self) -> float:
        return max(_end(run) for run in self.runs)

    @property
    def start(self) -> float:
        """The edge the line begins at, whichever way it is read."""
        return self.x1 if self.rtl else self.x0

    @property
    def stop(self) -> float:
        """The edge the line ends at, whichever way it is read."""
        return self.x0 if self.rtl else self.x1

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


def _stitch(runs: list[TextRun], base: str) -> list[tuple[TextRun, str]]:
    """Painted runs to reading-order text, styling still attached.

    Runs are reordered in blocks rather than one at a time: reordering is what
    moves a run's characters elsewhere in the line, and a block that is bold
    throughout stays bold wherever it lands. A block ends where the styling
    changes or where a gap wide enough to be a space falls, so the spaces
    themselves stay between the same two neighbours after the reversal.
    """
    if not runs:
        return []

    runs = _marks_first(runs, base)
    blocks: list[list[tuple[TextRun, str]]] = []
    # A separator block is finished the moment it opens: text may not join it,
    # or the space would drift to the far end of its neighbour on reversal.
    separator: list[bool] = []

    reach = _end(runs[0])
    for index, run in enumerate(runs):
        following = runs[index + 1] if index + 1 < len(runs) else None
        piece = _piece(runs[index - 1] if index else None, run, following)
        if piece is not None:
            if blocks and not separator[-1] and _same_style(blocks[-1][0][0], run):
                blocks[-1].append((run, piece))
            else:
                blocks.append([(run, piece)])
                separator.append(False)
        reach = max(reach, _end(run))
        if following is not None and _spaced(run, following, reach):
            blocks.append([(run, " ")])
            separator.append(True)

    ordered = list(reversed(blocks)) if base == "R" else blocks
    out: list[tuple[TextRun, str]] = []
    for block in ordered:
        restored = bidi_text.restore("".join(text for _, text in block), base)
        if restored:
            out.append((block[0][0], restored))
    return out


def _marks_first(runs: list[TextRun], base: str) -> list[TextRun]:
    """Move a run drawn *over* its neighbour in front of it.

    Two things are drawn this way and neither takes any room: a vowel mark,
    painted on top of its letter, and the space some writers draw where a
    non-joiner belongs. Which side of the letter the writer drew it on says
    nothing -- the writers tested disagree -- but which letter it is drawn over
    says everything, and putting it first is what survives the reversal a
    right-to-left line is about to go through.
    """
    if base != "R":
        return runs
    ordered = list(runs)
    for index in range(1, len(ordered)):
        drawn = ordered[index]
        before = ordered[index - 1]
        after = ordered[index + 1] if index + 1 < len(ordered) else None
        # Whichever neighbour it is drawn further over is the one it belongs
        # to. Something already sitting in front of its letter overlaps the
        # run after it, and moving that would hand it to the word before.
        if _mark_only(drawn):
            moves = _covered(drawn, before) > _covered(drawn, after)
        elif not drawn.text.strip():
            # A space needs the stricter test: kerning laps one letter a little
            # way over the next, and a space next to a kerned pair would look
            # drawn-over on the strength of that alone.
            moves = _over(drawn, before) and not _over(drawn, after)
        else:
            continue
        if moves:
            ordered[index - 1], ordered[index] = drawn, before
    return ordered


def _covered(run: TextRun, other: Optional[TextRun]) -> float:
    """How much of ``run``'s width is drawn over ``other``."""
    if other is None:
        return 0.0
    return max(0.0, min(_end(run), _end(other)) - max(run.x, other.x))


def _mark_only(run: TextRun) -> bool:
    return bool(run.text) and all(unicodedata.combining(char) for char in run.text)


def _same_style(one: TextRun, other: TextRun) -> bool:
    return one.bold == other.bold and one.italic == other.italic


def _piece(previous: Optional[TextRun], run: TextRun, following: Optional[TextRun]) -> Optional[str]:
    """The characters ``run`` contributes, or None when it draws nothing.

    A space drawn on top of the letter after it never took any room on the
    page, so it was not a space: it is where a non-joiner used to be.
    """
    if run.text.strip():
        return run.text
    if _over(run, following):
        return bidi_text.MARK if _between_letters(previous, following) else None
    return run.text


def _over(run: TextRun, other: Optional[TextRun]) -> bool:
    """True when ``run`` is drawn almost entirely on top of ``other``."""
    width = _end(run) - run.x
    if other is None or width <= 0:
        return False
    return _covered(run, other) > DRAWN_OVER * width


def _between_letters(previous: Optional[TextRun], following: Optional[TextRun]) -> bool:
    """True when both neighbours are Arabic, the only script with joiners."""
    before = previous.text.strip()[-1:] if previous is not None else ""
    after = following.text.strip()[:1] if following is not None else ""
    return bool(before) and bool(after) and all(
        unicodedata.bidirectional(char) == "AL" for char in (before, after)
    )


def _spaced(previous: TextRun, current: TextRun, reach: float) -> bool:
    """True when the gap before ``current`` is wide enough to be a space.

    The gap is measured from the furthest point drawn so far, not from the run
    just before it. A vowel mark or a non-joiner takes no room and leaves the
    pen where it was, and measuring from one of those would put a space in the
    middle of a word.
    """
    if previous.text.endswith(" ") or current.text.startswith(" "):
        return False
    return current.x - reach > SPACE_GAP * max(previous.size, current.size)


def baselines(runs: list[TextRun]) -> list[list[TextRun]]:
    """Runs grouped by the baseline they sit on, each group left to right."""
    groups: list[list[TextRun]] = []
    for run in sorted(runs, key=lambda item: (-item.y, item.x)):
        target: Optional[list[TextRun]] = None
        for group in groups:
            size = max(item.size for item in group)
            if abs(group[0].y - run.y) <= BASELINE_TOLERANCE * max(size, run.size, 1.0):
                target = group
                break
        if target is None:
            groups.append([run])
        else:
            target.append(run)
    for group in groups:
        # Runs that share an origin keep the order they were drawn in; which
        # of them comes first is settled later, by what each is drawn over.
        group.sort(key=lambda item: item.x)
    return groups


def cells(runs: list[TextRun]) -> list[list[TextRun]]:
    """One baseline's runs split where a gap is too wide to be a space.

    A space between words is about a quarter of the font size; the space
    between two table cells is several times that. Nothing here decides that
    the line *is* a table row -- only that it has more than one piece, which
    is what :mod:`~pytoolbox.pdf.tables` needs to look for a pattern in.
    """
    groups: list[list[TextRun]] = []
    for run in runs:
        if groups and run.x - _end(groups[-1][-1]) <= CELL_GAP * max(run.size, 1.0):
            groups[-1].append(run)
        else:
            groups.append([run])
    return groups


def to_lines(runs: list[TextRun], page: int = 0, base: Optional[str] = None) -> list[Line]:
    """Group runs sharing a baseline, ordered top to bottom then left to right."""
    lines = [Line(runs=group, page=page) for group in baselines(runs)]
    for line in lines:
        line.base = bidi_text.direction("".join(run.text for run in line.runs), base) or "L"
        line.flow = base or line.base
    lines.sort(key=lambda line: (-line.y, line.x0))
    return lines


def base_direction(pages: list[Page]) -> str:
    """The direction most of the document's letters are written in."""
    sample = "".join(run.text for page in pages for run in page.runs)
    return bidi_text.dominant(sample) or "L"


def _end(run: TextRun) -> float:
    """Where a run stops: measured if the reader could, estimated otherwise."""
    if run.end is not None:
        return run.end
    return run.x + GLYPH_WIDTH * run.size * len(run.text)


def split_columns(runs: list[TextRun], width: float) -> list[list[TextRun]]:
    """Group ``runs`` into columns, in reading order.

    Splitting happens before lines are formed, not after: the two columns of a
    real paper share their baselines, so grouping first would weld "left one"
    and "right one" into a single line and leave no gutter to find.
    """
    split = _gutter(runs, width)
    if split is None:
        return [list(runs)]

    left, right, spanning = [], [], []
    for run in runs:
        if _end(run) <= split:
            left.append(run)
        elif run.x >= split:
            right.append(run)
        else:
            spanning.append(run)

    # A title or figure crossing the gutter belongs above both columns.
    groups = [spanning] if spanning else []
    groups.extend([left, right])
    return groups


def _columns(runs: list[TextRun], width: float, base: str) -> list[list[TextRun]]:
    """Columns in reading order, which on a right-to-left page starts right."""
    groups = split_columns(runs, width)
    if base == "R" and len(groups) > 1:
        # Only the columns swap. A run spanning both still comes first.
        head = groups[:-2]
        head.extend(reversed(groups[-2:]))
        return head
    return groups


def _gutter(runs: list[TextRun], width: float) -> Optional[float]:
    """The x of a vertical whitespace gutter, if the page has a clean one."""
    if len(runs) < 4 or width <= 0:
        return None

    # Only the middle is considered: multi-column layouts are symmetric in
    # practice, and searching every x finds "gutters" inside indented text.
    candidate = width / 2
    left = [run for run in runs if _end(run) <= candidate]
    right = [run for run in runs if run.x >= candidate]
    if not left or not right:
        return None
    # Counted by line, not by run. A table row leaves the middle clear exactly
    # as a two-column page does, but it also puts two or three runs on every
    # baseline, so counting runs lets one table outvote a page of prose.
    groups = baselines(runs)
    clear = sum(
        1
        for group in groups
        if all(_end(run) <= candidate or run.x >= candidate for run in group)
    )
    if clear / len(groups) < MIN_COLUMN_SHARE:
        return None
    if min(run.x for run in right) - max(_end(run) for run in left) < MIN_GUTTER * width:
        return None
    # Both sides must span several lines, or this is a heading beside a logo.
    if len({round(run.y) for run in left}) < 2 or len({round(run.y) for run in right}) < 2:
        return None
    return candidate


def page_lines(
    page: Page,
    single_column: bool = False,
    base: Optional[str] = None,
    runs: Optional[list[TextRun]] = None,
) -> list[Line]:
    """Every line on ``page``, in reading order.

    ``runs`` narrows the page to a subset, which is how the runs a table has
    already claimed are kept out of the running text.
    """
    source = page.runs if runs is None else runs
    if single_column:
        return to_lines(source, page.number, base)
    lines: list[Line] = []
    for column in _columns(source, page.width, base or "L"):
        lines.extend(to_lines(column, page.number, base))
    return lines


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
