"""Tests for the active-window polling backends."""

from __future__ import annotations

import subprocess

from pytoolbox.core import activewindow as aw


def _fake_run(mapping):
    """Return a stand-in for ``subprocess.run`` keyed by the first two args."""

    def run(args, **kwargs):
        key = tuple(args[:2])
        result = mapping.get(key)
        if result is None:
            return subprocess.CompletedProcess(args, 1, "", "")
        stdout, returncode = result
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    return run


def test_detect_backend_prefers_xdotool_on_x11(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setattr(aw.shutil, "which", lambda name: "/usr/bin/xdotool" if name == "xdotool" else None)
    assert aw.detect_backend() == "xdotool"


def test_detect_backend_falls_back_to_xprop(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setattr(aw.shutil, "which", lambda name: "/usr/bin/xprop" if name == "xprop" else None)
    assert aw.detect_backend() == "xprop"


def test_detect_backend_none_when_nothing_available(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setattr(aw.shutil, "which", lambda name: None)
    assert aw.detect_backend() is None


def test_detect_backend_wayland_without_sway_or_hyprland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.delenv("SWAYSOCK", raising=False)
    monkeypatch.delenv("HYPRLAND_INSTANCE_SIGNATURE", raising=False)
    monkeypatch.setattr(aw.shutil, "which", lambda name: None)
    assert aw.detect_backend() is None


def test_xdotool_window_parses_title_and_class(monkeypatch):
    mapping = {
        ("xdotool", "getactivewindow"): ("123456\n", 0),
        ("xdotool", "getwindowname"): ("pytime.py — toolbox\n", 0),
        ("xdotool", "getwindowclassname"): ("code\n", 0),
    }
    monkeypatch.setattr(subprocess, "run", _fake_run(mapping))
    info = aw._xdotool_window()
    assert info.title == "pytime.py — toolbox"
    assert info.wm_class == "code"


def test_xdotool_window_returns_none_without_active_window(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_run({("xdotool", "getactivewindow"): ("", 1)}))
    assert aw._xdotool_window() is None


def test_xprop_window_parses_active_window_id():
    root_output = "_NET_ACTIVE_WINDOW(WINDOW): window id # 0x2c00003\n"
    info_output = (
        'WM_CLASS(STRING) = "code", "Code"\n'
        '_NET_WM_NAME(UTF8_STRING) = "pytime.py — toolbox"\n'
    )
    mapping = {
        ("xprop", "-root"): (root_output, 0),
        ("xprop", "-id"): (info_output, 0),
    }
    import subprocess as sp

    def run(args, **kwargs):
        key = tuple(args[:2])
        stdout, returncode = mapping[key]
        return sp.CompletedProcess(args, returncode, stdout, "")

    import pytoolbox.core.activewindow as mod

    old_run = mod.subprocess.run
    mod.subprocess.run = run
    try:
        info = mod._xprop_window()
    finally:
        mod.subprocess.run = old_run
    assert info.wm_class == "Code"
    assert info.title == "pytime.py — toolbox"


def test_sway_window_finds_focused_node(monkeypatch):
    tree = {
        "nodes": [
            {
                "focused": False,
                "nodes": [
                    {"focused": True, "app_id": "alacritty", "name": "user@host: ~/toolbox"},
                ],
            }
        ]
    }
    import json

    monkeypatch.setattr(aw, "_run", lambda args: json.dumps(tree))
    info = aw._sway_window()
    assert info.wm_class == "alacritty"
    assert info.title == "user@host: ~/toolbox"


def test_sway_window_none_when_nothing_focused(monkeypatch):
    monkeypatch.setattr(aw, "_run", lambda args: "{\"nodes\": []}")
    assert aw._sway_window() is None


def test_hyprland_window_parses_json(monkeypatch):
    import json

    monkeypatch.setattr(aw, "_run", lambda args: json.dumps({"class": "firefox", "title": "Example"}))
    info = aw._hyprland_window()
    assert info.wm_class == "firefox"
    assert info.title == "Example"


def test_get_idle_seconds_none_on_wayland(monkeypatch):
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert aw.get_idle_seconds() is None


def test_get_idle_seconds_parses_xprintidle_ms(monkeypatch):
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setattr(aw.shutil, "which", lambda name: "/usr/bin/xprintidle" if name == "xprintidle" else None)
    monkeypatch.setattr(aw, "_run", lambda args: "12345\n")
    assert aw.get_idle_seconds() == 12.345
