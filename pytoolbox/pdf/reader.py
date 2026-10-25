"""A PDF as positioned text runs, images, links and an outline.

This is the only module that imports :mod:`pypdf`. Everything downstream works
on the dataclasses below, which is what lets the layout and structure rules be
tested without building a PDF.

Text comes out in *visual* order -- the order the glyphs are painted in, left
to right -- because that is the only order a PDF records. A right-to-left
document has already been through the bidirectional algorithm by the time it
is written, so putting the words back in reading order is
:mod:`~pytoolbox.pdf.text`'s job, not this module's.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import click

#: Below this many characters, a page carrying a big image is a scan.
SCANNED_MAX_CHARS = 20

#: A scan's image covers most of the page.
SCANNED_MIN_COVERAGE = 0.5

#: Proportion of scanned pages at which the whole document counts as scanned.
SCANNED_DOCUMENT_SHARE = 0.8


@dataclass
class TextRun:
    """A stretch of text drawn with one font at one place on the page.

    ``end`` is where the run's last glyph stops, measured from the font's own
    widths. It is optional because a run can be built without a font to
    measure against; :mod:`~pytoolbox.pdf.layout` falls back to estimating the
    extent from the glyph count when it is missing.

    ``codes`` names the glyph behind each character, one per character of
    ``text``. Two glyphs of one font can stand for the same letter -- Arabic
    letters are drawn differently depending on what they join to -- and which
    of them was used is the only record some files keep of that shaping. It is
    empty when the glyphs could not be lined up with the letters.

    ``shapes`` says what the document's glyph numbers reveal about each
    character, one per character of ``text``: ``c`` for a shape that joins
    nothing after it, ``j`` for one that does and ``?`` where the document
    gives no reason to say. :mod:`~pytoolbox.pdf.shaping` fills it in; it is
    empty until then, and for a font that draws each letter one way.
    """

    text: str
    x: float
    y: float
    size: float
    font: str = ""
    bold: bool = False
    italic: bool = False
    end: Optional[float] = None
    codes: str = ""
    shapes: str = ""


@dataclass
class ImageBox:
    """An image and where it sits on the page."""

    name: str
    data: bytes
    x: float
    y: float
    width: float
    height: float


@dataclass
class LinkBox:
    """A link annotation's target and the rectangle it covers."""

    uri: str
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class RuleBox:
    """A painted rectangle, in page coordinates.

    Table cells are the reason these are collected. Geometry alone cannot tell
    a two-column page from a two-column table -- both are text, air, text --
    but a table is drawn, and a page of prose is not.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@dataclass
class OutlineEntry:
    """One bookmark. ``level`` is 1-based."""

    title: str
    level: int
    page: int


@dataclass
class Page:
    number: int
    width: float
    height: float
    runs: list[TextRun] = field(default_factory=list)
    images: list[ImageBox] = field(default_factory=list)
    links: list[LinkBox] = field(default_factory=list)
    rules: list[RuleBox] = field(default_factory=list)

    @property
    def scanned(self) -> bool:
        """No usable text, but a picture covering most of the page."""
        if sum(len(run.text.strip()) for run in self.runs) >= SCANNED_MAX_CHARS:
            return False
        area = self.width * self.height
        if not area:
            return False
        return any(
            (image.width * image.height) / area >= SCANNED_MIN_COVERAGE for image in self.images
        )


@dataclass
class Document:
    pages: list[Page] = field(default_factory=list)
    outline: list[OutlineEntry] = field(default_factory=list)

    @property
    def scanned(self) -> bool:
        """True when the file as a whole has no text worth converting."""
        if not self.pages:
            return False
        share = sum(1 for page in self.pages if page.scanned) / len(self.pages)
        return share >= SCANNED_DOCUMENT_SHARE


def read(
    source: Path,
    *,
    password: Optional[str] = None,
    pages: Optional[list[int]] = None,
    include_images: bool = True,
) -> Document:
    """Read ``source`` into positioned runs.

    ``pages`` selects zero-based page indexes; page numbers stay absolute, so a
    later message about page 3 means page 3 of the file, not of the selection.
    """
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover - the CLI reports this
        raise click.ClickException(
            f"pypdf2md needs the pdf2md extra: pip install 'pytoolbox[pdf2md]' ({exc})"
        ) from exc

    try:
        pdf = pypdf.PdfReader(str(source))
        page_count = len(pdf.pages)
    except Exception as exc:
        raise click.ClickException(f"{source} is not a PDF file ({exc}).") from exc

    if pdf.is_encrypted:
        try:
            opened = pdf.decrypt(password or "")
        except Exception:
            opened = 0
        if not opened:
            hint = "wrong password" if password else "try --password"
            raise click.ClickException(f"{source} is password-protected ({hint}).")

    wanted = range(page_count) if pages is None else pages
    document = Document(outline=_outline(pdf))
    for index in wanted:
        if 0 <= index < page_count:
            document.pages.append(_page(pdf.pages[index], index, include_images))
    return document


def _page(source: Any, number: int, include_images: bool) -> Page:
    box = source.mediabox
    page = Page(
        number=number,
        width=float(box.width),
        height=float(box.height),
        links=_links(source),
    )

    try:
        page.runs, placements, page.rules = _content(source)
    except Exception:
        # The walker reaches into pypdf's layout-mode internals, which are the
        # only place it exposes a real position per show-text operator. If a
        # future pypdf moves them, or a page defeats the walker, fall back to
        # the supported visitor: its positions are coarse -- every run on a
        # line reports the line's own origin -- but its text is still right.
        try:
            page.runs, placements = _visited(source)
        except Exception as exc:  # pragma: no cover - a damaged page stops that page only
            raise click.ClickException(f"page {number + 1} could not be read ({exc}).") from exc
        page.rules = []

    page.images = _images(source, placements, include_images)
    return page


#: A placed XObject: its name and the matrix that placed it.
Placement = tuple[str, list[float]]


#: Operators that put a path on the page, as opposed to clipping or dropping it.
PAINTING = (b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*")


def _content(source: Any) -> tuple[list[TextRun], list[Placement], list[RuleBox]]:
    """Walk the page's content stream, measuring every show-text operator.

    pypdf's own ``extract_text`` cannot answer "where is this word": it never
    advances the text matrix for the glyphs it draws, so every run on a line
    reports the line's origin. Without a real x per run there are no columns,
    no table cells and no way to tell a space from a kern, which is most of
    what this package needs, so the operators are walked here instead.
    """
    from pypdf._text_extraction._layout_mode._fixed_width_page import resolve_font
    from pypdf._text_extraction._layout_mode._text_state_manager import TextStateManager
    from pypdf.generic import ContentStream

    runs: list[TextRun] = []
    placements: list[Placement] = []
    rules: list[RuleBox] = []
    pending: list[RuleBox] = []
    repairs: dict[int, dict[int, str]] = {}
    state = TextStateManager()

    def rectangle(operands: Any) -> None:
        x, y, width, height = (float(value) for value in operands[:4])
        matrix = state.effective_transform
        corners = [
            _apply(matrix, x, y),
            _apply(matrix, x + width, y),
            _apply(matrix, x, y + height),
            _apply(matrix, x + width, y + height),
        ]
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        pending.append(RuleBox(min(xs), min(ys), max(xs), max(ys)))

    def show(raw: Any) -> None:
        if state.font is None:
            return
        shown = state.text_state_params(raw)
        movement = _movement(shown)
        if shown.text:
            font = str(getattr(shown.font, "name", "") or "")
            if id(shown.font) not in repairs:
                repairs[id(shown.font)] = _digits(shown.font)
            drawn, behind = _without_roomless(shown, *_letters(shown))
            if drawn:
                runs.append(
                    TextRun(
                        text=drawn.translate(repairs[id(shown.font)]),
                        codes=behind,
                        x=shown.tx,
                        y=shown.ty,
                        size=abs(shown.font_height) or abs(float(shown.font_size)),
                        font=font,
                        bold=_is_bold(font) or _weighted(shown.font),
                        italic=_is_italic(font) or _sloped(shown.font),
                        # A negative advance is a width the font did not supply,
                        # not a glyph drawn backwards.
                        end=max(_end_of(shown, movement), shown.tx),
                    )
                )
        # The whole point of the walk: step past what was just drawn, so the
        # next operator's position is its own rather than the line's.
        state.add_trm(movement)

    def walk(ops: Any, fonts: dict, resources: Any, end: Optional[bytes], depth: int) -> None:
        for operands, operator in ops:
            if operator == end:
                if operator == b"Q":
                    state.remove_q()
                elif operator == b"ET":
                    state.reset_tm()
                return
            if operator == b"q":
                state.add_q()
                spacing = _spacing(state)
                walk(ops, fonts, resources, b"Q", depth)
                _respace(state, spacing)
            elif operator == b"BT":
                walk(ops, fonts, resources, b"ET", depth)
            elif operator == b"cm":
                state.add_cm(*operands)
            elif operator == b"Tf":
                try:
                    state.set_font(resolve_font(fonts, operands[0]), float(operands[1]))
                except Exception:  # pragma: no cover - an unusable font shows no text
                    continue
            elif operator in (b"Td", b"TD", b"Tm", b"T*"):
                state.reset_trm()
                if operator == b"Tm":
                    state.reset_tm()
                elif operator == b"TD":
                    state.set_state_param(b"TL", -float(operands[1]))
                elif operator == b"T*":
                    operands = [0, -state.TL]
                state.add_tm(list(operands))
            elif operator == b"Tj":
                show(operands[0])
            elif operator == b"'":
                state.reset_trm()
                state.add_tm([0, -state.TL])
                show(operands[0])
            elif operator == b'"':
                state.reset_trm()
                state.set_state_param(b"Tw", operands[0])
                state.set_state_param(b"Tc", operands[1])
                state.add_tm([0, -state.TL])
                show(operands[2])
            elif operator == b"TJ":
                for item in operands[0]:
                    if isinstance(item, bytes):
                        show(item)
                    else:
                        # A TJ number moves the pen back by thousandths of an em.
                        step = -float(item) / 1000.0 * state.font_size * (state.Tz / 100.0)
                        state.add_trm([1.0, 0.0, 0.0, 1.0, step, 0.0])
            elif operator == b"Do" and operands:
                _do(operands[0], resources, fonts, placements, walk, state, depth)
            elif operator == b"re" and len(operands) >= 4:
                rectangle(operands)
            elif operator in PAINTING:
                rules.extend(pending)
                pending.clear()
            elif operator in (b"n", b"m", b"l", b"c", b"v", b"y", b"h"):
                # A path that is only clipped, or one built from segments
                # rather than a rectangle, is not a cell.
                if operator == b"n":
                    pending.clear()
            else:
                state.set_state_param(operator, operands)

    fonts = source._layout_mode_fonts()
    stream = ContentStream(source["/Contents"].get_object(), source.pdf, "bytes")
    walk(iter(stream.operations), fonts, source.get("/Resources"), None, 0)
    return runs, placements, rules


#: Bidirectional classes whose letters are painted right to left.
_ARABIC = ("AL", "R")

#: The zero of each digit set a font might actually be drawing: Persian's
#: extended Arabic-Indic digits, then Arabic-Indic.
EASTERN_ZEROS = ("۰", "٠")


def _digits(font: Any) -> dict[int, str]:
    """A repair table for a font whose mapping disagrees with itself.

    Digit glyphs come as a block of ten, and a font draws one set of them. So
    a font claiming eight of its ten are Persian and the other two are ASCII
    is not describing a font that mixes the two -- no such font exists -- it is
    describing two glyphs whose entries were filled in wrongly. Reading them
    back at face value turns ``۱۴۰۵`` into ``1۴0۵``.
    """
    mapping = getattr(font, "character_map", None) or {}
    western = 0
    eastern = dict.fromkeys(EASTERN_ZEROS, 0)
    for value in mapping.values():
        if len(value) != 1:
            continue
        if "0" <= value <= "9":
            western += 1
        else:
            for zero in EASTERN_ZEROS:
                if zero <= value <= chr(ord(zero) + 9):
                    eastern[zero] += 1
    if not western:
        return {}
    zero, drawn = max(eastern.items(), key=lambda item: item[1])
    # A font really holding both sets keeps both; only a clear majority for one
    # set says the odd ones out are mistakes.
    if drawn <= western:
        return {}
    return {ord("0") + step: chr(ord(zero) + step) for step in range(10)}


def _letters(shown: Any) -> tuple[str, str]:
    """The run's text, and the glyph behind each of its characters.

    Multi-letter glyphs are turned to face the same way as everything else on
    the way past. One glyph can stand for several letters -- ``لا`` is drawn as
    a single shape -- and the file says which letters those are, in *reading*
    order. Everything around them is in paint order, so a ligature dropped in
    as-is is the one piece of the line already the right way round, and comes
    out backwards once the line is reversed.

    Only Arabic ligatures are turned. A Latin one is in a line that will not
    be reversed at all, so it is already right.
    """
    codes = getattr(shown, "_decoded_value", "")
    mapping = getattr(shown.font, "character_map", None)
    if not codes or not mapping:
        return shown.text, ""

    letters: list[str] = []
    behind: list[str] = []
    for code in codes:
        drawn = mapping.get(code, code)
        if len(drawn) > 1 and all(unicodedata.bidirectional(one) in _ARABIC for one in drawn):
            drawn = drawn[::-1]
        letters.append(drawn)
        behind.append(code * len(drawn))
    return "".join(letters), "".join(behind)


def _movement(shown: Any) -> list[float]:
    """The matrix the pen moves by after ``shown``, spacing included.

    pypdf adds the character spacing (``Tc``) once per string. The PDF model
    adds it after *every* glyph, and Word uses it to squeeze and stretch whole
    lines -- sometimes by nearly a glyph's width, which is how a run of two
    spaces ends up taking no room at all. Counting it once puts the end of a
    run, and so every gap, overlap and table cell measured from it, in the
    wrong place.
    """
    font = shown.font
    word = shown._decoded_value if isinstance(shown.value, bytes) else shown.value
    width = 0.0
    spaces = 0
    for char in word:
        if char == font.space_char:
            width += font.space_width
            spaces += 1
        else:
            width += font.get_text_width(char)
    advance = (shown.font_size * width / 1000.0 + len(word) * shown.Tc + spaces * shown.Tw) * (
        shown.Tz / 100.0
    )
    return [1.0, 0.0, 0.0, 1.0, advance, 0.0]


#: A run of spaces that moves the pen less than this share of one space's own
#: width has been squeezed out of existence by the writer.
ROOMLESS_SHARE = 0.3

#: A space moving the pen less than this share of its width has been squeezed.
SQUEEZED_SHARE = 0.8


def _without_roomless(shown: Any, drawn: str, behind: str) -> tuple[str, str]:
    """Take out the spaces the writer left no room for.

    Word cancels spaces' widths with a negative character spacing. Sometimes
    that leaves a space glyph taking no room at all, with the pen moved on to
    the next word by hand: it is not a gap -- the real ones are the jumps
    between runs -- and keeping it splits a word where a letter changes shape.
    Sometimes a word gap is set as two spaces squeezed until they add up to
    about one. So spaces are judged in groups, by what the group takes
    together: none is nothing, a squeezed few are one, anything else stands.

    A text state that cannot be lined up with the letters is left as it is.
    """
    decoded = shown._decoded_value if isinstance(shown.value, bytes) else shown.value
    if not any(char.isspace() for char in drawn) or len(decoded) != len(drawn):
        return drawn, behind

    font = shown.font
    scale = shown.Tz / 100.0
    space = shown.font_size * font.space_width / 1000.0 * scale
    if space <= 0:
        return drawn, behind

    def advance(code: str) -> float:
        width = shown.font_size * font.get_text_width(code) / 1000.0
        if code == font.space_char:
            width = shown.font_size * font.space_width / 1000.0 + shown.Tw
        return (width + shown.Tc) * scale

    keep = [True] * len(drawn)
    index = 0
    while index < len(drawn):
        if not drawn[index].isspace():
            index += 1
            continue
        stop = index
        while stop < len(drawn) and drawn[stop].isspace():
            stop += 1
        moves = [advance(code) for code in decoded[index:stop]]
        if sum(moves) < ROOMLESS_SHARE * space:
            # Squeezed to nothing inside a word it is noise. Set on its own it
            # is all there is to say that two runs are two words, because a
            # justified line squeezes its gaps below what geometry would call
            # one; the writer's own space is kept, one of it, with no width.
            alone = stop - index == len(drawn)
            keep[index:stop] = [alone] + [False] * (stop - index - 1)
        elif stop - index > 1 and any(move < SQUEEZED_SHARE * space for move in moves):
            keep[index + 1 : stop] = [False] * (stop - index - 1)
        index = stop

    if all(keep):
        return drawn, behind
    aligned = len(behind) == len(drawn)
    return (
        "".join(char for char, kept in zip(drawn, keep) if kept),
        "".join(code for code, kept in zip(behind, keep) if kept) if aligned else behind,
    )


def _end_of(shown: Any, movement: list[float]) -> float:
    """Where the last glyph just shown stops, on the page.

    That is not where the pen goes next. The character spacing after the last
    glyph is air, not ink -- and when Word squeezes a line with a negative
    spacing it is nearly the glyph's whole width, so the next word starts
    right where this one's last letter does, not a letter's width past it.
    """
    from pypdf._text_extraction import mult

    ink = list(movement)
    if shown.text.strip():
        # A string of spaces has no ink, so there its end is the pen's.
        ink[4] -= shown.Tc * shown.Tz / 100.0
    return float(mult(ink, shown.transform)[4])


def _apply(matrix: Any, x: float, y: float) -> tuple[float, float]:
    """A point through a PDF transformation matrix."""
    a, b, c, d, e, f = (float(value) for value in matrix[:6])
    return a * x + c * y + e, b * x + d * y + f


#: Text state parameters that "q" saves and "Q" puts back, like any other part
#: of the graphics state. pypdf's own stack keeps the fonts and the matrices
#: but not these, and one line of justified text setting a character spacing of
#: -2.9 points would otherwise go on squeezing every line after it -- which
#: leaves each run's measured width short, and the page in the wrong order.
SPACING = ("Tc", "Tw", "Tz", "TL", "Ts")


def _spacing(state: Any) -> tuple:
    return tuple(getattr(state, name) for name in SPACING)


def _respace(state: Any, saved: tuple) -> None:
    for name, value in zip(SPACING, saved):
        setattr(state, name, value)


#: Form XObjects may nest; stop well before a cyclic file exhausts the stack.
MAX_FORM_DEPTH = 8


def _do(name: Any, resources: Any, fonts: dict, placements: list, walk: Any, state: Any, depth: int) -> None:
    """Draw an XObject: record an image, or walk into a form's own operators."""
    from pypdf._font import Font
    from pypdf.generic import ContentStream

    matrix = list(state.effective_transform)
    try:
        target = resources["/XObject"][name].get_object() if resources else None
    except Exception:
        target = None
    if target is None or target.get("/Subtype") != "/Form":
        placements.append((str(name), [float(value) for value in matrix]))
        return
    if depth >= MAX_FORM_DEPTH:  # pragma: no cover - only a pathological file nests this deep
        return

    own = target.get("/Resources")
    inner = dict(fonts)
    try:
        for key, value in (own or {}).get("/Font", {}).items():
            inner[key] = Font.from_font_resource(value.get_object())
    except Exception:  # pragma: no cover - a broken font must not lose the text
        pass

    state.add_q()
    spacing = _spacing(state)
    form_matrix = target.get("/Matrix")
    if form_matrix:
        state.add_cm(*[float(value) for value in form_matrix])
    walk(iter(ContentStream(target, target.indirect_reference.pdf).operations),
         inner, own or resources, None, depth + 1)
    state.remove_q()
    _respace(state, spacing)


def _visited(source: Any) -> tuple[list[TextRun], list[Placement]]:
    """Positions from pypdf's supported visitor, used when the walk fails."""
    runs: list[TextRun] = []
    placements: list[Placement] = []

    def visit_text(text: str, cm: Any, tm: Any, font_dict: Any, font_size: Any) -> None:
        if not text or not text.strip():
            return
        font = str(font_dict.get("/BaseFont", "") or "") if font_dict else ""
        # tm holds the text's own position; cm the enclosing transformation.
        x_scale = float(cm[0]) or 1.0
        y_scale = float(cm[3]) or 1.0
        runs.append(
            TextRun(
                text=text,
                x=float(tm[4]) * x_scale + float(cm[4]),
                y=float(tm[5]) * y_scale + float(cm[5]),
                size=float(font_size or 0) * abs(float(tm[3]) or 1.0) * abs(y_scale),
                font=font,
                bold=_is_bold(font),
                italic=_is_italic(font),
            )
        )

    def visit_operand(operator: Any, operands: Any, cm: Any, tm: Any) -> None:
        if operator == b"Do" and operands:
            placements.append((str(operands[0]), [float(value) for value in cm]))

    source.extract_text(visitor_text=visit_text, visitor_operand_before=visit_operand)
    return runs, placements


def _images(
    source: Any, placements: list[tuple[str, list[float]]], include_data: bool
) -> list[ImageBox]:
    """Every placed image, with its bytes unless they were not asked for.

    Placement is kept even when the bytes are not, because an image covering
    the page is how a scan is recognised.
    """
    embedded: dict[str, Any] = {}
    if include_data:
        try:
            embedded = {_stem(item.name): item for item in source.images}
        except Exception:  # pragma: no cover - a damaged image must not stop the text
            embedded = {}

    found: list[ImageBox] = []
    for name, cm in placements:
        item = embedded.get(_stem(name))
        found.append(
            ImageBox(
                name=item.name if item is not None else f"{_stem(name)}.png",
                data=item.data if item is not None else b"",
                x=cm[4],
                y=cm[5],
                width=abs(cm[0]),
                height=abs(cm[3]),
            )
        )
    return found


def _stem(name: str) -> str:
    """``/I1`` and ``I1.png`` both name the same XObject."""
    return name.lstrip("/").rsplit(".", 1)[0]


def _links(source: Any) -> list[LinkBox]:
    found: list[LinkBox] = []
    try:
        annotations = source.get("/Annots") or []
    except Exception:  # pragma: no cover
        return found
    for annotation in annotations:
        try:
            obj = annotation.get_object()
            if obj.get("/Subtype") != "/Link":
                continue
            action = obj.get("/A") or {}
            uri = action.get("/URI")
            rect = obj.get("/Rect")
            if not uri or not rect:
                continue
            x0, y0, x1, y1 = (float(value) for value in rect)
            found.append(LinkBox(str(uri), min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
        except Exception:  # pragma: no cover - one bad annotation is not fatal
            continue
    return found


def _outline(pdf: Any) -> list[OutlineEntry]:
    """Bookmarks flattened to (title, level, page), in document order."""
    entries: list[OutlineEntry] = []

    def walk(items: Any, level: int) -> None:
        for item in items:
            if isinstance(item, list):
                # pypdf nests a child list directly after its parent entry.
                walk(item, level + 1)
                continue
            try:
                title = str(item.title or "").strip()
                page = pdf.get_destination_page_number(item)
            except Exception:  # pragma: no cover - a broken bookmark is skipped
                continue
            if title:
                entries.append(OutlineEntry(title=title, level=level, page=page))

    try:
        walk(pdf.outline, 1)
    except Exception:  # pragma: no cover
        return []
    return entries


def _is_bold(font: str) -> bool:
    lowered = font.lower()
    return "bold" in lowered or "black" in lowered or "heavy" in lowered


def _is_italic(font: str) -> bool:
    lowered = font.lower()
    return "italic" in lowered or "oblique" in lowered


#: A subset embeds under a name like "ABCDEF+Vazir", which says nothing about
#: weight. The descriptor does, so it is consulted when the name is silent.
BOLD_WEIGHT = 600

#: /Flags bit 19 (1-based) is ForceBold; bit 7 is Italic.
FLAG_ITALIC = 1 << 6
FLAG_BOLD = 1 << 18


def _weighted(font: Any) -> bool:
    descriptor = getattr(font, "font_descriptor", None)
    if descriptor is None:
        return False
    try:
        if float(getattr(descriptor, "weight", 0) or 0) >= BOLD_WEIGHT:
            return True
    except (TypeError, ValueError):
        if _is_bold(str(getattr(descriptor, "weight", ""))):
            return True
    return bool(int(getattr(descriptor, "flags", 0) or 0) & FLAG_BOLD)


def _sloped(font: Any) -> bool:
    descriptor = getattr(font, "font_descriptor", None)
    if descriptor is None:
        return False
    if float(getattr(descriptor, "italic_angle", 0) or 0):
        return True
    return bool(int(getattr(descriptor, "flags", 0) or 0) & FLAG_ITALIC)
