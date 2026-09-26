"""Turn a raw window title/class into a project, file, and extension.

There's no reliable, install-free way for a CLI to learn a browser tab's
real URL or an editor's real file path (that needs a browser extension or an
editor plugin -- see ``docs/pytime.md``). This module works from window
*titles* instead, which every windowing system already exposes, and parses
the conventions common editors/terminals/browsers put in them. It is
necessarily heuristic: titles vary across app versions/locales/themes, so
unmatched windows fall back to a generic "other" bucket rather than a
guess presented as fact.

Defaults live in code; a user can extend (not replace) them by dropping a
JSON file at ``~/.pytime/rules.json`` -- see :func:`load_rules`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pytoolbox.core.activewindow import WindowInfo

#: wm_class values (lowercased) treated as code editors.
DEFAULT_EDITOR_CLASSES = {
    "code",
    "code - oss",
    "code-oss",
    "codium",
    "vscodium",
    "cursor",
    "code-url-handler",
    "sublime_text",
    "atom",
    "jetbrains-pycharm",
    "jetbrains-idea",
    "jetbrains-webstorm",
    "jetbrains-clion",
    "jetbrains-goland",
    "jetbrains-rider",
    "jetbrains-phpstorm",
    "jetbrains-rubymine",
    "jetbrains-datagrip",
    "pycharm",
    "idea",
    "webstorm",
    "clion",
    "goland",
    "rider",
    "phpstorm",
    "vim",
    "nvim",
}

#: wm_class values (lowercased) treated as terminal emulators.
DEFAULT_TERMINAL_CLASSES = {
    "gnome-terminal-server",
    "gnome-terminal",
    "konsole",
    "xterm",
    "alacritty",
    "kitty",
    "foot",
    "wezterm",
    "terminator",
    "tilix",
    "xfce4-terminal",
    "terminology",
    "st",
    "urxvt",
    "urxvt256c",
    # Wayland app ids (GNOME/KDE report these instead of X11 classes).
    "org.gnome.terminal",
    "org.gnome.console",
    "kgx",
    "org.gnome.ptyxis",
    "ptyxis",
    "org.kde.konsole",
    "org.wezfurlong.wezterm",
    "com.mitchellh.ghostty",
    "ghostty",
}

#: wm_class values (lowercased) treated as web browsers.
DEFAULT_BROWSER_CLASSES = {
    "google-chrome",
    "chromium",
    "chromium-browser",
    "firefox",
    "brave-browser",
    "microsoft-edge",
    "opera",
    "vivaldi-stable",
}

#: wm_class -> human-readable app name, for whichever category matched.
DEFAULT_APP_LABELS = {
    "code": "VS Code",
    "code - oss": "VS Code (OSS)",
    "code-oss": "VS Code (OSS)",
    "codium": "VSCodium",
    "vscodium": "VSCodium",
    "cursor": "Cursor",
    "sublime_text": "Sublime Text",
    "atom": "Atom",
    "jetbrains-pycharm": "PyCharm",
    "jetbrains-idea": "IntelliJ IDEA",
    "jetbrains-webstorm": "WebStorm",
    "jetbrains-clion": "CLion",
    "jetbrains-goland": "GoLand",
    "jetbrains-rider": "Rider",
    "jetbrains-phpstorm": "PhpStorm",
    "jetbrains-rubymine": "RubyMine",
    "jetbrains-datagrip": "DataGrip",
    "pycharm": "PyCharm",
    "idea": "IntelliJ IDEA",
    "webstorm": "WebStorm",
    "clion": "CLion",
    "goland": "GoLand",
    "rider": "Rider",
    "phpstorm": "PhpStorm",
    "vim": "Vim",
    "nvim": "Neovim",
    "gnome-terminal-server": "Terminal",
    "gnome-terminal": "Terminal",
    "konsole": "Konsole",
    "xterm": "XTerm",
    "alacritty": "Alacritty",
    "kitty": "Kitty",
    "foot": "Foot",
    "wezterm": "WezTerm",
    "terminator": "Terminator",
    "tilix": "Tilix",
    "xfce4-terminal": "Xfce Terminal",
    "terminology": "Terminology",
    "st": "st",
    "urxvt": "urxvt",
    "urxvt256c": "urxvt",
    "org.gnome.terminal": "Terminal",
    "org.gnome.console": "Console",
    "kgx": "Console",
    "org.gnome.ptyxis": "Ptyxis",
    "ptyxis": "Ptyxis",
    "org.kde.konsole": "Konsole",
    "org.wezfurlong.wezterm": "WezTerm",
    "com.mitchellh.ghostty": "Ghostty",
    "ghostty": "Ghostty",
    "google-chrome": "Chrome",
    "chromium": "Chromium",
    "chromium-browser": "Chromium",
    "firefox": "Firefox",
    "brave-browser": "Brave",
    "microsoft-edge": "Edge",
    "opera": "Opera",
    "vivaldi-stable": "Vivaldi",
}

#: Substring (lowercased, matched against the browser tab title) -> project
#: label. First match wins, so put more specific entries before generic ones
#: if you add your own.
DEFAULT_SITES = {
    "chatgpt": "ChatGPT",
    "claude.ai": "Claude",
    " claude": "Claude",
    "github": "GitHub",
    "gitlab": "GitLab",
    "stack overflow": "Stack Overflow",
    "notion": "Notion",
    "figma": "Figma",
    "jira": "Jira",
    "confluence": "Confluence",
    "slack": "Slack",
    "youtube": "YouTube",
    "gmail": "Gmail",
    "google docs": "Google Docs",
    "google sheets": "Google Sheets",
    "google calendar": "Google Calendar",
    "linear.app": "Linear",
    "trello": "Trello",
}

#: Suffixes editors/browsers append to their window title, stripped before
#: the remainder is treated as a project/page name.
_APP_SUFFIXES = (
    "visual studio code",
    "code - oss",
    "vscodium",
    "cursor",
    "pycharm",
    "intellij idea",
    "webstorm",
    "clion",
    "goland",
    "rider",
    "phpstorm",
    "rubymine",
    "datagrip",
    "sublime text",
    "google chrome",
    "chromium",
    "mozilla firefox",
    "firefox",
    "brave",
    "microsoft edge",
    "opera",
    "vivaldi",
)

_TITLE_SPLIT_RE = re.compile(r"^\s*[●*]?\s*(?P<file>.+?)\s*[—–-]\s*(?P<rest>.+?)\s*$")
_EXT_RE = re.compile(r"\.([A-Za-z0-9_+-]{1,10})$")
_PATH_RE = re.compile(r"([~/][^\s:;]+)")


def _strip_app_suffix(text: str) -> str:
    lowered = text.lower()
    for suffix in _APP_SUFFIXES:
        marker = f" - {suffix}"
        if lowered.endswith(marker):
            return text[: -len(marker)].strip()
        marker = f" — {suffix}"
        if lowered.endswith(marker):
            return text[: -len(marker)].strip()
    return text.strip()


def _extension_of(filename: str) -> str:
    match = _EXT_RE.search(filename.strip())
    return match.group(1).lower() if match else ""


@dataclass(frozen=True)
class Classified:
    """The result of classifying one :class:`WindowInfo`."""

    category: str  #: "editor" | "terminal" | "browser" | "other"
    app: str
    project: str
    detail: str
    ext: str


@dataclass(frozen=True)
class Rules:
    """Effective classification rules: defaults merged with user overrides."""

    editor_classes: set = field(default_factory=lambda: set(DEFAULT_EDITOR_CLASSES))
    terminal_classes: set = field(default_factory=lambda: set(DEFAULT_TERMINAL_CLASSES))
    browser_classes: set = field(default_factory=lambda: set(DEFAULT_BROWSER_CLASSES))
    app_labels: dict = field(default_factory=lambda: dict(DEFAULT_APP_LABELS))
    sites: dict = field(default_factory=lambda: dict(DEFAULT_SITES))


def default_rules_path() -> Path:
    """Where a user's rule overrides live, next to the pytime database."""
    return Path.home() / ".pytime" / "rules.json"


def load_rules(path: Optional[Path] = None) -> Rules:
    """Build the effective rule set: defaults, extended by an optional JSON file.

    The override file adds to the defaults rather than replacing them:
    ``{"editor_classes": [...], "terminal_classes": [...],
    "browser_classes": [...], "app_labels": {...}, "sites": {...}}`` --
    every key is optional, and lists/dicts are merged in, not swapped out.
    """
    rules = Rules()
    path = path or default_rules_path()
    if not path.is_file():
        return rules
    try:
        overrides = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return rules
    rules.editor_classes.update(c.lower() for c in overrides.get("editor_classes", []))
    rules.terminal_classes.update(c.lower() for c in overrides.get("terminal_classes", []))
    rules.browser_classes.update(c.lower() for c in overrides.get("browser_classes", []))
    rules.app_labels.update({k.lower(): v for k, v in overrides.get("app_labels", {}).items()})
    rules.sites.update({k.lower(): v for k, v in overrides.get("sites", {}).items()})
    return rules


def _classify_editor(win: WindowInfo, rules: Rules, app: str) -> Classified:
    match = _TITLE_SPLIT_RE.match(win.title)
    if match:
        file_part = match.group("file").strip()
        project = _strip_app_suffix(match.group("rest"))
    else:
        file_part = win.title.strip()
        project = ""
    return Classified(category="editor", app=app, project=project, detail=file_part, ext=_extension_of(file_part))


def _classify_terminal(win: WindowInfo, app: str) -> Classified:
    path_match = _PATH_RE.search(win.title)
    project = Path(path_match.group(1).rstrip("/")).name if path_match else ""
    label = app
    if "claude" in win.title.lower():
        label = f"{app} (Claude Code)" if app != "Terminal" else "Terminal (Claude Code)"
    return Classified(category="terminal", app=label, project=project, detail=win.title.strip(), ext="")


def _classify_browser(win: WindowInfo, rules: Rules, app: str) -> Classified:
    page_title = _strip_app_suffix(win.title)
    lowered = page_title.lower()
    project = ""
    for needle, label in rules.sites.items():
        if needle in lowered:
            project = label
            break
    return Classified(category="browser", app=app, project=project, detail=page_title, ext="")


def classify_window(win: WindowInfo, rules: Optional[Rules] = None) -> Classified:
    """Classify a raw window into a category, app, project, file/page, and extension."""
    rules = rules or Rules()
    wm_class = (win.wm_class or "").lower()
    app = rules.app_labels.get(wm_class, win.wm_class or "Unknown")

    if wm_class in rules.editor_classes:
        return _classify_editor(win, rules, app)
    if wm_class in rules.terminal_classes:
        return _classify_terminal(win, app if app != "Unknown" else "Terminal")
    if wm_class in rules.browser_classes:
        return _classify_browser(win, rules, app if app != "Unknown" else "Browser")
    return Classified(category="other", app=app, project="", detail=win.title.strip(), ext="")
