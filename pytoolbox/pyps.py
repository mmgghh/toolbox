"""Process and memory management (``pyps``).

Reads ``/proc`` directly instead of shelling out to ``ps``, ``free`` or
``pkill``: Termux's busybox userland supports fewer flags than GNU
coreutils, so parsing the kernel's own accounting keeps the same code working
unmodified on a phone and on a Debian box.
"""

from __future__ import annotations

import os
import pwd
import shutil
import signal as signal_module
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import click

from pytoolbox.core import console
from pytoolbox.core.fs import human_bytes
from pytoolbox.core.intervals import format_duration
from pytoolbox.core.options import (
    CONTEXT_SETTINGS,
    AliasedGroup,
    dry_run_option,
    json_option,
    version_option,
    yes_option,
)

PROC = Path("/proc")

#: Columns ``top``/``find`` can sort by, and how to read each one off a ``ProcessInfo``.
#: "mem" is the same ordering as "rss" -- mem% is just rss / total RAM, a
#: constant divisor -- but it's kept as its own key since the displayed
#: column is "mem%", not "rss", and typing --sort mem for it is the obvious move.
SORT_KEY_FUNCS = {
    "mem": lambda p: p.rss,
    "rss": lambda p: p.rss,
    "swap": lambda p: p.swap,
    "vsz": lambda p: p.vsz,
    "cpu": lambda p: p.cpu_percent,
    "pid": lambda p: p.pid,
}
SORT_KEYS = tuple(SORT_KEY_FUNCS)

#: JSON keeps plain field names for scripting; the text table adds "%" to the
#: two percentage columns since a bare "cpu"/"mem" reads as an absolute value.
JSON_HEADERS = ["pid", "user", "cpu", "mem", "rss", "swap", "state", "started", "cmd"]
TABLE_HEADERS = ["pid", "user", "cpu%", "mem%", "rss", "swap", "state", "started", "cmd"]

#: Command lines longer than this get an ellipsis in table mode, since a real
#: browser/Electron process line can run past a thousand characters and would
#: otherwise blow out every other column's alignment.
CMD_DISPLAY_WIDTH = 100

#: Where ``swapon``/``swapoff`` live on Debian/Ubuntu when not on $PATH.
#: A regular (non-root) user's PATH normally excludes /sbin and /usr/sbin
#: even though `sudo swapon ...` works fine from either, so plain
#: ``shutil.which`` alone would wrongly report the tool as missing.
SWAP_BINARY_DIRS = ("/sbin", "/usr/sbin", "/usr/local/sbin")


# ═══════════════════════════════════════════════════════════════════
# /proc parsing
# ═══════════════════════════════════════════════════════════════════


@dataclass
class ProcessInfo:
    """A snapshot of one process, read from ``/proc/<pid>``."""

    pid: int
    ppid: int
    uid: int
    name: str
    cmdline: str
    state: str
    threads: int
    rss: int  # bytes, resident
    vsz: int  # bytes, virtual
    swap: int  # bytes, swapped out
    cpu_percent: float  # average over the process's lifetime
    elapsed: float  # seconds since start

    @property
    def user(self) -> str:
        """Username for :attr:`uid`, or the raw uid when it has no passwd entry."""
        try:
            return pwd.getpwuid(self.uid).pw_name
        except KeyError:
            return str(self.uid)

    def as_row(self, total_mem: int) -> dict:
        """Row form used by table/JSON output."""
        return {
            "pid": self.pid,
            "user": self.user,
            "cpu": f"{self.cpu_percent:.1f}",
            "mem": f"{100 * self.rss / total_mem:.1f}" if total_mem else "0.0",
            "rss": human_bytes(self.rss),
            "swap": human_bytes(self.swap) if self.swap else "-",
            "state": self.state,
            "started": format_duration(self.elapsed),
            "cmd": self.cmdline or f"[{self.name}]",
        }


def require_proc() -> None:
    """Raise a clear error on platforms without ``/proc`` (macOS, Windows)."""
    if not PROC.is_dir():
        raise click.ClickException("pyps needs /proc, so it only works on Linux and Termux.")


def _mem_kb(value: str) -> int:
    """``"12345 kB"`` -> bytes."""
    parts = value.split()
    return int(parts[0]) * 1024 if parts else 0


def read_status(pid: int) -> dict[str, str]:
    """Parse ``/proc/<pid>/status`` into a ``{Key: value}`` dict."""
    text = (PROC / str(pid) / "status").read_text()
    data = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        data[key] = value.strip()
    return data


def read_cmdline(pid: int) -> str:
    """Full command line for ``pid`` (empty for kernel threads)."""
    raw = (PROC / str(pid) / "cmdline").read_bytes()
    parts = [chunk.decode("utf-8", "replace") for chunk in raw.split(b"\x00") if chunk]
    return " ".join(parts)


def read_stat_times(pid: int, clk_tck: float) -> tuple[float, float]:
    """Return ``(cpu_seconds, start_seconds_since_boot)`` from ``/proc/<pid>/stat``.

    ``comm`` (the second field) can itself contain spaces and parentheses, so
    the only safe split point is the last ``)`` in the line -- everything
    after it is space-separated and positionally fixed.
    """
    text = (PROC / str(pid) / "stat").read_text()
    rest = text[text.rfind(")") + 1 :].split()
    utime, stime = int(rest[11]), int(rest[12])
    starttime = int(rest[19])
    return (utime + stime) / clk_tck, starttime / clk_tck


def system_uptime() -> float:
    """Seconds since boot."""
    return float((PROC / "uptime").read_text().split()[0])


def read_meminfo() -> dict[str, int]:
    """Parse ``/proc/meminfo`` into a ``{Key: bytes}`` dict."""
    values = {}
    for line in (PROC / "meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[key] = int(parts[0]) * 1024
    return values


def total_memory_bytes() -> int:
    """Total installed RAM, in bytes."""
    return read_meminfo().get("MemTotal", 0)


def read_swap_devices() -> list[dict]:
    """Parse ``/proc/swaps``: every active swap partition or file."""
    lines = (PROC / "swaps").read_text().splitlines()[1:]  # header: Filename Type Size Used Priority
    devices = []
    for line in lines:
        parts = line.split()
        if len(parts) < 5:
            continue
        devices.append(
            {
                "device": parts[0],
                "type": parts[1],
                "size": human_bytes(int(parts[2]) * 1024),
                "used": human_bytes(int(parts[3]) * 1024),
                "priority": parts[4],
            }
        )
    return devices


def load_process(pid: int, clk_tck: float, uptime: float) -> Optional[ProcessInfo]:
    """Read one process's info, or ``None`` if it exited or is unreadable."""
    try:
        status = read_status(pid)
        cmdline = read_cmdline(pid)
        cpu_seconds, start_ticks = read_stat_times(pid, clk_tck)
    except (FileNotFoundError, ProcessLookupError, PermissionError):
        return None

    elapsed = max(uptime - start_ticks, 0.0)
    uid_field = status.get("Uid", "0").split()
    return ProcessInfo(
        pid=pid,
        ppid=int(status.get("PPid", "0") or 0),
        uid=int(uid_field[0]) if uid_field else 0,
        name=status.get("Name", ""),
        cmdline=cmdline,
        state=status.get("State", "").split()[0] if status.get("State") else "",
        threads=int(status.get("Threads", "0") or 0),
        rss=_mem_kb(status.get("VmRSS", "")),
        vsz=_mem_kb(status.get("VmSize", "")),
        swap=_mem_kb(status.get("VmSwap", "")),
        cpu_percent=100 * cpu_seconds / elapsed if elapsed > 0 else 0.0,
        elapsed=elapsed,
    )


def list_processes() -> list[ProcessInfo]:
    """Every readable process currently running."""
    clk_tck = os.sysconf("SC_CLK_TCK")
    uptime = system_uptime()
    processes = []
    for entry in PROC.iterdir():
        if not entry.name.isdigit():
            continue
        info = load_process(int(entry.name), clk_tck, uptime)
        if info is not None:
            processes.append(info)
    return processes


def find_matches(
    processes: list[ProcessInfo], pattern: str, exact: bool, include_cmdline: bool
) -> list[ProcessInfo]:
    """Processes whose name (and, with ``include_cmdline``, full command line) match ``pattern``."""
    needle = pattern.lower()
    matches = []
    for process in processes:
        haystacks = [process.name.lower()]
        if include_cmdline:
            haystacks.append(process.cmdline.lower())
        hit = any(h == needle for h in haystacks) if exact else any(needle in h for h in haystacks)
        if hit:
            matches.append(process)
    return matches


def print_process_rows(rows: list[dict], as_json: bool) -> None:
    """Print ``top``/``find``/``info`` rows.

    JSON output keeps the plain ``cpu``/``mem`` keys rows already carry, for
    scripting. The text table relabels them ``cpu%``/``mem%`` and truncates
    ``cmd``, since a real command line can run past a thousand characters and
    would otherwise blow out every other column's alignment.
    """
    if as_json:
        console.print_rows(rows, JSON_HEADERS, as_json=True)
        return

    table_rows = []
    for row in rows:
        table_row = dict(row)
        table_row["cpu%"] = table_row.pop("cpu")
        table_row["mem%"] = table_row.pop("mem")
        cmd = table_row["cmd"]
        if len(cmd) > CMD_DISPLAY_WIDTH:
            table_row["cmd"] = cmd[: CMD_DISPLAY_WIDTH - 1] + "…"
        table_rows.append(table_row)
    console.print_rows(table_rows, TABLE_HEADERS, as_json=False)


def parse_signal(value: str) -> int:
    """Accept a signal name (``KILL``, ``sigterm``) or number and return its int value."""
    value = value.strip()
    digits = value.lstrip("-")
    # isascii() too: a signal number is always plain ASCII, and a digit-like
    # character isdigit() accepts but int() can't parse (e.g. a superscript)
    # would otherwise crash here instead of falling through to the name lookup.
    if digits.isascii() and digits.isdigit():
        return int(value)
    name = value.upper()
    if not name.startswith("SIG"):
        name = f"SIG{name}"
    try:
        return signal_module.Signals[name].value
    except KeyError as exc:
        raise click.ClickException(f"Unknown signal: {value!r}") from exc


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


@click.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
@version_option
def ps_cli() -> None:
    """Process and memory management: top, search, kill, mem/swap usage.

    \b
    Examples:
      pyps top
      pyps top --sort swap
      pyps find chrome
      pyps kill firefox
      pyps info chrome
      pyps free
      pyps swap
      pyps swapoff /swapfile
    """


@ps_cli.command()
@click.option(
    "--sort", "sort_by", type=click.Choice(SORT_KEYS), default="rss", show_default=True,
    help="Column to sort by.",
)
@click.option("-n", "--limit", default=20, show_default=True, help="Processes to show (0 = all).")
@click.option("-u", "--user", default=None, help="Only show processes owned by this user.")
@json_option
def top(sort_by: str, limit: int, user: Optional[str], as_json: bool) -> None:
    """List processes, highest memory (or swap/cpu) first.

    \b
    Doubles as a full listing: `pyps top -n 0 --sort pid` behaves like `ps aux`.

    \b
    Examples:
      pyps top
      pyps top --sort mem
      pyps top --sort cpu
      pyps top -u alice -n 0
    """
    require_proc()
    processes = list_processes()
    if user:
        processes = [p for p in processes if p.user == user]

    processes.sort(key=SORT_KEY_FUNCS[sort_by], reverse=sort_by != "pid")
    if limit:
        processes = processes[:limit]

    total_mem = total_memory_bytes()
    rows = [p.as_row(total_mem) for p in processes]
    print_process_rows(rows, as_json)


@ps_cli.command()
@click.argument("pattern")
@click.option("--exact", is_flag=True, help="Match the process name exactly instead of a substring.")
@click.option(
    "--cmdline", "match_cmdline", is_flag=True,
    help="Also match against the full command line, not just the process name.",
)
@click.option(
    "--sort", "sort_by", type=click.Choice(SORT_KEYS), default="rss", show_default=True,
    help="Column to sort matches by.",
)
@json_option
def find(pattern: str, exact: bool, match_cmdline: bool, sort_by: str, as_json: bool) -> None:
    """Search running processes by name (PATTERN), highest memory first.

    \b
    Examples:
      pyps find chrome
      pyps find python --cmdline
      pyps find sshd --exact
      pyps find node --sort cpu
    """
    require_proc()
    matches = find_matches(list_processes(), pattern, exact, match_cmdline)
    matches.sort(key=SORT_KEY_FUNCS[sort_by], reverse=sort_by != "pid")
    if not matches:
        if as_json:
            console.emit_json([])
        else:
            console.result(f"No process matches {pattern!r}.")
        return
    total_mem = total_memory_bytes()
    rows = [p.as_row(total_mem) for p in matches]
    print_process_rows(rows, as_json)


@ps_cli.command()
@click.argument("target")
@click.option("--exact", is_flag=True, help="Match the process name exactly instead of a substring.")
@click.option(
    "--cmdline", "match_cmdline", is_flag=True,
    help="Also match against the full command line, not just the process name.",
)
@click.option(
    "-s", "--signal", "signal_name", default="TERM", show_default=True,
    help="Signal to send, by name or number.",
)
@click.option("-f", "--force", is_flag=True, help="Shortcut for --signal KILL.")
@yes_option
@dry_run_option
def kill(
    target: str,
    exact: bool,
    match_cmdline: bool,
    signal_name: str,
    force: bool,
    assume_yes: bool,
    dry_run: bool,
) -> None:
    """Kill processes by PID or by name/part of a name (TARGET).

    \b
    A purely numeric TARGET is treated as a PID; anything else is matched
    case-insensitively against the process name (and, with --cmdline, the
    full command line too). Every match is listed and confirmed before
    anything is signalled, unless -y is given.

    \b
    Examples:
      pyps kill 12345
      pyps kill firefox
      pyps kill -f chrome --cmdline
      pyps kill node --exact -y
    """
    require_proc()
    signal_number = parse_signal("KILL" if force else signal_name)
    processes = list_processes()

    # isascii() too: a PID is always plain ASCII, and a digit-like character
    # isdigit() accepts but int() can't parse (e.g. a superscript) would
    # otherwise crash here instead of falling through to the name match.
    if target.isascii() and target.isdigit():
        pid = int(target)
        matches = [p for p in processes if p.pid == pid]
        if not matches:
            raise click.ClickException(f"No process with PID {pid}.")
    else:
        matches = find_matches(processes, target, exact, match_cmdline)
        matches = [p for p in matches if p.pid != os.getpid()]
        if not matches:
            raise click.ClickException(f"No process matches {target!r}.")

    for process in matches:
        console.result(f"{process.pid:<8} {process.user:<12} {process.name:<20} {process.cmdline or f'[{process.name}]'}")

    console.dry_run_notice(dry_run)
    if dry_run:
        return

    signal_display = signal_module.Signals(signal_number).name
    if not console.confirm(f"Send {signal_display} to {console.plural(len(matches), 'process')}?", assume_yes):
        return

    denied = []
    for process in matches:
        try:
            os.kill(process.pid, signal_number)
        except ProcessLookupError:
            continue
        except PermissionError:
            denied.append(process)
    if denied:
        names = ", ".join(f"{p.pid} ({p.name})" for p in denied)
        raise click.ClickException(f"Permission denied for: {names}")
    console.success(f"Signalled {console.plural(len(matches), 'process')}.")


@ps_cli.command()
@click.argument("target")
@click.option("--exact", is_flag=True, help="Match the process name exactly instead of a substring.")
@click.option(
    "--cmdline", "match_cmdline", is_flag=True,
    help="Also match against the full command line, not just the process name.",
)
@json_option
def info(target: str, exact: bool, match_cmdline: bool, as_json: bool) -> None:
    """Show full detail for a process, found by PID or by name/part of a name.

    \b
    A purely numeric TARGET is a PID. Anything else is matched like `find`;
    if more than one process matches, a table is shown instead so you can
    pick the PID you meant.

    \b
    Examples:
      pyps info 1234
      pyps info chrome
      pyps info node --cmdline
    """
    require_proc()
    processes = list_processes()

    # isascii() too: a PID is always plain ASCII, and a digit-like character
    # isdigit() accepts but int() can't parse (e.g. a superscript) would
    # otherwise crash here instead of falling through to the name match.
    if target.isascii() and target.isdigit():
        process = next((p for p in processes if p.pid == int(target)), None)
        if process is None:
            raise click.ClickException(f"No process with PID {target}.")
    else:
        matches = find_matches(processes, target, exact, match_cmdline)
        if not matches:
            raise click.ClickException(f"No process matches {target!r}.")
        if len(matches) > 1:
            matches.sort(key=lambda p: p.rss, reverse=True)
            if not as_json:
                console.result(f"{len(matches)} processes match {target!r}; pass a PID to pick one:")
            total_mem = total_memory_bytes()
            print_process_rows([p.as_row(total_mem) for p in matches], as_json)
            return
        process = matches[0]

    total_mem = total_memory_bytes()
    payload = {
        "pid": process.pid,
        "ppid": process.ppid,
        "user": process.user,
        "name": process.name,
        "cmdline": process.cmdline or f"[{process.name}]",
        "state": process.state,
        "threads": process.threads,
        "cpu_percent": round(process.cpu_percent, 1),
        "mem_percent": round(100 * process.rss / total_mem, 1) if total_mem else 0.0,
        "rss": human_bytes(process.rss),
        "vsz": human_bytes(process.vsz),
        "swap": human_bytes(process.swap),
        "started": f"{format_duration(process.elapsed)} ago",
    }
    if as_json:
        console.emit_json(payload)
        return
    for key, value in payload.items():
        if key in ("cpu_percent", "mem_percent"):
            value = f"{value}%"
        console.result(f"{key:<12} {value}")


@ps_cli.command()
@json_option
def free(as_json: bool) -> None:
    """Show system-wide memory and swap usage, like `free -h`.

    \b
    Examples:
      pyps free
      pyps free --json
    """
    require_proc()
    meminfo = read_meminfo()
    mem_total = meminfo.get("MemTotal", 0)
    mem_free = meminfo.get("MemFree", 0)
    mem_available = meminfo.get("MemAvailable", mem_free)
    cached = meminfo.get("Cached", 0) + meminfo.get("SReclaimable", 0)
    mem_used = max(mem_total - mem_free - meminfo.get("Buffers", 0) - cached, 0)
    swap_total = meminfo.get("SwapTotal", 0)
    swap_free = meminfo.get("SwapFree", 0)
    swap_used = max(swap_total - swap_free, 0)

    if as_json:
        console.emit_json(
            {
                "mem": {"total": mem_total, "used": mem_used, "free": mem_free, "available": mem_available},
                "swap": {"total": swap_total, "used": swap_used, "free": swap_free},
            }
        )
        return

    console.result(f"{'':<6}{'total':>10}{'used':>10}{'free':>10}{'available':>12}")
    console.result(
        f"{'Mem:':<6}{human_bytes(mem_total):>10}{human_bytes(mem_used):>10}"
        f"{human_bytes(mem_free):>10}{human_bytes(mem_available):>12}"
    )
    console.result(
        f"{'Swap:':<6}{human_bytes(swap_total):>10}{human_bytes(swap_used):>10}{human_bytes(swap_free):>10}"
    )


@ps_cli.command()
@json_option
def swap(as_json: bool) -> None:
    """List active swap partitions/files, like `swapon --show`.

    \b
    Examples:
      pyps swap
      pyps swap --json
    """
    require_proc()
    devices = read_swap_devices()
    if not devices:
        if as_json:
            console.emit_json([])
        else:
            console.result("No active swap.")
        return
    console.print_rows(devices, ["device", "type", "size", "used", "priority"], as_json=as_json)


def _find_swap_binary(tool: str) -> Optional[str]:
    """Locate ``swapon``/``swapoff``, falling back to the usual sbin dirs.

    See :data:`SWAP_BINARY_DIRS` for why the fallback is needed.
    """
    binary = shutil.which(tool)
    if binary:
        return binary
    for directory in SWAP_BINARY_DIRS:
        candidate = Path(directory) / tool
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def _run_swap_tool(tool: str, device: Optional[str], all_devices: bool, assume_yes: bool, dry_run: bool) -> None:
    """Shell out to the system ``swapon``/``swapoff`` binary.

    There is no portable syscall wrapper for this in the standard library, and
    both tools already handle fstab lookups, label/UUID resolution and
    priority parsing correctly -- reimplementing that would just be a worse
    copy of util-linux.
    """
    if not device and not all_devices:
        raise click.ClickException("Give a DEVICE, or pass -a/--all.")
    binary = _find_swap_binary(tool)
    if binary is None:
        raise click.ClickException(
            f"`{tool}` was not found. It ships in util-linux on Debian/Ubuntu; "
            "unrooted Termux usually cannot manage swap at all."
        )

    command = [binary, "--all"] if all_devices else [binary, device]
    console.result(f"$ {' '.join(command)}")
    console.dry_run_notice(dry_run)
    if dry_run:
        return

    verb = "Enable" if tool == "swapon" else "Disable"
    target = "all configured swap" if all_devices else device
    if not console.confirm(f"{verb} {target}? This usually needs root.", assume_yes):
        return

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise click.ClickException((result.stderr or result.stdout or f"{tool} failed").strip())
    console.success(f"{verb}d {target}.")


@ps_cli.command()
@click.argument("device", required=False)
@click.option("-a", "--all", "all_devices", is_flag=True, help="Enable every swap device listed in fstab.")
@yes_option
@dry_run_option
def swapon(device: Optional[str], all_devices: bool, assume_yes: bool, dry_run: bool) -> None:
    """Enable swap on DEVICE (a partition or swapfile), or -a for everything in fstab.

    \b
    Wraps the system `swapon` binary; usually needs root.

    \b
    Examples:
      pyps swapon /swapfile
      pyps swapon --all
    """
    require_proc()
    _run_swap_tool("swapon", device, all_devices, assume_yes, dry_run)


@ps_cli.command()
@click.argument("device", required=False)
@click.option("-a", "--all", "all_devices", is_flag=True, help="Disable every active swap device.")
@yes_option
@dry_run_option
def swapoff(device: Optional[str], all_devices: bool, assume_yes: bool, dry_run: bool) -> None:
    """Disable swap on DEVICE, or -a for every active swap device.

    \b
    Wraps the system `swapoff` binary; usually needs root. Can briefly stall
    while swapped-out pages are read back into RAM.

    \b
    Examples:
      pyps swapoff /swapfile
      pyps swapoff --all
    """
    require_proc()
    _run_swap_tool("swapoff", device, all_devices, assume_yes, dry_run)


if __name__ == "__main__":  # pragma: no cover
    ps_cli()
