# `pyps` — process and memory management

Also available as `toolbox ps`.

```
top      List processes, highest memory (or swap/cpu) first
find     Search running processes by name (or full command line)
kill     Kill processes by PID or by name/part of a name
info     Show full detail for one process, or a picker table if a name is ambiguous
free     Show system-wide memory and swap usage, like `free -h`
swap     List active swap partitions/files, like `swapon --show`
swapon   Enable swap
swapoff  Disable swap
```

Everything reads `/proc` directly instead of shelling out to `ps`, `free` or
`pkill`, since Termux's busybox userland supports fewer flags than GNU
coreutils. Needs Linux or Termux (anything with `/proc`); `top`, `find`,
`kill`, `info` and `swap` all accept `--json`.

---

## Reading the output

- **rss** — Resident Set Size: physical RAM this process is actually using
  right now. This is the number to look at for "what's eating my memory".
- **vsz** — Virtual Size: the total address space the process has mapped,
  including memory-mapped files, shared libraries and reserved-but-unused
  space. Usually much larger than `rss` and a poor indicator of real memory
  pressure on its own.
- **swap** — how much of this process has been paged out to disk. `-` means
  none.
- **cpu%** / **mem%** — percentages. `cpu%` is the process's average over its
  whole lifetime (total CPU time ÷ wall-clock time since it started, the same
  approximation `ps` uses — not a live, moment-to-moment reading). `mem%` is
  `rss / total RAM`.
- **state** — one letter from the kernel:
  | Letter | Meaning |
  | --- | --- |
  | `R` | Running or runnable |
  | `S` | Sleeping, interruptible (waiting on something, e.g. I/O or a timer) |
  | `D` | Uninterruptible sleep (usually blocked on disk I/O; can't be killed until it returns) |
  | `T` | Stopped (by a job-control or debugger signal) |
  | `Z` | Zombie (exited, waiting for its parent to reap it) |
  | `I` | Idle (kernel threads, newer kernels) |

In table mode, JSON keeps plain `cpu`/`mem` keys for scripting; the text
table labels them `cpu%`/`mem%` since a bare number reads as an absolute
value otherwise.

## `top`

```shell
pyps top
pyps top --sort mem
pyps top --sort cpu
pyps top -u alice -n 0
pyps top --json
```

Lists processes sorted by memory (RSS) first by default; `--sort` also takes
`rss`, `swap`, `vsz`, `cpu` or `pid`. (`mem` and `rss` sort identically —
`mem%` is just `rss / total RAM`, a constant divisor — `mem` is offered
because it's what the displayed column is actually called.) `-n/--limit`
caps how many rows print (default 20, `0` for all), which also makes it a
full listing: `pyps top -n 0 --sort pid` behaves like `ps aux`. `-u/--user`
filters by owner. In table mode, long command lines are truncated to keep
the columns aligned; `--json` always carries the full command line.

## `find`

```shell
pyps find chrome
pyps find python --cmdline
pyps find sshd --exact
pyps find node --sort cpu
```

Searches process names for `PATTERN`, case-insensitive substring by default.
`--cmdline` also matches the full command line (handy for things like
`pyps find http.server --cmdline`); `--exact` requires an exact name match
instead of a substring. `--sort` takes the same columns as `top` (default
`rss`).

## `kill`

```shell
pyps kill 12345
pyps kill firefox
pyps kill -f chrome --cmdline
pyps kill node --exact -y
```

`TARGET` is a PID when it's purely numeric, otherwise a name pattern with the
same matching rules as `find` (`--exact`, `--cmdline`). Every match is listed
and confirmed before anything is signalled — pass `-y/--yes` to skip the
prompt, or `-n/--dry-run` to only see what would be hit. `-s/--signal` picks
the signal by name or number (default `TERM`); `-f/--force` is shorthand for
`--signal KILL`. `pyps` itself is never a match, so a broad pattern can't
kill the command running it.

## `info`

```shell
pyps info 1234
pyps info chrome
pyps info node --cmdline
pyps info 1234 --json
```

`TARGET` is a PID when it's purely numeric, otherwise a name pattern with the
same matching rules as `find`. A single match prints full detail: user,
state, thread count, CPU%, memory% and RSS/virtual/swap sizes, and how long
it's been running. Multiple matches print a picker table instead (the same
shape as `find`'s output) so you can re-run with the PID you meant; with
`--json` that case returns an array rather than an object, so check the
shape if scripting against it.

## `free`

```shell
pyps free
pyps free --json
```

System-wide memory and swap, computed the same way `free -h` computes them:

- **free** — literally unused RAM, untouched by anything.
- **available** — an estimate of what a new process could actually use
  without swapping (`free` + reclaimable buffers/cache). This is almost
  always the more useful number: Linux uses "free" RAM for disk cache, so
  `free` alone looks misleadingly low on a machine that's been up a while.
- **used** — `total − free − buffers − cache`.

## `swap`

```shell
pyps swap
pyps swap --json
```

Lists every active swap partition or file with its size, current usage and
priority — a read-only equivalent of `swapon --show`. Needs no privileges.

## `swapon` / `swapoff`

```shell
pyps swapon /swapfile
pyps swapon --all
pyps swapoff /swapfile
pyps swapoff --all -y
```

Thin wrappers around the system `swapon`/`swapoff` binaries (there's no
portable syscall for this in the standard library, and those tools already
handle fstab lookups and label/UUID resolution correctly). They're looked up
on `$PATH` and, failing that, in `/sbin`, `/usr/sbin` and `/usr/local/sbin`,
since a normal user's `$PATH` usually excludes those directories even though
the binaries work fine there. Confirms before running (skip with `-y`); `-n/
--dry-run` prints the command without running it. Both almost always need
root — expect a permission error unless you're running as root or via
`sudo`. `swapoff` can briefly stall while swapped-out pages are read back
into RAM.
