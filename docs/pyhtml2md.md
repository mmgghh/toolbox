# `pyhtml2md` — HTML to Markdown

Also available as `toolbox html2md`. Needs no extra: parsing is done with the
standard library's `html.parser`, so this works on a bare install and on
Termux.

```shell
pyhtml2md page.html                  # writes page.md
pyhtml2md page.html -o notes.md
pyhtml2md a.html b.html -d ./md      # one .md per input
pyhtml2md page.html -q
```

## Nothing is dropped

Tags with a clean Markdown equivalent convert to it. Everything else — a
`<div>`, `<span>`, `<figure>`, `<iframe>`, a custom element, an HTML comment —
passes through **verbatim as raw HTML**, since Markdown allows embedded raw
HTML and this is the one way to guarantee no reader-visible content is ever
silently discarded. This is the mirror image of
[`pymd2html`](pymd2html.md)'s own raw-HTML passthrough, applied in reverse.

The only things actually dropped are `<script>` and `<style>` contents and
`<head>` metadata (`<title>`, `<meta>`, `<link>`, `<base>`) — none of that is
something a browser renders as page content, so a Markdown reader would never
have seen it either.

## What converts

| HTML | Markdown |
| --- | --- |
| `<h1>`–`<h6>` | `#`–`######` |
| `<p>`, `<br>` | Paragraphs, hard line breaks |
| `<strong>`/`<b>`, `<em>`/`<i>` | `**bold**`, `*italic*` |
| `<del>`/`<s>`/`<strike>` | `~~struck~~` |
| `<code>`, `<pre><code class="language-x">` | `` `code` ``, fenced ```` ```x ```` blocks |
| `<a href title>` | `[text](url "title")` |
| `<img src alt title>` | `![alt](src "title")` |
| `<ul>`, `<ol>`, nested `<li>` | `-` / `1.` items, indented |
| `<li><input type=checkbox>` | `- [ ]` / `- [x]` |
| `<blockquote>` | `>` quoted, nested |
| `<table>` | A pipe table, alignment read from `text-align` |
| `<hr>` | `---` |

A link or image with no `href`/`src` and any tag with no Markdown mapping
(`<div>`, `<sub>`, `<mark>`, `<iframe>`, `<dl>`, form controls, custom
elements...) is reproduced as raw HTML, attributes and all — a block-level one
on its own lines, an inline one in place.

Literal Markdown syntax characters in ordinary text (`* _ [ ] \` <` anywhere,
`# - + >` and `1.` at the very start of a line) are backslash-escaped so
source text can never be misread as formatting.

## Options

| Option | Meaning |
| --- | --- |
| `-o, --output` | Output path. Single input only |
| `-d, --output-dir` | Write outputs into this directory |
| `-q, --quiet` | Do not print output paths |
| `-h, --help`, `-V, --version` | As everywhere else |

Several inputs each produce their own `.md`.
