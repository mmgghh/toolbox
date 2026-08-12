"""Build rsync argument lists.

``build_rsync_command`` is pure: options in, argument list out. It exists
apart from the CLI because rsync's filter rules are order-sensitive -- first
match wins -- while Click's ``multiple=True`` only preserves order within a
single option, never between two. The order is therefore fixed here and
tested as data.

Patterns are shell globs, not regex. rsync has no brace expansion and fails
silently on a pattern that matches nothing, so ``{a,b}`` is expanded before
rsync sees it and regex-shaped patterns are rejected outright.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import click

#: Per-directory merge filter that reads .gitignore files as exclude rules.
GITIGNORE_FILTER = ":- .gitignore"


@dataclass(frozen=True)
class RsyncOptions:
    """Everything ``build_rsync_command`` needs, already resolved.

    Pattern files are read by the caller and folded into ``exclude`` and
    ``match``, so this stays free of I/O.
    """

    source: str
    destination: str
    ssh_command: str

    # comparison
    ignore_existing: bool = False
    existing: bool = False
    checksum: bool = False
    size_only: bool = False

    # transport
    compress: bool = True
    bwlimit: Optional[str] = None
    sudo: bool = False

    # safety
    delete: bool = False
    mirror: bool = False
    backup_dir: Optional[str] = None
    stats: bool = False
    dry_run: bool = False

    # filtering
    exclude: tuple[str, ...] = field(default_factory=tuple)
    match: tuple[str, ...] = field(default_factory=tuple)
    gitignore: bool = False
    files_from: Optional[str] = None
    min_size: Optional[str] = None
    max_size: Optional[str] = None
    raw_patterns: bool = False

    verbose: int = 0

    @property
    def deletes(self) -> bool:
        """Whether this run removes anything at the destination."""
        return self.delete or self.mirror


# ═══════════════════════════════════════════════════════════════════
# Patterns
# ═══════════════════════════════════════════════════════════════════


def _find_group(pattern: str, start: int) -> Optional[tuple[int, int, list[str]]]:
    """Locate the next balanced ``{...}`` group and split it on top-level commas."""
    i = start
    while i < len(pattern):
        if pattern[i] == "\\":
            i += 2
            continue
        if pattern[i] == "{":
            depth = 0
            part_start = i + 1
            parts: list[str] = []
            j = i
            while j < len(pattern):
                char = pattern[j]
                if char == "\\":
                    j += 2
                    continue
                if char == "{":
                    depth += 1
                elif char == "}":
                    depth -= 1
                    if depth == 0:
                        parts.append(pattern[part_start:j])
                        return i, j, parts
                elif char == "," and depth == 1:
                    parts.append(pattern[part_start:j])
                    part_start = j + 1
                j += 1
            # Unbalanced: no group starts here after all, keep scanning.
        i += 1
    return None


def expand_braces(pattern: str) -> list[str]:
    """Expand ``{a,b}`` into separate patterns, the way a shell would.

    rsync itself cannot do this: a quoted ``*.{jpg,png}`` reaches it intact
    and matches nothing, without a warning. A group that holds no comma --
    ``{single}`` -- is left alone, as is an escaped ``\\{``.
    """
    search = 0
    while True:
        found = _find_group(pattern, search)
        if found is None:
            return [pattern]
        open_at, close_at, parts = found
        if len(parts) < 2:
            search = open_at + 1
            continue
        prefix, suffix = pattern[:open_at], pattern[close_at + 1 :]
        expanded: list[str] = []
        for part in parts:
            expanded.extend(expand_braces(prefix + part + suffix))
        return expanded


def looks_like_regex(pattern: str) -> bool:
    """Whether a pattern is regex-shaped and so would match nothing in rsync.

    Deliberately conservative. ``.*`` is a legitimate glob for dotfiles, ``+``
    and ``{2,3}`` appear in real filenames; only markers that are meaningless
    to a glob count.
    """
    if pattern.startswith("^"):
        return True
    if pattern.endswith("$") and not pattern.endswith("\\$"):
        return True
    if re.search(r"\\[.dws]", pattern):
        return True
    return bool(re.search(r"\([^()]*\|[^()]*\)", pattern))


def _suggest_glob(pattern: str) -> str:
    """Turn a regex-shaped pattern into the glob the user probably meant."""
    text = pattern[1:] if pattern.startswith("^") else pattern
    if text.endswith("$") and not text.endswith("\\$"):
        text = text[:-1]
    text = text.replace(".*", "*").replace("\\.", ".")
    return re.sub(r"\\[dws]", "*", text)


def prepare_patterns(patterns: tuple[str, ...], raw: bool = False) -> list[str]:
    """Validate and brace-expand a run of patterns, preserving their order."""
    if raw:
        return list(patterns)
    prepared: list[str] = []
    for pattern in patterns:
        if looks_like_regex(pattern):
            raise click.ClickException(
                f"{pattern!r} looks like a regex. rsync matches shell globs, so this "
                f"would silently match nothing -- did you mean {_suggest_glob(pattern)!r}? "
                "Pass --raw-patterns to send it through unchanged."
            )
        prepared.extend(expand_braces(pattern))
    return prepared


def read_pattern_file(path: Path) -> list[str]:
    """Read one pattern per line, skipping blank lines and ``#``/``;`` comments."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Could not read {path}: {exc}") from exc
    patterns = []
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", ";")):
            patterns.append(line)
    return patterns


# ═══════════════════════════════════════════════════════════════════
# Command construction
# ═══════════════════════════════════════════════════════════════════


def _validate(options: RsyncOptions) -> None:
    """Reject option pairs that fight, before rsync gets a chance to."""
    if options.checksum and options.size_only:
        raise click.ClickException(
            "--checksum compares file contents and --size-only ignores them. Pick one."
        )
    if options.existing and options.ignore_existing:
        raise click.ClickException(
            "--existing skips new files and --ignore-existing skips existing ones; "
            "together they transfer nothing."
        )
    if options.files_from and (options.match or options.gitignore):
        raise click.ClickException(
            "--files-from already lists exactly what to transfer, so it cannot be "
            "combined with --match/--match-from or --gitignore."
        )


def _filter_rules(options: RsyncOptions) -> list[str]:
    """Filter rules in the fixed order documented in the design.

    Excludes first, so ``-e node_modules --match '*.js'`` skips node_modules.
    Then the ``*/`` include that makes rsync descend into subdirectories, the
    user's matches, and a catch-all exclude.
    """
    rules: list[str] = []
    for pattern in prepare_patterns(options.exclude, options.raw_patterns):
        rules += ["--exclude", pattern]
    if options.gitignore:
        rules += ["--filter", GITIGNORE_FILTER]
    matches = prepare_patterns(options.match, options.raw_patterns)
    if matches:
        rules += ["--include", "*/"]
        for pattern in matches:
            rules += ["--include", pattern]
        # Everything not matched is excluded, and -m drops the empty directory
        # skeleton that "--include '*/'" would otherwise leave behind.
        rules += ["--exclude", "*", "-m"]
    return rules


def build_rsync_command(options: RsyncOptions) -> list[str]:
    """Assemble the full rsync argument list."""
    _validate(options)

    cmd = ["rsync", "-azP" if options.compress else "-aP"]
    cmd += ["-v"] * max(options.verbose, 1)

    cmd.append("--ignore-existing" if options.ignore_existing else "--update")
    if options.existing:
        cmd.append("--existing")
    if options.checksum:
        cmd.append("--checksum")
    if options.size_only:
        cmd.append("--size-only")

    if options.deletes:
        cmd.append("--delete")
    if options.mirror:
        cmd.append("--delete-excluded")
    if options.backup_dir:
        cmd += ["--backup", f"--backup-dir={options.backup_dir}"]

    if options.bwlimit:
        cmd.append(f"--bwlimit={options.bwlimit}")
    if options.min_size:
        cmd.append(f"--min-size={options.min_size}")
    if options.max_size:
        cmd.append(f"--max-size={options.max_size}")
    if options.stats:
        cmd.append("--stats")
    if options.dry_run:
        cmd.append("--dry-run")
    if options.sudo:
        cmd.append("--rsync-path=sudo rsync")

    cmd += ["-e", options.ssh_command]
    if options.files_from:
        cmd.append(f"--files-from={options.files_from}")
    cmd += _filter_rules(options)
    cmd += [options.source, options.destination]
    return cmd
