# toolbox

A set of small, dependency-light command-line tools for everyday local work —
files, text, Jalali/Gregorian dates, time tracking, SSH tunnels, network
checks, process/memory management and conversion between Markdown, Word, PDF
and HTML.

Runs on Linux, macOS, Windows and **Termux**. Anything that needs a system
tool degrades to a pure-Python fallback when that tool is missing, so the same
commands work on a phone and on a laptop.

```shell
toolbox jdate now              # today in both calendars
toolbox fm duplicates ~/photos # find identical files
toolbox net port example.com 443
toolbox ps top                 # processes eating the most memory/swap
toolbox time start "write docs"
toolbox md2pdf notes.md
toolbox md2html notes.md       # one self-contained page
toolbox docx2md reviewed.docx  # Word to Markdown, comments and all
toolbox docx2pdf report.docx   # Word to PDF
toolbox pdf2md paper.pdf       # PDF to Markdown, structure inferred
```

## The commands

| Command    | Also as        | What it does                                                    | Docs |
| ---------- | -------------- | --------------------------------------------------------------- | ---- |
| `pyfm`     | `toolbox fm`     | Split, merge, rename, deduplicate and organize files            | [docs/pyfm.md](docs/pyfm.md) |
| `pystr`    | `toolbox str`    | Search, replace, clipboard, case conversion, encoding, Persian normalization | [docs/pystr.md](docs/pystr.md) |
| `pyjdate`  | `toolbox jdate`  | Jalali ↔ Gregorian conversion, intervals, distances              | [docs/pyjdate.md](docs/pyjdate.md) |
| `pytime`   | `toolbox time`   | Time tracking in a single SQLite file, with reports              | [docs/pytime.md](docs/pytime.md) |
| `pyssh`    | `toolbox ssh`    | SOCKS tunnels (single and chained) and an rsync wrapper          | [docs/pyssh.md](docs/pyssh.md) |
| `pynet`    | `toolbox net`    | IP and its location, DNS, ports, ping, HTTP, WHOIS, quick file server | [docs/pynet.md](docs/pynet.md) |
| `pyps`     | `toolbox ps`     | Top memory/swap consumers, search and kill by name, `free`-like summary (Linux/Termux) | [docs/pyps.md](docs/pyps.md) |
| `pymd2pdf` | `toolbox md2pdf` | Markdown to PDF, including right-to-left Persian/Arabic          | [docs/pymd2pdf.md](docs/pymd2pdf.md) |
| `pymd2html` | `toolbox md2html` | Markdown to one self-contained HTML page, no dependencies     | [docs/pymd2html.md](docs/pymd2html.md) |
| `pydocx2md` | `toolbox docx2md` | Word to Markdown, with every comment anchored to its text      | [docs/pydocx2md.md](docs/pydocx2md.md) |
| `pydocx2pdf` | `toolbox docx2pdf` | Word to PDF, via LibreOffice when it is installed           | [docs/pydocx2pdf.md](docs/pydocx2pdf.md) |
| `pypdf2md` | `toolbox pdf2md` | PDF to Markdown, structure inferred from the page               | [docs/pypdf2md.md](docs/pypdf2md.md) |

Every command supports `-h/--help` and `-V/--version`, accepts unambiguous
subcommand prefixes (`pyfm part` == `pyfm partition`), suggests alternatives
when you mistype one, and has [shell completion](#shell-completion).

## Install

```shell
git clone https://github.com/mmgghh/toolbox.git
cd toolbox
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip

pip install -e .            # core commands
pip install -e ".[all]"     # everything, including Markdown-to-PDF
```

### Extras

The base install stays deliberately small (just `click` and `requests`) so it
installs anywhere without a compiler. Add what you need:

| Extra   | Adds                              | Needed for                              |
| ------- | --------------------------------- | --------------------------------------- |
| `pdf`   | `fpdf2`, `Pillow`                 | `pymd2pdf`, `pydocx2pdf` without LibreOffice |
| `pdf2md`| `pypdf`, `python-bidi`            | `pypdf2md`                              |
| `rtl`   | `arabic-reshaper`, `python-bidi`  | Persian/Arabic shaping in `pymd2pdf`    |
| `excel` | `openpyxl`                        | `pytime report --format excel`          |
| `socks` | `requests[socks]`                 | `pyssh --reconnect` proxy health checks |
| `all`   | all of the above                  |                                         |
| `dev`   | `pytest`, `pytest-cov`, `ruff`    | working on toolbox itself               |

### Shell completion

`toolbox completion` prints the setup for `toolbox` **and** every `py*`
command in one go. It supports bash, zsh and fish, and detects your shell from
`$SHELL` when you do not name one.

```shell
eval "$(toolbox completion bash)"        # this session only
toolbox completion bash >> ~/.bashrc     # every session
toolbox completion zsh  >> ~/.zshrc
toolbox completion fish > ~/.config/fish/completions/pytoolbox.fish
```

Subcommands, options, `--format` choices and path arguments all complete. A
missing optional dependency only affects its own command: `toolbox <TAB>` keeps
working even without the `pdf` extra installed.

### Check your setup

```shell
toolbox doctor
```

Reports which Python packages, system tools and clipboard helpers are
available on this machine, and what to install for the rest.

## Termux

Everything works on an unrooted Android device. A full setup:

```shell
pkg install python git openssh rsync fontconfig-utils
pkg install termux-api          # for pystr clipboard commands
pip install -e ".[all]"
```

Notes specific to Termux:

- `pynet ping` uses TCP handshakes when ICMP is unavailable, so it needs no
  root. Force it anywhere with `--tcp`.
- `pynet serve` is the easiest way to move a file between phone and laptop.
- `pymd2pdf` looks for fonts in `$PREFIX/share/fonts` and `~/.termux/fonts`
  as well as the usual Linux locations.
- Clipboard commands use `termux-clipboard-get/set` (install `termux-api`
  and the Termux:API app).
- If a compiled dependency will not build, skip the extra: `pip install -e .`
  keeps every command except `pymd2pdf` working.

## Where files are kept

| Purpose               | Location                                                  |
| --------------------- | --------------------------------------------------------- |
| Time-tracking database | `~/.pytime/pytime.db`, or `$PYTIME_DB`                    |
| Config                | `$XDG_CONFIG_HOME/pytoolbox` (`~/.config/pytoolbox`)      |
| Cache                 | `$XDG_CACHE_HOME/pytoolbox` (`~/.cache/pytoolbox`)        |
| Tunnel state/secrets  | `$XDG_RUNTIME_DIR/pytoolbox`, owner-only                  |

Set `PYTOOLBOX_HOME` to keep everything under one directory. `toolbox where`
prints the paths in use. macOS and Windows use their conventional locations
instead of the XDG ones.

## Shared conventions

- `-h/--help` everywhere, `-V/--version` everywhere, completion everywhere
  (`toolbox completion`).
- `-v/--verbose` (repeatable) for progress detail, `-q/--quiet` to suppress it.
- `-n/--dry-run` on anything that changes files, `-y/--yes` to skip prompts.
- `--json` (or `--format json`) wherever output is structured.
- Results go to **stdout**; progress, warnings and errors go to **stderr**, so
  piping works even with `-v`.
- Colour is disabled automatically when output is not a terminal, and by
  `NO_COLOR=1`.

## Development

```shell
pip install -e ".[all,dev]"
pytest              # 517 tests
ruff check .
```

The suite runs fully offline. Tests that need DejaVu fonts skip themselves
when the fonts are absent.

Shared code lives in [`pytoolbox/core/`](pytoolbox/core/): filesystem walking,
interval parsing, clipboard access, table rendering, output paths and the
Click option set every CLI reuses.

See [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).

## License

See [LICENSE](LICENSE).
