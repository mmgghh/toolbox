"""File and directory management commands (``pyfm``).

Every command that changes the filesystem accepts ``--dry-run`` and reports
exactly what it would do first, because these operations move real files
around and are not undoable.
"""

from __future__ import annotations

import math
import os
import random
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

import click
import requests

from pytoolbox.core import console
from pytoolbox.core.fs import (
    file_hash,
    get_size,
    human_bytes,
    iter_files,
    matching_entries,
    normalize_extensions,
    unique_path,
)
from pytoolbox.core.options import (
    CONTEXT_SETTINGS,
    AliasedGroup,
    json_option,
    verbose_option,
    version_option,
    yes_option,
)
from pytoolbox.data import PATTERNS, sentences

#: Number of files that must differ in size before hashing is worth it.
DUPLICATE_MIN_GROUP = 2

ORGANIZE_MODES = ("ext", "date", "name")


def compile_find_pattern(find: str) -> re.Pattern:
    """Compile a regex, expanding the bundled ``<UUID4>``/``<DOMAIN_PORT>`` shortcuts."""
    try:
        return re.compile(PATTERNS.get(find) or find)
    except re.error as exc:
        raise click.ClickException(f"{find!r} is not a valid regex pattern: {exc}") from exc


def resolve_destination(destination: Optional[Path], fallback: Path) -> Path:
    """Return ``destination`` when given, else ``fallback``."""
    return destination or fallback


def mkdirs(
    start_from: int,
    n_partition: int,
    destination: Path,
    name_prefix: str,
    dry_run: bool = False,
) -> dict[int, Path]:
    """Create ``n_partition`` numbered directories and return them by index."""
    dir_number_length = len(str(start_from + n_partition - 1))
    dirs: dict[int, Path] = {}
    for i in range(start_from, start_from + n_partition):
        current_dir = destination / f'{name_prefix}-{str(i).rjust(dir_number_length, "0")}'
        if current_dir.exists() and not dry_run:
            # Roll back the ones we just made so a failed run leaves no litter.
            for created in dirs.values():
                created.rmdir()
            raise click.ClickException(
                f"Directory {current_dir} already exists. Use a different --dir-prefix."
            )
        if not dry_run:
            current_dir.mkdir(parents=True)
        dirs[i] = current_dir
    return dirs


def _move(source: Path, destination: Path, dry_run: bool, verbose: int, copy: bool = False) -> None:
    verb = "copy" if copy else "move"
    console.info(f"{verb}: {source} -> {destination}", verbose, threshold=2)
    if dry_run:
        return
    if copy:
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    else:
        shutil.move(str(source), str(destination))


def split_based_on_count(
    dirs: dict[int, Path],
    files_or_dirs: list[Path],
    verbose: int,
    dry_run: bool = False,
    copy: bool = False,
) -> int:
    """Distribute entries evenly by count. Returns the number moved."""
    n_partition = len(dirs)
    files_moved = 0
    per_dir, residual = divmod(len(files_or_dirs), n_partition)

    remaining = list(files_or_dirs)
    for offset, index in enumerate(sorted(dirs)):
        take = per_dir + (1 if offset < residual else 0)
        for entry in remaining[:take]:
            _move(entry, dirs[index] / entry.name, dry_run, verbose, copy)
            files_moved += 1
        remaining = remaining[take:]
    return files_moved


def split_based_on_size(
    dirs: dict[int, Path],
    files_or_dirs: list[Path],
    verbose: int,
    dry_run: bool = False,
    copy: bool = False,
) -> int:
    """Distribute entries so the directories end up similar in total size.

    Greedy largest-first bin packing: repeatedly place the biggest remaining
    entry into whichever directory is currently smallest.
    """
    sized = sorted(((f, get_size(f)) for f in files_or_dirs), key=lambda x: x[1], reverse=True)
    dir_sizes = dict.fromkeys(dirs, 0)
    plan: dict[int, list[Path]] = {index: [] for index in dirs}

    for entry, size in sized:
        target = min(dir_sizes, key=lambda index: dir_sizes[index])
        plan[target].append(entry)
        dir_sizes[target] += size

    files_moved = 0
    for index, entries in plan.items():
        for entry in entries:
            _move(entry, dirs[index] / entry.name, dry_run, verbose, copy)
            files_moved += 1
        console.info(f"{dirs[index].name}: {human_bytes(dir_sizes[index])}", verbose, threshold=1)
    return files_moved


def split_based_on_dir_size(
    dirs_destination: Path,
    dir_prefix: str,
    files_or_dirs: list[Path],
    directory_size_mb: int,
    verbose: int,
    dry_run: bool = False,
    copy: bool = False,
    threshold: float = 0.05,
) -> int:
    """Fill directories up to roughly ``directory_size_mb`` megabytes each."""
    sized = sorted(((f, get_size(f)) for f in files_or_dirs), key=lambda x: x[1], reverse=True)
    per_dir_size = directory_size_mb * 1000 ** 2

    dirs = mkdirs(1, 1, dirs_destination, dir_prefix, dry_run)
    dir_sizes = dict.fromkeys(dirs, 0)
    plan: dict[int, list[Path]] = {index: [] for index in dirs}

    def new_target() -> int:
        index, new_dir = next(iter(mkdirs(len(dirs) + 1, 1, dirs_destination, dir_prefix, dry_run).items()))
        dirs[index] = new_dir
        dir_sizes[index] = 0
        plan[index] = []
        return index

    for entry, size in sized:
        target = min(dir_sizes, key=lambda index: dir_sizes[index])
        space = per_dir_size - dir_sizes[target]
        if size <= per_dir_size:
            # Allow a small overshoot rather than opening a new directory for
            # an entry that almost fits.
            if size > space + (threshold * per_dir_size):
                target = new_target()
        elif dir_sizes[target] > 0:
            # An oversized entry gets a directory to itself.
            target = new_target()
        plan[target].append(entry)
        dir_sizes[target] += size

    files_moved = 0
    for index, entries in plan.items():
        for entry in entries:
            _move(entry, dirs[index] / entry.name, dry_run, verbose, copy)
            files_moved += 1
        console.info(f"{dirs[index].name}: {human_bytes(dir_sizes[index])}", verbose, threshold=1)
    return files_moved


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


@click.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
@version_option
def file_management() -> None:
    """Files and directories: split, merge, rename, deduplicate, organize.

    \b
    Examples:
      pyfm partition -s ./photos --split-size 700 --dry-run
      pyfm merge -s ./seasons -d ./all --overwrite keep-both
      pyfm batch-rename -d ./downloads -f ' ' -r '_' -v
      pyfm duplicates ./photos --json
      pyfm organize ./downloads --by ext
    """


@file_management.command()
@click.option("--pattern", default=".*", help="Regex selecting entries by name (default: everything).")
@click.option("--dir-prefix", default="part", show_default=True, help="Prefix for the created directories.")
@click.option(
    "--split-based-on",
    type=click.Choice(["count", "size"], case_sensitive=False),
    help="With --partitions, balance directories by file count or by total size.",
)
@click.option("-c", "--split-count", type=click.IntRange(1), help="Put about this many entries in each directory.")
@click.option("--split-size", type=click.IntRange(1), help="Fill each directory with about this many megabytes.")
@click.option("-n", "--partitions", type=click.IntRange(2), help="Create exactly this many directories.")
@click.option(
    "-s",
    "--source",
    required=True,
    prompt=True,
    type=click.Path(exists=True, file_okay=False, readable=True, path_type=Path),
    help="Directory whose direct children are partitioned.",
)
@click.option(
    "-d",
    "--destination",
    type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
    help="Where to create the partition directories (default: the source).",
)
@click.option("--copy", "copy_files", is_flag=True, help="Copy instead of moving.")
@click.option("--dry-run", is_flag=True, help="Show what would happen without changing anything.")
@verbose_option
def partition(
    pattern: str,
    dir_prefix: str,
    split_based_on: Optional[Literal["count", "size"]],
    split_count: Optional[int],
    split_size: Optional[int],
    partitions: Optional[int],
    source: Path,
    destination: Optional[Path],
    copy_files: bool,
    dry_run: bool,
    verbose: int,
) -> None:
    """Split a directory's contents into numbered subdirectories.

    \b
    Choose exactly one of --partitions, --split-count or --split-size.
    Note that -n here means --partitions (kept for compatibility); use the
    long --dry-run flag for a preview.

    \b
    Examples:
      pyfm partition -n 5 -s ./data --split-based-on size
      pyfm partition --split-size 700 -s ./photos --dir-prefix disc -v
      pyfm partition --split-count 100 -s ./photos --pattern '.*\\.(jpg|png)$'
    """
    chosen = sum(param is not None for param in (split_size, split_count, partitions))
    if chosen != 1:
        raise click.ClickException(
            "Provide exactly one of --split-count, --split-size or --partitions."
        )
    if partitions and split_based_on is None:
        split_based_on = click.prompt(
            "Balance the partitions by file count or by total size?",
            type=click.Choice(["count", "size"], case_sensitive=False),
        )

    destination = resolve_destination(destination, source)
    console.dry_run_notice(dry_run)

    # Snapshot the entries before creating partition directories, otherwise the
    # new directories are picked up as input when destination == source.
    entries = matching_entries(source, pattern)
    if not entries:
        console.result(f"No entries in {source} match {pattern!r}.")
        return

    if partitions:
        dirs = mkdirs(1, partitions, destination, dir_prefix, dry_run)
        if split_based_on == "count":
            moved = split_based_on_count(dirs, entries, verbose, dry_run, copy_files)
        else:
            moved = split_based_on_size(dirs, entries, verbose, dry_run, copy_files)
    elif split_count:
        dirs = mkdirs(1, math.ceil(len(entries) / split_count), destination, dir_prefix, dry_run)
        moved = 0
        remaining = list(entries)
        for index in sorted(dirs):
            for entry in remaining[:split_count]:
                _move(entry, dirs[index] / entry.name, dry_run, verbose, copy_files)
                moved += 1
            remaining = remaining[split_count:]
    else:
        moved = split_based_on_dir_size(
            destination, dir_prefix, entries, split_size or 1, verbose, dry_run, copy_files
        )

    verb = "would be distributed" if dry_run else "distributed"
    console.result(f"{console.plural(moved, 'entry', 'entries')} {verb} into {destination}.")


@file_management.command()
@click.option("--file-pattern", default=".*", help="Regex selecting files to move, by filename.")
@click.option("--dir-pattern", default=".*", help="Regex selecting directories to descend into, by name.")
@click.option(
    "-s",
    "--source",
    required=True,
    prompt=True,
    type=click.Path(exists=True, file_okay=False, readable=True, path_type=Path),
    help="Directory tree to collect files from.",
)
@click.option(
    "-d",
    "--destination",
    required=True,
    prompt=True,
    type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
    help="Directory that receives all the files.",
)
@click.option(
    "--overwrite",
    type=click.Choice(["yes", "no", "same-size", "keep-both"], case_sensitive=False),
    default="keep-both",
    show_default=True,
    help="What to do when a file of the same name already exists.",
)
@click.option("--copy", "copy_files", is_flag=True, help="Copy instead of moving.")
@click.option("--keep-empty-dirs", is_flag=True, help="Do not remove source directories left empty.")
@click.option("-n", "--dry-run", is_flag=True, help="Show what would happen without changing anything.")
@verbose_option
def merge(
    file_pattern: str,
    dir_pattern: str,
    source: Path,
    destination: Path,
    overwrite: str,
    copy_files: bool,
    keep_empty_dirs: bool,
    dry_run: bool,
    verbose: int,
) -> None:
    """Flatten a directory tree into one directory.

    \b
    Examples:
      pyfm merge -s ./shows -d ./flat --file-pattern '.*\\.mp4$'
      pyfm merge -s ./source -d ./dest --overwrite same-size -v
      pyfm merge -s ./a -d ./b --dry-run
    """
    console.dry_run_notice(dry_run)
    source_abs = source.absolute()
    destination_abs = destination.absolute()
    files_moved = 0
    skipped = 0

    for root, _, files in os.walk(source_abs):
        root_path = Path(root)
        # Never harvest files out of the destination back into itself.
        if root_path == destination_abs:
            continue
        if not re.search(dir_pattern, root_path.name):
            continue
        for name in sorted(files):
            if not re.search(file_pattern, name):
                continue
            src = root_path / name
            target = destination_abs / name
            if target.exists():
                if overwrite == "no":
                    skipped += 1
                    continue
                if overwrite == "same-size" and get_size(target) != get_size(src):
                    skipped += 1
                    continue
                if overwrite in ("yes", "same-size") and target.is_file():
                    console.info(f"replace: {target}", verbose, threshold=2)
                    if not dry_run:
                        target.unlink()
                else:
                    target = unique_path(target)
            _move(src, target, dry_run, verbose, copy_files)
            files_moved += 1

    # Only tidy up directories this run actually emptied. A merge whose
    # pattern matched nothing should leave the source tree exactly as it was.
    if files_moved and not keep_empty_dirs and not copy_files and not dry_run:
        _prune_empty_dirs(source_abs, verbose)

    console.result(
        f"{console.plural(files_moved, 'file')} {'would be ' if dry_run else ''}merged into {destination_abs}."
    )
    if skipped:
        console.result(f"{console.plural(skipped, 'file')} skipped because of name collisions.")


def _prune_empty_dirs(root: Path, verbose: int = 0) -> int:
    """Remove empty directories under ``root`` (never ``root`` itself)."""
    removed = 0
    for current, _, _ in os.walk(root, topdown=False):
        path = Path(current)
        if path == root:
            continue
        try:
            if not any(path.iterdir()):
                path.rmdir()
                removed += 1
                console.info(f"removed empty {path}", verbose, threshold=2)
        except OSError:
            continue
    return removed


@file_management.command("batch-find-replace")
@click.option(
    "-d",
    "--dir",
    "directory",
    type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
    required=True,
    prompt=True,
    help="Directory to scan.",
)
@click.option(
    "-x",
    "--extension",
    multiple=True,
    default=["txt"],
    show_default=True,
    help="File extensions to include, repeatable.",
)
@click.option(
    "-f",
    "--find",
    required=True,
    prompt=True,
    help="Python regex. <UUID4> and <DOMAIN_PORT> expand to bundled patterns.",
)
@click.option("-r", "--replace", required=True, prompt=True, help="Replacement string (backrefs supported).")
@click.option("-R", "--recursive", is_flag=True, help="Descend into subdirectories.")
@click.option("-n", "--dry-run", is_flag=True, help="Report matches without writing.")
@verbose_option
def batch_find_replace(
    directory: Path,
    extension: tuple[str, ...],
    find: str,
    replace: str,
    recursive: bool,
    dry_run: bool,
    verbose: int,
) -> None:
    """Regex find/replace across files with the given extensions.

    \b
    Scans only the directory's direct children unless -R is given.

    \b
    Examples:
      pyfm batch-find-replace -d ./docs -x md -x txt -f foo -r bar -v
      pyfm batch-find-replace -d ./cfg -x env -f '<DOMAIN_PORT>' -r 'example.com:443' -n
      pyfm batch-find-replace -d ./src -x py -f 'old_name' -r 'new_name' -R
    """
    pattern = compile_find_pattern(find)
    extensions = normalize_extensions(extension)
    console.dry_run_notice(dry_run)

    total_replacements = 0
    files_changed = 0
    for file_path in iter_files(directory, depth=None if recursive else 0, extensions=extensions):
        try:
            content = file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            console.warn(f"skipping {file_path}: {exc}")
            continue
        new_content, count = pattern.subn(replace, content)
        if count:
            files_changed += 1
            total_replacements += count
            console.info(f"{file_path}: {count}", verbose, threshold=1)
            if not dry_run:
                file_path.write_text(new_content, encoding="utf-8")

    verb = "would change" if dry_run else "changed"
    console.result(f"{verb} {console.plural(files_changed, 'file')}, {total_replacements} replacements.")


@file_management.command("batch-rename")
@click.option(
    "-d",
    "--dir",
    "directory",
    type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
    required=True,
    help="Directory whose entries are renamed.",
)
@click.option(
    "-f",
    "--find",
    required=True,
    prompt=True,
    help="Python regex. <UUID4> and <DOMAIN_PORT> expand to bundled patterns.",
)
@click.option("-r", "--replace", required=True, prompt=True, help="Replacement string (backrefs supported).")
@click.option("--include-dirs", is_flag=True, help="Rename directories as well as files.")
@click.option("--exclude-files", is_flag=True, help="Rename only directories.")
@click.option("-D", "--depth", type=click.IntRange(0), default=0, show_default=True, help="Extra levels to descend.")
@click.option("-n", "--dry-run", is_flag=True, help="Show the new names without renaming.")
@verbose_option
def batch_rename(
    directory: Path,
    find: str,
    replace: str,
    include_dirs: bool,
    exclude_files: bool,
    depth: int,
    dry_run: bool,
    verbose: int,
) -> None:
    """Rename files and directories by regex.

    \b
    Examples:
      pyfm batch-rename -d ./downloads -f ' ' -r '_' -v
      pyfm batch-rename -d ./archive -f '2024' -r '2025' --include-dirs -D 2
      pyfm batch-rename -d . -f '^IMG_' -r 'photo-' --dry-run
    """
    pattern = compile_find_pattern(find)
    console.dry_run_notice(dry_run)
    if exclude_files:
        include_dirs = True

    renamed = 0
    conflicts = 0
    # Deepest level first: renaming a parent before its children would
    # invalidate the child paths we already collected.
    for level in range(depth, -1, -1):
        for path in sorted(directory.glob(f'{"*/" * level}*')):
            if path.is_dir() and not include_dirs:
                continue
            if path.is_file() and exclude_files:
                continue
            new_name, count = pattern.subn(replace, path.name)
            if not count or new_name == path.name:
                continue
            target = path.with_name(new_name)
            if target.exists():
                console.warn(f"skipping {path}: {new_name} already exists")
                conflicts += 1
                continue
            console.info(f"{path.name} -> {new_name}", verbose, threshold=1)
            if not dry_run:
                path.rename(target)
            renamed += 1

    verb = "would rename" if dry_run else "renamed"
    console.result(f"{verb} {console.plural(renamed, 'entry', 'entries')}.")
    if conflicts:
        console.result(f"{console.plural(conflicts, 'entry', 'entries')} skipped due to name conflicts.")


@file_management.command("generate-text-file")
@click.option(
    "-d",
    "--directory",
    type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
    prompt=True,
    help="Directory to write the files into.",
)
@click.option("-n", "--num_files", "--num-files", "num_files", type=click.IntRange(1), prompt=True, help="How many files to create.")
@click.option(
    "-l",
    "--num_lines",
    "--num-lines",
    "num_lines",
    type=click.IntRange(0),
    default=None,
    help="Lines per file (default: a random 0-100).",
)
@click.option("-p", "--name_prefix", "--name-prefix", "name_prefix", default="file", show_default=True, help="File name prefix.")
@verbose_option
def generate_text_file(
    directory: Path, num_files: int, num_lines: Optional[int], name_prefix: str, verbose: int
) -> None:
    """Create text files filled with random sentences, for testing.

    \b
    Examples:
      pyfm generate-text-file -d ./tmp -n 20 -l 50 -p sample -v
      pyfm generate-text-file -d ./tmp -n 5
    """
    for index in range(1, num_files + 1):
        file_name = f"{name_prefix}-{index}.txt"
        wanted = random.randint(0, 100) if num_lines is None else num_lines
        content: list[str] = []
        while len(content) < wanted:
            content += random.sample(sentences, k=min(wanted - len(content), len(sentences)))
        (directory / file_name).write_text("\n".join(content), encoding="utf-8")
        console.info(f"{file_name}: {wanted} lines", verbose, threshold=2)
    console.result(f"Generated {console.plural(num_files, 'file')} in {directory}.")


@file_management.command("extract-links")
@click.option("--pattern", default=".*", help="Keep only links matching this regex.")
@click.option("-s", "--source", required=True, prompt=True, help="URL, or path to a text/HTML file.")
@click.option(
    "-d",
    "--destination",
    type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
    help="Directory for links.txt (default: the working directory).",
)
@click.option("-o", "--output", type=click.Path(dir_okay=False, path_type=Path), help="Write to this file instead.")
@click.option("--overwrite", is_flag=True, help="Replace the output file instead of appending.")
@click.option("--unique", is_flag=True, help="Drop duplicate links.")
@click.option("--no-filter", is_flag=True, help="Keep asset and bare-domain links that are filtered out by default.")
@click.option("--stdout", "to_stdout", is_flag=True, help="Print links instead of writing a file.")
@verbose_option
def extract_links(
    pattern: str,
    source: str,
    destination: Optional[Path],
    output: Optional[Path],
    overwrite: bool,
    unique: bool,
    no_filter: bool,
    to_stdout: bool,
    verbose: int,
) -> None:
    """Pull http(s) links out of a file or web page.

    \b
    Examples:
      pyfm extract-links -s ./page.html -v
      pyfm extract-links -s 'https://example.com' --pattern '^https://example\\.com' --overwrite
      pyfm extract-links -s ./page.html --stdout --unique
    """
    match_pattern = re.compile(pattern)

    if source.startswith(("http://", "https://")):
        try:
            response = requests.get(source, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise click.ClickException(f"Could not fetch {source}: {exc}") from exc
        content = response.text
    else:
        source_path = Path(source)
        if not (source_path.is_file() and os.access(source_path, os.R_OK)):
            raise click.ClickException(f"{source} is not a readable file.")
        content = source_path.read_text(encoding="utf-8", errors="replace")

    links = re.findall(r'(https?://[^"\'\s<>)\]]+)', content)
    # By default drop stylesheet/script assets and bare domain roots, which are
    # rarely what someone extracting links is after.
    exclude_pattern = re.compile(r"^.*((\.(js|css|html|org|com|ir)(\?.*)?)|/)$")
    links = [
        link
        for link in links
        if match_pattern.match(link) and (no_filter or not exclude_pattern.match(link))
    ]
    if unique:
        links = list(dict.fromkeys(links))

    if to_stdout:
        for link in links:
            console.result(link)
        console.info(f"{len(links)} links from {source}", verbose, threshold=1)
        return

    output_file = output or resolve_destination(destination, Path.cwd()) / "links.txt"
    if overwrite:
        output_file.unlink(missing_ok=True)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("a", encoding="utf-8") as handle:
        for link in links:
            handle.write(link + "\n")

    console.result(f"Extracted {console.plural(len(links), 'link')} from {source} to {output_file}.")


@file_management.command("file-find-replace")
@click.option(
    "-p",
    "--path",
    "file_path",
    type=click.Path(exists=True, dir_okay=False, readable=True, writable=True, path_type=Path),
    required=True,
    prompt=True,
    help="File to edit.",
)
@click.option("-f", "--find", required=True, prompt=True, help="Literal string to search for (not a regex).")
@click.option("-r", "--replace", required=True, prompt=True, help="Replacement string.")
@click.option("-n", "--dry-run", is_flag=True, help="Report matches without writing.")
@verbose_option
def file_find_replace(file_path: Path, find: str, replace: str, dry_run: bool, verbose: int) -> None:
    """Replace a literal string in one file.

    \b
    Examples:
      pyfm file-find-replace -p ./notes.txt -f old-value -r new-value -v
      pyfm file-find-replace -p ./config -f 'localhost' -r '0.0.0.0' --dry-run
    """
    if find == "":
        raise click.ClickException("--find must not be empty.")
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise click.ClickException(f"Could not read {file_path}: {exc}") from exc

    occurrences = content.count(find)
    if occurrences == 0:
        console.result(f"No matches in {file_path}.")
        return

    if not dry_run:
        try:
            file_path.write_text(content.replace(find, replace), encoding="utf-8")
        except OSError as exc:
            raise click.ClickException(f"Could not write {file_path}: {exc}") from exc

    verb = "would replace" if dry_run else "replaced"
    console.result(f"{verb} {occurrences} occurrence(s) in {file_path}.")
    console.info(f"{find!r} -> {replace!r}", verbose, threshold=1)


@file_management.command()
@click.argument(
    "directory",
    default=".",
    type=click.Path(exists=True, file_okay=False, readable=True, path_type=Path),
)
@click.option("-x", "--extension", multiple=True, help="Only consider these extensions.")
@click.option("--min-size", type=int, default=1, show_default=True, help="Ignore files smaller than this many bytes.")
@click.option("--hidden", is_flag=True, help="Include hidden files.")
@click.option("--delete", is_flag=True, help="Delete every copy but the first of each group.")
@click.option("-n", "--dry-run", is_flag=True, help="With --delete, list what would be removed.")
@yes_option
@json_option
@verbose_option
def duplicates(
    directory: Path,
    extension: tuple[str, ...],
    min_size: int,
    hidden: bool,
    delete: bool,
    dry_run: bool,
    assume_yes: bool,
    as_json: bool,
    verbose: int,
) -> None:
    """Find files with identical contents.

    \b
    Files are grouped by size first and only hashed when a size is shared, so
    a large tree costs one stat() per file and very few reads.

    \b
    Examples:
      pyfm duplicates ./photos
      pyfm duplicates ./downloads -x pdf --json
      pyfm duplicates ./photos --delete --dry-run
    """
    extensions = normalize_extensions(extension) or None
    by_size: dict[int, list[Path]] = {}
    scanned = 0
    for path in iter_files(directory, include_hidden=hidden, extensions=extensions):
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size < min_size:
            continue
        scanned += 1
        by_size.setdefault(size, []).append(path)

    groups: list[list[Path]] = []
    wasted = 0
    for size, candidates in by_size.items():
        if len(candidates) < DUPLICATE_MIN_GROUP:
            continue
        by_hash: dict[str, list[Path]] = {}
        for path in candidates:
            try:
                digest = file_hash(path)
            except OSError as exc:
                console.warn(f"could not read {path}: {exc}")
                continue
            by_hash.setdefault(digest, []).append(path)
        for paths in by_hash.values():
            if len(paths) >= DUPLICATE_MIN_GROUP:
                groups.append(sorted(paths))
                wasted += size * (len(paths) - 1)

    console.info(f"scanned {console.plural(scanned, 'file')}", verbose, threshold=1)

    if as_json:
        console.emit_json(
            [
                {
                    "size": group[0].stat().st_size,
                    "count": len(group),
                    "files": [str(p) for p in group],
                }
                for group in groups
            ]
        )
    else:
        if not groups:
            console.result("No duplicate files found.")
            return
        for group in groups:
            size = group[0].stat().st_size
            console.result(f"{human_bytes(size)} x{len(group)}")
            for path in group:
                console.result(f"  {path}")
        console.result(f"{console.plural(len(groups), 'duplicate group')}, {human_bytes(wasted)} reclaimable.")

    if not delete or not groups:
        return

    victims = [path for group in groups for path in group[1:]]
    console.dry_run_notice(dry_run)
    if dry_run:
        for path in victims:
            console.result(f"would delete {path}")
        return
    if not console.confirm(f"Delete {console.plural(len(victims), 'file')}?", assume_yes):
        console.result("Aborted.")
        return
    removed = 0
    for path in victims:
        try:
            path.unlink()
            removed += 1
            console.info(f"deleted {path}", verbose, threshold=1)
        except OSError as exc:
            console.warn(f"could not delete {path}: {exc}")
    console.result(f"Deleted {console.plural(removed, 'file')}, freed {human_bytes(wasted)}.")


@file_management.command()
@click.argument(
    "directory",
    default=".",
    type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
)
@click.option(
    "--by",
    type=click.Choice(ORGANIZE_MODES, case_sensitive=False),
    default="ext",
    show_default=True,
    help="Group by file extension, modification date, or first letter of the name.",
)
@click.option(
    "--date-format",
    default="%Y-%m",
    show_default=True,
    help="strftime pattern naming the date directories (with --by date).",
)
@click.option("--pattern", default=".*", help="Only organize entries whose name matches this regex.")
@click.option("--copy", "copy_files", is_flag=True, help="Copy instead of moving.")
@click.option("-n", "--dry-run", is_flag=True, help="Show the plan without moving anything.")
@verbose_option
def organize(
    directory: Path,
    by: str,
    date_format: str,
    pattern: str,
    copy_files: bool,
    dry_run: bool,
    verbose: int,
) -> None:
    """Sort loose files into subdirectories.

    \b
    Only the directory's own files are touched; existing subdirectories are
    left alone, so running it twice is safe.

    \b
    Examples:
      pyfm organize ~/downloads --by ext
      pyfm organize ~/photos --by date --date-format '%Y/%m' --dry-run
      pyfm organize ./books --by name --pattern '\\.epub$'
    """
    console.dry_run_notice(dry_run)
    name_re = re.compile(pattern)
    moved = 0
    buckets: dict[str, int] = {}

    for entry in sorted(directory.iterdir()):
        if not entry.is_file() or not name_re.search(entry.name):
            continue
        if by == "ext":
            bucket = entry.suffix.lstrip(".").lower() or "no-extension"
        elif by == "date":
            bucket = datetime.fromtimestamp(entry.stat().st_mtime).strftime(date_format)
        else:
            first = entry.name[0].upper()
            bucket = first if first.isalnum() else "_other"

        target_dir = directory / bucket
        if not dry_run:
            target_dir.mkdir(parents=True, exist_ok=True)
        target = unique_path(target_dir / entry.name) if target_dir.exists() else target_dir / entry.name
        _move(entry, target, dry_run, verbose, copy_files)
        buckets[bucket] = buckets.get(bucket, 0) + 1
        moved += 1

    for bucket, count in sorted(buckets.items()):
        console.info(f"{bucket}/: {count}", verbose, threshold=1)
    verb = "would organize" if dry_run else "organized"
    console.result(f"{verb} {console.plural(moved, 'file')} into {len(buckets)} director{'y' if len(buckets) == 1 else 'ies'}.")


if __name__ == "__main__":  # pragma: no cover
    file_management()
