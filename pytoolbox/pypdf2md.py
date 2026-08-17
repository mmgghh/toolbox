"""Convert PDF files to Markdown, inferring the structure.

Also available as ``toolbox pdf2md``. Needs the ``pdf2md`` extra for ``pypdf``.

A PDF stores placed glyphs, not structure -- nothing in the file says
"heading". Everything above the reader is inference: headings come from the
outline when the author left one and from font size otherwise, paragraphs are
reflowed and de-hyphenated, running headers and page numbers are dropped,
tables are read off the cell borders the writer drew, and a Persian or Arabic
page is put back into reading order. Each rule is chosen to fail towards plain
text rather than towards mangled text.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import click

from pytoolbox.core.options import CONTEXT_SETTINGS, quiet_option, version_option
from pytoolbox.pdf import layout, markdown, reader, structure, tables

#: Suffix for the directory holding a document's extracted images.
ASSETS_SUFFIX = ".assets"


def parse_pages(spec: str) -> list[int]:
    """``"1-3,7"`` to zero-based page indexes."""
    pages: set[int] = set()
    for part in spec.split(","):
        piece = part.strip()
        if not piece:
            continue
        try:
            if "-" in piece:
                start, end = (int(value) for value in piece.split("-", 1))
                if start < 1 or end < start:
                    raise ValueError(piece)
                pages.update(range(start - 1, end))
            else:
                number = int(piece)
                if number < 1:
                    raise ValueError(piece)
                pages.add(number - 1)
        except ValueError as exc:
            raise click.UsageError(
                f"--pages expects page numbers like 1-20,25, not {piece!r}."
            ) from exc
    return sorted(pages)


def convert(
    source: Path,
    destination: Path,
    *,
    include_images: bool = True,
    pages: Optional[list[int]] = None,
    password: Optional[str] = None,
    single_column: bool = False,
    page_breaks: bool = False,
) -> list[Path]:
    """Convert one PDF, returning every path written."""
    document = reader.read(source, password=password, pages=pages, include_images=include_images)
    if document.scanned:
        raise click.ClickException(
            f"{source} has no text layer (looks scanned). OCR it first, for example: "
            f"ocrmypdf {source} out.pdf"
        )

    base = layout.base_direction(document.pages)
    # Tables are taken out of the page first: their rows read as two columns to
    # a gutter search, and their cells as paragraphs to everything after it.
    found = [tables.find(page, base) for page in document.pages]
    per_page = [
        layout.page_lines(page, single_column, base, runs=rest)
        for page, (_, rest) in zip(document.pages, found)
    ]
    per_page = layout.drop_furniture(per_page, document.pages)

    images = [image for page in document.pages for image in page.images if image.data]
    assets_name = destination.stem + ASSETS_SUFFIX
    blocks = structure.build(
        document,
        per_page,
        grids=[grids for grids, _ in found],
        include_images=include_images,
        page_breaks=page_breaks,
    )
    text = markdown.render(blocks, assets_dir=assets_name if images else None)

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    written = [destination]

    if images:
        assets = destination.parent / assets_name
        assets.mkdir(parents=True, exist_ok=True)
        for image in images:
            target = assets / Path(image.name).name
            target.write_bytes(image.data)
            if target not in written:
                written.append(target)

    return written


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
    "otherwise each <input>.pdf is written as <input>.md.",
)
@click.option(
    "-d",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write the Markdown into this directory instead of beside the inputs.",
)
@click.option(
    "--images/--no-images",
    default=True,
    show_default=True,
    help="Extract embedded images into <output>.assets and link them.",
)
@click.option(
    "-p",
    "--pages",
    "pages_spec",
    metavar="RANGE",
    help="Pages to convert, like 1-20,25. All pages by default.",
)
@click.option(
    "--single-column",
    is_flag=True,
    help="Skip column detection, for a page it reads wrongly.",
)
@click.option(
    "--page-breaks",
    is_flag=True,
    help="Separate pages with a --- thematic break.",
)
@click.option("--password", help="Password for an encrypted PDF.")
@quiet_option
@version_option
def pdf2md_cli(
    files: tuple[Path, ...],
    output: Optional[Path],
    output_dir: Optional[Path],
    images: bool,
    pages_spec: Optional[str],
    single_column: bool,
    page_breaks: bool,
    password: Optional[str],
    quiet: bool,
) -> None:
    """Convert PDF documents to Markdown.

    \b
    Structure is inferred from the page: headings from the outline or from
    font size, paragraphs reflowed and de-hyphenated, running headers and
    page numbers dropped, tables read off their drawn borders, and Persian,
    Arabic or Hebrew text put back into reading order. Scanned files are
    reported, not guessed at.

    \b
    Examples:
      pypdf2md report.pdf                  # writes report.md
      pypdf2md paper.pdf -o notes.md
      pypdf2md a.pdf b.pdf -d ./md         # one .md per input
      pypdf2md book.pdf --pages 1-20
    """
    if output is not None and len(files) > 1:
        raise click.UsageError("-o/--output takes a single input file; use -d/--output-dir instead.")

    pages = parse_pages(pages_spec) if pages_spec else None

    failures = 0
    for source in files:
        destination = _destination(source, output, output_dir)
        try:
            written = convert(
                source,
                destination,
                include_images=images,
                pages=pages,
                password=password,
                single_column=single_column,
                page_breaks=page_breaks,
            )
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
    pdf2md_cli()
