"""Filesystem helpers shared by ``pyfm`` and ``pystr``."""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
from collections.abc import Iterator, Sequence
from pathlib import Path
from re import Pattern
from typing import Optional

BINARY_SNIFF_BYTES = 2048
HASH_CHUNK_BYTES = 1024 * 1024


def human_bytes(size: float) -> str:
    """Render a byte count as a short human-readable string."""
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    value = float(size)
    for unit in units:
        if abs(value) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"  # pragma: no cover - unreachable, loop always returns


def get_size(path: Path) -> int:
    """Total size in bytes of a file or of everything under a directory."""
    if path.is_file():
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for dir_path, _, filenames in os.walk(path):
        for name in filenames:
            file_path = os.path.join(dir_path, name)
            # Symlinks would double-count their target (or dangle entirely).
            if os.path.islink(file_path):
                continue
            try:
                total += os.path.getsize(file_path)
            except OSError:
                continue
    return total


def file_hash(path: Path, algorithm: str = "sha256") -> str:
    """Hash a file's contents, reading it in chunks."""
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def unique_path(path: Path) -> Path:
    """Return ``path`` if free, else ``name(1).ext``, ``name(2).ext``, ..."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(1, 10_000):
        candidate = path.with_name(f"{stem}({index}){suffix}")
        if not candidate.exists():
            return candidate
    raise OSError(f"Could not find a free filename for {path}")


def is_hidden_name(name: str) -> bool:
    """Whether a path component is a dotfile."""
    return name.startswith(".") and name not in (".", "..")


def matches_any_glob(path: Path, patterns: Sequence[str]) -> bool:
    """Whether ``path`` matches any of the glob ``patterns`` by name or full path."""
    if not patterns:
        return False
    path_posix = path.as_posix()
    return any(
        fnmatch.fnmatch(path.name, pattern) or fnmatch.fnmatch(path_posix, pattern)
        for pattern in patterns
    )


def normalize_extensions(values: Sequence[str]) -> set[str]:
    """Turn ``('py', '.txt,md')`` into ``{'.py', '.txt', '.md'}``."""
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


def is_probably_text(path: Path, max_bytes: int = BINARY_SNIFF_BYTES) -> bool:
    """Heuristic binary check: NUL bytes or many control characters mean binary."""
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


def iter_files(
    root: Path,
    depth: Optional[int] = None,
    include_hidden: bool = False,
    follow_symlinks: bool = False,
    extensions: Optional[set[str]] = None,
    filename_pattern: Optional[Pattern[str]] = None,
    exclude: Sequence[str] = (),
    exclude_dir: Sequence[str] = (),
    max_bytes: Optional[int] = None,
) -> Iterator[Path]:
    """Walk ``root`` yielding files that pass every filter.

    ``root`` may itself be a file, in which case it is yielded (subject to the
    same filters) -- that is what lets callers accept either a file or a
    directory for the same argument.
    """

    def accepted(path: Path) -> bool:
        if extensions and path.suffix.lower() not in extensions:
            return False
        if filename_pattern and not filename_pattern.search(path.name):
            return False
        if not include_hidden and is_hidden_name(path.name):
            return False
        if matches_any_glob(path, exclude):
            return False
        if max_bytes is not None:
            try:
                if path.stat().st_size > max_bytes:
                    return False
            except OSError:
                return False
        return True

    if root.is_file():
        if accepted(root):
            yield root
        return

    if not root.is_dir():
        return

    for current_root, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        rel_depth = len(Path(current_root).relative_to(root).parts)
        if depth is not None and rel_depth >= depth:
            dirnames[:] = []
        if not include_hidden:
            dirnames[:] = [d for d in dirnames if not is_hidden_name(d)]
        if exclude_dir:
            dirnames[:] = [
                d for d in dirnames if not matches_any_glob(Path(current_root) / d, exclude_dir)
            ]
        for filename in sorted(filenames):
            file_path = Path(current_root) / filename
            if accepted(file_path):
                yield file_path


def matching_entries(source: Path, pattern: str) -> list[Path]:
    """Direct children of ``source`` whose name matches the regex ``pattern``."""
    return sorted(
        (entry for entry in source.iterdir() if re.search(pattern, entry.name)),
        key=lambda entry: entry.name,
    )

