"""Putting a line of extracted glyphs back into reading order.

A PDF records the order glyphs are painted in, which for Persian, Arabic or
Hebrew is the order they appear on the page rather than the order they are
read in: the writer already ran the bidirectional algorithm and stored the
result. Three things are undone here, in this order, because each needs what
the previous one leaves behind:

1. **Reading order.** Running the bidirectional algorithm over visual text is
   very nearly its own inverse, so it is what puts the words back. It cannot
   be exactly its own inverse -- ``(۲۰۲۶-۰۸-۱۶)`` and ``(۱۶-۰۸-۲۰۲۶)`` render
   identically in a right-to-left paragraph -- so where the page is ambiguous
   the reader's reading of it wins.
2. **Zero-width non-joiners.** Persian's ``می‌شود`` and ``میشود`` are different
   words, and the joiner between them has no glyph. It left a trace all the
   same: the letter before it kept its unjoined shape, which is the only
   reason it can be put back.
3. **Presentation forms.** Only then are the shaped glyphs (``ﻣ``, ``ﯽ``)
   folded back to the letters someone can search for (``م``, ``ی``).
"""

from __future__ import annotations

import unicodedata
from typing import Optional

#: The zero-width non-joiner, which Persian writes between many word parts.
ZWNJ = "‌"

#: Stand-in for a non-joiner while the line is being reordered. A non-joiner
#: is a boundary-neutral character, which the bidirectional algorithm is
#: required to delete; the Arabic letter mark rides along with the word it sits
#: in instead, and is swapped back once the reordering is done.
MARK = "؜"

#: Where Unicode keeps the shaped forms of Arabic letters.
PRESENTATION = ((0xFB50, 0xFDFF), (0xFE70, 0xFEFF))

#: Bidirectional classes that decide a paragraph's direction on their own.
STRONG_RTL = frozenset({"R", "AL"})
STRONG_LTR = frozenset({"L"})


def _shapes() -> tuple[dict, frozenset, frozenset]:
    """Index the shaped forms: what each is, and how its letters may join.

    Unicode names every form ("... INITIAL FORM"), and a letter has an initial
    or medial form exactly when it may join to the letter after it. That makes
    the character database the joining table, with nothing to keep in step.
    """
    forms: dict[str, tuple[str, str, str]] = {}
    forward: set[str] = set()
    backward: set[str] = set()
    for start, stop in PRESENTATION:
        for code in range(start, stop + 1):
            char = chr(code)
            name = unicodedata.name(char, "")
            shape = name.rsplit(" ", 2)[-2].lower() if name.endswith(" FORM") else ""
            if shape not in ("isolated", "final", "initial", "medial"):
                continue
            base = unicodedata.normalize("NFKC", char)
            if not base:  # pragma: no cover - every form decomposes
                continue
            forms[char] = (shape, base[0], base[-1])
            if shape in ("initial", "medial"):
                forward.add(base[-1])
            if shape in ("final", "medial"):
                backward.add(base[0])
    return forms, frozenset(forward), frozenset(backward)


_FORMS, _JOINS_FORWARD, _JOINS_BACKWARD = _shapes()

#: A shape that leaves the letter's left side open, so nothing follows it.
_OPEN_AFTER = ("isolated", "final")

#: A shape that leaves the letter's right side open, so nothing precedes it.
_OPEN_BEFORE = ("isolated", "initial")


def dominant(text: str) -> Optional[str]:
    """The direction most of ``text``'s letters are written in.

    For a whole document, where counting is the honest measure: a report with
    one English appendix is still a Persian report.
    """
    rtl = sum(1 for char in text if unicodedata.bidirectional(char) in STRONG_RTL)
    ltr = sum(1 for char in text if unicodedata.bidirectional(char) in STRONG_LTR)
    if rtl == ltr:
        return None
    return "R" if rtl > ltr else "L"


def joins_forward(char: str) -> bool:
    """True when ``char`` is a letter that joins onto the one after it."""
    return char in _JOINS_FORWARD


def joins_backward(char: str) -> bool:
    """True when ``char`` is a letter the one before it may join onto."""
    return char in _JOINS_BACKWARD


def direction(text: str, default: Optional[str] = None) -> Optional[str]:
    """``"R"``, ``"L"`` or ``default`` for text with no strong character.

    The standard's "first strong character" rule is no use here: it reads
    logical order, and putting the text into logical order is what the answer
    is needed for.

    So the document's own direction stands unless the line holds nothing of it
    at all. A Persian sentence quoting two English terms has more Latin
    letters in it than Persian ones and is still a Persian sentence; only a
    line with no Persian in it whatsoever really runs the other way.
    """
    rtl = ltr = False
    for char in text:
        category = unicodedata.bidirectional(char)
        rtl = rtl or category in STRONG_RTL
        ltr = ltr or category in STRONG_LTR
    if default == "R":
        return "R" if rtl else ("L" if ltr else default)
    if default == "L":
        return "L" if ltr else ("R" if rtl else default)
    if rtl != ltr:
        return "R" if rtl else "L"
    return default


def _get_display(text: str, base: str) -> str:
    try:
        from bidi.algorithm import get_display
    except ImportError:
        try:
            from bidi import get_display  # type: ignore[no-redef]
        except ImportError:
            # Without the algorithm, a right-to-left line still reads better
            # reversed than left in paint order; anything embedded in it does
            # not, but that is a smaller loss than the whole line being backwards.
            return text[::-1] if base == "R" else text
    return get_display(text, base_dir=base)


def reading_order(text: str, base: str) -> str:
    """``text`` as painted, returned as it is read."""
    if not text.strip():
        return text
    return _get_display(text, base)


def join_marks(text: str) -> str:
    """Restore the zero-width non-joiners the shaping absorbed.

    Two Arabic letters sitting next to each other, both wearing the shape they
    take when *nothing* is beside them, were separated by something invisible.
    Only a non-joiner is invisible, so that is what goes back.
    """
    out: list[str] = []
    for index, char in enumerate(text):
        out.append(char)
        following = text[index + 1] if index + 1 < len(text) else ""
        if _broken(char, following):
            out.append(ZWNJ)
    return "".join(out)


def _broken(char: str, following: str) -> bool:
    """True when these two letters could have joined but were kept apart."""
    left, right = _FORMS.get(char), _FORMS.get(following)
    if left is None or right is None:
        return False
    return (
        left[0] in _OPEN_AFTER
        and left[2] in _JOINS_FORWARD
        and right[0] in _OPEN_BEFORE
        and right[1] in _JOINS_BACKWARD
    )


def unshape(text: str) -> str:
    """Fold shaped glyphs back to the letters they stand for.

    Only the presentation blocks are touched. A blanket normalisation would
    also rewrite unrelated characters -- ``½``, ``ﬁ``, full-width Latin -- which
    is not this function's business.
    """
    if not any(_shaped(char) for char in text):
        return text
    return "".join(
        unicodedata.normalize("NFKC", char) if _shaped(char) else char for char in text
    )


def _shaped(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= stop for start, stop in PRESENTATION)


def settle_marks(text: str) -> str:
    """Turn each stand-in that landed between two letters into a joiner.

    One that did not was never a joiner: a space with no width at the end of a
    line, or a letter closed by the punctuation after it rather than by
    anything invisible. Those are dropped.
    """
    if MARK not in text:
        return text
    out: list[str] = []
    for index, char in enumerate(text):
        if char != MARK:
            out.append(char)
            continue
        before = unshape(out[-1]) if out else ""
        after = unshape(text[index + 1]) if index + 1 < len(text) else ""
        if joins_forward(before) and joins_backward(after):
            out.append(ZWNJ)
    return "".join(out)


def restore(text: str, base: str) -> str:
    """A painted line as written: reading order, joiners and letters."""
    ordered = settle_marks(reading_order(text, base))
    return unshape(join_marks(ordered))
