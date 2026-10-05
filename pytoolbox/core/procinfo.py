"""What is a terminal window running, and where? Read from ``/proc`` (Linux).

A terminal's title is only as good as the shell that sets it, so for a
focused terminal window we instead look at the processes behind it: every
tab is a shell (a session leader with its own pty) descending from the
terminal's process; the tab typed into most recently is the one on screen;
its foreground process is what's running there, with a real working
directory. When that is a tmux client, tmux itself is asked which pane is
active, and the pane is read the same way. Only the program's name (and, for terminal editors, the file's
base name) leaves this module -- never command-line arguments.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple, Optional

_PROC = Path("/proc")

#: Interpreters whose script, not themselves, is the program being run.
_INTERPRETERS = {"node", "nodejs", "bun", "deno", "python", "python3"}
_SHELLS = {"bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh", "nu", "xonsh"}
TERMINAL_EDITORS = {"vim", "nvim", "vi", "nano", "emacs", "hx", "helix", "micro", "kak"}
#: Programs whose own working directory says nothing about the work inside
#: them; callers fall back to the window title (which shell-init keeps
#: meaningful across ssh and multiplexers).
PASSTHROUGH = {"tmux", "screen", "zellij", "ssh", "mosh", "mosh-client", "et", "docker", "podman", "distrobox"}


@dataclass(frozen=True)
class TerminalContext:
    """The focused tab: its foreground program (``""`` at a prompt) and directory."""

    cwd: str
    program: str
    file: str = ""
    #: Title of the tmux pane, when the context came from inside tmux.
    title: str = ""


class _Proc(NamedTuple):
    ppid: int
    session: int
    tty_nr: int
    tpgid: int


def _stat(pid: int) -> Optional[_Proc]:
    try:
        raw = (_PROC / str(pid) / "stat").read_text()
    except OSError:
        return None
    # The command name is parenthesised and may contain spaces or ")".
    fields = raw[raw.rindex(")") + 2 :].split()
    try:
        return _Proc(int(fields[1]), int(fields[3]), int(fields[4]), int(fields[5]))
    except (IndexError, ValueError):
        return None


def _process_table() -> dict[int, _Proc]:
    table = {}
    try:
        entries = os.listdir(_PROC)
    except OSError:
        return table
    for entry in entries:
        if entry.isdigit():
            info = _stat(int(entry))
            if info is not None:
                table[int(entry)] = info
    return table


def _descendants(root: int, table: dict[int, _Proc]) -> list[int]:
    children: dict[int, list[int]] = {}
    for pid, info in table.items():
        children.setdefault(info.ppid, []).append(pid)
    found, stack = [], [root]
    while stack:
        for child in children.get(stack.pop(), []):
            found.append(child)
            stack.append(child)
    return found


def _link(pid: int, name: str) -> Optional[str]:
    try:
        return os.readlink(_PROC / str(pid) / name)
    except OSError:
        return None


def _tty_activity(pid: int) -> Optional[tuple[float, float]]:
    """(last input, last output) of the pty on a process's stdin."""
    tty = _link(pid, "fd/0")
    if not tty or not tty.startswith("/dev/"):
        return None
    try:
        info = os.stat(_PROC / str(pid) / "fd" / "0")
    except OSError:
        return None
    return info.st_atime, info.st_mtime


def _program(pid: int) -> tuple[str, str]:
    """(program name, edited file's base name) for a process."""
    try:
        argv = [a for a in (_PROC / str(pid) / "cmdline").read_bytes().decode(errors="replace").split("\0") if a]
    except OSError:
        argv = []
    if not argv:
        return "", ""
    name = Path(argv[0]).name
    if name in _INTERPRETERS:
        if any("claude" in arg for arg in argv[1:3]):
            return "claude", ""
        scripts = [a for a in argv[1:] if not a.startswith("-")]
        if scripts:
            name = Path(scripts[0]).stem
    name = name.lstrip("-")  # login shells show as "-bash"
    if name in _SHELLS:
        return "", ""
    file = ""
    if name in TERMINAL_EDITORS and len(argv) > 1 and not argv[-1].startswith("-"):
        file = Path(argv[-1]).name
    return name, file


def terminal_context(pid: Optional[int]) -> Optional[TerminalContext]:
    """Context of the focused tab of the terminal whose process is ``pid``.

    Several windows can share one terminal process (gnome-terminal-server
    hosts them all), so among its shells the one whose pty was typed into
    most recently wins -- the kernel updates that time with 8-second
    granularity, which is fine for telling tabs apart. Returns ``None`` off
    Linux, for sandboxed terminals whose processes aren't visible, or when
    nothing can be read.
    """
    if not pid or not _PROC.is_dir():
        return None
    table = _process_table()
    if pid not in table:
        return None
    candidates = []
    for child in _descendants(pid, table):
        info = table[child]
        if info.session != child or info.tty_nr == 0:
            continue
        activity = _tty_activity(child)
        if activity is not None:
            candidates.append((activity, child))
    if not candidates:
        return None
    shell = max(candidates)[1]
    context, foreground = _foreground_context(shell, table)
    if context is not None and context.program == "tmux":
        return _tmux_context(foreground, table) or context
    return context


def _foreground_context(shell: int, table: dict[int, _Proc]) -> tuple[Optional[TerminalContext], int]:
    """The foreground program of a shell's terminal, and its pid."""
    foreground = table[shell].tpgid
    if foreground not in table:
        foreground = shell
    program, file = _program(foreground)
    cwd = _link(foreground, "cwd") or _link(shell, "cwd")
    if not cwd:
        return None, foreground
    return TerminalContext(cwd=cwd, program=program, file=file), foreground


def _tmux_socket_args(client: int) -> list[str]:
    """``-L name`` / ``-S path`` the client was started with, to reach the same server."""
    try:
        argv = [a for a in (_PROC / str(client) / "cmdline").read_bytes().decode(errors="replace").split("\0") if a]
    except OSError:
        return []
    args: list[str] = []
    for index, arg in enumerate(argv[1:], start=1):
        if arg in ("-L", "-S") and index + 1 < len(argv):
            args += [arg, argv[index + 1]]
        elif arg[:2] in ("-L", "-S") and len(arg) > 2:
            args += [arg[:2], arg[2:]]
        elif not arg.startswith("-"):
            break  # the tmux command itself (attach, new-session, ...)
    return args


def _tmux_context(client: int, table: dict[int, _Proc]) -> Optional[TerminalContext]:
    """What the tmux client's active pane runs: tmux hides it from the process tree.

    tmux is asked which pane is active for the client on this terminal; that
    pane's shell is then read from /proc like any other. The pane title is
    kept for when the pane runs ssh and only its title means anything.
    """
    import subprocess

    tty = _link(client, "fd/0")
    if not tty or not tty.startswith("/dev/"):
        return None
    try:
        result = subprocess.run(
            ["tmux", *_tmux_socket_args(client), "display-message", "-c", tty, "-p", "#{pane_pid}\t#{pane_title}"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    pane_pid_text, _, pane_title = result.stdout.rstrip("\n").partition("\t")
    try:
        pane_pid = int(pane_pid_text)
    except ValueError:
        return None
    if pane_pid not in table:
        return None
    context, _ = _foreground_context(pane_pid, table)
    if context is None:
        return None
    return TerminalContext(cwd=context.cwd, program=context.program, file=context.file, title=pane_title)
