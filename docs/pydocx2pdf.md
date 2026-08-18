# `pydocx2pdf` — Word to PDF

Also available as `toolbox docx2pdf`.

```shell
pydocx2pdf report.docx                  # writes report.pdf
pydocx2pdf a.docx b.docx -d ./pdfs
pydocx2pdf spec.docx --engine markdown --comments
pydocx2pdf spec.docx -o final.pdf --page-size a5
```

## Two engines

There is no way to render a Word document faithfully without Word or something
that has read the same specification, and nothing like that runs on a phone.
So there are two engines, and by default the command uses the better one it
can find.

| Engine | Needs | Keeps |
| ------ | ----- | ----- |
| `libreoffice` | LibreOffice installed | the document's own layout: fonts, colours, page geometry, headers, footers, section breaks |
| `markdown` | nothing (the `pdf` extra for `pymd2pdf`) | the content: headings, lists, tables, images, equations, footnotes, right-to-left text |

`--engine auto` (the default) hands the file to a headless LibreOffice when
one is installed, and falls back to the Markdown pipeline when it is not —
**or when LibreOffice fails**, on the view that a PDF of the content beats no
PDF at all. The engine that did the work is named in the output line:

```
  report.docx -> report.pdf  (libreoffice)
```

Pin one with `--engine libreoffice` or `--engine markdown`. Asking for
LibreOffice on a machine without it is an error rather than a silent
downgrade.

### The Markdown pipeline

`.docx` → Markdown (the reader behind [`pydocx2md`](pydocx2md.md)) → PDF (the
writer behind [`pymd2pdf`](pymd2pdf.md)). The intermediate file lives in a
temporary directory and is deleted; `--keep-md` keeps it, and its images,
beside the PDF — useful for seeing exactly what the PDF was made from, or for
editing the text before rendering it again with `pymd2pdf`.

The document is typeset from scratch, so it will not look like the Word file.
What survives is everything that is *content*, including Persian and Arabic
text, which is shaped and laid out right-to-left when the `rtl` extra and a
Vazir font are installed.

Comments are left out by default — a PDF is usually the copy you send, not the
copy you review. `--comments` puts them back, each anchored to the text it was
written about, exactly as `pydocx2md` does it.

The layout options (`--page-size`, `--landscape`, `--margin`, `--font-size`,
`--title-page`) belong to this engine; LibreOffice takes its page setup from
the document and ignores them.

## LibreOffice, headless

The conversion runs with a throwaway user profile. Without one, LibreOffice
quietly refuses to convert anything while you have it open — the single most
common way a `soffice --convert-to pdf` script fails on a real desktop.

It exits 0 even when it converted nothing, so the file on disk is what decides
success or failure here, and whatever LibreOffice said about the problem is
shown.

Install it with `apt install libreoffice-writer`, `dnf install libreoffice`,
`brew install --cask libreoffice`, or the equivalent. `toolbox doctor` reports
whether it was found.

## Options

```
-o, --output PATH        Output PDF path (single input only)
-d, --output-dir DIR     Write the PDFs here instead of beside the inputs
    --engine ENGINE      auto (default), libreoffice, markdown
    --comments           Markdown engine: include Word comments
    --no-images          Markdown engine: leave the images out
    --keep-md            Markdown engine: keep the intermediate .md
    --page-size SIZE     Markdown engine: a3, a4, a5, letter, legal
    --landscape          Markdown engine: landscape orientation
    --margin MM          Markdown engine: page margin in millimetres
    --font-size PT       Markdown engine: body text size
    --title-page         Markdown engine: add a cover page from the first heading
-q, --quiet              Do not print the output paths
```

A batch keeps going when one file fails; the exit code still reports it.

## See also

- [`pydocx2md`](pydocx2md.md) — Word to Markdown, comments and all.
- [`pymd2pdf`](pymd2pdf.md) — the PDF writer, and its font requirements.
- [`pymd2html`](pymd2html.md) — the same content as a web page instead.
