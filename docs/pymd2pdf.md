# `pymd2pdf` — Markdown to PDF

Also available as `toolbox md2pdf`. Needs the `pdf` extra:

```shell
pip install 'pytoolbox[pdf]'          # fpdf2 + Pillow
pip install 'pytoolbox[pdf,rtl]'      # plus Persian/Arabic shaping
```

```shell
pymd2pdf README.md                    # writes README.pdf
pymd2pdf doc.md -o report.pdf
pymd2pdf a.md b.md c.md               # one PDF per input
pymd2pdf *.md -d ./pdfs --page-size a5
pymd2pdf notes.md --no-title-page --font-size 11 --offline
```

## What is supported

| Markdown | Rendering |
| --- | --- |
| `# … ######` | Headings, sized by level |
| `**bold**`, `__bold__` | Bold |
| `*italic*` | Italic |
| `~~strikethrough~~` | Greyed text |
| `` `code` `` | Monospace |
| ` ```lang ` fences | Shaded code block |
| `[text](url)` | Underlined blue text with a real PDF link |
| `![alt](src)` | Embedded image, scaled to fit, with a caption |
| Tables | Auto-sized columns, header fill, zebra rows; a cell that is only a link becomes a clickable link |
| `- `, `* `, `+ ` | Bullet lists, marker cycles by nesting depth |
| `1. ` | Numbered lists |
| `- [ ]` / `- [x]` | Task lists |
| `> ` | Blockquote with an indent bar |
| `---` | Horizontal rule |
| ` ```mermaid ` | Rendered diagram (see below) |

A cover page is generated from the first `# H1`, and that title is repeated in
the page header. Use `--no-title-page` to skip it.

## Options

| Option | Meaning |
| --- | --- |
| `-o, --output` | Output path (single input only) |
| `-d, --output-dir` | Write PDFs into this directory |
| `--page-size` | `a3`, `a4` (default), `a5`, `letter`, `legal` |
| `--landscape` | Landscape orientation |
| `--margin` | Page margin in millimetres (default 20) |
| `--font-size` | Body text size in points (default 10) |
| `--fallback-font` | Extra font file for glyphs the main faces lack (repeatable) |
| `--no-title-page` | Skip the generated cover page |
| `--offline` | Never use the network |
| `-q, --quiet` | Do not print output paths |

## Persian and Arabic

Right-to-left text is shaped (letters joined into their contextual forms) and
reordered with the bidi algorithm when both are available:

1. the `rtl` extra — `arabic-reshaper` and `python-bidi`
2. a Persian font — Vazirmatn is preferred, then Vazir, then Noto Naskh Arabic

Without them, Persian text still renders but the letters will not join and the
word order will be wrong; a warning explains what is missing.

Handled correctly:

- Whole-document base direction is detected from the text, so an English-only
  bullet inside a Persian list stays right-aligned with the rest of the list.
- Lines are wrapped in logical order and reordered per line, so paragraphs do
  not come out with the last sentence on the first line.
- List markers are shaped together with the item text, which puts the marker's
  punctuation on the correct side.
- Table columns are mirrored, so the first Markdown column lands on the right.
- Cell text is shaped before the table is laid out, which means its Markdown
  can no longer be parsed: a whole cell wrapped in `**` still renders bold,
  and emphasis around part of a cell has its markers dropped rather than
  printed literally.

### Installing a Persian font

Download Vazirmatn from <https://github.com/rastikerdar/vazirmatn> and drop
the TTFs into one of:

```
~/.local/share/fonts
~/.termux/fonts             (Termux)
$PREFIX/share/fonts         (Termux)
/usr/share/fonts/truetype/vazir
```

Then run `fc-cache -f` on Linux.

## Fonts

DejaVu is required for Latin text.

```shell
sudo apt-get install fonts-dejavu-core                            # Debian/Ubuntu
sudo dnf install dejavu-sans-fonts dejavu-sans-mono-fonts         # Fedora/RHEL
sudo pacman -S ttf-dejavu                                         # Arch
pkg install fontconfig-utils ttf-dejavu                           # Termux
brew install --cask font-dejavu                                   # macOS
```

Font directories are searched non-recursively and then one level deep, which
covers Debian's per-family subdirectories and Termux's `$PREFIX/share/fonts`
layout. If only some DejaVu faces are installed, the missing ones fall back to
the regular face rather than failing.

### Missing glyphs

No single face covers everything: Vazir has no arrows or emoji, and DejaVu has
no pictographs. Rather than dropping those characters, each one is drawn with
the first face that has it:

1. the face the text is set in (DejaVu, or Vazir for Persian)
2. DejaVu — arrows, maths, geometric shapes, box drawing
3. a symbol font, if one is installed, plus anything passed with
   `--fallback-font`
4. a text stand-in, when nothing can draw it: `→` becomes `->`, `✅` becomes
   `✓`, `☑` becomes `[x]`

Symbola gives the widest coverage of step 3:

```shell
sudo apt-get install fonts-symbola                                # Debian/Ubuntu
sudo dnf install gdouros-symbola-fonts                            # Fedora/RHEL
sudo pacman -S ttf-symbola                                        # Arch
```

Any other face works too:

```shell
pymd2pdf notes.md --fallback-font ~/fonts/NotoSansSymbols2-Regular.ttf
```

Two deliberate exceptions:

- **Colour emoji fonts cannot be used.** NotoColorEmoji and friends store their
  artwork as embedded bitmaps rather than outlines, which cannot be embedded in
  a PDF this way. Passing one with `--fallback-font` prints a warning and is
  ignored — registering it would claim coverage of every emoji and then draw
  blanks.
- **Colour-coded status emoji always become `●` `◐` `○`**, even when a symbol
  font can draw them. PDF text is drawn in one colour, so 🟢 🟡 🔴 would come
  out as three identical black discs, losing exactly the distinction they
  encode. The same applies to their square forms 🟩 🟨 🟥.

Variation selectors (the invisible `U+FE0F` that trails many emoji) are
dropped, since no face draws them.

## Mermaid diagrams

` ```mermaid ` blocks are rendered to an image using, in order:

1. a local `mermaid-cli` install (`npm install -g @mermaid-js/mermaid-cli`)
2. the mermaid.ink web API
3. the raw source shown as a code block

`--offline` skips step 2 entirely, which is the right default on a metered
connection or when documents contain anything you would rather not send to a
third-party service.

## Images

Standalone `![alt](path)` lines are embedded and scaled to fit the page, with
the alt text as a caption. `path` may be a local file (relative to the
Markdown file) or an http(s) URL; remote images are skipped under `--offline`.
SVGs are drawn as vectors and stay crisp at any size.

## From Python

```python
from pytoolbox.pymd2pdf import convert

convert("notes.md", "notes.pdf", page_size="A5", font_size=11, quiet=True)
```
