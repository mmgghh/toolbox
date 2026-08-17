"""Recovering the joiners a plain-letter font leaves out.

Persian writes a zero-width non-joiner between many word parts: ``به‌موقع``
and ``بهموقع`` are different words, and the difference is invisible. The
:mod:`~pytoolbox.pdf.text` rules get it back from the *shape* a letter is
drawn in -- a letter before a non-joiner keeps the shape it takes at the end
of a word -- but only when the file names the shapes, which is what a font
using Arabic presentation forms does.

Plenty of files do not. A document produced by a word processor draws the same
four shapes as four different glyphs and maps all four back to one plain
letter, so the shaping never reaches the text at all. The glyph *numbers* still
differ, though, and this module works out what they mean:

* A glyph is a "closed" shape -- one that joins nothing after it -- if most of
  its uses are where nothing *could* follow it: at the end of a word, or before
  a letter that takes no join from behind. The two kinds of glyph are not close
  to each other on this measure. In the document this was written against, the
  joined shapes are used that way 1% to 5% of the time and the closed ones 66%
  to 99%, so where the line falls hardly matters.
* A closed glyph used anywhere else was closed by something invisible. That
  is the non-joiner, and it goes back.

Only a letter the font draws with more than one glyph is considered. A font
with a single glyph per letter records nothing to read, and guessing from one
glyph would put a non-joiner inside every second word.
"""

from __future__ import annotations

from typing import Optional

from pytoolbox.pdf import text as bidi_text
from pytoolbox.pdf.layout import baselines
from pytoolbox.pdf.reader import Document, TextRun

#: A gap wider than this fraction of the font size separates two words, so the
#: letters either side of it were never joined whatever their shapes.
APART = 0.25

#: A glyph seen fewer times than this is left alone. The reading is a majority
#: one, and a majority of two is not a reading.
MIN_USES = 3

#: One glyph, named by the font it belongs to.
Glyph = tuple[str, str]

#: One drawn character: where it came from, and what it is.
Place = tuple[TextRun, int, str, Optional[Glyph]]


def restore_joiners(document: Document) -> None:
    """Put back the non-joiners a document's glyph numbers imply.

    The runs are edited in place, marking each non-joiner rather than writing
    it: the text is still in paint order here, and the bidirectional algorithm
    is required to delete a real non-joiner before it can be reordered.
    """
    if not _shaped(document):
        return
    lines = [line for page in document.pages for line in _lines(page.runs)]
    closed = _closed_shapes(lines)
    if not closed:
        return
    _mark(lines, closed)


def _shaped(document: Document) -> bool:
    """True when some font draws one letter with more than one glyph.

    Nothing below can say anything about a document where it does not, and
    finding out costs one pass over the runs rather than the sorting into
    lines that the rest of this needs.
    """
    drawn: dict[tuple[str, str], set[str]] = {}
    for page in document.pages:
        for run in page.runs:
            if len(run.codes) != len(run.text):
                continue
            for char, code in zip(run.text, run.codes):
                if not bidi_text.joins_forward(char):
                    continue
                shapes = drawn.setdefault((run.font, char), set())
                shapes.add(code)
                if len(shapes) > 1:
                    return True
    return False


def _lines(runs: list[TextRun]) -> list[list[Place]]:
    """Every baseline as the characters drawn along it, left to right."""
    lines: list[list[Place]] = []
    for group in baselines(runs):
        places: list[Place] = []
        previous: Optional[TextRun] = None
        for run in group:
            if previous is not None and _apart(previous, run):
                places.append((run, -1, " ", None))
            aligned = len(run.codes) == len(run.text)
            for offset, char in enumerate(run.text):
                glyph = (run.font, run.codes[offset]) if aligned else None
                places.append((run, offset, char, glyph))
            previous = run
        if places:
            lines.append(places)
    return lines


def _apart(previous: TextRun, run: TextRun) -> bool:
    end = previous.end if previous.end is not None else previous.x
    return run.x - end > APART * max(previous.size, run.size, 1.0)


def _closed_shapes(lines: list[list[Place]]) -> set[Glyph]:
    """The glyphs that are a shape joining nothing after them."""
    drawn: dict[tuple[str, str], set[str]] = {}
    letters: dict[Glyph, str] = {}
    uses: dict[Glyph, list[int]] = {}
    for places in lines:
        for index, (_, _, char, glyph) in enumerate(places):
            if glyph is None or not bidi_text.joins_forward(char):
                continue
            letters[glyph] = char
            drawn.setdefault((glyph[0], char), set()).add(glyph[1])
            # Paint order runs the other way, so the letter this one would
            # join onto is the character *before* it.
            following = places[index - 1][2] if index else ""
            tally = uses.setdefault(glyph, [0, 0])
            tally[bool(bidi_text.joins_backward(following))] += 1
    return {
        glyph
        for glyph, (closing, joining) in uses.items()
        # A letter the font draws one way tells us nothing: its single glyph
        # ends words like any other, and would condemn every other use of it.
        if closing > joining
        and closing + joining >= MIN_USES
        and len(drawn[(glyph[0], letters[glyph])]) > 1
    }


def _mark(lines: list[list[Place]], closed: set[Glyph]) -> None:
    """Write the marker in front of every letter closed by nothing visible."""
    edits: dict[int, tuple[TextRun, list[int]]] = {}
    for places in lines:
        for index, (run, offset, _char, glyph) in enumerate(places):
            if offset < 0 or glyph not in closed:
                continue
            following = places[index - 1][2] if index else ""
            if not bidi_text.joins_backward(following):
                continue  # closed by the letter after it, as it should be
            edits.setdefault(id(run), (run, []))[1].append(offset)

    for run, offsets in edits.values():
        # Late offsets first, so the earlier ones still point where they did.
        for offset in sorted(set(offsets), reverse=True):
            run.text = run.text[:offset] + bidi_text.MARK + run.text[offset:]
            if len(run.codes) >= offset:
                run.codes = run.codes[:offset] + "\x00" + run.codes[offset:]
