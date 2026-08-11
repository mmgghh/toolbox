# `pypdf2md` — PDF to Markdown

Also available as `toolbox pdf2md`. Needs the `pdf2md` extra:

```shell
pip install -e ".[pdf2md]"     # or ".[all]"
```

```shell
pypdf2md report.pdf                  # writes report.md
pypdf2md paper.pdf -o notes.md
pypdf2md a.pdf b.pdf -d ./md         # one .md per input
pypdf2md book.pdf --pages 1-20
pypdf2md scan.pdf --no-images -q
```

A PDF stores placed glyphs, not structure. Nothing in the file says "this is a
heading" — it says "draw these letters at 24 points, here". So everything below
is **inference**, and every rule is chosen to fail towards plain text rather
than towards mangled text: a line that cannot be classified confidently stays a
paragraph, which is the outcome that loses the least.

## What it works out

| From the page | Markdown |
| --- | --- |
| A bookmark in the outline | `#`–`######` at the bookmark's depth |
| A line larger than the body text | `#`–`######`, one level per distinct size |
| A short bold line with no closing punctuation | `###` |
| Consecutive body lines | One reflowed paragraph |
| A word split by a hyphen at a line break | Rejoined, hyphen removed |
| `•`, `-`, `1.`, `a)` at the start of a line | `- ` / `1. `, nested by indent |
| Bold and italic fonts | `**`, `*` |
| A link annotation, or a bare `https://` URL | `[text](url)` |
| An embedded image | `![](<name>.assets/imageN.png)` |
| A running header, footer or page number | Dropped |
| Two columns on a page | Read down one column, then the other |

Headings come from the **outline first**. A bookmark is the author's own
statement of structure, so it beats anything geometry could suggest; font size
is the fallback for the documents that have none.

Body text is taken to be the size **most lines** use, not the size most
characters use. On a short document a title can easily carry more characters
than the two paragraphs beneath it, and headings occupy few lines whatever
their length.

## Columns

Two-column PDFs read naively produce interleaved nonsense, because both columns
share their baselines: line one of the left column and line one of the right
are drawn at the same height. Columns are therefore found **before** lines are
assembled, by looking for a vertical whitespace gutter down the middle of the
page.

The split is only accepted when at least 60% of the page's text sits cleanly on
one side of the gutter and both sides span several lines. Anything less and the
page is read as one column, because a half-detected split interleaves real
sentences. `--single-column` skips detection entirely for a page it still gets
wrong.

## Options

| Option | Meaning |
| --- | --- |
| `-o, --output` | Output path. Single input only |
| `-d, --output-dir` | Write outputs into this directory |
| `--images / --no-images` | Extract images to `<name>.assets/`. On by default |
| `-p, --pages` | Page range, `1-20,25`. All pages by default |
| `--single-column` | Skip column detection |
| `--page-breaks` | Separate pages with a `---` thematic break |
| `--password` | For an encrypted PDF; the empty password is tried first |
| `-q, --quiet` | Do not print output paths |
| `-h, --help`, `-V, --version` | As everywhere else |

Several inputs each produce their own `.md`; one unreadable file is reported on
stderr and the rest still convert, with a non-zero exit code at the end.

## Limits

- **Tables are not reconstructed.** Inferring one from column-aligned text is
  the least reliable guess available, and a wrong guess destroys content that
  would otherwise have survived intact as plain lines. A table's cells come
  through as ordinary text.
- **Scanned pages are reported, not read.** There is no OCR. A file whose pages
  are images gets an error naming `ocrmypdf`; a document with only a few such
  pages converts, with a warning.
- **Running headers need three pages.** Two occurrences are not enough evidence
  that a line is furniture rather than content, so short documents keep theirs.
- **Digits are normalised only on short lines**, which is how `Page 3` and
  `Page 4` are recognised as one footer while `Chapter 3: Methods` and
  `Chapter 4: Results` stay two headings.
- **Only http(s), mailto and ftp links survive.** A PDF's annotations are
  supplied by whoever made the file, and a `javascript:` target would stay live
  once the Markdown is rendered. Other schemes keep their text and lose the
  link.
- Footnotes, blockquotes, colours, headers and footers as such, text boxes,
  equations and charts are dropped.

## Errors

| Message | Meaning |
| --- | --- |
| `is not a PDF file` | No PDF structure at that path, or the file is damaged |
| `is password-protected` | Retry with `--password` |
| `has no text layer (looks scanned)` | OCR it first: `ocrmypdf in.pdf out.pdf` |
| `needs the pdf2md extra` | `pip install 'pytoolbox[pdf2md]'` |
