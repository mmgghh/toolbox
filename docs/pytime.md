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
auto       Automatically record time from the active window (best-effort)
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

`start`/`end` require you to remember to run them. `pytime auto watch` instead
polls the currently focused window every few seconds and records time on its
own, based on what the window title/class say -- no browser extension or
editor plugin required, and nothing you have to remember to type.

```shell
pytime auto watch                          # foreground; Ctrl+C to stop
pytime auto watch --interval 10 --idle-timeout 10
nohup pytime auto watch >/tmp/pytime-auto.log 2>&1 &   # or a systemd user service

pytime auto status                         # what's being tracked right now
pytime auto report -g project -g ext       # e.g. hours per project, per file type
pytime auto report --category editor -g ext
pytime auto rules                          # active backend + how to extend classification
pytime auto probe                          # raw title/class of the focused window, and how it was classified
```

If a report shows a blank project or the wrong app, run `pytime auto probe`,
switch to that window within the 3-second delay, and compare the raw
`wm_class`/`title` with the classification. Unknown classes go in
`~/.pytime/rules.json`.

**What it actually knows, and what it doesn't.** There is no install-free way
for a CLI to read a browser tab's real URL or an editor's real open file path
-- tools that do that (WakaTime, ActivityWatch's browser integration) rely on
an editor plugin or a browser extension. `pytime auto` works only from the
window title and window class every desktop already exposes, parsed with
heuristics for common apps:

- **Editors** (VS Code, any JetBrains IDE, Sublime, vim/nvim): the title's
  `file — project` (VS Code) or `project – file` (JetBrains) convention gives
  a filename (and its extension) and a project name.
- **Terminals** (gnome-terminal, konsole, alacritty, kitty, ...): a
  `~/path`-looking fragment in the title becomes the project; a title
  mentioning "claude" is labeled `(Claude Code)`.
- **Browsers** (Chrome, Chromium, Firefox, Brave, Edge, Opera, Vivaldi): the
  tab title is matched against a small table of known sites (ChatGPT, Claude,
  GitHub, GitLab, Notion, Figma, Jira, Slack, YouTube, Gmail, Google
  Docs/Sheets/Calendar, Linear, Trello, ...) to fill in a project name.
- **Desktop apps** whose titles say nothing useful (the Claude and ChatGPT
  apps, Slack, Discord, Telegram, Obsidian, Zoom, ...) take the app itself as
  the project, e.g. the Claude app is always project `Claude` -- it can't tell
  which of your projects a conversation was about.
- Anything else is recorded under category `other` with the raw title as
  `detail` and no project, rather than a guessed one.

Titles vary across app versions, themes and locales, so treat this as
best-effort, not ground truth -- spot-check `pytime auto report` against
what you actually did. Extend the built-in rules (without replacing them) by
creating `~/.pytime/rules.json`; run `pytime auto rules` to see the file
location and its expected shape.

**Platform support.** Wayland deliberately hides the focused window from
ordinary programs, so each desktop needs its own helper:

| Session | Needs |
| --- | --- |
| X11 | `xdotool` (or `xprop` as a fallback) |
| GNOME on Wayland | the [Focused Window D-Bus](https://extensions.gnome.org/extension/5592/focused-window-d-bus/) Shell extension, enabled, then log out/in |
| KDE Plasma on Wayland | [`kdotool`](https://github.com/jinliu/kdotool) |
| Sway / Hyprland | `swaymsg` / `hyprctl` (ship with the compositor) |

`xprop` is not used on Wayland even when installed: it only sees XWayland
windows and would misattribute everything else. Idle/AFK pausing
(`--idle-timeout`) works on GNOME (via Mutter's idle monitor, no extension
needed) and on X11 with `xprintidle`; elsewhere the watcher never pauses on
its own. `toolbox doctor` and `pytime auto rules` say which backend this
machine uses, or exactly what to install if none.

Auto-tracked entries live in their own `activity_entries` table -- they never
appear in, or interfere with, `pytime report`/`status`/`edit` and friends.

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
