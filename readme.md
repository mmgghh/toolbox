# toolbox

`toolbox` is a collection of small Python command-line utilities for everyday local work:

- file management
- text search and replacement
- Jalali/Gregorian date conversion
- SQLite-backed time tracking
- SSH tunnel helpers
- Markdown-to-PDF conversion

It is intended to work on Linux, macOS, Windows, and Termux. The project has primarily been tested on Ubuntu with Python 3.9+.

## What is in this repository

### User-facing CLIs

| Command | Source module | Purpose |
| --- | --- | --- |
| `pyfm` | `pytoolbox/pyfm.py` | File and directory operations |
| `pystr` | `pytoolbox/pystr.py` | Text, clipboard, normalization, and translation helpers |
| `pyjdate` | `pytoolbox/pyjdate.py` | Jalali/Gregorian date conversion and interval utilities |
| `pytime` | `pytoolbox/pytime.py` | Time tracking with SQLite storage |
| `pyssh` | `pytoolbox/pyssh.py` | SOCKS tunnel helpers and `rsync` wrapper |
| `pymd2pdf` | `pytoolbox/pymd2pdf.py` | Markdown to PDF converter |

### Internal support modules

| Path | Role |
| --- | --- |
| `pytoolbox/data.py` | Shared sentence corpus and regex shortcuts used by `pyfm` |
| `pytoolbox/normalize_data.py` | Large normalization mapping used by `pystr normalize` |
| `pytoolbox/__init__.py` | Package marker |
| `setup.py` | Package metadata, dependencies, and console script registration |

> Note: `setup.py` currently declares a `pynet` console script, but `pytoolbox/pynet.py` is not present in this checkout. The commands documented below are the ones backed by source files in the repository.

## Requirements

- Python `>= 3.9`
- Core Python dependencies:
  - `Click`
  - `openpyxl`
  - `requests[socks]`
  - `fpdf2`
- Optional Python dependencies for better Persian/Arabic PDF rendering:
  - `arabic-reshaper`
  - `python-bidi`

### Optional system tools

Some commands depend on external programs:

- `pyssh tunnel` and `pyssh double-tunnel`: `ssh`
- password-based SSH helpers: `sshpass`
- `pyssh rsync-dir`: `rsync`
- clipboard commands in `pystr`: one of
  - `wl-copy` / `wl-paste`
  - `xclip`
  - `xsel`
  - macOS `pbcopy` / `pbpaste`
  - Windows PowerShell clipboard commands
  - Termux clipboard tools
- `pymd2pdf`: DejaVu fonts are required; Vazir fonts are optional for Persian/Arabic

## Installation

Clone the repository, create a virtual environment, and install it in editable mode:

```shell
git clone https://github.com/mmgghh/toolbox.git
cd toolbox
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install --editable .
```

If you want correct Persian/Arabic shaping in generated PDFs:

```shell
pip install --editable '.[rtl]'
```

## Quick start

```shell
pyfm --help
pystr --help
pyjdate --help
pytime --help
pyssh --help
pymd2pdf --help
```

## `pyfm`: file management

`pyfm` provides these subcommands:

- `partition`: split the direct children of a source directory into generated subdirectories
- `merge`: recursively move files from a source tree into one destination directory
- `batch-find-replace`: regex find/replace across top-level files with selected extensions
- `batch-rename`: rename files and directories by regex
- `generate-text-file`: create text files filled with bundled random sentences
- `extract-links`: extract HTTP/HTTPS links from a local file or URL into `links.txt`
- `file-find-replace`: literal find/replace in a single file

### `pyfm partition`

Choose exactly one of:

- `--partitions N`
- `--split-count N`
- `--split-size N`

Examples:

```shell
# Split direct children of /some/dir into 5 generated directories.
pyfm partition --partitions 5 --source /some/dir --split-based-on size

# Split /source into multiple directories with about 15 MB each.
pyfm partition --split-size 15 -s /source --dir-prefix part -v

# Split matching entries only.
pyfm partition --pattern '.*\.(jpg|png)$' --split-count 100 -s /photos
```

### `pyfm merge`

Examples:

```shell
# Merge files from all matching source subdirectories into one destination.
pyfm merge -s /source -d /destination --overwrite keep-both

# Move only .mp4 files from directories matching season names.
pyfm merge --file-pattern '.*\.mp4$' --dir-pattern '^Season' -s /shows -d /flat
```

### `pyfm batch-find-replace`

`batch-find-replace` accepts Python regex patterns. It also supports these bundled shortcuts:

- `<UUID4>`
- `<DOMAIN_PORT>`

Examples:

```shell
pyfm batch-find-replace -d ./docs -x md -x txt -f foo -r bar -v
pyfm batch-find-replace -d ./configs -x env -f '<DOMAIN_PORT>' -r 'example.com:443'
```

### `pyfm batch-rename`

Examples:

```shell
# Rename only direct children.
pyfm batch-rename -d ./downloads -f ' ' -r '_' -v

# Rename files and directories up to two levels deep.
pyfm batch-rename -d ./archive -f '2024' -r '2025' --include-dirs -D 2
```

### `pyfm generate-text-file`

Examples:

```shell
pyfm generate-text-file -d ./tmp -n 20 -l 50 -p sample -v
pyfm generate-text-file -d ./tmp -n 5
```

### `pyfm extract-links`

Examples:

```shell
# Extract links from a local HTML file.
pyfm extract-links -s ./page.html -d ./out -v

# Extract links from a URL and overwrite the previous links.txt.
pyfm extract-links -s 'https://example.com' --pattern '^https://example.com' --overwrite
```

### `pyfm file-find-replace`

Examples:

```shell
pyfm file-find-replace -p ./notes.txt -f old-value -r new-value -v
```

## `pystr`: text, clipboard, normalization, translation

`pystr` provides these subcommands:

- `search`: search files, directories, inline text, or stdin
- `replace`: preview or apply replacements across files
- `clip-search`: search the current clipboard contents
- `clip-replace`: replace text in the clipboard
- `normalize`: normalize Unicode-heavy text with the bundled mapping
- `translate`: convert digits/punctuation and common Arabic/Persian forms between `en` and `fa`
- `getclip`: print clipboard text
- `setclip`: write clipboard text

### Common `pystr search` tags

Instead of writing regex manually, `search` and `clip-search` support common tags:

- `url` / `link`
- `email`
- `ip`, `ipv4`, `ipv6`
- `phone` / `mobile`
- `zip`
- `postal`
- `date`
- `time`
- `uuid`
- `mac`

Examples:

```shell
# Search a directory tree.
pystr search ./src "TODO" -e py -v

# Regex search with stats.
pystr search . 'def\s+main' --regex -e py --stats

# Search for tagged content.
pystr search . --tag email --tag ip --only-matches

# Search inline text or stdin.
pystr search "token" --text "token=abcd"
echo "hello world" | pystr search "world" --stdin
```

### `pystr replace`

Examples:

```shell
# Preview first.
pystr replace ./src foo bar -e py --dry-run

# Apply changes and keep backups.
pystr replace ./docs TODO DONE -i --backup --yes

# Regex replacement.
pystr replace . '(\d+)' '[\1]' --regex --dry-run
```

### Clipboard helpers

Examples:

```shell
pystr clip-search "secret" --ignore-case
pystr clip-search --tag email --only-matches
pystr clip-replace "foo" "bar" --yes
pystr getclip --trim
echo "hello" | pystr setclip --stdin
```

### `pystr normalize` and `pystr translate`

Examples:

```shell
pystr normalize --text "Résumé - ١٢٣"
echo "متن   نمونه" | pystr normalize --stdin
pystr normalize ./notes.txt --inplace

pystr translate --to en --text "شماره ۱۲۳؟"
pystr translate --to fa --text "Issue 123?"
pystr translate ./notes.txt --to fa --inplace
```

## `pyjdate`: Jalali/Gregorian date tools

`pyjdate` provides these subcommands:

- `current`: print current time in Gregorian, Jalali, and Unix formats
- `convert`: convert full dates, partial dates, intervals, or Unix timestamps
- `interval`: show start/end of a year, month, day, or explicit date range
- `distance`: show distance from now to a target date
- `distance-between`: show distance between any two endpoints

### Supported input styles

Depending on the subcommand, inputs can be:

- Gregorian full dates like `2026-01-04 10:43`, `2026/01/04`, `Jan 04 2026`
- Jalali full dates like `1404/10/14 10:44:46`
- Unix timestamps
- PostgreSQL-style intervals like `1 y`, `-3.4 hours`, `2 days 04:30`

Examples:

```shell
pyjdate current

# Convert by interval relative to now.
pyjdate convert --interval "1 y"
pyjdate convert -i "-3.4 hours"

# Convert a concrete Gregorian or Jalali date.
pyjdate convert -c g --full-date "2026-01-04 10:43"
pyjdate convert -c j --full-date "1404/10/14 10:44:46"

# Show the range of an entire month or year.
pyjdate interval -c g -y 2026 -m 02
pyjdate interval -c j -y 1404

# Distance from now.
pyjdate distance -i "2 days 4 hours"
pyjdate distance -c g --full-date "2026-01-10 08:00"

# Distance between two endpoints.
pyjdate distance-between -c g -s "2026-01-01 00:00" -e "2026-01-02 12:00"
pyjdate distance-between -s "-3 days" -e "6 hours"
```

`distance` and `distance-between` print:

- Gregorian component distance
- Jalali component distance
- total days
- total hours
- total seconds

## `pytime`: SQLite-backed time tracking

`pytime` stores entries in:

```text
~/.pytime/pytime.db
```

You can override that with `--db /path/to/file.db`.

Available subcommands:

- `start`: start a new entry and automatically close any unfinished entry
- `end`: stop an active entry
- `add`: add a completed entry with start and end or start and duration
- `edit`: edit an existing entry
- `delete`: delete entries by filters
- `report`: print or export reports

### `pytime start` and `pytime end`

Examples:

```shell
pytime start -p toolbox "write README"
pytime end
pytime end --task "write README"
```

### `pytime add` and `pytime edit`

Examples:

```shell
# Add a completed entry.
pytime add -p toolbox "review PR" "2026-04-24 09:00" --end "2026-04-24 10:15" -c g

# Or compute the end from a duration.
pytime add -p toolbox "deep work" "1405/02/04 14:00" --duration "2 hours 30 minutes" -c j

# Edit by id or edit the most recent row.
pytime edit --id 3 --task "rewrite README"
pytime edit --last --duration "90 minutes"
```

### `pytime report`

Reports support:

- filtering by `--id`, `--project`, `--task`
- literal or regex filters
- time windows with `--interval`, `--start`, and `--end`
- grouping by `project`, `task`, `year`, `month`, `day`
- output formats: `table`, `csv`, `markdown`, `excel`

Examples:

```shell
# Plain table output.
pytime report --project toolbox

# Relative report window.
pytime report --interval "7 days"

# Grouped report.
pytime report --project toolbox --group-by project --group-by year,month,day -c g

# Export to files.
pytime report --format csv -o ./report.csv
pytime report --format markdown -o ./report.md
pytime report --format excel -o ./report.xlsx
```

### `pytime delete`

Examples:

```shell
# Delete one row.
pytime delete --id 7 --yes

# Delete matching rows and back them up to Excel first.
pytime delete --project toolbox --interval "30 days" --backup --yes
```

## `pyssh`: SSH helpers

`pyssh` provides these subcommands:

- `tunnel`: create a SOCKS proxy to a single remote server
- `double-tunnel`: create a SOCKS proxy to server 2 through server 1
- `rsync-dir`: wrapper around `rsync -avzP -e "ssh -p <port>"`

### `pyssh tunnel`

Examples:

```shell
# Using an inline server spec.
pyssh tunnel -s 'user:password@example.com:22' -p 9998

# Or from a one-line config file.
pyssh tunnel --server-conf ./server1.conf -p 9998 --reconnecting
```

Config files for `--server-conf`, `--server1-conf`, and `--server2-conf` are plain text files containing a single line in this format:

```text
user:password@host:port
```

### `pyssh double-tunnel`

Examples:

```shell
pyssh double-tunnel \
  --server1 'user1:password1@bridge.example.com:22' \
  --server2 'user2:password2@target.example.com:22' \
  --lp1 9998 \
  --lp2 9999
```

After a successful tunnel, traffic can be routed through:

```text
socks5://localhost:<port>
```

If `--public` is set, the SOCKS listener binds to `0.0.0.0`.

### `pyssh rsync-dir`

Examples:

```shell
pyssh rsync-dir -s ./local-dir -d user@example.com:/remote/path -p 22
pyssh rsync-dir -s user@example.com:/remote/path -d ./local-dir -p 22 --ignore-existing
```

## `pymd2pdf`: Markdown to PDF

`pymd2pdf` converts one or more Markdown files to PDF.

Supported Markdown features:

- headings
- bold text
- inline code
- fenced code blocks
- tables
- bullet lists
- numbered lists
- horizontal rules
- nested lists
- Persian/Arabic RTL rendering when the optional extras and fonts are installed

Examples:

```shell
# One file -> one PDF with the same base name.
pymd2pdf README.md

# One file -> custom output path.
pymd2pdf doc.md -o report.pdf

# Multiple files -> one PDF per input.
pymd2pdf a.md b.md c.md
```

### Fonts for `pymd2pdf`

DejaVu fonts are required. On common platforms:

```shell
# Debian/Ubuntu
sudo apt-get install fonts-dejavu-core

# Fedora/RHEL
sudo dnf install dejavu-sans-fonts dejavu-sans-mono-fonts

# Arch
sudo pacman -S ttf-dejavu

# macOS
brew install --cask font-dejavu
```

For better Persian/Arabic output, install `Vazir.ttf` and `Vazir-Bold.ttf` into one of:

- `~/.local/share/fonts`
- `/usr/share/fonts/truetype/vazir`
- `/usr/share/fonts/TTF`

Then refresh the font cache on Linux:

```shell
fc-cache -f
```

## Notes and caveats

- This repository is source-first. The most reliable source of truth for command behavior is `--help` and the modules in `pytoolbox/`.
- `pyfm batch-find-replace` operates on direct children of the target directory, not a recursive tree walk.
- `pyfm partition` also works on the direct children of the source directory.
- `pytime start` closes any previously unfinished entry before creating a new one.
- `pyssh` helpers depend on external programs and network access; they are thin wrappers, not full SSH clients.

## License

See `LICENSE`.
