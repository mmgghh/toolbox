"""Tests for reading a terminal's focused tab from /proc."""

from __future__ import annotations

import os
import shutil
import sys
import time

import pytest

from pytoolbox.core import procinfo
from pytoolbox.core.activewindow import WindowInfo
from pytoolbox.core.activity_rules import classify_window

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux") or not shutil.which("bash"), reason="needs Linux /proc and bash"
)


@pytest.fixture
def tabs(tmp_path):
    """Two pty-backed bash 'tabs' whose parent is this test process, like a terminal emulator."""
    import pty

    repo = tmp_path / "samt"
    (repo / ".git").mkdir(parents=True)
    (repo / "src").mkdir()
    started = []
    for _ in range(2):
        pid, fd = pty.fork()
        if pid == 0:  # pragma: no cover - child
            os.chdir(tmp_path)
            os.execvp("bash", ["bash", "--norc", "--noprofile", "-i"])
        started.append((pid, fd))
    time.sleep(0.5)
    yield tmp_path, repo, started
    for pid, fd in started:
        try:
            os.kill(pid, 9)
            os.waitpid(pid, 0)
        except OSError:
            pass
        os.close(fd)


def _typed_last(tabs, index):
    now = time.time()
    for position, (pid, _) in enumerate(tabs):
        stamp = now if position == index else now - 60
        os.utime(os.readlink(f"/proc/{pid}/fd/0"), (stamp, stamp))


def _wait_for(predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    return predicate()


def test_focused_tab_program_and_git_root(tabs):
    root, repo, started = tabs
    os.write(started[1][1], f"cd {repo}/src && exec -a claude sleep 30\n".encode())
    _typed_last(started, 1)

    def running():
        ctx = procinfo.terminal_context(os.getpid())
        return ctx if ctx and ctx.program == "claude" else None

    ctx = _wait_for(running)
    assert ctx is not None
    assert ctx.cwd == str(repo / "src")

    classified = classify_window(WindowInfo("mohammad@mg: ~", "org.gnome.Terminal", pid=os.getpid()))
    assert classified.project == "samt"
    assert classified.app == "Terminal (Claude Code)"


def test_idle_prompt_tab_reports_shell(tabs):
    root, _, started = tabs
    _typed_last(started, 0)
    ctx = procinfo.terminal_context(os.getpid())
    assert ctx is not None
    assert ctx.program == ""
    assert ctx.cwd == str(root)


@pytest.mark.skipif(not shutil.which("tmux"), reason="needs tmux")
def test_tab_running_tmux_reads_the_active_pane(tabs):
    import select
    import subprocess

    root, repo, started = tabs
    socket = f"pytime-test-{os.getpid()}"
    fd = started[1][1]
    os.write(fd, f"TERM=xterm-256color tmux -L {socket} -f /dev/null new-session\n".encode())
    try:
        time.sleep(1.5)
        os.write(fd, f"cd {repo}/src && exec -a claude sleep 30\n".encode())
        _typed_last(started, 1)

        def running():
            while select.select([fd], [], [], 0)[0]:  # keep the pty from filling up
                os.read(fd, 65536)
            ctx = procinfo.terminal_context(os.getpid())
            return ctx if ctx and ctx.program == "claude" else None

        ctx = _wait_for(running)
        assert ctx is not None, procinfo.terminal_context(os.getpid())
        assert ctx.cwd == str(repo / "src")
        classified = classify_window(WindowInfo("mohammad@mg: ~", "org.gnome.Terminal", pid=os.getpid()))
        assert (classified.project, classified.app) == ("samt", "Terminal (Claude Code)")
    finally:
        subprocess.run(["tmux", "-L", socket, "kill-server"], capture_output=True)


def test_tmux_socket_args(tmp_path, monkeypatch):
    monkeypatch.setattr(procinfo, "_PROC", tmp_path)
    for pid, argv in {1: ["tmux", "-L", "work", "attach"], 2: ["tmux", "-S/tmp/s", "new"], 3: ["tmux", "attach", "-t", "x"]}.items():
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
    assert procinfo._tmux_socket_args(1) == ["-L", "work"]
    assert procinfo._tmux_socket_args(2) == ["-S", "/tmp/s"]
    assert procinfo._tmux_socket_args(3) == []


def test_ssh_inside_tmux_uses_the_pane_title(monkeypatch):
    monkeypatch.setattr(
        procinfo,
        "terminal_context",
        lambda pid: procinfo.TerminalContext(cwd="/", program="ssh", title="claude @ ~/work/samt"),
    )
    result = classify_window(WindowInfo("mohammad@mg: ~", "org.gnome.Terminal", pid=123))
    assert result.project == "samt"
    assert "Claude Code" in result.app


def test_unknown_pid_falls_back_to_title():
    assert procinfo.terminal_context(None) is None
    assert procinfo.terminal_context(2**22 + 12345) is None
    result = classify_window(WindowInfo("claude @ ~/projects/toolbox", "kitty", pid=None))
    assert result.project == "toolbox"


def test_program_names(tmp_path, monkeypatch):
    monkeypatch.setattr(procinfo, "_PROC", tmp_path)

    def cmdline(pid, *argv):
        (tmp_path / str(pid)).mkdir()
        (tmp_path / str(pid) / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")

    cmdline(1, "node", "/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js", "--resume")
    cmdline(2, "-bash")
    cmdline(3, "nvim", "-p", "src/pytime.py")
    cmdline(4, "python3", "manage.py", "runserver")
    assert procinfo._program(1) == ("claude", "")
    assert procinfo._program(2) == ("", "")
    assert procinfo._program(3) == ("nvim", "pytime.py")
    assert procinfo._program(4) == ("manage", "")


def test_passthrough_programs_use_the_title(monkeypatch):
    monkeypatch.setattr(
        procinfo, "terminal_context", lambda pid: procinfo.TerminalContext(cwd="/", program="ssh")
    )
    result = classify_window(WindowInfo("claude @ ~/projects/toolbox", "kitty", pid=123))
    assert result.project == "toolbox"
    assert "Claude Code" in result.app
