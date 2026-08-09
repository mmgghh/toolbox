"""Text processing: search, replace, clipboard, case, encoding, translation.

The heavy lifting for walking files and talking to the clipboard lives in
``pytoolbox.core`` so that ``pyfm`` and ``pystr`` behave identically where
they overlap.
"""

# pylint: disable=line-too-long

from __future__ import annotations

import base64
import binascii
import re
import sys
import unicodedata
import urllib.parse
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from re import Pattern
from typing import Optional

import click

from pytoolbox.core import clipboard, console
from pytoolbox.core.fs import is_probably_text as _is_probably_text
from pytoolbox.core.fs import iter_files as _iter_files
from pytoolbox.core.fs import normalize_extensions as _normalize_extensions
from pytoolbox.core.options import (
    CONTEXT_SETTINGS,
    AliasedGroup,
    json_option,
    version_option,
)
from pytoolbox.normalize_data import NORMALIZE_RULES

ANSI_ESCAPE_RE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")

MONTH_NAME_PATTERN = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|"
    r"Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|"
    r"Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
URL_PATTERN = r"\b(?:https?://|ftp://|www\.)[^\s<>'\"\\)\]]+"
EMAIL_PATTERN = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
IPV4_PATTERN = r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
IPV6_PATTERN = (
    r"\b(?:"
    r"(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}|"
    r"(?:[A-Fa-f0-9]{1,4}:){1,7}:|"
    r"(?:[A-Fa-f0-9]{1,4}:){1,6}:[A-Fa-f0-9]{1,4}|"
    r"(?:[A-Fa-f0-9]{1,4}:){1,5}(?::[A-Fa-f0-9]{1,4}){1,2}|"
    r"(?:[A-Fa-f0-9]{1,4}:){1,4}(?::[A-Fa-f0-9]{1,4}){1,3}|"
    r"(?:[A-Fa-f0-9]{1,4}:){1,3}(?::[A-Fa-f0-9]{1,4}){1,4}|"
    r"(?:[A-Fa-f0-9]{1,4}:){1,2}(?::[A-Fa-f0-9]{1,4}){1,5}|"
    r"[A-Fa-f0-9]{1,4}:(?:(?::[A-Fa-f0-9]{1,4}){1,6})|"
    r":(?:(?::[A-Fa-f0-9]{1,4}){1,7}|:)"
    r")\b"
)
IP_PATTERN = rf"(?:{IPV4_PATTERN}|{IPV6_PATTERN})"
PHONE_PATTERN = r"\b(?:\+?\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)?\d{3,4}[\s.-]?\d{4}\b"
ZIP_PATTERN = r"\b\d{5}(?:-\d{4})?\b"
POSTAL_PATTERN = (
    r"\b(?:"
    r"\d{5}(?:-\d{4})?|"
    r"[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d|"
    r"[A-Za-z]{1,2}\d[A-Za-z\d]?\s?\d[A-Za-z]{2}"
    r")\b"
)
DATE_PATTERN = (
    rf"\b(?:"
    rf"\d{{4}}[-/]\d{{2}}[-/]\d{{2}}|"
    rf"\d{{2}}[-/]\d{{2}}[-/]\d{{4}}|"
    rf"{MONTH_NAME_PATTERN}\s+\d{{1,2}},?\s+\d{{4}}"
    rf")\b"
)
TIME_PATTERN = r"\b(?:[01]?\d|2[0-3]):[0-5]\d(?:[:][0-5]\d)?(?:\s?[APap][Mm])?\b"
UUID_PATTERN = r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}\b"
MAC_PATTERN = r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"

COMMON_TAG_PATTERNS: dict[str, str] = {
    "url": URL_PATTERN,
    "link": URL_PATTERN,
    "email": EMAIL_PATTERN,
    "ip": IP_PATTERN,
    "ipv4": IPV4_PATTERN,
    "ipv6": IPV6_PATTERN,
    "phone": PHONE_PATTERN,
    "mobile": PHONE_PATTERN,
    "zip": ZIP_PATTERN,
    "zip-code": ZIP_PATTERN,
    "zipcode": ZIP_PATTERN,
    "postal": POSTAL_PATTERN,
    "postal-code": POSTAL_PATTERN,
    "postalcode": POSTAL_PATTERN,
    "date": DATE_PATTERN,
    "time": TIME_PATTERN,
    "uuid": UUID_PATTERN,
    "mac": MAC_PATTERN,
}

TAG_HELP = (
    "Common tags (repeatable): "
    "url/link, email, ip/ipv4/ipv6, phone/mobile, "
    "zip (zip-code/zipcode), postal (postal-code/postalcode), "
    "date, time, uuid, mac."
)

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"
EN_DIGITS = "0123456789"

ARABIC_TO_PERSIAN_LETTERS = {
    "ي": "ی",
    "ى": "ی",
    "ئ": "ی",
    "ك": "ک",
    "ة": "ه",
    "ؤ": "و",
    "أ": "ا",
    "إ": "ا",
    "ٱ": "ا",
}

ARABIC_PUNCT_TO_EN = {
    "؟": "?",
    "،": ",",
    "؛": ";",
    "٪": "%",
    "٫": ".",
    "٬": ",",
}

EN_PUNCT_TO_FA = {
    "?": "؟",
    ",": "،",
    ";": "؛",
    "%": "٪",
}

EN_DASHES = "–—‑−"
FA_KASHIDA = "ـ"


@dataclass(frozen=True)
class SearchStats:
    """Aggregate search statistics."""

    files_scanned: int = 0
    files_matched: int = 0
    matches: int = 0


@dataclass(frozen=True)
class ReplacementPlan:
    """Planned replacements for a file."""

    path: Path
    matches: int


@dataclass(frozen=True)
class LineMatch:
    """A line match result."""

    line_no: int
    line: str
    count: int


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return ANSI_ESCAPE_RE.sub("", text)


def normalize_whitespace(text: str) -> str:
    """Collapse consecutive whitespace into single spaces and trim."""
    return " ".join(text.split())


def slugify(text: str, *, allow_unicode: bool = False) -> str:
    """Convert text to a URL-friendly slug."""
    value = text.strip()
    if not allow_unicode:
        value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^\w\s-]", "", value)
    value = re.sub(r"[\s_-]+", "-", value)
    return value.strip("-")


def to_snake_case(text: str) -> str:
    """Convert a string to snake_case."""
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    value = re.sub(r"[\s-]+", "_", value)
    return value.lower()


def to_kebab_case(text: str) -> str:
    """Convert a string to kebab-case."""
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", text)
    value = re.sub(r"[\s_]+", "-", value)
    return value.lower()


def _compile_filename_pattern(pattern: Optional[str]) -> Optional[Pattern[str]]:
    if not pattern:
        return None
    try:
        return re.compile(pattern)
    except re.error as exc:
        raise click.ClickException(f"Invalid --file-name regex: {exc}") from exc


def _compile_search_pattern(
    query: str,
    use_regex: bool,
    ignore_case: bool,
    whole_word: bool,
) -> Pattern[str]:
    pattern = query if use_regex else re.escape(query)
    if whole_word:
        pattern = rf"\b{pattern}\b"
    flags = re.MULTILINE
    if ignore_case:
        flags |= re.IGNORECASE
    try:
        return re.compile(pattern, flags)
    except re.error as exc:
        raise click.ClickException(f"Invalid search pattern: {exc}") from exc


def _resolve_tag_patterns(tags: Sequence[str]) -> list[str]:
    resolved: list[str] = []
    unknown: list[str] = []
    for tag in tags:
        key = tag.strip().lower()
        if not key:
            continue
        pattern = COMMON_TAG_PATTERNS.get(key)
        if pattern is None:
            unknown.append(tag)
        else:
            resolved.append(pattern)
    if unknown:
        valid = ", ".join(sorted(COMMON_TAG_PATTERNS.keys()))
        raise click.ClickException(f"Unknown tag(s): {', '.join(unknown)}. Available tags: {valid}.")
    return resolved


def _build_search_pattern(
    query: Optional[str],
    tags: Sequence[str],
    use_regex: bool,
    ignore_case: bool,
    whole_word: bool,
) -> Pattern[str]:
    parts: list[str] = []
    if query:
        query_pattern = query if use_regex else re.escape(query)
        if whole_word:
            query_pattern = rf"\b{query_pattern}\b"
        parts.append(query_pattern)
    tag_patterns = _resolve_tag_patterns(tags)
    if tag_patterns:
        tag_union = "|".join(f"(?:{pattern})" for pattern in tag_patterns)
        parts.append(tag_union)
    if not parts:
        raise click.ClickException("QUERY or --tag is required.")
    pattern_text = "|".join(f"(?:{pattern})" for pattern in parts)
    flags = re.MULTILINE
    if ignore_case:
        flags |= re.IGNORECASE
    try:
        return re.compile(pattern_text, flags)
    except re.error as exc:
        raise click.ClickException(f"Invalid search pattern: {exc}") from exc


@lru_cache(maxsize=1)
def _normalize_translation_table() -> dict[int, Optional[str]]:
    mapping: dict[int, Optional[str]] = {}
    for line in NORMALIZE_RULES.splitlines():
        if not line:
            continue
        if "\t" in line:
            left, right = line.split("\t", 1)
        else:
            left, right = line, ""
        if not left:
            continue
        mapping[ord(left)] = right if right != "" else None
    return mapping


def normalize_text(text: str) -> str:
    """Normalize text using the built-in normalize.rules mapping."""
    return text.translate(_normalize_translation_table())


def _digit_map(source: str, target: str) -> dict[str, str]:
    return dict(zip(source, target))


@lru_cache(maxsize=2)
def _translation_table(language: str) -> dict[int, str]:
    lang = language.lower()
    if lang == "en":
        mapping: dict[str, str] = {}
        mapping.update(_digit_map(PERSIAN_DIGITS, EN_DIGITS))
        mapping.update(_digit_map(ARABIC_DIGITS, EN_DIGITS))
        mapping.update(ARABIC_PUNCT_TO_EN)
        mapping.update(dict.fromkeys(EN_DASHES, "-"))
        mapping[FA_KASHIDA] = "_"
        return {ord(key): value for key, value in mapping.items()}
    if lang == "fa":
        mapping = {}
        mapping.update(_digit_map(EN_DIGITS, PERSIAN_DIGITS))
        mapping.update(_digit_map(ARABIC_DIGITS, PERSIAN_DIGITS))
        mapping.update(ARABIC_TO_PERSIAN_LETTERS)
        mapping.update(EN_PUNCT_TO_FA)
        mapping.update(dict.fromkeys(f"-_{EN_DASHES}", FA_KASHIDA))
        return {ord(key): value for key, value in mapping.items()}
    raise click.ClickException("Invalid destination language. Use 'en' or 'fa'.")


def translate_text(text: str, language: str) -> str:
    """Translate digits/letters/punctuation into English or Persian forms."""
    return text.translate(_translation_table(language))


def _collect_line_hits(
    lines: Iterable[str],
    pattern: Pattern[str],
    capture_lines: bool,
    capture_values: bool,
) -> tuple[int, list[LineMatch], list[str]]:
    matches: list[LineMatch] = []
    values: list[str] = []
    total = 0
    for line_no, line in enumerate(lines, 1):
        line_matches = list(pattern.finditer(line))
        if not line_matches:
            continue
        total += len(line_matches)
        if capture_lines:
            matches.append(LineMatch(line_no=line_no, line=line.rstrip("\n"), count=len(line_matches)))
        if capture_values:
            values.extend(match.group(0) for match in line_matches)
    return total, matches, values


def _format_path(path: Path, absolute: bool) -> str:
    return str(path.resolve()) if absolute else str(path)


def _emit_text_search_results(
    label: str,
    text: str,
    pattern: Pattern[str],
    verbose: int,
    count: bool,
    stats: bool,
    only_matches: bool,
    as_json: bool = False,
) -> None:
    lines = text.splitlines()
    total, matches, values = _collect_line_hits(
        lines,
        pattern,
        capture_lines=as_json or (verbose > 0 and not only_matches),
        capture_values=as_json or only_matches,
    )
    if as_json:
        console.emit_json(
            {
                "label": label,
                "matches": total,
                "lines": [{"line": m.line_no, "text": m.line, "count": m.count} for m in matches],
                "values": values,
            }
        )
        return
    if total == 0:
        if stats:
            click.echo("Scanned: 1 input, Matched: 0 input, Matches: 0")
        return

    if only_matches:
        for value in values:
            click.echo(value)
    elif verbose > 0:
        for match in matches:
            click.echo(f"{label}:{match.line_no}: {match.line}")
    elif count:
        click.echo(f"{label}:{total}")
    else:
        click.echo(label)

    if stats:
        click.echo(f"Scanned: 1 input, Matched: 1 input, Matches: {total}")


def _read_text_source(
    path: Optional[Path],
    input_text: Optional[str],
    from_stdin: bool,
    encoding: str,
    errors: str,
) -> tuple[str, Optional[Path]]:
    if input_text is not None and from_stdin:
        raise click.ClickException("Use either --text or --stdin, not both.")
    if input_text is not None:
        return input_text, None
    if from_stdin:
        return sys.stdin.read(), None
    if path is None:
        raise click.ClickException("Provide PATH, --text, or --stdin.")
    if not path.exists():
        raise click.ClickException(f"Path not found: {path}")
    try:
        return path.read_text(encoding=encoding, errors=errors), path
    except OSError as exc:
        raise click.ClickException(f"Could not read {path}: {exc}") from exc


def _emit_text_output(
    text: str,
    source_path: Optional[Path],
    inplace: bool,
    encoding: str,
    errors: str,
) -> None:
    if inplace:
        if source_path is None:
            raise click.ClickException("--inplace requires a file PATH.")
        try:
            source_path.write_text(text, encoding=encoding, errors=errors)
        except OSError as exc:
            raise click.ClickException(f"Could not write {source_path}: {exc}") from exc
        return
    click.echo(text, nl=True)


def _apply_replacement(
    text: str,
    pattern: Pattern[str],
    replacement: str,
    regex: bool,
) -> tuple[str, int]:
    if regex:
        return pattern.subn(replacement, text)
    return pattern.subn(lambda _: replacement, text)


def get_clipboard_text() -> str:
    """Read clipboard text (Termux/Linux/macOS/Windows)."""
    return clipboard.get_text()


def set_clipboard_text(text: str) -> None:
    """Write text to the clipboard (Termux/Linux/macOS/Windows)."""
    clipboard.set_text(text)


@click.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
@version_option
def str_cli():
    """Text: search, replace, clipboard, case, encoding, normalization.

    \b
    Examples:
      pystr search ./src "TODO" -v
      pystr search . --tag email --only-matches
      pystr replace ./src "foo" "bar" -e py --dry-run
      pystr case "Hello World" --to snake
      pystr encode "hello" --as base64
      pystr count ./README.md
      pystr normalize --text "Résumé — ١٢٣"
      pystr translate --to en --text "شماره ۱۲۳؟"
      pystr clip-search --tag url
      echo "hello" | pystr setclip --stdin
    """


@str_cli.command("search")
# Optional so that `--text`/`--stdin` searches need no PATH at all, which is
# what the help has always promised.
@click.argument("path_or_query", required=False, type=str)
@click.argument("query", required=False, type=str)
@click.option("-v", "--verbose", count=True, help="Print matching lines with file name and line number.")
@click.option("-d", "--depth", type=int, default=None, help="Max directory depth to search (0 = only the root).")
@click.option("-i", "--ignore-case", is_flag=True, help="Case-insensitive search.")
@click.option("-e", "--extension", multiple=True, help="File extensions to include (e.g., -e .py -e txt).")
@click.option("--file-name", default=None, help="Regex to include files with matching names.")
@click.option("--regex/--literal", default=False, help="Treat query as regex or literal string (default: literal).")
@click.option("-w", "--whole-word", is_flag=True, help="Match whole words only.")
@click.option("-t", "--tag", "tags", multiple=True, help=TAG_HELP)
@click.option("-o", "--only-matches", is_flag=True, help="Print only the matching text (overrides --verbose/--count).")
@click.option("--exclude", multiple=True, help="Glob patterns to exclude files.")
@click.option("--exclude-dir", multiple=True, help="Glob patterns to exclude directories.")
@click.option("--hidden", is_flag=True, help="Include hidden files and directories.")
@click.option("--follow-symlinks", is_flag=True, help="Follow symlinks while walking directories.")
@click.option("--max-size", type=float, default=None, help="Skip files larger than this size (MB).")
@click.option("--encoding", default="utf-8", help="Text encoding to use when reading files.")
@click.option(
    "--errors",
    default="replace",
    type=click.Choice(["strict", "ignore", "replace"], case_sensitive=False),
    help="Encoding error handler.",
)
@click.option("--stats", is_flag=True, help="Print summary statistics.")
@click.option("--count", is_flag=True, help="Print match counts per file instead of just file names.")
@click.option("--absolute", is_flag=True, help="Print absolute paths.")
@click.option("--binary", is_flag=True, help="Include binary files (default: skipped).")
@click.option("--text", "input_text", default=None, help="Search within provided text instead of files (PATH can be omitted).")
@click.option("--stdin", "from_stdin", is_flag=True, help="Read text to search from stdin (PATH can be omitted).")
@click.option("--label", default=None, help="Label for input text results (default: input/stdin).")
@json_option
def search(
    path_or_query: str,
    query: Optional[str],
    verbose: int,
    depth: Optional[int],
    ignore_case: bool,
    extension: Sequence[str],
    file_name: Optional[str],
    regex: bool,
    whole_word: bool,
    tags: Sequence[str],
    only_matches: bool,
    exclude: Sequence[str],
    exclude_dir: Sequence[str],
    hidden: bool,
    follow_symlinks: bool,
    max_size: Optional[float],
    encoding: str,
    errors: str,
    stats: bool,
    count: bool,
    absolute: bool,
    binary: bool,
    input_text: Optional[str],
    from_stdin: bool,
    label: Optional[str],
    as_json: bool,
):
    """Search for a query in text files under PATH or within provided text.

    Examples:
        pystr search ./src "TODO"
        pystr search . "error" -i -e log --stats
        pystr search . "def\\s+main" --regex -e py -v
        pystr search ./logs "timeout" --file-name ".*\\.log$" --count
        pystr search . --tag email --tag ip
        pystr search . --tag email --only-matches
        pystr search "token" --text "token=abcd"
        echo "hello world" | pystr search "world" --stdin
    """
    if input_text is not None or from_stdin:
        if input_text is not None and from_stdin:
            raise click.ClickException("Use either --text or --stdin, not both.")
        effective_query = query
        if effective_query is None and not tags:
            effective_query = path_or_query
        if effective_query is None and not tags:
            raise click.ClickException("QUERY or --tag is required when using --text/--stdin.")
        pattern = _build_search_pattern(effective_query, tags, regex, ignore_case, whole_word)
        text_value = input_text if input_text is not None else sys.stdin.read()
        label_value = label or ("stdin" if from_stdin else "input")
        _emit_text_search_results(
            label_value, text_value, pattern, verbose, count, stats, only_matches, as_json
        )
        return

    if path_or_query is None:
        raise click.ClickException("PATH is required (or use --text/--stdin).")
    if query is None and not tags:
        raise click.ClickException("QUERY or --tag is required.")
    path = Path(path_or_query)
    if not path.exists():
        raise click.ClickException(f"Path not found: {path}")
    extensions = _normalize_extensions(extension)
    filename_pattern = _compile_filename_pattern(file_name)
    max_bytes = int(max_size * 1024 * 1024) if max_size is not None else None
    pattern = _build_search_pattern(query, tags, regex, ignore_case, whole_word)
    stats_acc = SearchStats()
    json_results: list[dict] = []

    for file_path in _iter_files(
        path,
        depth,
        hidden,
        follow_symlinks,
        extensions if extensions else None,
        filename_pattern,
        exclude,
        exclude_dir,
        max_bytes,
    ):
        stats_acc = SearchStats(
            files_scanned=stats_acc.files_scanned + 1,
            files_matched=stats_acc.files_matched,
            matches=stats_acc.matches,
        )
        if not binary and not _is_probably_text(file_path):
            continue
        try:
            with open(file_path, encoding=encoding, errors=errors) as handle:
                total, matches, values = _collect_line_hits(
                    handle,
                    pattern,
                    capture_lines=as_json or (verbose > 0 and not only_matches),
                    capture_values=as_json or only_matches,
                )
        except OSError as exc:
            click.echo(f"Could not read {file_path}: {exc}", err=True)
            continue

        if total == 0:
            continue

        stats_acc = SearchStats(
            files_scanned=stats_acc.files_scanned,
            files_matched=stats_acc.files_matched + 1,
            matches=stats_acc.matches + total,
        )

        formatted_path = _format_path(file_path, absolute)
        if as_json:
            json_results.append(
                {
                    "path": formatted_path,
                    "matches": total,
                    "lines": [
                        {"line": m.line_no, "text": m.line, "count": m.count} for m in matches
                    ],
                    "values": values,
                }
            )
        elif only_matches:
            for value in values:
                click.echo(value)
        elif verbose > 0:
            for match in matches:
                click.echo(f"{formatted_path}:{match.line_no}: {match.line}")
        elif count:
            click.echo(f"{formatted_path}:{total}")
        else:
            click.echo(formatted_path)

    if as_json:
        console.emit_json(
            {
                "files_scanned": stats_acc.files_scanned,
                "files_matched": stats_acc.files_matched,
                "matches": stats_acc.matches,
                "results": json_results,
            }
        )
        return

    if stats:
        click.echo(
            f"Scanned: {stats_acc.files_scanned} files, "
            f"Matched: {stats_acc.files_matched} files, "
            f"Matches: {stats_acc.matches}"
        )


@str_cli.command("replace")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.argument("query", type=str)
@click.argument("replacement", type=str)
@click.option("-y", "--yes", is_flag=True, help="Apply changes without confirmation.")
@click.option("--dry-run", is_flag=True, help="Show summary and exit without making changes.")
@click.option("-v", "--verbose", count=True, help="Print files as they are modified.")
@click.option("-d", "--depth", type=int, default=None, help="Max directory depth to search (0 = only the root).")
@click.option("-i", "--ignore-case", is_flag=True, help="Case-insensitive search.")
@click.option("-e", "--extension", multiple=True, help="File extensions to include (e.g., -e .py -e txt).")
@click.option("--file-name", default=None, help="Regex to include files with matching names.")
@click.option("--regex/--literal", default=False, help="Treat query as regex or literal string (default: literal).")
@click.option("-w", "--whole-word", is_flag=True, help="Match whole words only.")
@click.option("--exclude", multiple=True, help="Glob patterns to exclude files.")
@click.option("--exclude-dir", multiple=True, help="Glob patterns to exclude directories.")
@click.option("--hidden", is_flag=True, help="Include hidden files and directories.")
@click.option("--follow-symlinks", is_flag=True, help="Follow symlinks while walking directories.")
@click.option("--max-size", type=float, default=None, help="Skip files larger than this size (MB).")
@click.option("--encoding", default="utf-8", help="Text encoding to use when reading files.")
@click.option(
    "--errors",
    default="replace",
    type=click.Choice(["strict", "ignore", "replace"], case_sensitive=False),
    help="Encoding error handler.",
)
@click.option("--backup", is_flag=True, help="Write a backup file before replacing.")
@click.option("--backup-suffix", default=".bak", help="Suffix for backup files (default: .bak).")
@click.option("--binary", is_flag=True, help="Include binary files (default: skipped).")
def replace(
    path: Path,
    query: str,
    replacement: str,
    yes: bool,
    dry_run: bool,
    verbose: int,
    depth: Optional[int],
    ignore_case: bool,
    extension: Sequence[str],
    file_name: Optional[str],
    regex: bool,
    whole_word: bool,
    exclude: Sequence[str],
    exclude_dir: Sequence[str],
    hidden: bool,
    follow_symlinks: bool,
    max_size: Optional[float],
    encoding: str,
    errors: str,
    backup: bool,
    backup_suffix: str,
    binary: bool,
):
    """Replace matches with a replacement string in text files under PATH.

    Examples:
        pystr replace ./src "foo" "bar" -e py --yes
        pystr replace . "(\\d+)" "[\\1]" --regex --dry-run
        pystr replace ./docs "TODO" "DONE" -i --backup
    """
    extensions = _normalize_extensions(extension)
    filename_pattern = _compile_filename_pattern(file_name)
    max_bytes = int(max_size * 1024 * 1024) if max_size is not None else None
    pattern = _compile_search_pattern(query, regex, ignore_case, whole_word)

    plans: list[ReplacementPlan] = []
    total_matches = 0

    for file_path in _iter_files(
        path,
        depth,
        hidden,
        follow_symlinks,
        extensions if extensions else None,
        filename_pattern,
        exclude,
        exclude_dir,
        max_bytes,
    ):
        if not binary and not _is_probably_text(file_path):
            continue
        try:
            content = file_path.read_text(encoding=encoding, errors=errors)
        except OSError as exc:
            click.echo(f"Could not read {file_path}: {exc}", err=True)
            continue
        _, count = _apply_replacement(content, pattern, replacement, regex)
        if count:
            plans.append(ReplacementPlan(path=file_path, matches=count))
            total_matches += count

    if not plans:
        click.echo("No matches found.")
        return

    click.echo(f"Files to update: {len(plans)}")
    click.echo(f"Total replacements: {total_matches}")
    for plan in plans:
        click.echo(f"{plan.path}: {plan.matches}")

    if dry_run:
        return

    if not yes and not click.confirm("Apply these changes?", default=False):
        click.echo("Aborted.")
        return

    for plan in plans:
        try:
            content = plan.path.read_text(encoding=encoding, errors=errors)
        except OSError as exc:
            click.echo(f"Could not read {plan.path}: {exc}", err=True)
            continue
        new_content, count = _apply_replacement(content, pattern, replacement, regex)
        if count == 0:
            continue
        if backup:
            backup_path = plan.path.with_name(plan.path.name + backup_suffix)
            try:
                backup_path.write_text(content, encoding=encoding)
            except OSError as exc:
                click.echo(f"Could not write backup {backup_path}: {exc}", err=True)
                continue
        try:
            plan.path.write_text(new_content, encoding=encoding)
        except OSError as exc:
            click.echo(f"Could not write {plan.path}: {exc}", err=True)
            continue
        if verbose:
            click.echo(f"Updated {plan.path} ({count} replacements)")


@str_cli.command("clip-search")
@click.argument("query", required=False, type=str)
@click.option("-v", "--verbose", count=True, help="Print matching lines with line numbers.")
@click.option("-i", "--ignore-case", is_flag=True, help="Case-insensitive search.")
@click.option("--regex/--literal", default=False, help="Treat query as regex or literal string (default: literal).")
@click.option("-w", "--whole-word", is_flag=True, help="Match whole words only.")
@click.option("-t", "--tag", "tags", multiple=True, help=TAG_HELP)
@click.option("-o", "--only-matches", is_flag=True, help="Print only the matching text (overrides --verbose/--count).")
@click.option("--count", is_flag=True, help="Print match count.")
def clip_search(
    query: Optional[str],
    verbose: int,
    ignore_case: bool,
    regex: bool,
    whole_word: bool,
    tags: Sequence[str],
    only_matches: bool,
    count: bool,
):
    """Search clipboard text for a query.

    Examples:
        pystr clip-search "secret"
        pystr clip-search "token" -i --count
        pystr clip-search "(\\w+)" --regex -v
        pystr clip-search --tag email
        pystr clip-search --tag email --only-matches
    """
    if query is None and not tags:
        raise click.ClickException("QUERY or --tag is required.")
    pattern = _build_search_pattern(query, tags, regex, ignore_case, whole_word)
    text = get_clipboard_text()
    lines = text.splitlines()
    total, matches, values = _collect_line_hits(
        lines,
        pattern,
        capture_lines=verbose > 0 and not only_matches,
        capture_values=only_matches,
    )

    if total == 0:
        click.echo("No matches found in clipboard.")
        return

    if only_matches:
        for value in values:
            click.echo(value)
    elif verbose > 0:
        for match in matches:
            click.echo(f"clipboard:{match.line_no}: {match.line}")
    elif count:
        click.echo(f"clipboard:{total}")
    else:
        click.echo("clipboard")


@str_cli.command("clip-replace")
@click.argument("query", type=str)
@click.argument("replacement", type=str)
@click.option("-y", "--yes", is_flag=True, help="Apply changes without confirmation.")
@click.option("--dry-run", is_flag=True, help="Show summary and exit without making changes.")
@click.option("-i", "--ignore-case", is_flag=True, help="Case-insensitive search.")
@click.option("--regex/--literal", default=False, help="Treat query as regex or literal string (default: literal).")
@click.option("-w", "--whole-word", is_flag=True, help="Match whole words only.")
@click.option("--print", "print_output", is_flag=True, help="Print updated clipboard text to stdout.")
def clip_replace(
    query: str,
    replacement: str,
    yes: bool,
    dry_run: bool,
    ignore_case: bool,
    regex: bool,
    whole_word: bool,
    print_output: bool,
):
    """Replace matches in clipboard text and update the clipboard.

    Examples:
        pystr clip-replace "foo" "bar"
        pystr clip-replace "(\\d+)" "[\\1]" --regex --yes
        pystr clip-replace "secret" "[redacted]" --dry-run
    """
    pattern = _compile_search_pattern(query, regex, ignore_case, whole_word)
    text = get_clipboard_text()
    new_text, count = _apply_replacement(text, pattern, replacement, regex)

    if count == 0:
        click.echo("No matches found in clipboard.")
        return

    click.echo(f"Clipboard replacements: {count}")
    if dry_run:
        return
    if not yes and not click.confirm("Apply changes to clipboard?", default=False):
        click.echo("Aborted.")
        return

    set_clipboard_text(new_text)
    if print_output:
        click.echo(new_text)


@str_cli.command("normalize")
@click.argument("path", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--text", "input_text", default=None, help="Normalize the provided text instead of a file.")
@click.option("--stdin", "from_stdin", is_flag=True, help="Read text to normalize from stdin.")
@click.option("--inplace", is_flag=True, help="Overwrite the input file in place.")
@click.option("--encoding", default="utf-8", help="Text encoding to use when reading files.")
@click.option(
    "--errors",
    default="replace",
    type=click.Choice(["strict", "ignore", "replace"], case_sensitive=False),
    help="Encoding error handler.",
)
def normalize(
    path: Optional[Path],
    input_text: Optional[str],
    from_stdin: bool,
    inplace: bool,
    encoding: str,
    errors: str,
):
    """Normalize text using the bundled normalize.rules mapping.

    Examples:
        pystr normalize --text "Résumé — ١٢٣"
        echo "… hello" | pystr normalize --stdin
        pystr normalize ./docs/readme.txt --inplace
    """
    text, source_path = _read_text_source(path, input_text, from_stdin, encoding, errors)
    normalized = normalize_text(text)
    _emit_text_output(normalized, source_path, inplace, encoding, errors)


@str_cli.command("translate")
@click.argument("path", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--to", "dest", required=True, type=click.Choice(["en", "fa"], case_sensitive=False))
@click.option("--text", "input_text", default=None, help="Translate the provided text instead of a file.")
@click.option("--stdin", "from_stdin", is_flag=True, help="Read text to translate from stdin.")
@click.option("--inplace", is_flag=True, help="Overwrite the input file in place.")
@click.option("--encoding", default="utf-8", help="Text encoding to use when reading files.")
@click.option(
    "--errors",
    default="replace",
    type=click.Choice(["strict", "ignore", "replace"], case_sensitive=False),
    help="Encoding error handler.",
)
def translate(
    path: Optional[Path],
    dest: str,
    input_text: Optional[str],
    from_stdin: bool,
    inplace: bool,
    encoding: str,
    errors: str,
):
    """Translate digits/punctuation for English or Persian display.

    Examples:
        pystr translate --to en --text "شماره ۱۲۳؟"
        pystr translate --to fa --text "Issue 123?"
        pystr translate ./notes.txt --to fa --inplace
    """
    text, source_path = _read_text_source(path, input_text, from_stdin, encoding, errors)
    translated = translate_text(text, dest)
    _emit_text_output(translated, source_path, inplace, encoding, errors)


@str_cli.command("getclip")
@click.option("--strip-ansi", "strip_ansi_output", is_flag=True, help="Remove ANSI escape sequences.")
@click.option("--trim", is_flag=True, help="Trim surrounding whitespace.")
def getclip(strip_ansi_output: bool, trim: bool):
    """Print clipboard text.

    Examples:
        pystr getclip
        pystr getclip --trim
    """
    text = get_clipboard_text()
    if strip_ansi_output:
        text = strip_ansi(text)
    if trim:
        text = text.strip()
    click.echo(text, nl=True)


@str_cli.command("setclip")
@click.argument("text", required=False, type=str)
@click.option("--stdin", "from_stdin", is_flag=True, help="Read clipboard text from stdin.")
@click.option("--strip-ansi", "strip_ansi_input", is_flag=True, help="Remove ANSI escape sequences.")
@click.option("--trim", is_flag=True, help="Trim surrounding whitespace.")
def setclip(text: Optional[str], from_stdin: bool, strip_ansi_input: bool, trim: bool):
    """Set clipboard text.

    Examples:
        pystr setclip "hello"
        echo "hello" | pystr setclip --stdin
    """
    if text is not None and from_stdin:
        raise click.ClickException("Use either TEXT argument or --stdin, not both.")
    if text is None and not from_stdin:
        raise click.ClickException("Provide TEXT or use --stdin.")
    value = sys.stdin.read() if from_stdin else text or ""
    if strip_ansi_input:
        value = strip_ansi(value)
    if trim:
        value = value.strip()
    set_clipboard_text(value)


# ═══════════════════════════════════════════════════════════════════
# Case conversion, encoding, statistics
# ═══════════════════════════════════════════════════════════════════

def to_camel_case(text: str) -> str:
    """Convert a string to camelCase."""
    words = re.split(r"[\s_\-]+", text.strip())
    words = [w for w in words if w]
    if not words:
        return ""
    head, *tail = words
    return head.lower() + "".join(word[:1].upper() + word[1:].lower() for word in tail)


def to_pascal_case(text: str) -> str:
    """Convert a string to PascalCase."""
    words = [w for w in re.split(r"[\s_\-]+", text.strip()) if w]
    return "".join(word[:1].upper() + word[1:].lower() for word in words)


def to_title_case(text: str) -> str:
    """Capitalize the first letter of each word, leaving separators alone."""
    return re.sub(r"\b[\w']+", lambda m: m.group(0)[:1].upper() + m.group(0)[1:].lower(), text)


#: ``--to`` values accepted by ``pystr case``.
CASE_CONVERTERS = {
    "lower": str.lower,
    "upper": str.upper,
    "title": to_title_case,
    "snake": to_snake_case,
    "kebab": to_kebab_case,
    "camel": to_camel_case,
    "pascal": to_pascal_case,
    "slug": slugify,
    "slug-unicode": lambda text: slugify(text, allow_unicode=True),
}


@str_cli.command("case")
@click.argument("path", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--to",
    "target",
    required=True,
    type=click.Choice(sorted(CASE_CONVERTERS), case_sensitive=False),
    help="Case style to convert to.",
)
@click.option("--text", "input_text", default=None, help="Convert this text instead of a file.")
@click.option("--stdin", "from_stdin", is_flag=True, help="Read the text from stdin.")
@click.option("--inplace", is_flag=True, help="Overwrite the input file.")
@click.option("--per-line", is_flag=True, help="Convert each line separately (keeps line structure).")
@click.option("--encoding", default="utf-8", show_default=True, help="Text encoding for file IO.")
@click.option(
    "--errors",
    default="replace",
    type=click.Choice(["strict", "ignore", "replace"], case_sensitive=False),
    help="Encoding error handler.",
)
def case(
    path: Optional[Path],
    target: str,
    input_text: Optional[str],
    from_stdin: bool,
    inplace: bool,
    per_line: bool,
    encoding: str,
    errors: str,
):
    """Convert text between naming conventions.

    \b
    Styles: lower, upper, title, snake, kebab, camel, pascal, slug, slug-unicode.

    \b
    Examples:
      pystr case --to snake --text "Hello World"
      pystr case --to slug --text "Résumé of 2026!"
      echo "someValue" | pystr case --to kebab --stdin
      pystr case ./headings.txt --to title --per-line --inplace
    """
    text, source_path = _read_text_source(path, input_text, from_stdin, encoding, errors)
    convert = CASE_CONVERTERS[target.lower()]
    if per_line:
        converted = "\n".join(convert(line) for line in text.splitlines())
    else:
        converted = convert(text.strip() if target.lower() in ("slug", "slug-unicode") else text)
    _emit_text_output(converted, source_path, inplace, encoding, errors)


#: Encodings understood by ``pystr encode`` / ``pystr decode``.
ENCODINGS = ("base64", "base64url", "hex", "url", "url-plus", "rot13")


def _apply_encoding(text: str, scheme: str, decode: bool) -> str:
    scheme = scheme.lower()
    try:
        if scheme == "base64":
            return (
                base64.b64decode(text.strip() + "=" * (-len(text.strip()) % 4)).decode("utf-8", "replace")
                if decode
                else base64.b64encode(text.encode("utf-8")).decode("ascii")
            )
        if scheme == "base64url":
            return (
                base64.urlsafe_b64decode(text.strip() + "=" * (-len(text.strip()) % 4)).decode("utf-8", "replace")
                if decode
                else base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")
            )
        if scheme == "hex":
            return (
                bytes.fromhex(re.sub(r"\s+", "", text)).decode("utf-8", "replace")
                if decode
                else text.encode("utf-8").hex()
            )
        if scheme == "url":
            return urllib.parse.unquote(text) if decode else urllib.parse.quote(text, safe="")
        if scheme == "url-plus":
            return urllib.parse.unquote_plus(text) if decode else urllib.parse.quote_plus(text)
        if scheme == "rot13":
            # Symmetric: encoding and decoding are the same operation.
            return __import__("codecs").encode(text, "rot13")
    except (binascii.Error, ValueError) as exc:
        raise click.ClickException(f"Could not {'decode' if decode else 'encode'} as {scheme}: {exc}") from exc
    raise click.ClickException(f"Unknown encoding: {scheme}")


def _encoding_command(name: str, decode: bool):
    """Build the shared body of the encode/decode commands."""

    @str_cli.command(name)
    @click.argument("path", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path))
    @click.option(
        "--as",
        "scheme",
        default="base64",
        show_default=True,
        type=click.Choice(ENCODINGS, case_sensitive=False),
        help="Encoding scheme.",
    )
    @click.option("--text", "input_text", default=None, help="Use this text instead of a file.")
    @click.option("--stdin", "from_stdin", is_flag=True, help="Read the text from stdin.")
    @click.option("--inplace", is_flag=True, help="Overwrite the input file.")
    @click.option("--encoding", default="utf-8", show_default=True, help="Text encoding for file IO.")
    @click.option(
        "--errors",
        default="replace",
        type=click.Choice(["strict", "ignore", "replace"], case_sensitive=False),
        help="Encoding error handler.",
    )
    def command(path, scheme, input_text, from_stdin, inplace, encoding, errors):
        text, source_path = _read_text_source(path, input_text, from_stdin, encoding, errors)
        _emit_text_output(_apply_encoding(text, scheme, decode), source_path, inplace, encoding, errors)

    verb = "Decode" if decode else "Encode"
    command.__doc__ = f"""{verb} text as base64, hex, URL escapes or rot13.

    \b
    Schemes: {', '.join(ENCODINGS)}.

    \b
    Examples:
      pystr {name} --text "hello" --as base64
      echo "hello" | pystr {name} --stdin --as hex
      pystr {name} ./token.txt --as base64url
    """
    command.help = command.__doc__
    command.short_help = f"{verb} text (base64, hex, url, rot13)."
    return command


encode = _encoding_command("encode", decode=False)
decode = _encoding_command("decode", decode=True)


@str_cli.command("count")
@click.argument("path", required=False, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--text", "input_text", default=None, help="Count this text instead of a file.")
@click.option("--stdin", "from_stdin", is_flag=True, help="Read the text from stdin.")
@click.option("--top", type=int, default=0, help="Also list the N most frequent words.")
@click.option("--encoding", default="utf-8", show_default=True, help="Text encoding for file IO.")
@click.option(
    "--errors",
    default="replace",
    type=click.Choice(["strict", "ignore", "replace"], case_sensitive=False),
    help="Encoding error handler.",
)
@json_option
def count_command(
    path: Optional[Path],
    input_text: Optional[str],
    from_stdin: bool,
    top: int,
    encoding: str,
    errors: str,
    as_json: bool,
):
    """Report line, word and character counts for text.

    \b
    Examples:
      pystr count ./README.md
      pystr count ./notes.txt --top 10
      echo "a b c" | pystr count --stdin --json
    """
    from collections import Counter

    text, _ = _read_text_source(path, input_text, from_stdin, encoding, errors)
    words = re.findall(r"\b[\w'-]+\b", text, flags=re.UNICODE)
    payload: dict = {
        "lines": len(text.splitlines()),
        "words": len(words),
        "characters": len(text),
        "characters_no_spaces": len(re.sub(r"\s", "", text)),
        "bytes": len(text.encode(encoding, errors="replace")),
    }
    if top > 0:
        payload["top_words"] = [
            {"word": word, "count": n}
            for word, n in Counter(word.lower() for word in words).most_common(top)
        ]

    if as_json:
        console.emit_json(payload)
        return
    for key in ("lines", "words", "characters", "characters_no_spaces", "bytes"):
        click.echo(f"{key.replace('_', ' '):<22} {payload[key]}")
    for entry in payload.get("top_words", []):
        click.echo(f"{entry['count']:>6}  {entry['word']}")


if __name__ == "__main__":  # pragma: no cover
    str_cli()
