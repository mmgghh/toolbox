# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

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

### Fixed

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
