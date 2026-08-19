"""Turning arbitrary keys into SQL identifiers.

JSON keys and spreadsheet headers are not identifiers: they carry spaces,
dots, punctuation, reserved words and case-only differences. Names are folded
to lower snake_case and de-duplicated, and every identifier is emitted quoted,
so a column called ``select`` or ``order`` is still legal.

Folding is not the same as transliterating. Latin accents are dropped, because
``prenom`` is what someone typing that column name would write; every other
script is kept as it is, because there is no ASCII spelling of ``نام واحد`` and
both SQLite and PostgreSQL accept it as a quoted identifier.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

#: Characters that separate words rather than belonging to one. The last is a
#: zero-width non-joiner, which separates Persian words without a space.
_SEPARATORS = re.compile("[\\s\\-./\\\\‌]+")
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_UNDERSCORES = re.compile(r"_+")

#: Unicode categories for combining marks, which are part of the letter they
#: sit on: Arabic harakat, Devanagari matras. Stripping them mangles the word.
_MARKS = frozenset({"Mn", "Mc"})

#: Fallback stem when a key has nothing usable in it at all.
FALLBACK = "column"

#: PostgreSQL's identifier limit is NAMEDATALEN - 1, counted in *bytes*.
MAX_BYTES = 63


def sanitize(name: str) -> str:
    """Return ``name`` as a lower snake_case identifier.

    ``"First Name"`` becomes ``first_name``, ``"userID"`` becomes ``user_id``,
    ``"Prénom"`` becomes ``prenom``, ``"نام واحد"`` becomes ``نام_واحد``, and a
    key with no usable characters at all becomes ``column``.
    """
    spaced = _SEPARATORS.sub("_", _fold_latin(name).strip())
    split = _CAMEL_BOUNDARY.sub("_", spaced)
    cleaned = "".join(char if _is_word_character(char) else "_" for char in split).lower()
    cleaned = _UNDERSCORES.sub("_", cleaned).strip("_")
    if not cleaned:
        return FALLBACK
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return truncate(cleaned)


def as_identifier(name: str, raw: bool = False) -> str:
    """One key as an identifier: folded by default, kept verbatim under ``raw``.

    Every identifier is emitted quoted, so a verbatim key is always legal SQL.
    ``raw`` therefore only fixes what is genuinely broken: a key with nothing
    in it, and a key longer than an identifier can be -- PostgreSQL truncates
    those itself, silently, which would turn two long keys into one column
    without saying so.
    """
    if not raw:
        return sanitize(name)
    if not name.strip():
        return FALLBACK
    return truncate(name)


def truncate(name: str) -> str:
    """Cut a name to what PostgreSQL will keep, without splitting a character.

    The limit is in bytes, not characters, so a Persian or Chinese name runs
    out after about half as many letters as an English one.
    """
    encoded = name.encode("utf-8")
    if len(encoded) <= MAX_BYTES:
        return name
    return encoded[:MAX_BYTES].decode("utf-8", "ignore")


def unique(names: Iterable[str], raw: bool = False) -> list[str]:
    """Turn ``names`` into identifiers and break collisions with a suffix.

    Two keys that differ only in case or in punctuation fold to the same
    identifier, which would be a duplicate-column error at ``CREATE TABLE``
    time. The first one keeps the plain name; later ones get ``_2``, ``_3``.
    Verbatim keys can collide too, once truncation is in play.
    """
    seen: dict[str, int] = {}
    result: list[str] = []
    for name in names:
        base = as_identifier(name, raw)
        count = seen.get(base, 0) + 1
        seen[base] = count
        if count == 1:
            result.append(base)
            continue
        candidate = f"{base}_{count}"
        while candidate in seen:
            count += 1
            seen[base] = count
            candidate = f"{base}_{count}"
        seen[candidate] = 1
        result.append(candidate)
    return result


def index_name(table: str, columns: Iterable[str]) -> str:
    """Name an index after the table and the columns it covers."""
    joined = "_".join(columns)
    return truncate(f"idx_{sanitize(table)}_{sanitize(joined)}")


def _fold_latin(name: str) -> str:
    """Drop accents from Latin letters, leaving every other script alone.

    ``é`` becomes ``e`` because that is how the column would be typed. A
    character is only folded when doing so leaves something ASCII behind, so a
    mark that stands on its own -- a Devanagari virama, an Arabic harakat --
    survives rather than being deleted for having no Latin spelling.
    """
    folded = []
    for char in unicodedata.normalize("NFKC", name):
        stripped = "".join(
            part for part in unicodedata.normalize("NFKD", char) if not unicodedata.combining(part)
        )
        folded.append(stripped if stripped and stripped.isascii() else char)
    return "".join(folded)


def _is_word_character(char: str) -> bool:
    """True for anything that can sit inside an identifier."""
    return char.isalnum() or char == "_" or unicodedata.category(char) in _MARKS
