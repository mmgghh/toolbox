"""OMML equations as LaTeX.

Word stores equations in its own Office MathML dialect. Markdown has no such
notion, but ``$...$`` holding LaTeX is what every renderer that does maths
expects, so that is the target here.

Only the constructs Word writes for everyday formulae are translated. Anything
else falls back to the text inside it, which is a poor equation but never a
missing one -- and a dropped formula is the failure that matters.
"""

from __future__ import annotations

from typing import Optional
from xml.etree import ElementTree as ET

from pytoolbox.docx.package import NS, attr, qn

#: Characters that mean something else to LaTeX and must be spelled out.
_ESCAPES = {
    "\\": r"\backslash ",
    "{": r"\{",
    "}": r"\}",
    "%": r"\%",
    "$": r"\$",
    "&": r"\&",
    "#": r"\#",
    "_": r"\_",
    "^": r"\^{}",
    "~": r"\~{}",
}

#: The n-ary operators Word offers, by the character it stores.
_NARY = {
    "∑": r"\sum",
    "∏": r"\prod",
    "∐": r"\coprod",
    "∫": r"\int",
    "∬": r"\iint",
    "∭": r"\iiint",
    "∮": r"\oint",
    "⋃": r"\bigcup",
    "⋂": r"\bigcap",
    "⋁": r"\bigvee",
    "⋀": r"\bigwedge",
}

#: Combining marks Word uses for accents, by the command that draws them.
_ACCENTS = {
    "̂": r"\hat",
    "̃": r"\tilde",
    "̄": r"\bar",
    "̅": r"\overline",
    "̆": r"\breve",
    "̇": r"\dot",
    "̈": r"\ddot",
    "̌": r"\check",
    "̀": r"\grave",
    "́": r"\acute",
    "⃗": r"\vec",
}

#: Brackets whose LaTeX spelling differs from the character itself. An empty
#: delimiter is Word's "no bracket this side", which LaTeX writes as a dot.
_DELIMITERS = {
    "": ".",
    "{": r"\{",
    "}": r"\}",
    "⟨": r"\langle",
    "⟩": r"\rangle",
    "‖": r"\|",
    "⌈": r"\lceil",
    "⌉": r"\rceil",
    "⌊": r"\lfloor",
    "⌋": r"\rfloor",
}

#: Function names LaTeX sets upright with a command of their own.
_FUNCTIONS = {
    "sin", "cos", "tan", "cot", "sec", "csc",
    "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "coth",
    "log", "ln", "exp", "lim", "max", "min", "det", "gcd", "dim", "ker",
    "sup", "inf", "deg", "arg",
}

_MATH_NS = NS["m"]


def latex_of(element: ET.Element) -> str:
    """Convert one ``m:oMath`` (or any node inside one) to LaTeX."""
    return _children(element)


def _children(parent: ET.Element) -> str:
    return "".join(_node(child) for child in parent)


def _node(element: ET.Element) -> str:
    tag = element.tag
    # Every m:...Pr element holds formatting, never content. Skipping them as a
    # family keeps one rule where there would otherwise be a dozen.
    if tag.startswith(f"{{{_MATH_NS}}}") and tag.endswith("Pr"):
        return ""
    handler = _HANDLERS.get(tag)
    if handler is not None:
        return handler(element)
    if tag == qn("m:t"):
        return _escape(element.text or "")
    return _children(element)


def _escape(text: str) -> str:
    return "".join(_ESCAPES.get(char, char) for char in text)


def _group(latex: str) -> str:
    """Brace anything that is not a single symbol, so scripts bind to all of it."""
    return latex if len(latex) == 1 else f"{{{latex}}}"


def _part(element: ET.Element, tag: str) -> str:
    """LaTeX for one named child, empty when Word left it out."""
    child = element.find(qn(tag))
    return _children(child) if child is not None else ""


def _property(element: ET.Element, group: str, name: str) -> Optional[str]:
    """Read ``m:val`` off a property such as ``m:naryPr/m:chr``."""
    props = element.find(qn(group))
    if props is None:
        return None
    child = props.find(qn(name))
    return attr(child, "m:val") if child is not None else None


def _hidden(element: ET.Element, group: str, name: str) -> bool:
    value = _property(element, group, name)
    return value is not None and value not in ("0", "false", "off")


# ─────────────────────────────────────────────────────────────────────
# Constructs
# ─────────────────────────────────────────────────────────────────────


def _subscript(element: ET.Element) -> str:
    return f"{_group(_part(element, 'm:e'))}_{{{_part(element, 'm:sub')}}}"


def _superscript(element: ET.Element) -> str:
    return f"{_group(_part(element, 'm:e'))}^{{{_part(element, 'm:sup')}}}"


def _sub_superscript(element: ET.Element) -> str:
    base = _group(_part(element, "m:e"))
    return f"{base}_{{{_part(element, 'm:sub')}}}^{{{_part(element, 'm:sup')}}}"


def _prescript(element: ET.Element) -> str:
    scripts = f"{{}}_{{{_part(element, 'm:sub')}}}^{{{_part(element, 'm:sup')}}}"
    return scripts + _group(_part(element, "m:e"))


def _fraction(element: ET.Element) -> str:
    numerator = _part(element, "m:num")
    denominator = _part(element, "m:den")
    kind = _property(element, "m:fPr", "m:type")
    if kind == "lin":
        return f"{_group(numerator)}/{_group(denominator)}"
    if kind == "noBar":
        return rf"\binom{{{numerator}}}{{{denominator}}}"
    return rf"\frac{{{numerator}}}{{{denominator}}}"


def _radical(element: ET.Element) -> str:
    degree = "" if _hidden(element, "m:radPr", "m:degHide") else _part(element, "m:deg")
    root = f"[{degree}]" if degree else ""
    return rf"\sqrt{root}{{{_part(element, 'm:e')}}}"


def _delimited(element: ET.Element) -> str:
    begin = _delimiter(_property(element, "m:dPr", "m:begChr"), "(")
    end = _delimiter(_property(element, "m:dPr", "m:endChr"), ")")
    separator = _property(element, "m:dPr", "m:sepChr")
    separator = "," if separator is None else separator
    parts = [_children(child) for child in element.findall(qn("m:e"))]
    return rf"\left{begin}{separator.join(parts)}\right{end}"


def _delimiter(char: Optional[str], default: str) -> str:
    if char is None:
        char = default
    return _DELIMITERS.get(char, char)


def _nary(element: ET.Element) -> str:
    operator = _NARY.get(_property(element, "m:naryPr", "m:chr") or "∑", r"\sum")
    limits = ""
    lower = "" if _hidden(element, "m:naryPr", "m:subHide") else _part(element, "m:sub")
    upper = "" if _hidden(element, "m:naryPr", "m:supHide") else _part(element, "m:sup")
    if lower:
        limits += f"_{{{lower}}}"
    if upper:
        limits += f"^{{{upper}}}"
    return f"{operator}{limits}{{{_part(element, 'm:e')}}}"


def _function(element: ET.Element) -> str:
    name = _part(element, "m:fName")
    command = f"\\{name}" if name in _FUNCTIONS else name
    return f"{command}{{{_part(element, 'm:e')}}}"


def _accented(element: ET.Element) -> str:
    char = _property(element, "m:accPr", "m:chr") or "̂"
    command = _ACCENTS.get(char, r"\hat")
    return f"{command}{{{_part(element, 'm:e')}}}"


def _barred(element: ET.Element) -> str:
    above = _property(element, "m:barPr", "m:pos") == "top"
    command = r"\overline" if above else r"\underline"
    return f"{command}{{{_part(element, 'm:e')}}}"


def _lower_limit(element: ET.Element) -> str:
    return rf"\underset{{{_part(element, 'm:lim')}}}{{{_part(element, 'm:e')}}}"


def _upper_limit(element: ET.Element) -> str:
    return rf"\overset{{{_part(element, 'm:lim')}}}{{{_part(element, 'm:e')}}}"


def _matrix(element: ET.Element) -> str:
    rows = [
        " & ".join(_children(cell) for cell in row.findall(qn("m:e")))
        for row in element.findall(qn("m:mr"))
    ]
    return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"


def _equation_array(element: ET.Element) -> str:
    rows = [_children(row) for row in element.findall(qn("m:e"))]
    return r"\begin{aligned}" + r" \\ ".join(rows) + r"\end{aligned}"


_HANDLERS = {
    qn("m:sSub"): _subscript,
    qn("m:sSup"): _superscript,
    qn("m:sSubSup"): _sub_superscript,
    qn("m:sPre"): _prescript,
    qn("m:f"): _fraction,
    qn("m:rad"): _radical,
    qn("m:d"): _delimited,
    qn("m:nary"): _nary,
    qn("m:func"): _function,
    qn("m:acc"): _accented,
    qn("m:bar"): _barred,
    qn("m:limLow"): _lower_limit,
    qn("m:limUpp"): _upper_limit,
    qn("m:m"): _matrix,
    qn("m:eqArr"): _equation_array,
}
