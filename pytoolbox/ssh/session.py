"""Backgrounding ssh, and keeping a usable handle on it.

Letting ssh background itself with ``-f`` is better than spawning it and
walking away: with ``ExitOnForwardFailure=yes`` it forks only once every
remote forward is established, so its exit status says whether the tunnel came
up. Pairing that with a control socket gives a handle that ``-O check`` and
``-O exit`` can drive, which a bare PID cannot.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Optional

from pytoolbox.core import console, paths

#: Unix socket paths are capped at 108 bytes; leave room for the name.
MAX_SOCKET_PATH = 100

#: ssh -O check answers on stderr: "Master running (pid=12345)".
MASTER_PID_RE = re.compile(r"pid=(\d+)")

CONTROL_TIMEOUT_SECONDS = 10


def control_dir() -> Path:
    """Directory holding one control socket per background session."""
    return paths.ensure_dir(paths.runtime_dir() / "ctl", private=True)


def control_path(name: str) -> Optional[Path]:
    """Where this session's control socket goes, or ``None`` if unusable.

    An explicit ``-S`` overrides whatever ``ControlPath`` the user's ssh config
    sets, so pyssh never collides with their own multiplexed connections.
    """
    if paths.is_windows():
        return None
    path = control_dir() / name
    return path if len(str(path)) <= MAX_SOCKET_PATH else None


def _control_command(socket_path: Path, action: str, destination: str) -> list[str]:
    return ["ssh", "-S", str(socket_path), "-O", action, destination]


def _ask_master(socket_path: Path, action: str, destination: str):
    if shutil.which("ssh") is None:
        return None
    try:
        return subprocess.run(
            _control_command(socket_path, action, destination),
            capture_output=True,
            text=True,
            timeout=CONTROL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - defensive
        return None


def master_pid(socket_path: Path, destination: str) -> Optional[int]:
    """The pid of the multiplexing master, or ``None`` if it is not running."""
    completed = _ask_master(socket_path, "check", destination)
    if completed is None or completed.returncode != 0:
        return None
    match = MASTER_PID_RE.search(f"{completed.stderr}{completed.stdout}")
    return int(match.group(1)) if match else None


def master_alive(socket_path: Path, destination: str) -> bool:
    """Whether a multiplexing master is still answering on this socket."""
    return master_pid(socket_path, destination) is not None


def stop_master(socket_path: Path, destination: str) -> bool:
    """Ask the master to exit. Returns whether one was there to ask."""
    completed = _ask_master(socket_path, "exit", destination)
    return completed is not None and completed.returncode == 0


def run_background(cmd: Sequence[str], verbose: int = 0) -> subprocess.CompletedProcess:
    """Run an ``ssh -f`` command line and hand back its result.

    ssh returns once it has authenticated and established every remote
    forward, so the exit status is the readiness signal for a ``-R`` tunnel --
    no polling, and no race.
    """
    console.info(f"$ {' '.join(cmd)}", verbose, threshold=1)
    return subprocess.run(list(cmd), capture_output=True, text=True)
