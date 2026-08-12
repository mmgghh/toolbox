"""Convert Word ``.docx`` files to Markdown, keeping the comments.

Also available as ``toolbox docx2md``. Needs no optional dependency: the
reader is built on ``zipfile`` and ``xml.etree``, so this works on a bare
install and on Termux.

Comments are the reason this exists. Plenty of tools turn a Word file into
Markdown; they drop the review conversation on the floor. Here each comment
keeps a numbered marker at the text it was written about, with the thread
quoted under the paragraph or table that holds it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from pytoolbox.core.options import CONTEXT_SETTINGS, quiet_option, version_option
from pytoolbox.docx.comments import load_comments
from pytoolbox.docx.document import items_of, parse_document
from pytoolbox.docx.inline import ImageRef
from pytoolbox.docx.markdown import RenderOptions, render
from pytoolbox.docx.notes import load_notes
from pytoolbox.docx.numbering import load_numbering
from pytoolbox.docx.package import open_docx
from pytoolbox.docx.styles import load_styles

#: Suffix for the directory holding a document's extracted images.
ASSETS_SUFFIX = ".assets"


def convert(
    source: Path,
    destination: Path,
    include_comments: bool = True,
    include_images: bool = True,
) -> list[Path]:
    """Convert one document, returning every path written."""
    pkg = open_docx(source)
    numbering = load_numbering(pkg)
    blocks = parse_document(pkg, numbering, load_styles(pkg))
    comments = load_comments(pkg) if include_comments else {}
    notes = load_notes(pkg)

    assets_name = destination.stem + ASSETS_SUFFIX
    images = _images_in(blocks) if include_images else []
    options = RenderOptions(
        comments=include_comments,
        assets_dir=assets_name if images else None,
    )

    text = render(blocks, comments, notes=notes, options=options)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    written = [destination]

    if images:
        assets = destination.parent / assets_name
        assets.mkdir(parents=True, exist_ok=True)
        for part_name in images:
            data = pkg.media(part_name)
            if data is None:
                continue
            target = assets / Path(part_name).name
            target.write_bytes(data)
            written.append(target)

    return written


def _images_in(blocks) -> list[str]:
    """Part names of every image referenced by the document, without repeats."""
    found: list[str] = []
    for block in blocks:
        for item in items_of(block):
            if isinstance(item, ImageRef) and item.part_name not in found:
                found.append(item.part_name)
    return found


def _destination(source: Path, output: Optional[Path], output_dir: Optional[Path]) -> Path:
    if output is not None:
        return output
    if output_dir is not None:
        return output_dir / (source.stem + ".md")
    return source.with_suffix(".md")


@click.command(context_settings=CONTEXT_SETTINGS)
@click.argument(
    "files",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help="Output Markdown path. Only valid with a single input file; "
    "otherwise each <input>.docx is written as <input>.md.",
)
@click.option(
    "-d",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write the Markdown into this directory instead of beside the inputs.",
)
@click.option(
    "--comments/--no-comments",
    default=True,
    show_default=True,
    help="Include comments, anchored to the text they annotate.",
)
@click.option(
    "--images/--no-images",
    default=True,
    show_default=True,
    help="Extract embedded images into <output>.assets and link them.",
)
@quiet_option
@version_option
def docx2md_cli(
    files: tuple[Path, ...],
    output: Optional[Path],
    output_dir: Optional[Path],
    comments: bool,
    images: bool,
    quiet: bool,
) -> None:
    """Convert Word documents to Markdown, comments and all.

    \b
    Examples:
      pydocx2md report.docx                 # writes report.md
      pydocx2md a.docx b.docx -d ./md       # one .md per input
      pydocx2md spec.docx --no-comments     # body only
      pydocx2md spec.docx -o notes.md
    """
    if output is not None and len(files) > 1:
        raise click.UsageError("-o/--output takes a single input file; use -d/--output-dir instead.")

    failures = 0
    for source in files:
        destination = _destination(source, output, output_dir)
        try:
            written = convert(source, destination, include_comments=comments, include_images=images)
        except click.ClickException as exc:
            # One unreadable file should not abandon the rest of the batch,
            # but it must still show up in the exit code.
            failures += 1
            click.secho(f"error: {exc.format_message()}", fg="red", err=True)
            continue
        if not quiet:
            click.echo(f"  {source} -> {written[0]}")
            for extra in written[1:]:
                click.echo(f"    + {extra}")

    if failures:
        raise click.exceptions.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    docx2md_cli()
