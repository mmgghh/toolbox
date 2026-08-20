# Contributing

## Setup

```shell
python3 -m venv venv
source venv/bin/activate
pip install -e ".[all,dev]"
```

## Checks

```shell
pytest              # the whole suite, offline, a few seconds
ruff check .
ruff check . --fix
```

The suite never touches the network and never writes outside `tmp_path`.
Tests that need DejaVu fonts skip themselves when the fonts are absent.

## Layout

```
pytoolbox/
  cli.py          the `toolbox` umbrella command
  core/           everything shared between CLIs
    paths.py      XDG/Termux directories, font discovery
    console.py    output helpers, stdout vs stderr, colour, JSON
    intervals.py  PostgreSQL-style interval parsing and duration formatting
    fs.py         directory walking, hashing, size and text heuristics
    clipboard.py  per-platform clipboard backends
    tables.py     table/markdown/csv/json/excel rendering and export
    options.py    reusable Click options, AliasedGroup, context settings
    markdown.py   Markdown escaping and emphasis, shared by both writers
  dataset/        reading JSON/CSV/Excel into a schema and into SQL
    types.py      the value-type lattice and CSV text inference
    naming.py     keys and headers folded into SQL identifiers
    readers.py    the JSON, CSV and Excel readers
    sources.py    finding the rows in a document, and --root
    schema.py     the inferred structure tree, and its top level as columns
    render.py     the tree view
    summarize.py  per-field statistics
    select.py     filtering fields by name and by value type
    edit.py       which names a rename touches, and to what
    writers.py    renamed names written back into the file
    interactive.py  the --interactive prompts
    sql/          dialects, the script emitter and the SQLite back end
  docx/           the .docx reader and its Markdown writer
  pdf/            the PDF reader, layout and structure inference
  mdpdf/          the Markdown-to-PDF typesetter
    state.py      settings one conversion shares (body size, offline, glyphs)
    shaping.py    Persian/Arabic reshaping and bidi reordering
    fonts.py      DejaVu, Persian and symbol-fallback face discovery
    document.py   the fpdf2 subclass, colour palette and page metrics
    render.py     headings, paragraphs, lists, quotes, inline markers
    tables.py     column measurement and grid drawing
    media.py      images, SVG and Mermaid diagrams
  pyfm.py pystr.py pyjdate.py pytime.py pyssh.py pynet.py pycalc.py pydata.py
  pymd2pdf.py pymd2html.py pydocx2md.py pydocx2pdf.py pypdf2md.py
  data.py normalize_data.py
tests/
docs/
```

Anything used by two or more CLIs belongs in `core/`. `pytime` builds on
`pyjdate` for calendar work; that is the only cross-CLI dependency.

## Conventions

**CLI surface.** Every command group uses `AliasedGroup` and
`CONTEXT_SETTINGS` from `core.options`, so `-h`, prefix matching and
"did you mean" behave the same everywhere. Reuse the shared option decorators
(`verbose_option`, `yes_option`, `dry_run_option`, `json_option`,
`format_option`, `version_option`) rather than redeclaring them.

**Streams.** Results go to stdout; progress, warnings and errors go to stderr
(`console.info`, `console.warn`, `console.error`). This is what keeps
`pystr search ... | xargs` working with `-v`.

**Destructive commands** must support `--dry-run` and confirm before acting
unless `--yes` is given. Use `console.confirm`, which returns the default
answer in non-interactive sessions instead of raising on EOF.

**Errors** are `click.ClickException` with a message that says what to do
next, not a traceback.

**Backwards compatibility.** Existing flag names and command names are kept.
Add new spellings as aliases rather than renaming.

**Termux.** Never assume a system binary exists. Check with `shutil.which`
and fall back to a pure-Python path, or explain what to install. Never write
inside the package directory — use `core.paths`.

**Help text.** Every command's docstring ends with an `Examples:` block inside
`\b`, showing two or three real invocations.

## Tests

Use the `runner` fixture (a Click `CliRunner`) for CLI tests and plain
functions for logic. `result.stdout` is stdout alone; `result.output` also
contains stderr — parse JSON from `result.stdout`.

Fixtures in `conftest.py`: `tree` (a small directory tree with a known
duplicate), `isolated_home` (redirects every pytoolbox directory into
`tmp_path`).

Test behaviour through the public surface, and prefer a failing assertion that
names the real-world consequence over one that pins an implementation detail.

## Adding a command

1. Write it in the relevant module, or a new `py<name>.py` for a new area.
2. Register the console script in `pyproject.toml` under `[project.scripts]`.
3. For a new area, add it to `SUBCOMMANDS` in `pytoolbox/cli.py`.
4. Add tests and a section in `docs/`.
5. Add a line to `CHANGELOG.md`.
