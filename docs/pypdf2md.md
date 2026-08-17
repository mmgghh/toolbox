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
| A ruled grid of cells | A Markdown table |
| Persian, Arabic or Hebrew text | Put back into reading order |

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

## Tables

A PDF has no idea what a table is, and position alone cannot tell one from a
two-column page: both are text, a band of air, then more text, holding their
edges down the page. The difference is that a table is **drawn**, so the grid
is read off the rectangles the writer painted — the cell boxes one writer
fills, or the hairline borders another strokes. Both are read as the lines they
look like on the page.

Working from the grid rather than from the gaps settles the awkward cases for
free, because each is a question about the grid and the grid is known: an empty
cell, a cell whose text wrapped over three lines, a cell merged across two
columns. A table split by a page break is sewn back together when the second
half opens with the first's header, which is how a printed table says it is
continued.

Cells are read with the paragraph and list rules but not the heading ones: a
header cell is bold and short, which is exactly the shape of a heading. Markdown
has no row spans and no line breaks inside a cell, so a wrapped cell is put back
on one line and the top row becomes the header whether or not it was one.

A table nobody drew a border around is read as running text. Inferring one from
spacing is the least reliable guess available, and a wrong guess destroys
content that would otherwise have survived as plain lines.

## Right-to-left documents

A PDF stores glyphs in the order they are painted. For Persian, Arabic or
Hebrew that is the order they appear **on the page**, not the order they are
read in: the writer already ran the bidirectional algorithm and stored the
result. Three things are undone, in this order, because each needs what the
one before it leaves behind:

1. **Reading order.** Running the bidirectional algorithm over visual text is
   very nearly its own inverse, so it is what puts the words back. It cannot be
   exactly its own inverse — `(۲۰۲۶-۰۸-۱۶)` and `(۱۶-۰۸-۲۰۲۶)` render
   identically in a right-to-left paragraph — so where the page is genuinely
   ambiguous, the page's own reading wins.
2. **Zero-width non-joiners.** `می‌شود` and `میشود` are different words and the
   joiner between them has no glyph, but it leaves a trace: the letter before
   it keeps its unjoined shape. Where a writer draws a space for it instead,
   that space takes no room on the page, which is the other way it is spotted.
3. **Presentation forms.** Only then are the shaped glyphs (`ﻣ`, `ﯽ`) folded
   back to the letters someone can search for (`م`, `ی`).

A ligature is turned before any of that. One glyph can stand for several
letters — `لا` is drawn as a single shape — and the file spells those out in
*reading* order while everything around them is in paint order, so a ligature
left as it comes is the one piece of the line already the right way round.

Vowel marks are returned to the letters they sit on — whichever neighbour a
mark is drawn further over is its letter — and the geometric rules measure from
the margin the text starts at rather than always from the left, so a Persian
list nests towards the left and a table's first column is the rightmost one.

The line's own direction and the page's are kept apart. A Persian sentence
quoting two English terms has more Latin letters in it than Persian ones and is
still a Persian sentence, so the document's direction stands unless a line holds
nothing of it at all; and a line of Latin inside a Persian table still hangs off
the right margin, which is what the paragraph and indent rules read.

This needs `python-bidi`, part of the `pdf2md` extra. Without it a right-to-left
document still converts, but stays in painted order.

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

- **Only ruled tables are found.** A table drawn with no borders at all is read
  as running text; see [Tables](#tables).
- **Bidirectional text is not always recoverable.** A date, or a run of Latin
  terms separated by commas inside a Persian sentence, can be laid out
  identically from more than one source ordering. The page's own reading is
  what comes back.
- **A list marker drawn as a shape is lost.** Some writers paint bullets as
  filled circles rather than text, and a marker that is not in the text layer
  cannot be read; those items come through as paragraphs.
- **Non-joiners need shaped text.** They are recovered from the unjoined shape
  a letter kept, or from a space drawn with no width. A file whose glyphs map
  straight to plain letters records neither, and `می‌شود` comes back `میشود`.
- **A table inside a table loses the inner grid.** Only the outermost boxes are
  read, which is what keeps a cell's own backgrounds out of the grid.
- **What the file says about a character is taken at its word.** A font whose
  own mapping claims its `۱` glyph is an ASCII `1` will produce `1۴0۵`; there
  is nothing else to go on.
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
