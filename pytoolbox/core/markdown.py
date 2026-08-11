"""Markdown primitives shared by the docx and pdf writers.

Deliberately takes primitives rather than a run object: each package keeps its
own inline dataclasses, and neither has to import the other's.
"""

from __future__ import annotations

from typing import Optional

#: Characters that would otherwise be read as Markdown syntax.
_ESCAPE = str.maketrans({ch: "\\" + ch for ch in r"\`*_[]"})


def escape(text: str) -> str:
    """Neutralise Markdown syntax in literal text."""
    return text.translate(_ESCAPE)


def emphasis(
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    strike: bool = False,
    code: bool = False,
    link: Optional[str] = None,
) -> str:
    """Escape ``text`` and wrap it in its emphasis markers and link.

    Takes the raw text, not escaped text: code spans must *not* be escaped,
    since backticks already make their content literal, and a caller cannot
    know that without duplicating the rule.
    """
    if not text:
        return ""

    if code:
        rendered = f"`{text}`"
    elif not text.strip():
        # Whitespace only: splitting it into lead and trail would double it.
        rendered = text
    else:
        escaped = escape(text)
        # Markers must hug the text: "** bold **" renders literally.
        lead = escaped[: len(escaped) - len(escaped.lstrip())]
        trail = escaped[len(escaped.rstrip()) :]
        body = escaped.strip()
        if strike:
            body = f"~~{body}~~"
        if bold:
            body = f"**{body}**"
        if italic:
            body = f"*{body}*"
        rendered = f"{lead}{body}{trail}"

    if link:
        return f"[{rendered.strip()}]({_target(link)})"
    return rendered


def _target(link: str) -> str:
    """A link target that cannot break out of its own parentheses."""
    if any(char in link for char in " ()<>"):
        # The pointy-bracket form is what Markdown provides for this; the
        # remaining brackets are percent-encoded, as a browser would send them.
        inner = link.replace("<", "%3C").replace(">", "%3E")
        return f"<{inner}>"
    return link
