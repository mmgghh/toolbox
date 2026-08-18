#!/usr/bin/env python3
"""Convert Word ``.docx`` files to PDF.

Exposes the ``pydocx2pdf`` console script, also available as ``toolbox docx2pdf``.

Two engines, because no single one is available everywhere:

``libreoffice``
    Hands the file to a headless LibreOffice, which is Word's own layout as
    closely as anything gets: fonts, colours, page geometry, headers and
    footers. Needs LibreOffice installed, which rules out most phones.

``markdown``
    Reads the document with pytoolbox's own ``.docx`` reader, writes Markdown,
    and renders that with ``pymd2pdf``. Needs no system binary -- it works on
    Termux -- and keeps the content: headings, lists, tables, images,
    equations, footnotes and right-to-left text. What it does not keep is the
    document's *design*; the PDF is typeset from scratch.

``--engine auto`` (the default) uses LibreOffice when it is installed and the
Markdown pipeline when it is not, so the command works on every machine and
gets the better result where it can.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import click

from pytoolbox.core import console
from pytoolbox.core.options import CONTEXT_SETTINGS, quiet_option, version_option

#: Names LibreOffice's launcher goes by, plus the macOS bundle it hides in.
SOFFICE_NAMES = ("soffice", "libreoffice")
SOFFICE_PATHS = (
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    "/usr/lib/libreoffice/program/soffice",
)

#: Long enough for a first run, which unpacks a user profile before converting.
SOFFICE_TIMEOUT = 180.0

ENGINES = ("auto", "libreoffice", "markdown")


def find_soffice() -> Optional[str]:
    """Path to a LibreOffice launcher, or ``None`` when it is not installed."""
    for name in SOFFICE_NAMES:
        found = shutil.which(name)
        if found:
            return found
    for candidate in SOFFICE_PATHS:
        if Path(candidate).exists():
            return candidate
    return None


def convert_with_libreoffice(
    source: Path, destination: Path, binary: Optional[str] = None, timeout: float = SOFFICE_TIMEOUT
) -> Path:
    """Convert one document with a headless LibreOffice."""
    launcher = binary or find_soffice()
    if launcher is None:
        raise click.ClickException("LibreOffice is not installed. Try --engine markdown.")

    with tempfile.TemporaryDirectory(prefix="pydocx2pdf-") as work:
        outdir = Path(work) / "out"
        outdir.mkdir()
        command = [
            launcher,
            # A private profile: without it the conversion silently does
            # nothing whenever the user already has LibreOffice open, which is
            # the single most common way this fails.
            f"-env:UserInstallation=file://{Path(work) / 'profile'}",
            "--headless",
            "--norestore",
            "--convert-to",
            "pdf",
            "--outdir",
            str(outdir),
            str(source),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise click.ClickException(f"LibreOffice did not finish within {timeout:.0f}s.") from exc
        except OSError as exc:
            raise click.ClickException(f"Could not run {launcher}: {exc}") from exc

        # LibreOffice names the output after the input and exits 0 even when it
        # converted nothing, so the file on disk is the only real answer.
        produced = outdir / (source.stem + ".pdf")
        if not produced.exists():
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise click.ClickException(
                "LibreOffice produced no PDF"
                + (f": {detail[-1]}" if detail else " and said nothing about why.")
            )
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), str(destination))
    return destination


def convert_with_markdown(
    source: Path,
    destination: Path,
    comments: bool = False,
    images: bool = True,
    keep_md: Optional[Path] = None,
    **pdf_options,
) -> Path:
    """Convert via Markdown: the ``.docx`` reader, then the PDF writer."""
    try:
        from pytoolbox import pymd2pdf
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise click.ClickException(
            f"The Markdown engine needs the PDF extra ({exc}). "
            f"Try `pip install 'pytoolbox[pdf]'`, or install LibreOffice."
        ) from exc

    from pytoolbox.pydocx2md import convert as docx_to_markdown

    with tempfile.TemporaryDirectory(prefix="pydocx2pdf-") as work:
        # Images land in <stem>.assets beside the Markdown, which is exactly
        # where pymd2pdf looks for them, so the whole pipeline stays in here.
        staging = (keep_md.parent if keep_md else Path(work)) / f"{source.stem}.md"
        docx_to_markdown(source, staging, include_comments=comments, include_images=images)
        destination.parent.mkdir(parents=True, exist_ok=True)
        pymd2pdf.convert(staging, destination, **pdf_options)
        if keep_md and keep_md != staging:
            shutil.move(str(staging), str(keep_md))
    return destination


def convert(
    source: Path,
    destination: Path,
    engine: str = "auto",
    comments: bool = False,
    images: bool = True,
    keep_md: Optional[Path] = None,
    quiet: bool = False,
    **pdf_options,
) -> str:
    """Convert one document, returning the name of the engine that did it.

    With ``engine="auto"`` a LibreOffice failure is not fatal: the Markdown
    pipeline is tried next, since a PDF of the content beats no PDF at all.
    """
    if engine in ("auto", "libreoffice"):
        launcher = find_soffice()
        if launcher is None and engine == "libreoffice":
            raise click.ClickException(
                "LibreOffice is not installed. Install it, or use --engine markdown."
            )
        if launcher is not None:
            try:
                convert_with_libreoffice(source, destination, launcher)
                return "libreoffice"
            except click.ClickException:
                if engine == "libreoffice":
                    raise
                console.warn(f"LibreOffice could not convert {source.name}; falling back to Markdown.")

    convert_with_markdown(
        source,
        destination,
        comments=comments,
        images=images,
        keep_md=keep_md,
        quiet=quiet,
        **pdf_options,
    )
    return "markdown"


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
    help="Output PDF path. Only valid with a single input file; "
    "otherwise each <input>.docx is written as <input>.pdf.",
)
@click.option(
    "-d",
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Write the PDFs into this directory instead of beside the inputs.",
)
@click.option(
    "--engine",
    type=click.Choice(ENGINES, case_sensitive=False),
    default="auto",
    show_default=True,
    help="Converter to use: LibreOffice, the Markdown pipeline, or whichever is available.",
)
@click.option(
    "--comments/--no-comments",
    default=False,
    show_default=True,
    help="Markdown engine: include Word comments, anchored to the text they annotate.",
)
@click.option(
    "--images/--no-images",
    default=True,
    show_default=True,
    help="Markdown engine: embed the document's images.",
)
@click.option(
    "--keep-md",
    is_flag=True,
    help="Markdown engine: keep the intermediate .md (and its images) beside the PDF.",
)
@click.option(
    "--page-size",
    type=click.Choice(("a3", "a4", "a5", "letter", "legal"), case_sensitive=False),
    default="a4",
    show_default=True,
    help="Markdown engine: paper size.",
)
@click.option("--landscape", is_flag=True, help="Markdown engine: landscape orientation.")
@click.option(
    "--margin",
    type=click.FloatRange(5, 60),
    default=20,
    show_default=True,
    help="Markdown engine: page margin in millimetres.",
)
@click.option(
    "--font-size", type=click.FloatRange(4, 24), default=None, help="Markdown engine: body text size in points."
)
@click.option(
    "--title-page",
    is_flag=True,
    help="Markdown engine: add a cover page made from the document's first heading.",
)
@quiet_option
@version_option
def docx2pdf_cli(
    files: tuple[Path, ...],
    output: Optional[Path],
    output_dir: Optional[Path],
    engine: str,
    comments: bool,
    images: bool,
    keep_md: bool,
    page_size: str,
    landscape: bool,
    margin: float,
    font_size: Optional[float],
    title_page: bool,
    quiet: bool,
) -> None:
    """Convert Word documents to PDF.

    \b
    Uses a headless LibreOffice when one is installed, which keeps the
    document's own layout. Without LibreOffice it reads the .docx directly and
    typesets the content with pymd2pdf -- headings, lists, tables, images,
    equations and right-to-left text survive, the visual design does not. That
    path needs no system binary, so it also works on Termux.

    \b
    Examples:
      pydocx2pdf report.docx                  # writes report.pdf
      pydocx2pdf a.docx b.docx -d ./pdfs
      pydocx2pdf spec.docx --engine markdown --comments
      pydocx2pdf spec.docx -o final.pdf --page-size a5

    \b
    Options below marked "Markdown engine" have no effect when LibreOffice
    does the conversion: it lays the document out the way Word would, so
    there is nothing for them to decide.
    """
    if output and len(files) > 1:
        raise click.UsageError("-o/--output can only be used with a single input file.")
    if output and output_dir:
        raise click.UsageError("Use either -o/--output or -d/--output-dir, not both.")
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)

    failures = 0
    for source in files:
        if output:
            destination = output
        elif output_dir:
            destination = output_dir / source.with_suffix(".pdf").name
        else:
            destination = source.with_suffix(".pdf")
        try:
            used = convert(
                source,
                destination,
                engine=engine.lower(),
                comments=comments,
                images=images,
                keep_md=destination.with_suffix(".md") if keep_md else None,
                quiet=True,
                page_size=page_size.upper(),
                orientation="L" if landscape else "P",
                margin=margin,
                font_size=font_size,
                title_page=title_page,
            )
        except click.ClickException as exc:
            # One unconvertible file should not abandon the rest of the batch,
            # but it must still show up in the exit code.
            failures += 1
            console.error(exc.format_message())
            continue
        if not quiet:
            console.echo(f"  {source} -> {destination}  ({used})", err=True)

    if failures:
        raise click.exceptions.Exit(1)


if __name__ == "__main__":  # pragma: no cover
    docx2pdf_cli()
