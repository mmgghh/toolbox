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


def detect_backend() -> Optional[str]:
    """Return the name of the backend this system can use, or ``None``.

    Checked once per process (cheap enough that callers don't need to cache
    it themselves); returns the same string ``pytime auto rules``/``toolbox
    doctor`` show, so users can tell what will actually run.
    """
    if _is_wayland():
        if os.environ.get("SWAYSOCK") or shutil.which("swaymsg"):
            if _run(["swaymsg", "-t", "get_tree"]) is not None:
                return "sway"
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") or shutil.which("hyprctl"):
            if _run(["hyprctl", "activewindow", "-j"]) is not None:
                return "hyprland"
        return None
    if shutil.which("xdotool"):
        return "xdotool"
    if shutil.which("xprop"):
        return "xprop"
    return None


def _xdotool_window() -> Optional[WindowInfo]:
    win_id = _run(["xdotool", "getactivewindow"])
    if win_id is None or not win_id.strip():
        return None
    win_id = win_id.strip()
    title = (_run(["xdotool", "getwindowname", win_id]) or "").strip()
    wm_class = (_run(["xdotool", "getwindowclassname", win_id]) or "").strip()
    if not title and not wm_class:
        return None
    return WindowInfo(title=title, wm_class=wm_class)


_XPROP_ACTIVE_RE = re.compile(r"window id # (0x[0-9a-fA-F]+)")
_XPROP_STRING_RE = re.compile(r'=\s*"(.*)"\s*$')
_XPROP_CLASS_RE = re.compile(r'=\s*"[^"]*",\s*"([^"]*)"\s*$')


def _xprop_window() -> Optional[WindowInfo]:
    root = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    if root is None:
        return None
    match = _XPROP_ACTIVE_RE.search(root)
    if not match:
        return None
    win_id = match.group(1)
    info = _run(["xprop", "-id", win_id, "WM_CLASS", "_NET_WM_NAME", "WM_NAME"])
    if info is None:
        return None
    title = ""
    wm_class = ""
    for line in info.splitlines():
        if line.startswith("_NET_WM_NAME") or (not title and line.startswith("WM_NAME")):
            string_match = _XPROP_STRING_RE.search(line)
            if string_match:
                title = string_match.group(1)
        elif line.startswith("WM_CLASS"):
            class_match = _XPROP_CLASS_RE.search(line)
            if class_match:
                wm_class = class_match.group(1)
    if not title and not wm_class:
        return None
    return WindowInfo(title=title, wm_class=wm_class)


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
    return WindowInfo(title=title, wm_class=wm_class)


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
    return WindowInfo(title=title, wm_class=wm_class)


_BACKENDS = {
    "xdotool": _xdotool_window,
    "xprop": _xprop_window,
    "sway": _sway_window,
    "hyprland": _hyprland_window,
}


def get_active_window(backend: Optional[str] = None) -> Optional[WindowInfo]:
    """Return the focused window's title/class, or ``None`` if unavailable."""
    backend = backend or detect_backend()
    if backend is None:
        return None
    return _BACKENDS[backend]()


def get_idle_seconds() -> Optional[float]:
    """Seconds since the last keyboard/mouse input, or ``None`` if unknown.

    Only implemented for X11 via ``xprintidle`` -- Wayland compositors don't
    expose idle time to arbitrary clients, so AFK detection is simply
    unavailable there (``pytime auto watch`` keeps tracking regardless).
    """
    if _is_wayland() or not shutil.which("xprintidle"):
        return None
    raw = _run(["xprintidle"])
    if raw is None:
        return None
    try:
        return int(raw.strip()) / 1000
    except ValueError:
        return None
