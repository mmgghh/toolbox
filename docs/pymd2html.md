# `pymd2html` — Markdown to HTML

Also available as `toolbox md2html`.

```shell
pymd2html README.md                    # writes README.html
pymd2html notes.md -o - | less         # to stdout
pymd2html *.md -d ./site
pymd2html post.md --fragment           # body only, for a template
pymd2html doc.md --css mine.css --lang fa
```

Needs no optional dependency — the Markdown parser is part of pytoolbox, so
this works on a bare install and on Termux.

## What comes out

One **self-contained** file per input. The stylesheet is embedded and nothing
is fetched from the network, so the page opens the same from an email
attachment, a USB stick or a phone with no signal.

The built-in stylesheet follows the reader's light/dark preference, sizes for
a phone as well as a laptop, and lays out identically in either direction
(logical CSS properties, not left/right ones).

## What it understands

| Markdown | HTML |
| -------- | ---- |
| `#` … `######` | `<h1>` … `<h6>`, each with an `id` anchor |
| `**bold**`, `__bold__` | `<strong>` |
| `*italic*`, `_italic_` | `<em>` |
| `~~struck~~` | `<del>` |
| `` `code` ``, ```` ```lang ```` | `<code>`, `<pre><code class="language-lang">` |
| `- item`, `1. item` | `<ul>`, `<ol start="n">`, nested to any depth |
| `- [x] done` | a disabled, checked checkbox |
| `> quote` | `<blockquote>`, holding blocks of its own |
| `\| a \| b \|` + `\|:--\|--:\|` | `<table>` with per-column alignment |
| `---`, `***` | `<hr>` |
| `[text](url "title")`, `![alt](src)` | `<a>`, `<img>` |
| `<https://x.dev>`, bare `https://x.dev` | `<a>` |
| two trailing spaces | `<br>` |

Headings get anchors (`## Shell completion` → `id="shell-completion"`), so
in-page links like `[see below](#shell-completion)` work. A repeated heading
gets `-2`, `-3` and so on.

Snake\_case names, `2 * 3 * 4` and other accidental emphasis markers are left
alone: underscore emphasis only fires at a word boundary.

## Right-to-left

A document with more Persian, Arabic or Hebrew letters than Latin ones gets
`<html dir="rtl">` automatically, which is all a browser needs to lay the page
out in the right direction and still handle embedded English correctly. Code
blocks stay left-to-right, since a shell command is not prose.

Force it either way with `--rtl` / `--ltr`, and set the document language with
`--lang fa`.

## Raw HTML

HTML in the Markdown is passed through, so `<details>`, `<kbd>` and a stray
`<br>` do what you meant. For a document you did not write, `--escape-html`
shows the tags as text instead.

Link targets that would run code (`javascript:`, `vbscript:`, `file:`) are
never emitted as links, with or without that flag; they degrade to their text.

## Options

```
-o, --output PATH      Output path, or '-' for stdout (single input only)
-d, --output-dir DIR   Write the HTML here instead of beside the inputs
    --fragment         Body only: no <html>, <head> or stylesheet
    --css FILE         Embed this stylesheet instead of the built-in one
    --no-css           Embed no stylesheet at all
    --title TEXT       Page title (default: the first H1, else the file name)
    --lang TEXT        Value for <html lang>
    --rtl / --ltr      Force a direction instead of detecting one
    --escape-html      Show raw HTML as text
-q, --quiet            Do not print the output paths
```

## As a library

```python
from pytoolbox.pymd2html import render_body, render_document

render_body("# Hi\n\ntext")        # fragment
render_document("# Hi", lang="en") # whole page
```

## See also

- [`pymd2pdf`](pymd2pdf.md) — the same Markdown, rendered to PDF.
- [`pydocx2md`](pydocx2md.md) and [`pypdf2md`](pypdf2md.md) — get Markdown out
  of a Word or PDF file first.
