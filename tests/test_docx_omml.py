"""Tests for turning OMML equations into LaTeX."""

from __future__ import annotations

from xml.etree import ElementTree as ET

from pytoolbox.docx.omml import latex_of

NS = (
    'xmlns:m="http://schemas.openxmlformats.org/officeDocument/2006/math" '
    'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
)


def latex(inner: str) -> str:
    """Convert an ``m:oMath`` body written as raw OMML."""
    return latex_of(ET.fromstring(f"<m:oMath {NS}>{inner}</m:oMath>"))


def r(text: str) -> str:
    return f'<m:r><m:t xml:space="preserve">{text}</m:t></m:r>'


def test_a_run_of_maths_text_is_its_text():
    assert latex(r("E = mc")) == "E = mc"


def test_runs_are_joined_in_order():
    assert latex(r("a + ") + r("b")) == "a + b"


def test_a_subscript_becomes_an_underscore():
    assert latex(f"<m:sSub><m:e>{r('T')}</m:e><m:sub>{r('B')}</m:sub></m:sSub>") == "T_{B}"


def test_a_multi_character_base_is_grouped():
    """``ST_{i}`` would hang the subscript off the T alone."""
    assert latex(f"<m:sSub><m:e>{r('ST')}</m:e><m:sub>{r('i')}</m:sub></m:sSub>") == "{ST}_{i}"


def test_a_superscript_becomes_a_caret():
    assert latex(f"<m:sSup><m:e>{r('e')}</m:e><m:sup>{r('x')}</m:sup></m:sSup>") == "e^{x}"


def test_both_scripts_are_kept():
    body = f"<m:sSubSup><m:e>{r('x')}</m:e><m:sub>{r('i')}</m:sub><m:sup>{r('2')}</m:sup></m:sSubSup>"
    assert latex(body) == "x_{i}^{2}"


def test_a_fraction_becomes_frac():
    assert latex(f"<m:f><m:num>{r('a')}</m:num><m:den>{r('b')}</m:den></m:f>") == r"\frac{a}{b}"


def test_a_linear_fraction_keeps_its_slash():
    body = (
        f'<m:f><m:fPr><m:type m:val="lin"/></m:fPr>'
        f"<m:num>{r('a')}</m:num><m:den>{r('b')}</m:den></m:f>"
    )
    assert latex(body) == "a/b"


def test_a_radical_becomes_sqrt():
    body = f"<m:rad><m:radPr><m:degHide m:val=\"1\"/></m:radPr><m:deg/><m:e>{r('x')}</m:e></m:rad>"
    assert latex(body) == r"\sqrt{x}"


def test_a_radical_keeps_an_explicit_degree():
    body = f"<m:rad><m:deg>{r('3')}</m:deg><m:e>{r('x')}</m:e></m:rad>"
    assert latex(body) == r"\sqrt[3]{x}"


def test_delimiters_default_to_parentheses():
    assert latex(f"<m:d><m:e>{r('x')}</m:e></m:d>") == r"\left(x\right)"


def test_delimiters_use_the_declared_characters():
    body = f'<m:d><m:dPr><m:begChr m:val="["/><m:endChr m:val="]"/></m:dPr><m:e>{r("x")}</m:e></m:d>'
    assert latex(body) == r"\left[x\right]"


def test_several_delimited_parts_are_separated():
    body = f"<m:d><m:e>{r('a')}</m:e><m:e>{r('b')}</m:e></m:d>"
    assert latex(body) == r"\left(a,b\right)"


def test_an_n_ary_operator_becomes_its_command():
    body = (
        f'<m:nary><m:naryPr><m:chr m:val="∑"/></m:naryPr>'
        f"<m:sub>{r('i=0')}</m:sub><m:sup>{r('n')}</m:sup><m:e>{r('x')}</m:e></m:nary>"
    )
    assert latex(body) == r"\sum_{i=0}^{n}{x}"


def test_an_n_ary_without_limits_omits_them():
    body = (
        f'<m:nary><m:naryPr><m:chr m:val="∫"/><m:subHide m:val="1"/>'
        f"<m:supHide m:val=\"1\"/></m:naryPr><m:sub/><m:sup/><m:e>{r('f')}</m:e></m:nary>"
    )
    assert latex(body) == r"\int{f}"


def test_a_named_function_keeps_its_name():
    body = f"<m:func><m:fName>{r('sin')}</m:fName><m:e>{r('x')}</m:e></m:func>"
    assert latex(body) == r"\sin{x}"


def test_an_accent_becomes_its_command():
    body = f'<m:acc><m:accPr><m:chr m:val="̃"/></m:accPr><m:e>{r("x")}</m:e></m:acc>'
    assert latex(body) == r"\tilde{x}"


def test_a_bar_above_becomes_overline():
    body = f'<m:bar><m:barPr><m:pos m:val="top"/></m:barPr><m:e>{r("x")}</m:e></m:bar>'
    assert latex(body) == r"\overline{x}"


def test_a_lower_limit_sits_under_its_base():
    body = f"<m:limLow><m:e>{r('max')}</m:e><m:lim>{r('n')}</m:lim></m:limLow>"
    assert latex(body) == r"\underset{n}{max}"


def test_a_matrix_becomes_rows_of_cells():
    row = f"<m:mr><m:e>{r('a')}</m:e><m:e>{r('b')}</m:e></m:mr>"
    assert latex(f"<m:m>{row}{row}</m:m>") == r"\begin{matrix}a & b \\ a & b\end{matrix}"


def test_control_properties_contribute_no_text():
    """``m:ctrlPr`` carries Word run formatting, which LaTeX has no use for."""
    body = '<m:sSub><m:sSubPr><m:ctrlPr><w:rPr><w:b/></w:rPr></m:ctrlPr></m:sSubPr>'
    body += f"<m:e>{r('T')}</m:e><m:sub>{r('B')}</m:sub></m:sSub>"
    assert latex(body) == "T_{B}"


def test_latex_specials_in_text_are_escaped():
    assert latex(r("50% of {x}")) == r"50\% of \{x\}"


def test_an_unknown_construct_still_yields_its_text():
    """Degrading to bare text keeps content that structure cannot survive."""
    assert latex(f"<m:borderBox><m:e>{r('x+1')}</m:e></m:borderBox>") == "x+1"
