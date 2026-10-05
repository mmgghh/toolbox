"""Best-effort active-window polling for ``pytime auto`` (X11 and Wayland).

There is no cross-platform, dependency-free way to ask "what window has
focus" -- every desktop exposes it differently, and Wayland compositors
mostly don't expose it to arbitrary clients at all (by design, for
security). This module shells out to whatever system tool is available and
degrades to ``None`` when nothing is, rather than raising: callers treat a
missing backend as "auto-tracking unavailable here", not a crash.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Optional

#: Timeout for each shell-out, so a wedged X server or compositor can't hang
#: the watch loop indefinitely.
_TIMEOUT = 2.0


@dataclass(frozen=True)
class WindowInfo:
    """The raw, unclassified state of the currently focused window."""

    title: str
    wm_class: str
    #: Process owning the window, when the backend reports it; lets a terminal
    #: window be traced to what is running in it (see ``procinfo``).
    pid: Optional[int] = None


def _as_pid(value: object) -> Optional[int]:
    try:
        pid = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _run(args: list[str]) -> Optional[str]:
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def _is_wayland() -> bool:
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    return os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"


def _desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


#: Needs the "Focused Window D-Bus" GNOME Shell extension
#: (https://extensions.gnome.org/extension/5592/focused-window-d-bus/):
#: GNOME deliberately gives no other way to read the focused window on Wayland.
_GNOME_FOCUSED_WINDOW = [
    "gdbus", "call", "--session",
    "--dest", "org.gnome.Shell",
    "--object-path", "/org/gnome/shell/extensions/FocusedWindow",
    "--method", "org.gnome.shell.extensions.FocusedWindow.Get",
]

_GNOME_IDLE = [
    "gdbus", "call", "--session",
    "--dest", "org.gnome.Mutter.IdleMonitor",
    "--object-path", "/org/gnome/Mutter/IdleMonitor/Core",
    "--method", "org.gnome.Mutter.IdleMonitor.GetIdletime",
]


def detect_backend() -> Optional[str]:
    """Return the name of the backend this system can use, or ``None``.

    Returns the same string ``pytime auto rules``/``toolbox doctor`` show, so
    users can tell what will actually run. See :func:`backend_hint` for why
    ``None`` came back.
    """
    if _is_wayland():
        if os.environ.get("SWAYSOCK") or shutil.which("swaymsg"):
            if _run(["swaymsg", "-t", "get_tree"]) is not None:
                return "sway"
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or shutil.which("hyprctl"):
            if _run(["hyprctl", "activewindow", "-j"]) is not None:
                return "hyprland"
        if shutil.which("kdotool") and _run(["kdotool", "getactivewindow"]) is not None:
            return "kdotool"
        if shutil.which("gdbus") and _run(_GNOME_FOCUSED_WINDOW) is not None:
            return "gnome"
        # xprop still runs under XWayland but only sees X11 windows, so it
        # would silently misattribute every native Wayland window.
        return None
    if shutil.which("xdotool"):
        return "xdotool"
    if shutil.which("xprop"):
        return "xprop"
    return None


def backend_hint() -> str:
    """Explain what to install when :func:`detect_backend` finds nothing."""
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "") or "unknown"
    if not _is_wayland():
        return "Install xdotool (or xprop), e.g. `sudo apt install xdotool`."
    lowered = _desktop()
    if "gnome" in lowered or "ubuntu" in lowered or "unity" in lowered:
        return (
            f"Wayland on {desktop}: install and enable the GNOME Shell extension "
            "\"Focused Window D-Bus\" (https://extensions.gnome.org/extension/5592/focused-window-d-bus/), "
            "then log out and back in."
        )
    if "kde" in lowered or "plasma" in lowered:
        return (
            f"Wayland on {desktop}: install kdotool (https://github.com/jinliu/kdotool), "
            "e.g. `cargo install kdotool` or your distro's package."
        )
    return (
        f"Wayland on {desktop}: supported are GNOME (Focused Window D-Bus extension), "
        "KDE (kdotool), Sway and Hyprland."
    )


def _xdotool_window(binary: str = "xdotool") -> Optional[WindowInfo]:
    win_id = _run([binary, "getactivewindow"])
    if win_id is None or not win_id.strip():
        return None
    win_id = win_id.strip()
    title = (_run([binary, "getwindowname", win_id]) or "").strip()
    wm_class = (_run([binary, "getwindowclassname", win_id]) or "").strip()
    if not title and not wm_class:
        return None
    return WindowInfo(title=title, wm_class=wm_class, pid=_as_pid(_run([binary, "getwindowpid", win_id])))


def _parse_gvariant_string(raw: str) -> Optional[str]:
    """Pull the string out of gdbus's ``('...',)`` reply."""
    import ast

    try:
        value = ast.literal_eval(raw.strip())
    except (ValueError, SyntaxError):
        return None
    if isinstance(value, tuple) and value and isinstance(value[0], str):
        return value[0]
    return None


def _gnome_window() -> Optional[WindowInfo]:
    import json

    raw = _run(_GNOME_FOCUSED_WINDOW)
    if raw is None:
        return None
    payload = _parse_gvariant_string(raw)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    title = data.get("title") or ""
    wm_class = data.get("wm_class") or data.get("wm_class_instance") or ""
    if not title and not wm_class:
        return None
    return WindowInfo(title=title, wm_class=wm_class, pid=_as_pid(data.get("pid")))


_XPROP_ACTIVE_RE = re.compile(r"window id # (0x[0-9a-fA-F]+)")
_XPROP_STRING_RE = re.compile(r'=\s*"(.*)"\s*$')
_XPROP_CLASS_RE = re.compile(r'=\s*"[^"]*",\s*"([^"]*)"\s*$')
_XPROP_PID_RE = re.compile(r"=\s*(\d+)\s*$")


def _xprop_window() -> Optional[WindowInfo]:
    root = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    if root is None:
        return None
    match = _XPROP_ACTIVE_RE.search(root)
    if not match:
        return None
    win_id = match.group(1)
    info = _run(["xprop", "-id", win_id, "WM_CLASS", "_NET_WM_NAME", "WM_NAME", "_NET_WM_PID"])
    if info is None:
        return None
    title = ""
    wm_class = ""
    pid = None
    for line in info.splitlines():
        if line.startswith("_NET_WM_NAME") or (not title and line.startswith("WM_NAME")):
            string_match = _XPROP_STRING_RE.search(line)
            if string_match:
                title = string_match.group(1)
        elif line.startswith("WM_CLASS"):
            class_match = _XPROP_CLASS_RE.search(line)
            if class_match:
                wm_class = class_match.group(1)
        elif line.startswith("_NET_WM_PID"):
            pid_match = _XPROP_PID_RE.search(line)
            if pid_match:
                pid = _as_pid(pid_match.group(1))
    if not title and not wm_class:
        return None
    return WindowInfo(title=title, wm_class=wm_class, pid=pid)


def _sway_focused(node: dict) -> Optional[dict]:
    if node.get("focused"):
        return node
    for key in ("nodes", "floating_nodes"):
        for child in node.get(key, []):
            found = _sway_focused(child)
            if found is not None:
                return found
    return None


def _sway_window() -> Optional[WindowInfo]:
    import json

    raw = _run(["swaymsg", "-t", "get_tree"])
    if raw is None:
        return None
    try:
        tree = json.loads(raw)
    except json.JSONDecodeError:
        return None
    node = _sway_focused(tree)
    if node is None:
        return None
    title = node.get("name") or ""
    wm_class = node.get("app_id") or (node.get("window_properties") or {}).get("class") or ""
    if not title and not wm_class:
        return None
    return WindowInfo(title=title, wm_class=wm_class, pid=_as_pid(node.get("pid")))


def _hyprland_window() -> Optional[WindowInfo]:
    import json

    raw = _run(["hyprctl", "activewindow", "-j"])
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    title = data.get("title") or ""
    wm_class = data.get("class") or ""
    if not title and not wm_class:
        return None
    return WindowInfo(title=title, wm_class=wm_class, pid=_as_pid(data.get("pid")))


_BACKENDS = {
    "xdotool": _xdotool_window,
    "xprop": _xprop_window,
    "sway": _sway_window,
    "hyprland": _hyprland_window,
    "kdotool": lambda: _xdotool_window("kdotool"),
    "gnome": _gnome_window,
}


def get_active_window(backend: Optional[str] = None) -> Optional[WindowInfo]:
    """Return the focused window's title/class, or ``None`` if unavailable."""
    backend = backend or detect_backend()
    if backend is None:
        return None
    return _BACKENDS[backend]()


#: Screensaver D-Bus interfaces that report whether the screen is locked:
#: GNOME's own, then the freedesktop one KDE and others implement.
_SCREENSAVER_QUERIES = (
    ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver.GetActive"),
    ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver.GetActive"),
)


def is_screen_locked() -> Optional[bool]:
    """Whether the session's screen is locked, or ``None`` if it can't be told.

    The focused-window backends keep reporting the last window while the
    lock screen is up, so without this a locked screen would keep the timer
    running until the idle timeout caught it.
    """
    if not shutil.which("gdbus"):
        return None
    for dest, path, method in _SCREENSAVER_QUERIES:
        raw = _run(["gdbus", "call", "--session", "--dest", dest, "--object-path", path, "--method", method])
        if raw is None:
            continue
        lowered = raw.strip().lower()
        if "true" in lowered:
            return True
        if "false" in lowered:
            return False
    return None


def get_idle_seconds() -> Optional[float]:
    """Seconds since the last keyboard/mouse input, or ``None`` if unknown.

    X11 uses ``xprintidle``; GNOME (X11 or Wayland) answers over D-Bus via
    Mutter's IdleMonitor. Other Wayland compositors don't expose idle time to
    arbitrary clients, so AFK detection is unavailable there and
    ``pytime auto watch`` keeps tracking regardless.
    """
    if shutil.which("gdbus") and "gnome" in _desktop():
        raw = _run(_GNOME_IDLE)
        if raw is not None:
            match = re.search(r"(\d+)\s*,?\s*\)\s*$", raw)
            if match:
                return int(match.group(1)) / 1000
    if _is_wayland() or not shutil.which("xprintidle"):
        return None
    raw = _run(["xprintidle"])
    if raw is None:
        return None
    try:
        return int(raw.strip()) / 1000
    except ValueError:
        return None
