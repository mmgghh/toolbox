"""Putting painted glyphs back into reading order."""

import pytest

from pytoolbox.pdf import text

pytest.importorskip("bidi")


def test_a_right_to_left_line_is_put_back_in_reading_order():
    # As painted: the Latin word sits at the left of the line, which is where
    # the bidirectional algorithm put it, and it is read last.
    painted = "BRD ﯼﺪﯿﻠﮐ ﯼﺎﻫﺶﺳﺮﭘ"

    assert text.restore(painted, "R") == "پرسش‌های کلیدی BRD"


def test_shaped_glyphs_become_the_letters_they_stand_for():
    assert text.unshape("ﭘﺎﺳﺦ") == "پاسخ"


def test_unshaping_leaves_everything_outside_the_arabic_blocks_alone():
    # A blanket normalisation would rewrite these too.
    assert text.unshape("½ ﬁt") == "½ ﬁt"


def test_a_non_joiner_is_recovered_from_the_shapes():
    # Sheen keeps its final shape and heh its initial one, which only happens
    # when something unjoinable stood between them.
    assert text.join_marks("ﺶﻫ") == "ﺶ‌ﻫ"


def test_letters_that_simply_do_not_join_gain_no_non_joiner():
    # Alef never joins to what follows it, so its shape says nothing.
    assert text.join_marks("ﺎﺳ") == "ﺎﺳ"


def test_the_stand_in_mark_comes_back_as_a_non_joiner():
    assert text.restore(f"ﻫ{text.MARK}ﺶ", "R") == "ش‌ه"


def test_direction_keeps_the_document_s_own_unless_the_line_has_none_of_it():
    # More Latin letters than Persian ones, and still a Persian line.
    assert text.direction("گزارش quarterly revenue", "R") == "R"
    assert text.direction("quarterly revenue", "R") == "L"
    assert text.direction("گزارش", "L") == "R"


def test_direction_falls_back_when_nothing_is_strong():
    assert text.direction("۱۴۰۵ (۲۵)", "R") == "R"
    assert text.direction("123", None) is None


def test_direction_reads_the_letters_when_no_document_direction_is_known():
    assert text.direction("گزارش") == "R"
    assert text.direction("report") == "L"


def test_a_left_to_right_line_is_left_alone():
    assert text.restore("Quarterly Report", "L") == "Quarterly Report"
