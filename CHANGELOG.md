# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- **`pydocx2md` dropped every tick, cross and bullet drawn from a symbol
  font.** Word does not store those as text: a ticked box is
  `<w:sym w:font="Wingdings" w:char="F0FE"/>`, the font name plus a code
  point, and nothing about it reaches the run's `w:t`. The reader collected
  only `w:t`, so a contract whose requirements table marks each row as in or
  out of scope converted to a column of blank cells — and the sentence
  explaining the convention lost the tick from inside its own brackets.
  Wingdings and Symbol are now mapped to their Unicode equivalents; a glyph
  from any other font falls back to the plain character at its code point.

- **`pydocx2md` shredded a table of contents into one link per word.** Word
  starts a fresh `w:r` wherever its revision tracking needs one, so a single
  entry arrives as a dozen runs that share every property. Each was rendered
  on its own, giving `[Chapter](#_Toc1)[ 1](#_Toc1)[Aims](#_Toc1)` and, where
  a run held only the space between two words, an empty `[](#_Toc1)`. Runs
  that agree on their formatting are joined back together first, which also
  ends the `**bold****text**` the same splitting produced in body text.

- **`pydocx2md` lost every heading in a document written with its own chapter
  styles.** A heading was recognised by its style *id* — `Heading1` and the
  styles based on it — or by an outline level the paragraph carried directly.
  A localised Word names those styles in the author's language and gives them
  generated ids like `a0`, and records the depth once in the style, so neither
  test found them. Outline level is now inherited through the `basedOn` chain
  like the other style properties, and Word's 9 is read as "body text" rather
  than as a tenth heading level.

- **`pydocx2md` turned numbered headings into list items.** Word numbers
  headings through the same `numPr` a bulleted list uses, and paragraphs were
  tested for list membership first, so every chapter heading in a document
  with automatic heading numbers came out as a one-line list. Headings are
  now tested first: an outline level makes a heading whatever numbering the
  style also carries.

### Added

- **`pydata edit` renames the titles, keys and column names of a file in
  place.** `pydata edit sales.csv --rename "First Name=full_name"` fixes a
  header without opening a spreadsheet, and `pydata edit api.json -i` walks
  the keys one at a time, offering each current name as the default so Enter
  keeps it and `--suggest` offers the snake_case spelling instead.

  Only names change. A CSV keeps every data row byte for byte, because just
  the header line is rewritten; a workbook keeps its formulas, styles, column
  widths and other sheets, because only the header cells are assigned; a JSON
  document keeps its key order, its indentation and the envelope around
  `--root`. A copy is kept as `FILE.bak` unless `--no-backup` or `--output` is
  given, the new file is moved into place rather than written over the old
  one, and `--dry-run` shows the change table without touching anything.

- **`pydata` reads JSON, CSV and Excel and turns them into a schema, a
  summary and a SQL table.** `pydata tree api.json` draws the structure —
  every key, its types, and how many records actually had a value there;
  `pydata summary` puts statistics beside it; `pydata filter --key 'addr*'
  --type int` prints part of it; and `pydata sql -t users --db app.db` creates
  the table and inserts the rows.

  A JSON document is rarely already a list of rows, so it looks for one: a
  top-level list is the rows, a single object is one row, and an envelope like
  `{"data": {"items": [...]}}` is followed to its one list of objects — or, if
  there are several, refused with each candidate named so `--root data.items`
  can pick. Records with missing keys are fine: a key present in 80 of 120
  records becomes a nullable column.

  `--db` writes a real SQLite file through the standard library; `--dialect
  postgres --sql out.sql` writes a script instead, with `--sql -` piping
  straight into `psql`. Nested objects and lists are stored whole, as `jsonb`
  on PostgreSQL and as `TEXT` on SQLite. Column names are folded to lower
  `snake_case` and quoted — so the table stays comfortable to query, since a
  verbatim `"userID"` column would have to be quoted in every later query on
  PostgreSQL. Folding is not transliteration: Latin accents are dropped
  (`Prénom` to `prenom`), while a script with no ASCII spelling keeps its
  letters (`نام واحد` to `نام_واحد`, `日本語 の 列` to `日本語_の_列`), and names
  are cut to PostgreSQL's 63-*byte* limit without splitting a character.
  `--raw-names` keeps the keys exactly as they are and `--column OLD=NEW`
  renames one; `--pk`,
  `--index` and `--unique-index` are validated against the data first, so a
  primary key that is not unique is refused with the offending value named
  rather than failing halfway through the insert. `-i/--interactive` shows the
  summary and asks for the table name, key and indexes, keeping every prompt
  on stderr so a piped script stays clean.

  CSV types are inferred a column at a time — one unparseable cell keeps the
  whole column as text, and leading zeros keep `01730` a postcode rather than
  a number — while Excel keeps the types the sheet recorded. Reading `.xlsx`
  needs the existing `excel` extra; everything else is standard library.

- **`pycalc` does arithmetic from the shell.** `pycalc '2**5+56-1'` prints
  `87`. `^` means "to the power of", as it does on a calculator and in a
  spreadsheet rather than in Python, with `--caret xor` and an always-present
  `xor(a, b)` for the other meaning. It takes `+ - * / // % **`, bitwise
  operators, comparisons, parentheses, decimal, hex, binary, octal and
  scientific numbers, `pi`/`e`/`tau`, and the usual library of functions —
  `sqrt`, `log`, `factorial`, the trigonometric family and the rest — with a
  "did you mean" for a name you misremember.

  Whole answers stay exact (`2**100` prints all of it), and everything else is
  rounded to twelve significant digits, so `0.1 + 0.2` prints `0.3` instead of
  the true-but-useless `0.30000000000000004`; `--precision 17` shows the exact
  binary value and `--base hex|bin|oct` re-bases a whole one. With no
  expression it reads one per line from stdin, where `ans` is the previous
  answer.

  The expression is parsed with Python's parser and then walked node by node,
  evaluating only the arithmetic listed above: no `eval`, no attributes, no
  indexing, no imports. Two calculations that are quick to type and impossible
  to finish — a power with more than 100,000 digits, a factorial over 10,000 —
  are refused rather than attempted.

- **`pydocx2pdf` converts Word documents to PDF.** Nothing renders a `.docx`
  faithfully except software that has read the same specification, and none of
  that runs on a phone — so there are two engines and the command picks the
  better one it can find. With LibreOffice installed the file goes to a
  headless conversion that keeps the document's own layout; without it, the
  `.docx` reader and `pymd2pdf` typeset the content from scratch, which needs
  no system binary and therefore works on Termux. `--engine` pins one
  explicitly, and asking for LibreOffice where there is none is an error
  rather than a silent downgrade; in `auto` a LibreOffice *failure* still
  falls back, because a PDF of the content beats no PDF. The output line names
  the engine that did the work. The headless run gets a throwaway user
  profile, which is what stops it from quietly converting nothing while you
  have LibreOffice open, and success is judged by the file on disk rather than
  by an exit code LibreOffice sets to 0 either way. Comments are left out by
  default — a PDF is the copy you send, not the copy you review — and
  `--comments` puts them back; `--keep-md` keeps the intermediate Markdown.

- **`pymd2html` converts Markdown to HTML.** One self-contained page per
  input: the stylesheet is embedded and nothing is fetched from the network,
  so the file opens the same from an email attachment, a USB stick or a phone
  with no signal. It follows the reader's light/dark preference and lays out
  in either direction from the same rules. The parser is part of pytoolbox
  rather than a Markdown library, so this needs no optional dependency and
  runs on Termux: headings with anchors, emphasis, code spans and fences,
  tables with per-column alignment, nested and task lists, blockquotes, rules,
  images, links, autolinks and raw HTML passthrough. A document with more
  Persian, Arabic or Hebrew letters than Latin ones becomes a right-to-left
  page on its own; `--rtl`/`--ltr` and `--lang` override that. `--fragment`
  emits the body alone for a template, `--css`/`--no-css` replace the
  stylesheet, `-o -` writes to stdout, and `--escape-html` shows raw HTML as
  text for documents you did not write. Link targets that would run code
  (`javascript:` and friends) never become links.

- **`pynet ip` can say where an address is.** `pynet ip --geo` adds city,
  region, country, coordinates, timezone and network to the public address,
  and `pynet ip 1.1.1.1` (or a hostname, which is resolved first) locates any
  address. The lookup is opt-in and never load-bearing: plain `pynet ip`
  contacts no geolocation service, and when the lookup fails — offline, or a
  blocked provider — the addresses still print and the exit code stays 0. Only
  an explicit `pynet ip ADDRESS`, where the location is the whole answer,
  exits 1. Three free keyless providers are tried in turn (ipwho.is, ipapi.co,
  freeipapi.com), all over HTTPS so the address is not sent in the clear, and
  each one's answer is normalised to the same field names. What comes back is
  where the address block is registered, which is often the ISP rather than
  anyone's actual location.

- **`pypdf2md` reads tables.** A PDF has no idea what a table is, and position
  alone cannot tell one from a two-column page — both are text, a band of air,
  then more text, lined up down the page. But a table is *drawn*, so the grid
  is now read off the rectangles the writer painted, which also settles where
  an empty cell was and which lines belong to a cell that wrapped. Cells come
  out as a GitHub-flavoured Markdown table, right-aligned for a right-to-left
  document, and a table split over a page break is sewn back together by its
  repeated header. A table nobody drew a border for is still read as running
  text, which is the old behaviour: no table is invented from spacing alone.

- **`pypdf2md` reads Persian, Arabic and Hebrew in the right order.** A PDF
  stores glyphs in the order they are painted, which for these scripts is the
  order they appear on the page, not the order they are read in. Everything
  after the reader now works from that assumption: the bidirectional algorithm
  is run in reverse to recover reading order, shaped presentation forms
  (`ﻣ`, `ﯽ`) fold back to the letters someone can search for (`م`, `ی`), and
  the zero-width non-joiners that make `می‌شود` a different word from `میشود`
  are put back — recovered from the unjoined shapes the letters kept, or from
  a space the writer drew but never advanced past. Vowel marks are returned to
  the letters they sit on, headings and lists are measured from the margin the
  text starts at rather than always from the left, and a table's first column
  is the rightmost one. Needs `python-bidi`, now part of the `pdf2md` extra;
  without it those documents still convert, but stay in painted order.

- **`pymd2pdf` draws glyphs its main font lacks instead of dropping them.**
  Vazir has no arrows, maths symbols or emoji, so a Persian document full of
  `⚠ ↔ ✗ ✅ 🔴` printed a wall of *"Font Vazir is missing the following
  glyphs"* and left blanks in the PDF. DejaVu is now registered as a per-glyph
  fallback face, followed by a symbol font (Symbola, Noto Sans Symbols, Segoe
  UI Symbol, Apple Symbols) when one is installed — so each character is drawn
  by the first face that actually has it. `--fallback-font FILE` adds any other
  face and is repeatable.

  Colour emoji fonts are rejected on purpose: they store bitmaps rather than
  outlines, so registering one would claim coverage of every emoji and then
  draw nothing.

  What no installed face can draw still degrades to a text stand-in (`→` to
  `->`, `✅` to `✓`), but that table is now driven by the fonts' real coverage
  rather than assumed from the font's name, and applies to Latin text as well
  as Persian. Colour-coded status emoji are always replaced with `●` `◐` `○`:
  PDF text is one colour, so 🟢 and 🔴 would otherwise render as the same black
  disc. Invisible variation selectors (`U+FE0F`) are dropped rather than
  reported as missing.

### Changed

- **`pymd2pdf` is now a package rather than one 1500-line module.** Font
  discovery, Persian shaping, the fpdf2 subclass, the block renderers, tables
  and images/Mermaid each live in their own module under `pytoolbox/mdpdf/`,
  matching how `pydocx2md` and `pypdf2md` were already laid out; `pymd2pdf`
  itself is left with the conversion loop and the command line. The settings a
  conversion shares — body size, offline mode, the glyph substitution table —
  moved to `mdpdf.state`, because a value written in one module and read in
  another has to live somewhere both can see it rebound. Rendered output is
  unchanged, byte for byte.

- **`pyjdate interval` and `pynet whois` learned `--json`.** They were the
  last two commands in their tools that printed a result without one —
  `interval` gives `start` and `end` in the shape `convert --json` already
  used, and `whois` keeps the free-form record as a single string alongside
  the server that answered, which is what makes it safe to embed.

- **`pyssh` no longer accepts `--reconnecting` as a second spelling of
  `--reconnect`.** It was an undocumented alias, not the off-switch of a
  `--flag/--no-flag` pair, and reading it as one was the natural mistake.

- **`-n` means `--dry-run` in every `pyfm` command that changes files.** It
  used to mean `--partitions` in `pyfm partition`, which was the one command
  where the flag everything else uses for "show me what you would do" instead
  took a number and did the thing — so `partition`'s own dry-run had to be
  spelled out in full. `--partitions` keeps working; it just no longer has a
  short form. `pyfm generate-text-file` still takes `-n` as its file count:
  it creates new files rather than rewriting existing ones, so it has no
  dry-run for `-n` to collide with.

- **`pyfm generate-text-file` drops the `--num_files`, `--num_lines` and
  `--name_prefix` spellings** in favour of the hyphenated names that every
  other option in the project uses. `--num-files`, `--num-lines` and
  `--name-prefix` are unchanged.

- **`pystr` states its `--encoding` and `--errors` identically everywhere.**
  The eight commands that take them described the same two options in two
  different ways and showed the default in half of them; they now share one
  definition, so `--help` reads the same whichever command you asked about.

### Fixed

- **`pymd2pdf --font-size` left bold and italic text at the old size.** The
  helper that draws a line's styled runs took the body size as a default
  argument, and a default is evaluated once when the module is imported —
  so it kept the original 10pt while the plain text around it scaled. A
  document set at `--font-size 20` came out with every bold word rendered at
  half the size of the sentence containing it. The size is now read when the
  helper runs.

- **`pypdf2md` dropped every half-space in a word-processor document.** A word
  processor draws the four shapes of an Arabic letter as four different glyphs
  and maps all four back to the one plain letter, so nothing about the shaping
  reaches the text and `به‌موقع` came back as `بهموقع` — a different word. The
  glyph *numbers* still differ, and reading the whole document tells them
  apart: a glyph most of whose uses are where nothing could follow it is a
  shape that joins nothing after it, and one of those used anywhere else was
  closed by something invisible. The two kinds are nowhere near each other on
  that measure — 1–5% against 66–99% in the file this was written against — and
  a font that draws a letter only one way is left alone, since guessing from a
  single glyph would put a non-joiner inside every second word. 989 joiners
  came back in a 27-page document, none of them wrong.

- **`pypdf2md` reported some Persian digits as ASCII.** Digit glyphs come as a
  block of ten and a font draws one set of them, so a font claiming eight of
  its ten are Persian and the other two ASCII is not describing a font that
  mixes the two — no such font exists — but two entries filled in wrongly.
  Taken at face value they turned `۱۴۰۵` into `1۴0۵`. A font with a clear
  majority for one set now has the odd ones out read as that set; a font that
  really holds both keeps both.

- **`pypdf2md` let one line's character spacing squeeze the whole page.**
  `Tc` belongs to the graphics state, so `Q` puts back whatever it was; neither
  pypdf's text-state stack nor the walk on top of it did that. A Word document
  setting `Tc -2.9` inside a `q … Q` to squeeze one justified line went on
  shrinking every measured width after it, which left runs overlapping, the
  page in the wrong order and Persian words interleaved letter by letter
  (`این سند` as `اینس ند`). `Tc`, `Tw`, `Tz`, `TL` and `Ts` are now saved and
  restored with the rest of the state.

- **`pypdf2md` reversed Arabic ligatures.** One glyph can stand for several
  letters — `لا` is drawn as a single shape — and the file spells them out in
  *reading* order while everything around them is in paint order. Reversing the
  line then turned the one piece that was already right, so `لازم` came back as
  `الزم` and `تلاش` as `تالش`. Multi-letter Arabic glyphs are now turned to face
  the same way as their neighbours before anything else looks at them.

- **`pypdf2md` read a bordered table as several empty ones.** Two writer habits
  broke the grid: giving borders a thickness with padding inside them, which
  leaves a three-point sliver beside every real row and column; and painting a
  background behind each *line* of a cell as well as behind the cell, whose
  edges look exactly like row boundaries. A track too narrow to hold a glyph is
  now given back to the row or column it was cut from, and a box drawn inside
  another box is left out of the grid — a drawn *line* still counts wherever it
  sits, since writers that stroke their borders draw them along the edge of the
  cell they have already filled.

- **`pypdf2md` had no real position for any word but the first on a line.**
  pypdf's text extraction never advances the text matrix for the glyphs it
  draws, so every run on a line reported the line's own origin — and some
  reported (0, 0). Every geometric rule in the package reads positions, so
  columns, paragraph breaks, indentation and the difference between a space
  and a kern were all being decided from coordinates that did not exist. The
  page's content stream is now walked directly, measuring each run against the
  font's own widths, and each run also reports where it stops. pypdf's
  supported visitor stays as a fallback if that walk ever fails.

  The same extraction was also mangling Persian on its own account: it treats
  Arabic-Indic digits as right-to-left, so `۲۵ مرداد ۱۴۰۵` came back as
  `۵۲ ﻣﺮﺩﺍﺩ ۵۰۴۱`, and it emitted direction changes as separate fragments that
  were then concatenated in painted order. Text now comes out of the reader
  untouched, in painted order, and is reordered once, properly.

- **`pymd2pdf` printed raw Markdown inside table cells.** fpdf2's own markdown
  parser understands `**bold**` but not links, so a cell holding
  `[Mercor](https://www.mercor.com/)` printed that source verbatim — and
  padded the column to the width of the URL. Cells now show the label; a cell
  that is *only* a link becomes a real, clickable PDF link annotation, drawn
  blue and underlined like links in body text.

  Persian cells were worse off: their text is shaped and bidi reordered before
  fpdf2 sees it, so no markdown in them could be parsed and emphasis around
  *part* of a cell printed its `**` markers literally. Those markers are now
  removed (whole-cell bold still renders bold, as before).

  Linked cells also each left a stray invisible click target behind, because
  fpdf2's cell-measuring pass leaks the annotations it creates whenever the
  page already has one. `pymd2pdf` now hands each table an empty annotation
  list and merges the real annotations back afterwards.

- **`pydocx2md` dropped bullets from style-based lists.** A paragraph is a list
  item in Word either because it carries the numbering itself or because its
  paragraph style does; only the first was read, so documents written with the
  built-in *List Bullet* and *List Number* styles came out as plain text with
  no markers at all. Styles are now resolved through their `basedOn` chain, a
  paragraph's own `numPr` still wins over the style's, and `numId="0"` — Word's
  way of cancelling a style's numbering — no longer makes a list item.

  Heading detection follows the same chain: a house style based on `Heading2`
  is now a level-2 heading instead of a paragraph.

- **`pydocx2md` dropped equations entirely.** Word writes them as Office
  MathML, which the inline reader never visited, so every formula vanished
  without trace. They are now converted to LaTeX between dollars: `$…$` inline
  and `$$…$$` for a displayed equation. Fractions, radicals, scripts,
  delimiters, n-ary operators, accents, bars, limits, matrices and equation
  arrays are translated; anything unrecognised degrades to the text inside it
  rather than disappearing.

- **`pydocx2md` could write a broken table row.** A line break inside a cell —
  from `w:br`, or now from a displayed equation — ended the row early. Breaks
  inside a cell become `<br>`.

- **`pyssh rsync-dir` built a broken command for an identity path containing a
  space.** The `-e` value was assembled with `" ".join(...)`, so
  `--identity '/my keys/id_ed25519'` reached ssh as two arguments. It is now
  built with `shlex.join`.

### Added

- **`pyssh rsync-dir` can now say what to copy, not just what to skip.**
  `--match GLOB` transfers only matching files, at any depth — the recursive
  "only these" form, which in raw rsync means remembering to write
  `--include '*/' --include GLOB --exclude '*' --prune-empty-dirs` in that
  order. Because rsync applies filter rules first-match-wins while Click cannot
  preserve order between two different options, the order is now fixed and
  documented: excludes always beat matches, so `-e node_modules --match '*.js'`
  skips `node_modules`.

  Patterns are globs, and the two ways that silently transfer nothing are now
  handled. `{a,b}` is expanded before rsync sees it, since rsync has no brace
  expansion and a quoted `*.{jpg,png}` would match zero files without
  complaint. Regex-shaped patterns are rejected with the glob they probably
  meant, triggering only on markers meaningless to a glob — `.*` stays valid,
  being the ordinary way to say "dotfiles". `--raw-patterns` opts out of both.

  Also added: `--gitignore`, `--match-from`/`--exclude-from`, `--files-from`,
  `--min-size`/`--max-size`; `--mirror`, `--backup-dir`, `--stats` and a
  confirmation prompt before any deleting run (`-y` to skip, never prompted for
  `--dry-run`); `--bwlimit`, `--no-compress`, `--sudo`, `-o/--ssh-option`, and
  `user:password@host:/path` targets via `sshpass`, which the tunnel commands
  already accepted but `rsync-dir` did not; `-c/--checksum`, `--size-only` and
  `--existing`. Combinations that cancel out are rejected up front rather than
  handed to rsync.

- **New `pypdf2md` command** (`toolbox pdf2md`) — converts a digital PDF to
  Markdown, working the structure out from the page. A PDF stores placed
  glyphs, not structure, so headings come from the outline where the author
  left bookmarks and from font size where they did not; paragraphs are
  reflowed and words split by a hyphen at a line break are rejoined; bullet
  and numbered lists keep their nesting; links come from annotations and from
  bare URLs; embedded images are extracted to `<name>.assets/`.

  Running headers, footers and page numbers are dropped once they have
  repeated on three pages, with digits normalised on short lines so `Page 3`
  and `Page 4` count as one footer while `Chapter 3: Methods` stays a heading.
  Two-column pages are detected from the whitespace gutter and read one column
  at a time — necessarily before lines are assembled, since both columns share
  their baselines. `--single-column` overrides it.

  Tables are deliberately **not** inferred: guessing one from column-aligned
  text destroys content that otherwise survives as plain lines. Scanned files
  are reported with a pointer to `ocrmypdf` rather than converted to nothing.
  Needs the new `pdf2md` extra (`pypdf`), which is pure Python and installs on
  Termux without a compiler.

- **New `pydocx2md` command** (`toolbox docx2md`) — converts Word `.docx` to
  Markdown and keeps the comments, which is the part every other converter
  drops. Each comment gets a numbered marker at the text it annotates and its
  thread quoted under the containing paragraph or table; replies nest as
  `[1.1]`, and comments marked done are labelled `(resolved)`. Headings, lists,
  tables, images, footnotes and hyperlinks convert; tracked insertions are kept
  and tracked deletions dropped, so the output reads as the final text.

  The reader is built on `zipfile` and `xml.etree`, so this adds **no
  dependency** and works on a bare Termux install. Headings are matched on the
  OOXML style id rather than its display name, so documents authored in a
  localised Word convert correctly.

- **Shell completion for every command.** `toolbox completion [bash|zsh|fish]`
  prints the setup for `toolbox` and every standalone `py*` command at
  once, and detects the shell from `$SHELL` when none is given. Subcommands,
  options, `--format` choices and path arguments all complete.
- `toolbox doctor` points at `toolbox completion`.

### Fixed

- A missing optional dependency no longer breaks shell completion for the
  *whole* umbrella command. Completion asks the lazy group for every
  subcommand, so the import error raised while loading (say) `md2pdf` without
  `fpdf2` installed left `toolbox <TAB>` printing a traceback and no
  candidates. The subcommand now stays listed and raises its install hint only
  when it is actually invoked.

## [0.2.0] — 2026-08-09

A broad overhaul: new commands, a shared core, real tests, and fixes for
several bugs that made parts of `pyssh` unusable. Existing command and flag
names are unchanged; everything below is additive except where noted.

### Added

**New `toolbox` umbrella command** — `toolbox fm|str|jdate|time|ssh|net|md2pdf`
mirrors the standalone `py*` commands, which all still work. Subcommand
modules load lazily, so an optional dependency missing for one command does
not break the rest.

- `toolbox doctor` — reports which Python packages, system tools, clipboard
  helpers and directories are available, with install hints for what is not.
- `toolbox where` — prints the directories pytoolbox reads and writes.

**New `pynet` command** — the entry point existed in `setup.py` but the module
did not, so every install shipped a broken `pynet` script. It now exists:
`ip`, `dns`, `port`, `scan`, `ping`, `http`, `serve`, `whois`, `url`. All of
it works unprivileged, including on Termux; `ping` falls back to timed TCP
handshakes where ICMP is unavailable.

**`pyfm`**

- `duplicates` — finds files with identical contents (size grouping first,
  hashing only where sizes collide), with `--delete`.
- `organize` — sorts loose files into subdirectories by extension, date or
  first letter.
- `--dry-run` on every destructive command.
- `-R/--recursive` for `batch-find-replace`.
- `--copy` for `partition` and `merge`; `--stdout`, `--unique`, `-o` and
  `--no-filter` for `extract-links`.

**`pystr`**

- `case` — lower, upper, title, snake, kebab, camel, pascal, slug,
  slug-unicode.
- `encode` / `decode` — base64, base64url, hex, url, url-plus, rot13.
- `count` — line, word, character and byte counts, plus `--top N` words.
- `--json` for `search`.

**`pyjdate`**

- Weekday names in both calendars in all output.
- `--fa` prints Jalali month and weekday names in Persian script.
- `--json` for `current`, `convert`, `distance` and `distance-between`.
- `now` as an alias for `current`.
- A human-readable duration line (`1d 12h`) in distance output.

**`pytime`**

- `status` — what is being timed right now, with elapsed time (`--json` for
  statuslines).
- `resume` — start a new entry from a previous entry's project and task.
- `projects` / `tasks` — summaries with entry counts, total hours and last use.
- `--format json` for reports, and a totals line under table output
  (`--no-total` to omit).
- `$PYTIME_DB` to set the database path without passing `--db`.

**`pyssh`**

- `status` and `stop` for tunnels started in the background.
- `-b/--background`, `-i/--identity`, `-o/--ssh-option` for both tunnel
  commands.
- `--delete`, `-e/--exclude`, `--identity` and `-n/--dry-run` for `rsync-dir`.

**`pymd2pdf`**

- Blockquotes, task lists, links (as real PDF links), italic and
  strikethrough.
- `--page-size`, `--landscape`, `--margin`, `--font-size`, `--no-title-page`,
  `-d/--output-dir`, `-q/--quiet`.
- `--offline` — never touches the network (no remote images, no mermaid.ink).
- Vazirmatn is preferred over Vazir, with Noto Naskh Arabic as a last resort.

**Project**

- A test suite: 290 tests covering calendar conversion, interval parsing,
  filesystem logic, and every CLI. Runs offline in a few seconds.
- GitHub Actions CI across Python 3.9–3.13 on Linux plus macOS and Windows,
  a lint job, and a job that verifies a bare install still works.
- `docs/` with a page per command, `CONTRIBUTING.md`, this changelog.
- `pyproject.toml` replaces the metadata in `setup.py`, with `pdf`, `rtl`,
  `excel`, `socks`, `all` and `dev` extras.

### Fixed

- **`pyssh tunnel` never stayed up.** The non-reconnecting path started ssh
  and then immediately ran its cleanup handler, killing the tunnel it had just
  reported as running. It now blocks until Ctrl-C (or detaches with
  `--background`) and waits for the port to actually listen before claiming
  success.
- **`pyssh` crashed on key-only servers.** A spec without a password wrote
  `None` to the sshpass file and raised `TypeError`. Key authentication is now
  the normal path, and `sshpass` is only required — and only reported as
  missing — when a password is actually supplied.
- **`pyssh` wrote PID and password files inside the installed package.** They
  now go to `$XDG_RUNTIME_DIR/pytoolbox` with owner-only permissions and are
  removed once the handshake completes. This is the one behaviour change in
  this release; a `pip install`ed copy could not write to its own directory at
  all before.
- **`pyssh rsync-dir` built a shell string** with a mis-quoted `-e` value, so
  the ssh port was passed inside literal quotes and paths with spaces broke.
  It now passes an argument list with no shell involved.
- **`pystr search --text/--stdin` required a PATH** despite the help saying it
  could be omitted.
- **`pymd2pdf` could not find fonts on Termux** — no `$PREFIX/share/fonts` or
  `~/.termux/fonts` in the search path, and a partial DejaVu install crashed
  instead of falling back.
- `pyfm merge` no longer prunes source directories when nothing was moved, and
  no longer harvests files out of the destination directory when it sits
  inside the source tree.
- `pyfm batch-rename` skips and reports renames that would overwrite an
  existing entry instead of silently clobbering it.
- `pyfm partition` rolls back the directories it created when a name collides,
  rather than leaving a half-built layout behind.
- Interval parsing errors now name the unit that was not understood and list
  the ones that are.

### Changed

- Shared logic moved into `pytoolbox/core/`: the PostgreSQL interval parser
  (previously duplicated between `pyjdate` and `pytime`), directory walking
  (duplicated between `pyfm` and `pystr`), clipboard access, and table
  rendering. Public names are re-exported from their old modules.
- `-h` works as `--help`, and `-V/--version` was added, on every command.
- Command groups accept unambiguous prefixes (`pyfm part` → `pyfm partition`)
  and suggest alternatives for typos.
- Progress, warnings and errors moved to stderr so results can be piped; and
  colour is suppressed when output is not a terminal or `NO_COLOR` is set.
- `pyfm merge` defaults to `--overwrite keep-both` instead of prompting.
- `pytime` records whole-second timestamps; sub-second precision was noise.
- `readme.md` renamed to `README.md`.

## [0.1.0]

Initial version: `pyfm`, `pystr`, `pyjdate`, `pytime`, `pyssh`, `pymd2pdf`.
