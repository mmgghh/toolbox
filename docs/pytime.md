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

## Schema

```sql
CREATE TABLE time_entries (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    project  TEXT,
    task     TEXT NOT NULL,
    start_ts REAL NOT NULL,   -- Unix timestamp
    end_ts   REAL             -- NULL while running
);
```

Timestamps are stored as epoch seconds and rendered in the local timezone, so
the database is unambiguous across timezone changes.
