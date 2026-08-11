"""The Markdown primitives both writers share."""

from pytoolbox.core.markdown import emphasis, escape


def test_escape_neutralises_markdown_syntax():
    assert escape("a *b* [c]") == r"a \*b\* \[c\]"


def test_emphasis_plain_text_is_untouched():
    assert emphasis("hello") == "hello"


def test_emphasis_escapes_the_text_it_wraps():
    assert emphasis("a * b", bold=True) == r"**a \* b**"


def test_emphasis_combines_bold_and_italic():
    assert emphasis("hi", bold=True, italic=True) == "***hi***"


def test_emphasis_applies_strikethrough_inside_the_other_markers():
    assert emphasis("gone", bold=True, strike=True) == "**~~gone~~**"


def test_emphasis_code_wins_over_bold():
    # Backticks are literal inside code, so emphasis markers would be printed.
    assert emphasis("x = 1", bold=True, code=True) == "`x = 1`"


def test_emphasis_does_not_escape_a_code_span():
    # Backticks already make the content literal; escaping would show the slashes.
    assert emphasis("a * b", code=True) == "`a * b`"


def test_emphasis_link_wraps_the_styled_text():
    assert emphasis("spec", bold=True, link="https://e.com") == "[**spec**](https://e.com)"


def test_emphasis_keeps_surrounding_spaces_outside_the_markers():
    # "** bold **" does not render as bold in any Markdown flavour.
    assert emphasis(" hi ", bold=True) == " **hi** "


def test_emphasis_of_blank_text_adds_no_markers():
    assert emphasis("   ", bold=True) == "   "


def test_emphasis_of_empty_text_is_empty():
    assert emphasis("", bold=True) == ""


def test_a_link_containing_parentheses_cannot_break_out():
    # Bare, this would end the link target early and spill markup into the page.
    out = emphasis("wiki", link="https://e.com/A_(disambiguation)")

    assert out == "[wiki](<https://e.com/A_(disambiguation)>)"


def test_a_link_containing_angle_brackets_is_encoded():
    out = emphasis("x", link="https://e.com/<script>")

    assert out == "[x](<https://e.com/%3Cscript%3E>)"
