"""Recovering non-joiners from a font's glyph numbers."""

import pytest

from pytoolbox.pdf import shaping
from pytoolbox.pdf import text as bidi_text
from pytoolbox.pdf.layout import to_lines
from pytoolbox.pdf.reader import Document, Page, TextRun

pytest.importorskip("bidi")

#: One glyph per letter, except heh, which the font draws two ways: "A" joined
#: to what follows it and "B" not joined. That is the whole point.
GLYPHS = {"ب": "b", "ه": "A", "ا": "i", "ر": "r", "م": "m"}


def painted(word, y, heh="A"):
    """A line holding ``word``, painted the way a PDF stores it: reversed."""
    visual = word[::-1]
    codes = "".join("B" if letter == "ه" and heh == "B" else GLYPHS[letter] for letter in visual)
    return TextRun(text=visual, x=72.0, y=y, size=10.0, end=72.0 + 5 * len(visual),
                   font="Nazanin", codes=codes)


def document(*runs):
    return Document(pages=[Page(number=0, width=612.0, height=792.0, runs=list(runs))])


def read(run):
    return to_lines([run], 0, "R")[0].text


def test_a_closed_shape_where_a_join_was_possible_is_a_non_joiner():
    # "راه" ends in heh, so its closed shape is the ordinary one there. The
    # same shape inside "بهم" can only have been closed by a non-joiner.
    joined = painted("بهار", 700)
    ends = [painted("راه", 700 - n * 12, heh="B") for n in range(1, 4)]
    inside = painted("بهم", 640, heh="B")

    shaping.restore_joiners(document(joined, *ends, inside))

    assert read(inside) == "به‌م"
    assert read(joined) == "بهار"
    assert read(ends[0]) == "راه"


def test_a_letter_the_font_draws_one_way_is_left_alone():
    # With a single glyph for heh there is nothing to read, and reading it
    # anyway would put a non-joiner inside every second word.
    ends = [painted("راه", 700 - n * 12) for n in range(3)]
    inside = painted("بهم", 640)

    shaping.restore_joiners(document(*ends, inside))

    assert read(inside) == "بهم"


def test_a_shape_used_mostly_where_it_joins_is_not_a_closed_one():
    # One stray reading must not condemn every other use of a joined shape.
    joined = [painted("بهار", 700 - n * 12) for n in range(6)]
    stray = painted("راه", 620)
    ends = [painted("راه", 600 - n * 12, heh="B") for n in range(3)]

    shaping.restore_joiners(document(*joined, stray, *ends))

    assert read(joined[0]) == "بهار"


def test_a_document_with_nothing_to_read_is_untouched():
    assert not shaping._shaped(document(painted("بهار", 700)))


def test_a_marker_that_lands_outside_a_word_is_dropped():
    assert bidi_text.settle_marks(f"راه{bidi_text.MARK} است") == "راه است"
    assert bidi_text.settle_marks(f"به{bidi_text.MARK}م") == "به‌م"
