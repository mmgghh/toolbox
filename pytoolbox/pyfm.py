import math
import os
import random
import re
import shutil
from pathlib import Path
from string import ascii_letters
from typing import Literal, Optional

import click

from pytoolbox.data import sentences, PATTERNS


def get_size(path: Path) -> int:
    if path.is_file():
        return os.path.getsize(path)
    else:
        total_size = 0
        for dir_path, dir_names, filenames in os.walk(path):
            for f in filenames:
                fp = os.path.join(dir_path, f)
                # skip if it is symbolic link
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)

        return total_size


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
        exit(-1)

    return dirs


def split_based_on_count(dirs: dict[int, Path], files_or_dirs: list[Path], verbose: int):
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
    files_moved = 0
    sored_files = sorted(
        [(f, get_size(f)) for f in files_or_dirs],
        key=lambda x: x[1],
        reverse=True
    )
    dirs_size = {k: 0 for k in dirs.keys()}
    partitions = {k: [] for k in dirs.keys()}
    for (file_or_dir, size) in sored_files:
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
    start_from = 1
    dirs = mkdirs(start_from, n_partition, destination, dir_prefix)
    # Get all files in source directory that match the regex pattern
    files_or_dirs = [f for f in source.iterdir() if re.search(pattern, f.name)]

    if split_based_on == 'count':
        split_based_on_count(dirs, files_or_dirs, verbose)
    else:
        split_based_on_size(dirs, files_or_dirs, verbose)


def split_based_on_file_count(
        pattern: str, dir_prefix: str, file_count: int,
        source: Path, destination: Path, verbose: int
):
    start_from = 1
    # Get all files in source directory that match the regex pattern.
    files_or_dirs = [f for f in source.iterdir() if re.search(pattern, f.name)]
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

    start_from = 1
    # Get all files in source directory that match the regex pattern.
    files_or_dirs = [f for f in source.iterdir() if re.search(pattern, f.name)]

    sored_files = sorted(
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

    for (file_or_dir, size) in sored_files:
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
              help='regular expression to filter only matching files.')
@click.option('--dir-prefix', default='part',
              help='new directories prefix. It will be something like PREFIX-1, PREFIX-2 ...')
@click.option('--split-based-on',
              type=click.Choice(['count', 'size'], case_sensitive=False),
              help='Specifies whether to divide by the number of files or by their size.'
                   'Only effective on --split-percentage and --partitions.')
@click.option('--split-percentage', type=click.FloatRange(.1, 50),
              help='The percentage of each of the partitions.')
@click.option('-c', '--split-count', type=click.IntRange(1, ),
              help='Approximate number of files per directory.')
@click.option('-s', '--split-size', type=click.IntRange(1, ),
              help='Approximate size of each directory in Megabyte.')
@click.option('-n', '--partitions', type=click.IntRange(2, ),
              help='The number of partitions.')
@click.option('-s', '--source', required=True, prompt=True,
              type=click.Path(exists=True, file_okay=False, readable=True, path_type=Path))
@click.option('-d', '--destination',
              type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path))
@click.option('-v', '--verbose', count=True)
def partition(pattern: str, dir_prefix: str, split_based_on: Literal['count', 'size'],
              split_percentage: float, split_count: int, split_size: int, partitions: int,
              source: Path, destination: Path, verbose: int):
    """
    Creates subdirectories within the destination directory and distributes the contents of source directory
    based on the number or size of them.
    """
    if sum(
            bool(param) for param in (split_percentage, split_size, split_count, partitions)
    ) != 1:  # only one option is allowed and required
        click.echo(
            'At least (and only) one of '
            '`split-percentage`, '
            '`split-count`, '
            '`split-size`, '
            '`partitions`'
            ' is required.', err=True
        )
        return
    if split_percentage or partitions:
        if split_based_on is None:
            split_based_on = click.prompt(
                'Split data based on size of files or count of them?',
                type=click.Choice(['count', 'size'], case_sensitive=False),
            )

    if not destination:
        destination = source

    # (split_percentage, split_size, split_count, partitions)
    if partitions:
        return split_to_n_dir(
            pattern, dir_prefix, split_based_on,
            partitions, source, destination, verbose
        )
    if split_count:
        return split_based_on_file_count(
            pattern, dir_prefix, split_count, source, destination, verbose
        )
    if split_size:
        return split_based_on_dir_size(
            pattern, dir_prefix, split_size,
            source, destination, verbose
        )

    click.echo('Not implemented error.', err=True)


@click.command()
@click.option('--file-pattern', default='.*',
              help='regular expression to filter only matching files.')
@click.option('--dir-pattern', default='.*',
              help='regular expression to filter only matching directories.')
@click.option('-s', '--source', required=True, prompt=True,
              type=click.Path(exists=True, file_okay=False, readable=True, path_type=Path),
              help='Root directory to traverse and merge all files in all of its subdirectories.')
@click.option('-d', '--destination', required=True, prompt=True,
              type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path))
@click.option('--overwrite',
              type=click.Choice(['yes', 'no', 'same-size', 'keep-both'], case_sensitive=False),
              help="overwrite files with the same name?")
@click.option('-v', '--verbose', count=True)
def merge(
        file_pattern: str, dir_pattern: str, source: Path,
        destination: Path, overwrite: bool, verbose: int
):
    """
    Merges (moves) the contents of a source directory into a destination directory.
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
    # remove empty directories
    for root, _, files in os.walk(source.absolute()):
        if get_size(Path(root)) == 0:
            shutil.rmtree(root)

    if verbose > 0:
        click.echo(f"{files_moved} files merged into {destination.absolute()}.")


@click.command()
@click.option('-d', '--dir', type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
              required=True, prompt=True, help='A path to an existing directory.')
@click.option('-x', '--extension', type=click.STRING, multiple=True, default=['txt'],
              help='A list of file extensions.')
@click.option('-f', '--find', type=click.STRING, required=True, prompt=True,
              help='A Python regex with optional named group support.'
                   'Enter <UUID4> for uuid pattern, and '
                   '<DOMAIN_PORT> for sub.domain:port .e.g. ber.com:443, www.example.co.uk:8080. '
                   '(Use \\g<UUID4> and \\g<DOMAIN_PORT> in replacement string as backrefs if necessary.)')
@click.option('-r', '--replace', type=click.STRING, required=True, prompt=True,
              help='A replacement string with optional backref support.')
@click.option('-v', '--verbose', count=True)
def batch_find_replace(dir: Path, extension: list[str], find: str, replace: str, verbose: int):
    """
    Finds and replaces all the matching texts with replacement string in all files
    with target extensions in target directory.
    """
    try:
        pattern = re.compile(PATTERNS.get(find) or find)
    except re.error as e:
        click.echo(f'{find} is not a valid regex pattern!')
        click.echo(repr(e), err=True)
        return

    total_num_replacements = 0
    total_files_changed = 0
    for file_path in dir.iterdir():
        if file_path.suffix.lstrip('.') in extension:
            with file_path.open('r+') as file:
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
        click.echo(f"{total_files_changed} files changed.\n{total_num_replacements} changes have been made.")


@click.command()
@click.option('-d', '--dir', type=click.Path(exists=True, file_okay=False, writable=True, path_type=Path),
              required=True, help='A path to an existing directory.')
@click.option('-f', '--find', type=click.STRING, required=True, prompt=True,
              help='A Python regex with optional named group support.'
                   'Enter <UUID4> for uuid pattern, and '
                   '<DOMAIN_PORT> for sub.domain:port .e.g. ber.com:443, www.example.co.uk:8080. '
                   '(Use \\g<UUID4> and \\g<DOMAIN_PORT> in replacement string as backrefs if necessary.)')
@click.option('-r', '--replace', type=click.STRING, required=True, prompt=True,
              help='A replacement string with optional backref support.')
@click.option('--include-dirs', is_flag=True,
              help='If this flag is set, directories also will be renamed.')
@click.option('--exclude-files', is_flag=True,
              help='If this flag is set, files will not be renamed.')
@click.option('-D', '--depth', type=click.INT, default=0,
              help='Specifies how many levels of subtree should be processed. By default only direct files/dirs'
                   'in `/path/to/dir` will be processed. If depth is 1 files/dirs in `path/to/dir/*/` also '
                   'will be processed. If depth is 2 files/dirs in `path/to/dir/*/*/` also will be processed and so on.')
@click.option('-v', '--verbose', count=True)
def batch_rename(dir: Path, find: str, replace: str, include_dirs: bool, exclude_files: bool, depth: int, verbose: int):
    """
    Finds and replaces all matching texts in files/directories name with replacement string
    in target directory and its subdirectories. Directories will not be renamed unless --include-dirs flag is set.
    """
    try:
        pattern = re.compile(PATTERNS.get(find) or find)
    except re.error as e:
        click.echo(f'{find} is not a valid regex pattern!')
        click.echo(repr(e), err=True)
        return

    total_rename = 0

    for level in range(depth, -1, -1):  # loop over all levels from depth to 0
        for file_path in dir.glob(f'{"*/" * level}*'):  # use glob with level parameter
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
              prompt=True, help='A path to an existing directory.')
@click.option('-n', '--num_files', type=click.IntRange(min=1), prompt=True, help='The number of files.')
@click.option('-l', '--num_lines', type=click.IntRange(min=0), default=None,
              help='The number of file lines. By default a random number between 0 and 100 will be used.')
@click.option('-p', '--name_prefix', default='file',
              help='An optional template for file names .e.g: template-n.txt. By default file will be used.')
@click.option('-v', '--verbose', count=True,
              help='If -v is provided then the command logs the files directory full path and the number of files generated. '
                   'If -vv is provided also the name of file and the number of lines of each file will be logged.')
def generate_text_file(
        directory: Path, num_files: int, num_lines: Optional[int],
        name_prefix: str, verbose: int
):
    """
    Generates some text files with each line containing a random sentence.
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
        with open(directory / file_name, 'w') as f:
            f.write('\n'.join(content))

        # Log the file name and the number of lines if verbose level is 2
        if verbose >= 2:
            click.echo(f"Generated {file_name} with {num_sentences} lines.")
    # Log the directory and the number of files if verbose level is 1 or more
    if verbose >= 1:
        click.echo(f"Generated {num_files} files in {directory}.")


@click.group()
def file_management():
    pass


file_management.add_command(partition)
file_management.add_command(merge)
file_management.add_command(generate_text_file)
file_management.add_command(batch_find_replace)
file_management.add_command(batch_rename)


if __name__ == '__main__':
    file_management()
