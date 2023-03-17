import math
import os
import re
import shutil
from pathlib import Path
from typing import Literal

import click


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


def mkdirs(start_from: int, n_partition: int, destination: Path, name_prefix: str) -> dict[int, Path]:
    """
    Returns: A dict of index and corresponding directory Path.
    """
    dir_number_length = len(str(start_from + n_partition - 1))
    dirs = {}
    # Create n new directories with prefix
    for i in range(start_from, start_from + n_partition):
        current_dir = destination / f'{name_prefix}-{str(i).rjust(dir_number_length, "0")}'
        current_dir.mkdir()
        dirs[i] = current_dir

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
    # Create n new directories with prefix
    try:
        dirs = mkdirs(start_from, n_partition, destination, dir_prefix)
    except FileExistsError as e:
        click.echo(
            f'Directory {e.filename} already exists! '
            f'Use a unique prefix.', err=True
        )
        return
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
    # Create n new directories with prefix
    n_partition = math.ceil(len(files_or_dirs) / file_count)
    try:
        dirs = mkdirs(start_from, n_partition, destination, dir_prefix)
    except FileExistsError as e:
        click.echo(
            f'Directory {e.filename} already exists! '
            f'Use a unique prefix.', err=True
        )
        return

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
              type=click.Path(exists=True, file_okay=False, readable=True, path_type=Path))
@click.option('-v', '--verbose', count=True)
def partition(pattern: str, dir_prefix: str, split_based_on: Literal['count', 'size'],
              split_percentage: float, split_count: int, split_size: int, partitions: int,
              source: Path, destination: Path, verbose: int):
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
    click.echo('Not implemented error.', err=True)


@click.group()
def file_management():
    pass


file_management.add_command(partition)


if __name__ == '__main__':
    partition()
