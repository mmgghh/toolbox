"""Text processing utilities and CLI commands."""

# pylint: disable=line-too-long

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Pattern, Sequence

import click


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


def _normalize_extensions(values: Sequence[str]) -> set[str]:
    normalized: set[str] = set()
    for value in values:
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            if not item.startswith("."):
                item = f".{item}"
            normalized.add(item.lower())
    return normalized


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


def _is_hidden_name(name: str) -> bool:
    return name.startswith(".") and name not in (".", "..")


def _matches_any_glob(path: Path, patterns: Sequence[str]) -> bool:
    if not patterns:
        return False
    path_posix = path.as_posix()
    return any(fnmatch.fnmatch(path.name, pat) or fnmatch.fnmatch(path_posix, pat) for pat in patterns)


def _iter_files(
    root: Path,
    depth: Optional[int],
    include_hidden: bool,
    follow_symlinks: bool,
    extensions: Optional[set[str]],
    filename_pattern: Optional[Pattern[str]],
    exclude: Sequence[str],
    exclude_dir: Sequence[str],
    max_bytes: Optional[int],
) -> Iterable[Path]:
    if root.is_file():
        if extensions and root.suffix.lower() not in extensions:
            return
        if filename_pattern and not filename_pattern.search(root.name):
            return
        if not include_hidden and _is_hidden_name(root.name):
            return
        if _matches_any_glob(root, exclude):
            return
        if max_bytes is not None:
            try:
                if root.stat().st_size > max_bytes:
                    return
            except OSError:
                return
        yield root
        return

    if not root.is_dir():
        return

    for current_root, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        rel_depth = len(Path(current_root).relative_to(root).parts)
        if depth is not None and rel_depth >= depth:
            dirnames[:] = []
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not _is_hidden_name(d)]
        if exclude_dir:
            dirnames[:] = [
                d for d in dirnames
                if not _matches_any_glob(Path(current_root) / d, exclude_dir)
            ]
        for filename in filenames:
            if not include_hidden and _is_hidden_name(filename):
                continue
            file_path = Path(current_root) / filename
            if _matches_any_glob(file_path, exclude):
                continue
            if extensions and file_path.suffix.lower() not in extensions:
                continue
            if filename_pattern and not filename_pattern.search(filename):
                continue
            if max_bytes is not None:
                try:
                    if file_path.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
            yield file_path


def _is_probably_text(path: Path, max_bytes: int = 2048) -> bool:
    try:
        with open(path, "rb") as handle:
            sample = handle.read(max_bytes)
    except OSError:
        return False
    if not sample:
        return True
    if b"\x00" in sample:
        return False
    non_text = sum(byte < 9 or (13 < byte < 32) for byte in sample)
    return (non_text / len(sample)) < 0.3


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
) -> None:
    lines = text.splitlines()
    total, matches, values = _collect_line_hits(
        lines,
        pattern,
        capture_lines=verbose > 0 and not only_matches,
        capture_values=only_matches,
    )
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


def _apply_replacement(
    text: str,
    pattern: Pattern[str],
    replacement: str,
    regex: bool,
) -> tuple[str, int]:
    if regex:
        return pattern.subn(replacement, text)
    return pattern.subn(lambda _: replacement, text)


def _run_clipboard_command(cmd: Sequence[str], input_text: Optional[str] = None) -> str:
    result = subprocess.run(
        list(cmd),
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or "Unknown clipboard error."
        raise click.ClickException(stderr)
    return result.stdout


def _run_clipboard_command_detached(cmd: Sequence[str], input_text: Optional[str] = None) -> None:
    try:
        process = subprocess.Popen(
            list(cmd),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise click.ClickException(str(exc)) from exc

    if process.stdin is None:
        raise click.ClickException("Clipboard command stdin is unavailable.")
    try:
        process.stdin.write(input_text or "")
        process.stdin.close()
    except OSError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        process.wait(timeout=0.2)
    except subprocess.TimeoutExpired:
        return
    if process.returncode != 0:
        raise click.ClickException(f"Clipboard command failed with code {process.returncode}.")


def _clipboard_backend() -> tuple[Sequence[str], Sequence[str]]:
    if shutil.which("termux-clipboard-get") and shutil.which("termux-clipboard-set"):
        return (["termux-clipboard-get"], ["termux-clipboard-set"])
    if sys.platform == "darwin":
        return (["pbpaste"], ["pbcopy"])
    if sys.platform == "win32":
        shell = "powershell"
        if shutil.which("pwsh"):
            shell = "pwsh"
        get_cmd = [shell, "-NoProfile", "-Command", "Get-Clipboard"]
        set_cmd = [shell, "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"]
        return (get_cmd, set_cmd)
    if shutil.which("wl-paste") and shutil.which("wl-copy"):
        return (["wl-paste", "--no-newline"], ["wl-copy"])
    if shutil.which("xclip"):
        return (["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard"])
    if shutil.which("xsel"):
        return (["xsel", "--clipboard", "--output"], ["xsel", "--clipboard", "--input"])
    raise click.ClickException(
        "Clipboard helper not found. Install wl-clipboard, xclip, xsel, or use Termux/Windows/macOS clipboard tools."
    )


def get_clipboard_text() -> str:
    """Read clipboard text for major platforms (Termux/Windows/Linux/macOS)."""
    get_cmd, _ = _clipboard_backend()
    return _run_clipboard_command(get_cmd)


def set_clipboard_text(text: str) -> None:
    """Write text to the clipboard for major platforms (Termux/Windows/Linux/macOS)."""
    _, set_cmd = _clipboard_backend()
    if set_cmd and set_cmd[0] in ("wl-copy", "xclip", "xsel"):
        _run_clipboard_command_detached(set_cmd, input_text=text)
    else:
        _run_clipboard_command(set_cmd, input_text=text)


@click.group()
def str_cli():
    """Text processing helpers.

    Examples:
        pystr search ./src "TODO"
        pystr search . "error" -i -e log --stats
        pystr search . --tag email --tag ip
        pystr replace ./src "foo" "bar" -e py --yes
        pystr replace . "(\\d+)" "[\\1]" --regex --dry-run
        pystr search "token" --text "token=abcd"
        echo "hello world" | pystr search "world" --stdin
        pystr clip-search "token" --ignore-case
        pystr clip-replace "foo" "bar" --yes
    """


@str_cli.command("search")
@click.argument("path_or_query", type=str)
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
        _emit_text_search_results(label_value, text_value, pattern, verbose, count, stats, only_matches)
        return

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
            with open(file_path, "r", encoding=encoding, errors=errors) as handle:
                total, matches, values = _collect_line_hits(
                    handle,
                    pattern,
                    capture_lines=verbose > 0 and not only_matches,
                    capture_values=only_matches,
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
        if only_matches:
            for value in values:
                click.echo(value)
        elif verbose > 0:
            for match in matches:
                click.echo(f"{formatted_path}:{match.line_no}: {match.line}")
        elif count:
            click.echo(f"{formatted_path}:{total}")
        else:
            click.echo(formatted_path)

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


if __name__ == "__main__":
    str_cli()
