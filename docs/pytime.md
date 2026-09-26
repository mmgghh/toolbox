# `pytime` — time tracking

Also available as `toolbox time`.

```
start      Start timing a task
status     Show what is currently being timed
end        Stop the running entry
resume     Start a new entry with a previous entry's project and task
add        Insert a completed entry
edit       Change an existing entry
delete     Remove entries matching filters
report     Report on tracked time, grouped and exported
projects   List projects with entry counts and total hours
tasks      List tasks with entry counts and total hours
auto       Automatic tracking from the active window (watch, service, today, report, suggest, ...)
```

Entries live in one SQLite file, so the data stays readable with any SQLite
client and syncs as a single file.

| Setting | Default |
| --- | --- |
| Database | `~/.pytime/pytime.db` |
| Override for one run | `--db /path/to/file.db` |
| Override permanently | `export PYTIME_DB=/path/to/file.db` |

---

## Daily use

```shell
pytime start -p toolbox "write docs"
pytime status
pytime end
pytime resume            # start the same task again
```

`start` closes any entry that is still running first, so you can never
accidentally have two timers going. `status` reports the running entry and how
long it has been open; `--json` makes it script- or statusline-friendly:

```shell
pytime status --json | jq -r '.entries[0].elapsed'
```

## Recording past work

```shell
# Explicit end time.
pytime add -p toolbox "review PR" "2026-04-24 09:00" --end "2026-04-24 10:15" -c g

# Or a duration.
pytime add -p toolbox "deep work" "1405/02/04 14:00" --duration "2 hours 30 minutes" -c j
```

`-c/--calendar` picks how the dates are read (`g`/`gregorian`, `j`/`jalali`).
Without it, pytime tries Jalali first and falls back to Gregorian, preferring
whichever lands closer to now — pass `-c` explicitly when it matters.

Entries longer than five hours print a warning, since that usually means a
timer was left running.

## Editing and deleting

```shell
pytime edit --id 3 --task "rewrite README"
pytime edit --last --duration "90 minutes"
pytime edit --id 7 --start "2026-04-24 09:30" -c g

pytime delete --id 7 --yes
pytime delete --project toolbox --interval "30 days" --backup --yes
```

`--backup` exports the matching rows to an Excel file before deleting (needs
the `excel` extra). Deletion asks for confirmation unless `--yes`.

## Reports

```shell
pytime report                                   # everything
pytime report --interval "7 days"               # relative window
pytime report -p toolbox -t docs                # filter by project/task
pytime report -p '^tool' --regex                # regex filters
pytime report -s "2026-04-01" -e "2026-04-30" -c g
```

**Grouping** with `-g/--group-by` (repeatable, comma lists allowed):
`project`, `task`, `year`, `month`, `day`. Grouping by month requires year,
and by day requires month.

```shell
pytime report -g project
pytime report -g project -g year,month,day -c g
```

**Formats** with `--format`: `table` (default), `csv`, `markdown`, `json`,
`excel`. Table output ends with a totals line unless `--no-total`:

```
Total: 12.5 hours (12:30) across 9 entries
```

```shell
pytime report --format json                     # to stdout
pytime report --format markdown -o week.md
pytime report --format excel -o report.xlsx     # needs the excel extra
```

With `-o`, the "written to ..." message goes to stderr, so
`pytime report --format json | jq` stays clean.

## Summaries

```shell
pytime projects
pytime tasks -p toolbox
pytime projects --json
```

```
project | entries | hours | last_used
--------+---------+-------+-----------
toolbox | 12      | 18.25 | 2026-08-09
notes   | 3       | 2.5   | 2026-08-02
```

## Automatic tracking (`pytime auto`)

`start`/`end` require you to remember to run them. `pytime auto` instead
watches the focused window and records time on its own, from what the window
title/class say -- no browser extension or editor plugin, nothing to
remember to type. Auto entries live in their own table and never mix with
manual ones (until you choose to copy them over with `suggest --apply`).

### Setup

```shell
toolbox doctor                             # which window backend works here (see Platform support)
pytime auto service install --now          # run the watcher now and at every login
# only needed for tmux/ssh; see "Terminals and Claude Code" below
echo 'eval "$(pytime auto shell-init bash)"' >> ~/.bashrc
```

### Day to day

```shell
pytime auto today                          # hours per project and per app, today
pytime auto today --days 7
pytime auto status                         # what's being tracked right now
pytime auto report -g app -g project       # detailed, filterable, exportable
pytime auto report --category editor -g ext
pytime auto suggest                        # proposed manual entries for today
pytime auto suggest --apply                # ...added to your manual pytime entries
```

### The service

`pytime auto service` manages a systemd *user* service (no root) that runs
`pytime auto watch` from login, restarting it if it exits -- e.g. when it
starts before the desktop is ready to say which window is focused.

```shell
pytime auto service install [--now] [--interval 5] [--idle-timeout 5] [--force]
pytime auto service enable [--now]         # start at login (--now: also start it now)
pytime auto service disable [--now]        # don't start at login (--now: also stop it)
pytime auto service start | stop | restart | status
pytime auto service logs [-f]              # activity changes and pauses, from the journal
pytime auto service uninstall              # stop, disable, remove the unit file
```

The unit pins the Python it was installed with (so a virtualenv keeps
working) and the database path, and records your `PATH` (backends like
`kdotool` often live in `~/.cargo/bin`). Re-run `install --force` after
moving either. If your desktop never reaches `graphical-session.target`
(some plain X11 window managers), install with `--target default.target`.
Restart the service after editing `~/.pytime/rules.json`.

Without systemd, run `pytime auto watch` from your desktop's autostart instead.

### Pauses

The watcher stops the current entry, rather than counting the time, while:
the screen is locked (GNOME, or any desktop with the freedesktop screensaver
D-Bus interface); you've been idle longer than `--idle-timeout` minutes (GNOME,
or X11 with `xprintidle`); no window is focused; or the focused window matches
an `ignore` rule.

### Terminals and Claude Code

For a focused terminal, `pytime auto` doesn't trust the title: it follows the
window's process in `/proc` to each tab's shell, takes the tab you typed into
most recently, and reads what's running there and in which directory. That
directory is resolved to its git repository root:

```
claude running in ~/projects/samt/src   -> Terminal (Claude Code), project samt
nvim pytime.py in ~/projects/toolbox    -> Terminal, project toolbox, file pytime.py (py)
a prompt in ~/projects/toolbox          -> Terminal, project toolbox, detail "shell"
a prompt in ~                           -> Terminal, no project
```

If the tab is attached to tmux, tmux is asked which pane is active and that
pane is read the same way -- no tmux configuration needed, and `-L`/`-S`
sockets are followed.

Only the program's name (and, for terminal editors, the file's name) is
stored, never its arguments. This needs no setup, but it can't see inside
`ssh`, `screen`/`zellij` or containers, and sandboxed (Flatpak) terminals
hide their processes entirely. There the title is used -- the tmux pane's
title for ssh inside tmux, else the window's -- and `pytime auto shell-init
bash|zsh` makes the title useful: it keeps it as `<command> @ <git root>`,
and works across ssh if the remote shell runs it too:

```shell
# pytime must be on PATH when the rc file runs; from a virtualenv, use its full path:
echo 'eval "$(~/projects/toolbox/venv/bin/pytime auto shell-init bash)"' >> ~/.bashrc
```

Like the `/proc` reader, the hook only puts a command's first word in the
title. It also sets `CLAUDE_CODE_DISABLE_TERMINAL_TITLE=1`, since Claude Code
would otherwise replace the title with its conversation topic. In bash it
uses bash-preexec if you have it, or a `DEBUG` trap otherwise (replacing any
`DEBUG` trap you had).

`pytime auto probe` on a terminal shows both what `/proc` found and the raw
title, so you can see which one was used.

### Reports, search and cleanup

`report` and `delete` take the same filters: `--id`, `-p/--project`,
`--no-project`, `--app`, `--category`, `--ext`, `-q/--search` (substring of
the captured title/file/page), `--interval`, `-s/--start`, `-e/--end`.

```shell
pytime auto report --no-project -g app     # what the rules didn't attribute
pytime auto report -q youtube              # search captured titles
pytime auto report --min-seconds 0         # every stored entry, unfolded
pytime auto delete --app Chrome -q youtube # preview + confirm, then delete
pytime auto delete --interval "1 hour" --yes
pytime auto delete --all --yes             # every auto entry; manual entries are never touched
```

`report` folds back-to-back entries shorter than `--min-seconds` (default 10)
into the entry before them, so an alt-tab through three windows doesn't show
as three rows; totals are unchanged and stored entries are never modified.
Grouping by `project` alone puts everything unattributed in one blank row --
add `-g app` to split it.

### Suggested manual entries

`pytime auto suggest` turns auto entries into timesheet-style blocks: a
project's entries join across breaks shorter than `--merge-gap` (10 minutes),
a brief detour inside a stretch of work (a two-minute look at chat) is
absorbed into it, unattributed time is dropped, and blocks shorter than
`--min-block` (15 minutes) are left out. `--apply` adds them as manual
entries (task `auto-tracked`, or `-t`), skipping any block that overlaps an
existing manual entry -- so running it twice never double-counts.

### How windows are classified

There is no install-free way for a CLI to read a browser tab's real URL or an
editor's real open file -- tools that do that (WakaTime, ActivityWatch's
browser integration) rely on an editor plugin or a browser extension.
`pytime auto` works from the window title and class, with heuristics for
common apps:

- **Editors** (VS Code, any JetBrains IDE, Sublime, vim/nvim): the title's
  `file — project` (VS Code) or `project – file` (JetBrains) convention gives
  a file (and its extension) and a project.
- **Terminals**: the focused tab's program and directory from `/proc` (see
  above); otherwise a `~/path` or `shell-init` title, resolved to its git root.
- **Browsers** (Chrome, Chromium, Firefox, Brave, Edge, Opera, Vivaldi):
  GitHub tabs are attributed to their repository (`… · owner/repo` →
  `repo`), GitLab tabs to their project, Jira tabs (`[ABC-12] …`) to the
  project key; other tabs are matched against a table of known sites
  (ChatGPT, Claude, Notion, Figma, Slack, YouTube, Gmail, Google
  Docs/Sheets/Calendar, Linear, Trello, ...).
- **Desktop apps** whose titles say nothing useful (the Claude and ChatGPT
  apps, Slack, Discord, Telegram, Obsidian, Zoom, ...) take the app itself as
  the project -- the Claude app can't tell which of your projects a
  conversation was about.
- Anything else is category `other`, with the raw title as `detail` and no
  project rather than a guessed one.

If a report shows a blank project or the wrong app, run `pytime auto probe`,
focus that window within 3 seconds, and compare the raw `wm_class`/`title`
with the classification.

### `~/.pytime/rules.json`

Extends the built-in rules (never replaces them); every key is optional, and
`pytime auto rules` validates the file and prints this shape:

```json
{
  "projects": {"SAMT": ["samt", "samt-backend"]},
  "ignore": {"apps": ["org.keepassxc.keepassxc"], "titles": ["bank"]},
  "redact": {"apps": ["thunderbird"], "titles": ["private"]},
  "sites": {"my-site.com": "My Site"},
  "app_projects": {"com.example.app": "Example"},
  "editor_classes": ["my-editor-wm-class"],
  "terminal_classes": [],
  "browser_classes": [],
  "app_labels": {"my-editor-wm-class": "My Editor"}
}
```

- `projects` rolls different names for one project into one: a PyCharm
  project `samt`, a terminal in `~/work/samt`, and GitHub repo
  `org/samt-backend` can all report as `SAMT`. Aliases match the detected
  project name, case-insensitively.
- `ignore` (by window class, or title regex) skips a window entirely: while
  it's focused nothing is recorded. `redact` keeps the time and the app but
  stores no project, title or file. Private and incognito browser windows
  are redacted by default.
- A malformed file is reported (by `watch`, `probe` and `rules`) instead of
  silently ignored.

### Platform support

Wayland deliberately hides the focused window from ordinary programs, so each
desktop needs its own helper:

| Session | Needs |
| --- | --- |
| X11 | `xdotool` (or `xprop` as a fallback) |
| GNOME on Wayland | the [Focused Window D-Bus](https://extensions.gnome.org/extension/5592/focused-window-d-bus/) Shell extension, enabled, then log out/in |
| KDE Plasma on Wayland | [`kdotool`](https://github.com/jinliu/kdotool) |
| Sway / Hyprland | `swaymsg` / `hyprctl` (ship with the compositor) |

`xprop` is not used on Wayland even when installed: it only sees XWayland
windows and would misattribute everything else. `toolbox doctor` and
`pytime auto rules` say which backend this machine uses, or exactly what to
install if none.

## Schema

```sql
CREATE TABLE time_entries (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    project  TEXT,
    task     TEXT NOT NULL,
    start_ts REAL NOT NULL,   -- Unix timestamp
    end_ts   REAL             -- NULL while running
);

CREATE TABLE activity_entries (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,   -- "editor" | "terminal" | "browser" | "other"
    app      TEXT NOT NULL,
    project  TEXT,
    detail   TEXT,            -- filename (editor) or page/window title
    ext      TEXT,            -- file extension, editor entries only
    start_ts REAL NOT NULL,
    end_ts   REAL             -- NULL while running
);
```

Timestamps are stored as epoch seconds and rendered in the local timezone, so
the database is unambiguous across timezone changes.
