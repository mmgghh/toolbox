"""Cross-platform clipboard access (Termux, Linux, macOS, Windows).

Termux is checked before everything else: an Android device can report
``sys.platform == 'linux'`` and even have ``xclip`` installed under a Wayland-
less environment where it cannot work, while ``termux-clipboard-*`` always
talks to the real Android clipboard.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import Optional

import click

#: How long to wait for a detached clipboard owner to fail before assuming it
#: parked itself in the background (X11 selections need a live owner process).
DETACH_GRACE_SECONDS = 0.2


class ClipboardUnavailable(click.ClickException):
    """Raised when no clipboard helper is installed."""

    def __init__(self) -> None:
        super().__init__(
            "No clipboard helper found. Install one of:\n"
            "  Termux        : pkg install termux-api  (plus the Termux:API app)\n"
            "  Linux/Wayland : wl-clipboard\n"
            "  Linux/X11     : xclip or xsel\n"
            "  macOS/Windows : built in (pbcopy/pbpaste, PowerShell)"
        )


def backend() -> tuple[Sequence[str], Sequence[str]]:
    """Return the ``(read_command, write_command)`` pair for this platform."""
    if shutil.which("termux-clipboard-get") and shutil.which("termux-clipboard-set"):
        return (["termux-clipboard-get"], ["termux-clipboard-set"])
    if sys.platform == "darwin":
        return (["pbpaste"], ["pbcopy"])
    if sys.platform == "win32":
        shell = "pwsh" if shutil.which("pwsh") else "powershell"
        return (
            [shell, "-NoProfile", "-Command", "Get-Clipboard"],
            [shell, "-NoProfile", "-Command", "Set-Clipboard -Value ([Console]::In.ReadToEnd())"],
        )
    if shutil.which("wl-paste") and shutil.which("wl-copy"):
        return (["wl-paste", "--no-newline"], ["wl-copy"])
    if shutil.which("xclip"):
        return (["xclip", "-selection", "clipboard", "-o"], ["xclip", "-selection", "clipboard"])
    if shutil.which("xsel"):
        return (["xsel", "--clipboard", "--output"], ["xsel", "--clipboard", "--input"])
    raise ClipboardUnavailable()


def available() -> bool:
    """Whether a clipboard helper is usable on this machine."""
    try:
        backend()
    except ClipboardUnavailable:
        return False
    return True


def backend_name() -> str:
    """Name of the clipboard helper that would be used."""
    try:
        read_cmd, _ = backend()
    except ClipboardUnavailable:
        return "none"
    return read_cmd[0]


def _run(cmd: Sequence[str], input_text: Optional[str] = None) -> str:
    try:
        result = subprocess.run(
            list(cmd), input=input_text, text=True, capture_output=True, check=False
        )
    except OSError as exc:
        raise click.ClickException(f"Clipboard command failed: {exc}") from exc
    if result.returncode != 0:
        raise click.ClickException(result.stderr.strip() or "Unknown clipboard error.")
    return result.stdout


def _run_detached(cmd: Sequence[str], input_text: Optional[str] = None) -> None:
    """Run a clipboard writer that must outlive us (X11/Wayland selection owners)."""
    try:
        process = subprocess.Popen(
            list(cmd),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise click.ClickException(f"Clipboard command failed: {exc}") from exc

    if process.stdin is None:  # pragma: no cover - PIPE always gives a handle
        raise click.ClickException("Clipboard command stdin is unavailable.")
    try:
        process.stdin.write(input_text or "")
        process.stdin.close()
    except OSError as exc:
        raise click.ClickException(f"Clipboard command failed: {exc}") from exc

    try:
        process.wait(timeout=DETACH_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        return
    if process.returncode != 0:
        raise click.ClickException(f"Clipboard command failed with code {process.returncode}.")


def get_text() -> str:
    """Read the clipboard as text."""
    read_cmd, _ = backend()
    return _run(read_cmd)


def set_text(text: str) -> None:
    """Write text to the clipboard."""
    _, write_cmd = backend()
    if write_cmd and write_cmd[0] in ("wl-copy", "xclip", "xsel"):
        _run_detached(write_cmd, input_text=text)
    else:
        _run(write_cmd, input_text=text)
