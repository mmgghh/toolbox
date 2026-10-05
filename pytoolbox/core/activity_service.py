"""Run ``pytime auto watch`` as a systemd user service.

A user service (``systemctl --user``) needs no root, starts with the
graphical session, and restarts the watcher if it dies -- e.g. when it
starts before the desktop is ready to answer "which window is focused".
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

UNIT_NAME = "pytime-auto.service"

#: Variables the watcher uses to find the desktop. The session manager
#: normally hands them to systemd at login; importing them on start covers
#: a first start from a terminal, and compositors that don't.
SESSION_VARIABLES = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XDG_CURRENT_DESKTOP",
    "XDG_SESSION_TYPE",
    "SWAYSOCK",
    "HYPRLAND_INSTANCE_SIGNATURE",
)

TARGETS = ("graphical-session.target", "default.target")


class ServiceError(RuntimeError):
    """A systemctl/journalctl call failed or isn't possible here."""


def unit_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "systemd" / "user" / UNIT_NAME


def _quote(arg: str) -> str:
    # systemd unit syntax: '%' starts a specifier and must be doubled; only
    # words with spaces or quotes need quoting.
    arg = arg.replace("%", "%%")
    if re.fullmatch(r"[\w@+=:,./-]+", arg):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_unit(
    db_path: Path,
    interval: float,
    idle_timeout: float,
    target: str = "graphical-session.target",
    python: Optional[str] = None,
    path_env: Optional[str] = None,
) -> str:
    """The unit file text; pins this Python (so a venv install keeps working) and the database."""
    command = [
        python or sys.executable,
        "-m",
        "pytoolbox.pytime",
        "--db",
        str(db_path),
        "auto",
        "watch",
        "--interval",
        f"{interval:g}",
        "--idle-timeout",
        f"{idle_timeout:g}",
    ]
    path_env = path_env if path_env is not None else os.environ.get("PATH", "")
    lines = [
        "[Unit]",
        "Description=pytime auto: automatic time tracking from the active window",
        "Documentation=https://github.com/mmgghh/toolbox/blob/main/docs/pytime.md",
    ]
    if target == "graphical-session.target":
        lines += ["PartOf=graphical-session.target", "After=graphical-session.target"]
    lines += [
        # Keep retrying: at login the desktop may not answer for a while.
        "StartLimitIntervalSec=0",
        "",
        "[Service]",
        "Type=simple",
        "ExecStart=" + " ".join(_quote(arg) for arg in command),
        "Restart=on-failure",
        "RestartSec=15",
        "Environment=PYTHONUNBUFFERED=1",
    ]
    if path_env:
        # Backends such as kdotool often live in ~/.cargo/bin or ~/.local/bin,
        # which the systemd user manager's PATH doesn't include.
        lines.append("Environment=" + _quote(f"PATH={path_env}"))
    lines += ["", "[Install]", f"WantedBy={target}", ""]
    return "\n".join(lines)


def _require(tool: str) -> None:
    if not shutil.which(tool):
        raise ServiceError(
            f"`{tool}` was not found. The pytime auto service needs systemd; "
            "without it, run `pytime auto watch` from your desktop's autostart instead."
        )


def systemctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    _require("systemctl")
    result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout).strip() or f"exit status {result.returncode}"
        raise ServiceError(f"systemctl --user {' '.join(args)} failed: {message}")
    return result


def import_session_environment() -> list[str]:
    """Hand this shell's display variables to the systemd user manager."""
    names = [name for name in SESSION_VARIABLES if os.environ.get(name)]
    if names:
        systemctl("import-environment", *names, check=False)
    return names


def is_installed() -> bool:
    return unit_path().is_file()


def install(unit_text: str) -> Path:
    path = unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text, encoding="utf-8")
    systemctl("daemon-reload")
    return path


def uninstall() -> Optional[Path]:
    path = unit_path()
    if not path.is_file():
        return None
    systemctl("disable", "--now", UNIT_NAME, check=False)
    path.unlink()
    systemctl("daemon-reload")
    return path


def state() -> dict[str, str]:
    """``enabled``/``active`` as systemd reports them (non-zero exits are answers here)."""
    enabled = systemctl("is-enabled", UNIT_NAME, check=False).stdout.strip() or "unknown"
    active = systemctl("is-active", UNIT_NAME, check=False).stdout.strip() or "unknown"
    return {"enabled": enabled, "active": active}


def logs(lines: int, follow: bool) -> int:
    _require("journalctl")
    args = ["journalctl", "--user", "-u", UNIT_NAME, "-n", str(lines), "--no-pager"]
    if follow:
        args.append("-f")
    return subprocess.call(args)
