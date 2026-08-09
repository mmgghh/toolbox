# Changelog

All notable changes to this project are documented here.
This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Shell completion for every command.** `toolbox completion [bash|zsh|fish]`
  prints the setup for `toolbox` and all seven standalone `py*` commands at
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
