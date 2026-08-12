# `pydocx2md` — Word to Markdown, with comments

Also available as `toolbox docx2md`. Needs no extra: the reader is built on
`zipfile` and `xml.etree`, so it works on a bare install and on Termux.

```shell
pydocx2md report.docx                 # writes report.md
pydocx2md spec.docx -o notes.md
pydocx2md a.docx b.docx -d ./md       # one .md per input
pydocx2md spec.docx --no-comments     # body only
pydocx2md scan.docx --no-images -q
```

Plenty of tools turn a Word file into Markdown. This one keeps the review
conversation: every comment gets a numbered marker at the text it was written
about, and the thread is quoted under the paragraph or table that holds it.

## Comments

```markdown
| Feature              | Status  |
| -------------------- | ------- |
| Offline mode **[1]** | Planned |
| SQLite store **[2]** | Done    |

> **[1]** Sara Ahmadi · 2026-03-14
> Is offline mode in scope for v1?
>
> > **[1.1]** Mohammad · 2026-03-15
> > Yes, hard requirement for Termux.
>
> **[2]** Ali Rezaei · 2026-03-16 (resolved)
> Why not Postgres here?
```

- Comments are numbered **in document order**. Word's own comment ids are
  arbitrary and are not shown.
- The marker goes **after** the commented text, where it cannot break emphasis
  or a table row.
- Bodies follow the block that holds the anchor — after the paragraph, or after
  the whole table for a comment on a cell, since Markdown cannot put a
  blockquote inside one.
- Replies nest as `[1.1]` and never take a top-level number, so a marker in the
  text always names a thread. Threading comes from `commentsExtended.xml`;
  documents without that part have flat comments, which is normal.
- Comments marked done in Word are labelled `(resolved)`.

## What is supported

| Word | Markdown |
| --- | --- |
| Heading 1–6 | `#`–`######` |
| Bold, italic, strikethrough | `**`, `*`, `~~` |
| Monospace fonts (Consolas, Courier New, …) | `` `code` `` |
| Hyperlink | `[text](url)` |
| Bullet and numbered lists, nested | `- ` / `1. `, indented per level |
| Table | Pipe table |
| Embedded image | `![alt](<name>.assets/imageN.png)` |
| Equation | LaTeX in `$…$`, or `$$…$$` on its own lines |
| Footnote and endnote | `[^n]`, defined at the end |
| Comment | Numbered marker plus a quoted thread |
| Tracked insertion | Kept |
| Tracked deletion | Dropped |

Headings are matched on the style **id**, not its display name, so a document
written in a localised Word still converts. Persian and Arabic text passes
through untouched — Markdown is plain text, so none of the reshaping
`pymd2pdf` needs applies here.

A list is recognised whether the paragraph carries the numbering itself — what
Word writes for the toolbar buttons — or takes it from its style, as the
built-in *List Bullet* and *List Number* do. Styles are followed up their
`basedOn` chain, so a house style built on `Heading2` is a heading and one
built on a list style is a list.

## Equations

Word's equations are Office MathML, which Markdown has no notion of, so they
are written as LaTeX between dollars — `$…$` inside a sentence, and `$$…$$` on
lines of its own for one Word centres on its own line:

```markdown
$$
T_{B}(t) = T_{B,0} × D(λ, t, A_{B})
$$
```

Fractions, radicals, sub- and superscripts, brackets, n-ary operators (`∑`,
`∫`, …), accents, over- and underlines, function names, matrices and equation
arrays are translated. Anything else falls back to the text inside it: a poor
equation, but never a missing one.

## Options

| Option | Meaning |
| --- | --- |
| `-o, --output` | Output path. Single input only |
| `-d, --output-dir` | Write outputs into this directory |
| `--comments / --no-comments` | Include comments. On by default |
| `--images / --no-images` | Extract images to `<name>.assets/`. On by default |
| `-q, --quiet` | Do not print output paths |
| `-h, --help`, `-V, --version` | As everywhere else |

Several inputs each produce their own `.md`; one unreadable file is reported on
stderr and the rest still convert, with a non-zero exit code at the end.

## Limits

- **Merged cells** cannot be expressed in Markdown. A horizontal merge becomes
  empty continuation cells and a vertical merge repeats blank.
- **Multi-paragraph cells** join with `<br>`, since a pipe table row is one line.
- Colours, fonts, headers and footers, text boxes and charts are dropped.
- **Unicode in equations** is passed through as-is (`λ`, `×`). KaTeX, MathJax
  and Typst render it; a plain LaTeX toolchain needs `unicode-math`.
- Tracked deletions are accepted silently. There is no flag to review them; use
  Word for that.

## Errors

| Message | Meaning |
| --- | --- |
| `is a Word 97-2003 .doc` | The old binary format. Convert first: `libreoffice --headless --convert-to docx <file>` |
| `is password-protected` | Remove the password in Word |
| `is not a Word .docx file` | Not a zip, or no `word/document.xml` inside |
