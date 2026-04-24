"""File management utilities and CLI commands."""

# pylint: disable=line-too-long

import math
import os
import random
import re
import shutil
import sys
from pathlib import Path
from string import ascii_letters
from typing import Literal, Optional

import click
import requests

from pytoolbox.data import sentences, PATTERNS


def get_size(path: Path) -> int:
    """Return total size in bytes for a file or directory tree."""
    if path.is_file():
        return os.path.getsize(path)
    total_size = 0
    for dir_path, _, filenames in os.walk(path):
        for f in filenames:
            fp = os.path.join(dir_path, f)
            # skip if it is symbolic link
            if not os.path.islink(fp):
                total_size += os.path.getsize(fp)

    return total_size


def compile_find_pattern(find: str) -> Optional[re.Pattern]:
    """Compile a regex pattern with support for predefined shortcuts."""
    try:
        return re.compile(PATTERNS.get(find) or find)
    except re.error as exc:
        click.echo(f'{find} is not a valid regex pattern!', err=True)
        click.echo(repr(exc), err=True)
        return None


def matching_entries(source: Path, pattern: str) -> list[Path]:
    """Return source entries whose names match the regex pattern."""
    return [entry for entry in source.iterdir() if re.search(pattern, entry.name)]


def resolve_destination(destination: Optional[Path], fallback: Path) -> Path:
    """Return destination if provided; otherwise return fallback."""
    return destination or fallback


def mkdirs(
        start_from: int, n_partition: int,
        destination: Path, name_prefix: str
) -> dict[int, Path]:
    """
    Returns: A dict of index and corresponding directory Path.
    """
    dir_number_length = len(str(start_from + n_partition - 1))
    dirs = {}
    # Create n new directories with prefix
    try:
        for i in range(start_from, start_from + n_partition):
            current_dir = destination / f'{name_prefix}-{str(i).rjust(dir_number_length, "0")}'
            current_dir.mkdir()
            dirs[i] = current_dir
    except FileExistsError as e:
        for d in dirs.values():
            d.rmdir()
        click.echo(
            f'Directory {e.filename} already exists! '
            f'Use a unique prefix.', err=True
        )
        sys.exit(1)

    return dirs


def split_based_on_count(dirs: dict[int, Path], files_or_dirs: list[Path], verbose: int):
    """Split files into directories based on count."""
    start_from = min(dirs.keys())
    n_partition = len(dirs)
    # Move files into directories
    files_moved = 0
    # Calculate number of files and files per directory
    files_per_dir = len(files_or_dirs) // n_partition
    residual = len(files_or_dirs) % n_partition

    for i in range(start_from, start_from + n_partition):
        # Move files into directory
        files_per_current_dir = files_per_dir + (1 if i <= residual else 0)
        files_to_move = files_or_dirs[:files_per_current_dir]
        for file in files_to_move:
            shutil.move(file, dirs[i])

        # Update files_moved variable
        files_moved += len(files_to_move)

        # Remove moved files from list of files
        files_or_dirs = files_or_dirs[files_per_current_dir:]

    if verbose > 0:
        click.echo(f"Total number of files/dirs moved: {files_moved}")


def split_based_on_size(dirs: dict[int, Path], files_or_dirs: list[Path], verbose: int):
    """Split files into directories based on size."""
    files_moved = 0
    sorted_files = sorted(
        [(f, get_size(f)) for f in files_or_dirs],
        key=lambda x: x[1],
        reverse=True
    )
    dirs_size = {k: 0 for k in dirs.keys()}
    partitions = {k: [] for k in dirs.keys()}
    for (file_or_dir, size) in sorted_files:
        target = list(dirs_size.keys())[0]
        partitions[target].append(file_or_dir)
        dirs_size[target] += size
        dirs_size = {k: v for k, v in sorted(dirs_size.items(), key=lambda item: item[1])}

    for i, files in partitions.items():
        current_dir = dirs[i]
        for f in files:
            shutil.move(f, current_dir)

        # Update files_moved variable
        files_moved += len(files)

    if verbose > 0:
        click.echo(f"Total number of files/dirs moved: {files_moved}")


def split_to_n_dir(
        pattern: str, dir_prefix: str, split_based_on: Literal['count', 'size'],
        n_partition: int, source: Path, destination: Path, verbose: int
):
    """Split files into a fixed number of directories."""
    start_from = 1
    # Snapshot matching entries BEFORE creating partition dirs, otherwise the
    # new dirs are picked up by iterdir() when destination == source.
    files_or_dirs = matching_entries(source, pattern)
    dirs = mkdirs(start_from, n_partition, destination, dir_prefix)

    if split_based_on == 'count':
        split_based_on_count(dirs, files_or_dirs, verbose)
    else:
        split_based_on_size(dirs, files_or_dirs, verbose)


def split_based_on_file_count(
        pattern: str, dir_prefix: str, file_count: int,
        source: Path, destination: Path, verbose: int
):
    """Split files into directories by a target file count."""
    start_from = 1
    # Get all files in source directory that match the regex pattern.
    files_or_dirs = matching_entries(source, pattern)
    n_partition = math.ceil(len(files_or_dirs) / file_count)
    dirs = mkdirs(start_from, n_partition, destination, dir_prefix)

    files_moved = 0
    files_per_dir = file_count

    for i, current_dir in dirs.items():
        # Move files into directory
        files_to_move = files_or_dirs[:files_per_dir]
        for file in files_to_move:
            shutil.move(file, current_dir)

        # Update files_moved variable
        files_moved += len(files_to_move)

        # Remove moved files from list of files
        files_or_dirs = files_or_dirs[files_per_dir:]

    if verbose > 0:
        click.echo(f"Total number of files/dirs moved: {files_moved}")


def split_based_on_dir_size(
        pattern: str, dir_prefix: str, directory_size: int,
        source: Path, destination: Path, verbose: int, threshold=0.05
):
    """Split files into directories by approximate size in MB."""

    start_from = 1
    # Get all files in source directory that match the regex pattern.
    files_or_dirs = matching_entries(source, pattern)

    sorted_files = sorted(
        [(f, get_size(f)) for f in files_or_dirs],
        key=lambda x: x[1],
        reverse=True
    )

    dirs = mkdirs(start_from, 1, destination, dir_prefix)
    dirs_size = {k: 0 for k in dirs.keys()}
    per_dir_size = directory_size * 1000 ** 2  # convert to bytes

    partitions = {k: [] for k in dirs.keys()}

    def new_target():
        idx, new_dir = list(mkdirs(start_from + len(dirs), 1, destination, dir_prefix).items())[0]
        dirs[idx] = new_dir
        dirs_size[idx] = 0
        partitions[idx] = []
        return idx

    for (file_or_dir, size) in sorted_files:
        target = list(dirs_size.keys())[0]
        space = per_dir_size - dirs_size[target]
        if size <= per_dir_size:
            if size > space + (threshold * per_dir_size):
                target = new_target()
        elif dirs_size[target] > 0:
            target = new_target()

        partitions[target].append(file_or_dir)
        dirs_size[target] += size
        dirs_size = {k: v for k, v in sorted(dirs_size.items(), key=lambda item: item[1])}

    files_moved = 0
    for i, files in partitions.items():
        current_dir = dirs[i]
        for f in files:
            shutil.move(f, current_dir)

        # Update files_moved variable
        files_moved += len(files)

    if verbose > 0:
        click.echo(f"Total number of files/dirs moved: {files_moved}")


@click.command()
@click.option('--pattern', default='.*',
              help='Regex to select entries in the source directory (matched against name).')
@click.option('--dir-prefix', default='part',
              help='Prefix for created directories (e.g., part-001, part-002).')
@click.option('--split-based-on',
              type=click.Choice(['count', 'size'], case_sensitive=False),
              help='When splitting by partitions, balance by file count or total size.')
@click.option('-c', '--split-count', type=click.IntRange(1, ),
              help='Approximate number of files per directory.')
@click.option('--split-size', type=click.IntRange(1, ),
              help='Approximate size of each directory in megabytes.')
@click.option('-n', '--partitions', type=click.IntRange(2, ),
              help='Number of partitions to create.')
@click.option('-s', '--source', required=True, prompt=True,
              type=click.Path(exists=True, file_okay=False, readable=True, path_type=Path),
              help='Directory whose contents will be partitioned.')
@click.option('-d', '--destination',
              type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
              help='Destination directory for created partitions (defaults to source).')
@click.option('-v', '--verbose', count=True)
def partition(pattern: str, dir_prefix: str, split_based_on: Literal['count', 'size'],
              split_count: int, split_size: int, partitions: int,
              source: Path, destination: Path, verbose: int):
    """
    Create subdirectories and distribute source contents by count or size.
    """
    if sum(
            param is not None for param in (split_size, split_count, partitions)
    ) != 1:  # only one option is allowed and required
        click.echo(
            'Exactly one of '
            '`--split-count`, '
            '`--split-size`, '
            '`--partitions`'
            ' is required.', err=True
        )
        return
    if partitions and split_based_on is None:
        split_based_on = click.prompt(
            'Split data based on size of files or count of them?',
            type=click.Choice(['count', 'size'], case_sensitive=False),
        )

    destination = resolve_destination(destination, source)

    if partitions:
        split_to_n_dir(
            pattern, dir_prefix, split_based_on,
            partitions, source, destination, verbose
        )
    elif split_count:
        split_based_on_file_count(
            pattern, dir_prefix, split_count, source, destination, verbose
        )
    elif split_size:
        split_based_on_dir_size(
            pattern, dir_prefix, split_size,
            source, destination, verbose
        )


@click.command()
@click.option('--file-pattern', default='.*',
              help='Regex to select files to move (matched against filename).')
@click.option('--dir-pattern', default='.*',
              help='Regex to select directories to scan (matched against directory name).')
@click.option('-s', '--source', required=True, prompt=True,
              type=click.Path(exists=True, file_okay=False, readable=True, path_type=Path),
              help='Root directory to traverse and merge files from.')
@click.option('-d', '--destination', required=True, prompt=True,
              type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path))
@click.option('--overwrite',
              type=click.Choice(['yes', 'no', 'same-size', 'keep-both'], case_sensitive=False),
              help='Collision handling: yes, no, same-size, keep-both.')
@click.option('-v', '--verbose', count=True)
def merge(
        file_pattern: str, dir_pattern: str, source: Path,
        destination: Path, overwrite: str, verbose: int
):
    """
    Merge (move) files from source subdirectories into a destination directory.
    """
    files_moved = 0
    for root, _, files in os.walk(source.absolute()):
        current_dir = root.rpartition('/')[-1]
        if re.search(dir_pattern, current_dir):
            for f in files:
                if re.search(file_pattern, f):
                    file_to_move = Path(root) / f
                    file_in_destination = Path(destination) / f
                    renamed_file = ''
                    if file_in_destination.exists():
                        if overwrite == 'no' or (
                            overwrite == 'same-size'
                            and get_size(file_in_destination) != get_size(file_to_move)
                        ):
                            continue
                        if overwrite == 'yes' and not file_in_destination.is_dir():
                            os.remove(file_in_destination)
                        else:
                            for i in range(1, 10**3):
                                name, ext = os.path.splitext(f)
                                renamed_file = f'{name}({i}){ext}'

                                if not (Path(destination) / renamed_file).exists():
                                    break
                            else:
                                i = "".join(
                                    random.choice(ascii_letters) for _ in range(10)
                                )
                                name, ext = os.path.splitext(f)
                                renamed_file = f'{name}({i}){ext}'
                            if not overwrite == 'keep-both':
                                existing = 'file' if file_in_destination.is_file() else 'directory'
                                choice = click.prompt(
                                    f"Cannot move file {f} to {destination.absolute()} "
                                    f"because a {existing} exists with the same name!\n"
                                    f"1: rename file to <{renamed_file}>\n"
                                    f"2: skip\n",
                                    type=click.Choice(['1', '2']),
                                    err=True, show_choices=False,
                                )
                                if choice == '2':
                                    continue
                    shutil.move(file_to_move, destination / renamed_file)
                    files_moved += 1
                    if verbose > 1:
                        click.echo(f"Moved: {file_to_move}")
    # remove empty subdirectories (bottom-up; never the source root itself)
    source_abs = source.absolute()
    for root, _, _ in os.walk(source_abs, topdown=False):
        root_path = Path(root)
        if root_path == source_abs:
            continue
        if not any(root_path.iterdir()):
            root_path.rmdir()

    if verbose > 0:
        click.echo(f"{files_moved} files merged into {destination.absolute()}.")


@click.command()
@click.option('-d', '--dir', 'directory',
              type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
              required=True, prompt=True, help='Directory to scan.')
@click.option('-x', '--extension', type=click.STRING, multiple=True, default=['txt'],
              help='File extensions to include (repeatable).')
@click.option('-f', '--find', type=click.STRING, required=True, prompt=True,
              help='A Python regex with optional named group support.'
                   'Enter <UUID4> for uuid pattern, and '
                   '<DOMAIN_PORT> for sub.domain:port .e.g. ber.com:443, www.example.co.uk:8080. '
                   '(Use \\g<UUID4> and \\g<DOMAIN_PORT> in replacement string as backrefs if necessary.)')
@click.option('-r', '--replace', type=click.STRING, required=True, prompt=True,
              help='Replacement string (regex backrefs supported).')
@click.option('-v', '--verbose', count=True)
def batch_find_replace(directory: Path, extension: list[str], find: str, replace: str, verbose: int):
    """
    Find and replace text in all files with selected extensions in a directory.
    """
    pattern = compile_find_pattern(find)
    if pattern is None:
        return

    total_num_replacements = 0
    total_files_changed = 0
    for file_path in directory.iterdir():
        if file_path.suffix.lstrip('.') in extension:
            with file_path.open('r+', encoding='utf-8') as file:
                content = file.read()
                new_content, num_replacements = pattern.subn(replace, content)

                if num_replacements:
                    file.seek(0)
                    file.write(new_content)
                    file.truncate()
                    total_files_changed += 1

                if verbose > 1:
                    click.echo(f"{file_path.absolute()} changes: {num_replacements}")
                total_num_replacements += num_replacements
    if verbose:
        click.echo(
            f"{total_files_changed} files changed.\n"
            f"{total_num_replacements} changes have been made."
        )


@click.command()
@click.option('-d', '--dir', 'directory',
              type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
              required=True, help='Directory to rename entries in.')
@click.option('-f', '--find', type=click.STRING, required=True, prompt=True,
              help='A Python regex with optional named group support.'
                   'Enter <UUID4> for uuid pattern, and '
                   '<DOMAIN_PORT> for sub.domain:port .e.g. ber.com:443, www.example.co.uk:8080. '
                   '(Use \\g<UUID4> and \\g<DOMAIN_PORT> in replacement string as backrefs if necessary.)')
@click.option('-r', '--replace', type=click.STRING, required=True, prompt=True,
              help='Replacement string (regex backrefs supported).')
@click.option('--include-dirs', is_flag=True,
              help='Rename directories too.')
@click.option('--exclude-files', is_flag=True,
              help='Skip files; only rename directories.')
@click.option('-D', '--depth', type=click.INT, default=0,
              help='How many levels of subdirectories to process. 0 = direct children only.')
@click.option('-v', '--verbose', count=True)
def batch_rename(
    directory: Path,
    find: str,
    replace: str,
    include_dirs: bool,
    exclude_files: bool,
    depth: int,
    verbose: int,
):
    """
    Rename files and/or directories by applying a regex find/replace to their names.
    """
    pattern = compile_find_pattern(find)
    if pattern is None:
        return

    total_rename = 0

    for level in range(depth, -1, -1):  # loop over all levels from depth to 0
        for file_path in directory.glob(f'{"*/" * level}*'):  # use glob with level parameter
            file_path = Path(file_path)  # convert to Path object
            if include_dirs is False and file_path.is_dir():
                continue
            if exclude_files and file_path.is_file():
                continue
            new_name, num_replacements = pattern.subn(replace, file_path.name)
            original_file_path = file_path.absolute()
            if num_replacements:
                new_path = file_path.with_name(new_name)
                file_path.rename(new_path)

                if verbose >= 1:
                    click.echo(f"{original_file_path} renamed to {new_name}")
                total_rename += 1

    click.echo(f"{total_rename} files/directories renamed.")


@click.command()
@click.option('-d', '--directory', type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
              prompt=True, help='Directory to write files into.')
@click.option('-n', '--num_files', type=click.IntRange(min=1), prompt=True, help='The number of files.')
@click.option('-l', '--num_lines', type=click.IntRange(min=0), default=None,
              help='Number of lines per file. Default is random 0-100.')
@click.option('-p', '--name_prefix', default='file',
              help='Prefix for file names (e.g., report).')
@click.option('-v', '--verbose', count=True,
              help='Use -v for summary, -vv for per-file details.')
def generate_text_file(
        directory: Path, num_files: int, num_lines: Optional[int],
        name_prefix: str, verbose: int
):
    """
    Generate text files filled with random sentences.
    """
    # Create a list of file names based on the name prefix and the number of files
    file_names = [f"{name_prefix}-{i}.txt" for i in range(1, num_files + 1)]
    # Loop through the file names
    for file_name in file_names:
        # Generate a random number of lines if not specified
        if num_lines is None:
            num_sentences = random.randint(0, 100)
        else:
            num_sentences = num_lines
        # Create a list of random sentences from the tuple
        content = []
        while (content_length := len(content)) < num_sentences:
            content += random.sample(sentences, k=min(num_sentences - content_length, len(sentences)))

        # Write the content to the file in the directory
        with open(directory / file_name, 'w', encoding='utf-8') as f:
            f.write('\n'.join(content))

        # Log the file name and the number of lines if verbose level is 2
        if verbose >= 2:
            click.echo(f"Generated {file_name} with {num_sentences} lines.")
    # Log the directory and the number of files if verbose level is 1 or more
    if verbose >= 1:
        click.echo(f"Generated {num_files} files in {directory}.")


@click.command()
@click.option('--pattern', default='.*',
              help='Regex to filter extracted links.')
@click.option('-s', '--source', required=True, prompt=True, type=str,
              help='URL or path to a text/HTML file.')
@click.option('-d', '--destination',
              type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
              help='Directory to write links.txt (defaults to current directory).')
@click.option('--overwrite', is_flag=True,
              help='Overwrite existing links.txt instead of appending.')
@click.option('-v', '--verbose', count=True)
def extract_links(pattern: str, source: str, destination: Path, verbose: int, overwrite: bool):
    """
    Extract links from a file or URL and write them to links.txt in the destination directory.

    If a links.txt file already exists, new extracted links will be appended to the file.
    If a regex pattern is provided, only matching links will be extracted.
    """
    destination = resolve_destination(destination, Path.cwd())

    # Compile the regex pattern
    match_pattern = re.compile(pattern)

    # Check if the source is a url or a file path
    if source.startswith('http'):  # If it is a url
        # Use requests to get the content of the url
        response = requests.get(source, timeout=10)
        if response.status_code == 200:  # If the request is successful
            content = response.text
        else:  # If the request fails
            click.echo(f'Could not get the content of {source}', err=True)
            return
    else:  # If it is a file path
        source = Path(source)  # Convert it to a Path object
        if source.exists() and source.is_file() and os.access(source, os.R_OK):  # If it is a valid file
            # Open the source file and read its content
            with open(source, encoding='utf-8') as f:
                content = f.read()
        else:  # If it is not a valid file
            click.echo(f'{source} is not a valid file path.', err=True)
            return

    # Find all the links in the content
    links = re.findall(r'(https?://[^\"|\'| ]+)', content)

    exclude_pattern = re.compile(r'^.*((\.(js|css|html|org|com|ir)(\?.*)?)|/)$')
    # Filter the links by the pattern
    links = [
        link
        for link in links
        if match_pattern.match(link) and not exclude_pattern.match(link)
    ]

    # Open the destination file in append mode
    output_file = destination / 'links.txt'
    if overwrite:
        output_file.unlink(missing_ok=True)

    with open(output_file, 'a', encoding='utf-8') as f:
        # Write each link in a new line
        for link in links:
            f.write(link + '\n')

    # Print some feedback if verbose is enabled
    if verbose > 0:
        click.echo(f'Extracted {len(links)} links from {source} to {destination / "links.txt"}')


@click.command()
@click.option(
    '-p',
    '--path',
    'file_path',
    type=click.Path(exists=True, dir_okay=False, readable=True, writable=True, path_type=Path),
    required=True,
    prompt=True,
    help='Path to the file to edit.',
)
@click.option('-f', '--find', required=True, prompt=True, help='Literal string to search for (no regex).')
@click.option('-r', '--replace', required=True, prompt=True, help='Replacement string.')
@click.option('-v', '--verbose', count=True)
def file_find_replace(file_path: Path, find: str, replace: str, verbose: int):
    """
    Replace all occurrences of a literal string in a single file.
    """
    if find == '':
        click.echo('Find string must be non-empty.', err=True)
        return

    try:
        content = file_path.read_text(encoding='utf-8')
    except OSError as exc:
        click.echo(f'Could not read {file_path}: {exc}', err=True)
        return

    occurrences = content.count(find)
    if occurrences == 0:
        if verbose:
            click.echo(f'No matches found in {file_path}.')
        return

    new_content = content.replace(find, replace)
    try:
        file_path.write_text(new_content, encoding='utf-8')
    except OSError as exc:
        click.echo(f'Could not write {file_path}: {exc}', err=True)
        return

    if verbose:
        click.echo(f'{occurrences} replacements made in {file_path}.')


@click.group()
def file_management():
    """CLI group for file management commands."""
    pass


for cmd in (
    partition, merge,
    generate_text_file,
    batch_find_replace, batch_rename,
    extract_links, file_find_replace,
):
    file_management.add_command(cmd)


if __name__ == '__main__':
    file_management()
