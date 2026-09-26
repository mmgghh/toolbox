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
    "jetbrains-pycharm-ce": "PyCharm CE",
    "jetbrains-idea": "IntelliJ IDEA",
    "jetbrains-idea-ce": "IntelliJ IDEA CE",
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
    "claude": "Claude",
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

#: wm_class (lowercased) of standalone desktop apps -> project label. Their
#: titles rarely say more than the app's name, so the app itself is the
#: most specific honest answer (the same way a ChatGPT browser tab is
#: attributed to "ChatGPT").
DEFAULT_APP_PROJECTS = {
    "com.anthropic.claude": "Claude",
    "claude": "Claude",
    "chatgpt": "ChatGPT",
    "com.openai.chatgpt": "ChatGPT",
    "slack": "Slack",
    "com.slack.slack": "Slack",
    "discord": "Discord",
    "com.discordapp.discord": "Discord",
    "telegramdesktop": "Telegram",
    "org.telegram.desktop": "Telegram",
    "obsidian": "Obsidian",
    "md.obsidian.obsidian": "Obsidian",
    "notion": "Notion",
    "spotify": "Spotify",
    "com.spotify.client": "Spotify",
    "zoom": "Zoom",
    "us.zoom.zoom": "Zoom",
    "thunderbird": "Thunderbird",
    "org.mozilla.thunderbird": "Thunderbird",
    "org.gnome.nautilus": "Files",
}

#: Suffixes editors/browsers append to their window title, stripped before
#: the remainder is treated as a project/page name.
_APP_SUFFIXES = (
    "visual studio code",
    "code - oss",
    "vscodium",
    "cursor",
    "pycharm community edition",
    "pycharm",
    "intellij idea community edition",
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

_SEPARATOR_RE = re.compile(r"\s+[—–-]\s+")
_BRACKET_RE = re.compile(r"\s*\[([^\]]*)\]\s*$")
_EXT_RE = re.compile(r"\.([A-Za-z0-9_+-]{1,10})$")
_PATH_RE = re.compile(r"([~/][^\s:;]+)")


def _strip_app_suffix(text: str) -> str:
    lowered = text.lower()
    for suffix in _APP_SUFFIXES:
        for dash in ("-", "—", "–"):
            marker = f" {dash} {suffix}"
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


#: Title patterns (regex, case-insensitive) redacted by default: private and
#: incognito browser windows still count toward time, but nothing about
#: which page it was is stored.
DEFAULT_REDACT_TITLES = [r"incognito", r"private browsing", r"inprivate"]


class RulesError(ValueError):
    """``~/.pytime/rules.json`` exists but can't be used as written."""


@dataclass(frozen=True)
class Rules:
    """Effective classification rules: defaults merged with user overrides."""

    editor_classes: set = field(default_factory=lambda: set(DEFAULT_EDITOR_CLASSES))
    terminal_classes: set = field(default_factory=lambda: set(DEFAULT_TERMINAL_CLASSES))
    browser_classes: set = field(default_factory=lambda: set(DEFAULT_BROWSER_CLASSES))
    app_labels: dict = field(default_factory=lambda: dict(DEFAULT_APP_LABELS))
    sites: dict = field(default_factory=lambda: dict(DEFAULT_SITES))
    app_projects: dict = field(default_factory=lambda: dict(DEFAULT_APP_PROJECTS))
    #: lowercased alias -> canonical project name.
    projects: dict = field(default_factory=dict)
    ignore_classes: set = field(default_factory=set)
    ignore_titles: list = field(default_factory=list)
    redact_classes: set = field(default_factory=set)
    redact_titles: list = field(default_factory=lambda: [re.compile(p, re.I) for p in DEFAULT_REDACT_TITLES])


def default_rules_path() -> Path:
    """Where a user's rule overrides live, next to the pytime database."""
    return Path.home() / ".pytime" / "rules.json"


def _compile_patterns(patterns: list, key: str) -> list:
    compiled = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.I))
        except re.error as exc:
            raise RulesError(f"Invalid regex {pattern!r} in {key}: {exc}") from exc
    return compiled


def load_rules(path: Optional[Path] = None) -> Rules:
    """Build the effective rule set: defaults, extended by an optional JSON file.

    The override file adds to the defaults rather than replacing them; every
    key is optional::

        {
          "editor_classes": [...], "terminal_classes": [...], "browser_classes": [...],
          "app_labels": {"wm_class": "Label"},
          "sites": {"substring": "Project"},
          "app_projects": {"wm_class": "Project"},
          "projects": {"Canonical": ["alias", "other-alias"]},
          "ignore": {"apps": ["wm_class"], "titles": ["regex"]},
          "redact": {"apps": ["wm_class"], "titles": ["regex"]}
        }

    Raises :class:`RulesError` when the file exists but is malformed, so a
    typo is reported instead of silently falling back to the defaults.
    """
    rules = Rules()
    path = path or default_rules_path()
    if not path.is_file():
        return rules
    try:
        overrides = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RulesError(f"Could not read {path}: {exc}") from exc
    if not isinstance(overrides, dict):
        raise RulesError(f"{path} must contain a JSON object.")

    rules.editor_classes.update(c.lower() for c in overrides.get("editor_classes", []))
    rules.terminal_classes.update(c.lower() for c in overrides.get("terminal_classes", []))
    rules.browser_classes.update(c.lower() for c in overrides.get("browser_classes", []))
    rules.app_labels.update({k.lower(): v for k, v in overrides.get("app_labels", {}).items()})
    rules.sites.update({k.lower(): v for k, v in overrides.get("sites", {}).items()})
    rules.app_projects.update({k.lower(): v for k, v in overrides.get("app_projects", {}).items()})
    for canonical, aliases in overrides.get("projects", {}).items():
        if isinstance(aliases, str):
            aliases = [aliases]
        for alias in [canonical, *aliases]:
            rules.projects[alias.lower()] = canonical
    for key, classes, titles in (
        ("ignore", rules.ignore_classes, rules.ignore_titles),
        ("redact", rules.redact_classes, rules.redact_titles),
    ):
        section = overrides.get(key, {})
        classes.update(c.lower() for c in section.get("apps", []))
        titles.extend(_compile_patterns(section.get("titles", []), f"{key}.titles"))
    return rules


def privacy_action(win: WindowInfo, rules: Rules) -> str:
    """Return ``"ignore"``, ``"redact"`` or ``""`` for a window."""
    wm_class = (win.wm_class or "").lower()
    title = win.title or ""
    if wm_class in rules.ignore_classes or any(p.search(title) for p in rules.ignore_titles):
        return "ignore"
    if wm_class in rules.redact_classes or any(p.search(title) for p in rules.redact_titles):
        return "redact"
    return ""


def _without_bracket(part: str) -> str:
    return _BRACKET_RE.sub("", part).strip()


def _parse_editor_title(title: str, project_first: bool) -> tuple[str, str]:
    """Return ``(project, file)`` from an editor title, in either order.

    VS Code writes ``file — project``; JetBrains IDEs write ``project – file``
    (optionally ``project [~/path]``). Whichever part carries a file
    extension is taken as the file, so the order only matters as a fallback
    when no part does (a welcome screen, a settings tab, ...).
    """
    text = _strip_app_suffix(title.strip().lstrip("●*").strip())
    parts = [part.strip() for part in _SEPARATOR_RE.split(text) if part.strip()]
    if not parts:
        return "", ""

    file_part = next((part for part in parts if _extension_of(_without_bracket(part))), None)
    if file_part is not None:
        rest = [part for part in parts if part is not file_part]
        project = _without_bracket(rest[0]) if rest else ""
        if not project:
            for part in parts:
                bracket = _BRACKET_RE.search(part)
                if bracket:
                    project = Path(bracket.group(1).rstrip("/")).name
                    break
        return project, _without_bracket(file_part).rsplit("/", 1)[-1]

    if len(parts) == 1:
        return _without_bracket(parts[0]), ""
    project, detail = (parts[0], parts[1]) if project_first else (parts[1], parts[0])
    return _without_bracket(project), _without_bracket(detail)


def _classify_editor(win: WindowInfo, rules: Rules, app: str, project_first: bool = False) -> Classified:
    project, file_part = _parse_editor_title(win.title, project_first)
    return Classified(category="editor", app=app, project=project, detail=file_part, ext=_extension_of(file_part))


#: Title written by ``pytime auto shell-init``: ``<command> @ <project dir>``.
_COMMAND_TITLE_RE = re.compile(r"^(?P<cmd>.+?) @ (?P<path>[~/].*)$")

#: Programs whose last argument is the file being edited in a terminal.
_TERMINAL_EDITORS = {"vim", "nvim", "vi", "nano", "emacs", "hx", "helix", "micro", "kak"}


def project_root_name(path_text: str) -> str:
    """Name of the git repository containing ``path_text``, else its last part.

    Lets a terminal sitting in ``~/projects/toolbox/pytoolbox/core`` count as
    project ``toolbox``. Only consults the filesystem when the path exists
    on this machine; never walks above the home directory.
    """
    raw = path_text.rstrip("/") or path_text
    candidate = Path(raw).expanduser()
    try:
        current = candidate.resolve() if candidate.is_dir() else None
        home = Path.home().resolve()
    except OSError:
        current = None
    if current is not None:
        for directory in (current, *current.parents):
            if directory == home or directory == directory.parent:
                break
            if (directory / ".git").exists():
                return directory.name
    name = Path(raw).name
    return "" if name in ("", "~") else name


def _classify_terminal(win: WindowInfo, app: str) -> Classified:
    title = win.title.strip()
    match = _COMMAND_TITLE_RE.match(title)
    if match:
        command = match.group("cmd").strip()
        path_text = match.group("path").strip()
        words = command.split()
        program = Path(words[0]).name.lower() if words else ""
        is_claude = program == "claude"
    else:
        command, program = "", ""
        path_match = _PATH_RE.search(title)
        path_text = path_match.group(1) if path_match else ""
        is_claude = re.search(r"\bclaude\b", title.replace(path_text, "").lower()) is not None

    project = project_root_name(path_text) if path_text else ""
    detail, ext = (command or title), ""
    if program in _TERMINAL_EDITORS and len(command.split()) > 1:
        detail = command.split()[-1].rsplit("/", 1)[-1]
        ext = _extension_of(detail)
    label = f"{app} (Claude Code)" if is_claude else app
    return Classified(category="terminal", app=label, project=project, detail=detail, ext=ext)


def _site_matches(needle: str, lowered: str) -> bool:
    # Whole-word match, so "claude" hits "Claude Code" but a site name never
    # matches inside an unrelated longer word.
    return re.search(rf"(?<![\w]){re.escape(needle)}(?![\w])", lowered) is not None


#: GitHub tab titles name the repository: "Title · Issue #1 · owner/repo",
#: "path at main · owner/repo", or a repo home page's "owner/repo: about".
_GITHUB_TRAILING_RE = re.compile(r"·\s*[\w.-]+/(?P<repo>[\w.-]+)\s*$")
_GITHUB_LEADING_RE = re.compile(r"^(?:github - )?[\w.-]+/(?P<repo>[\w.-]+)(?::\s|$)", re.I)
#: GitLab: "Issues · group / sub / project · GitLab".
_GITLAB_RE = re.compile(r"·\s*(?P<path>[^·]+?)\s*·\s*gitlab\s*$", re.I)
#: Jira: "[ABC-123] Title - Jira".
_JIRA_RE = re.compile(r"\[(?P<key>[A-Z][A-Z0-9]+)-\d+\]")


def _site_project(page_title: str) -> str:
    """A repository or issue-tracker project named in the tab title, if any."""
    for pattern in (_GITHUB_TRAILING_RE, _GITHUB_LEADING_RE):
        match = pattern.search(page_title)
        if match:
            return match.group("repo")
    match = _GITLAB_RE.search(page_title)
    if match:
        return match.group("path").split("/")[-1].strip()
    if "jira" in page_title.lower():
        match = _JIRA_RE.search(page_title)
        if match:
            return match.group("key")
    return ""


def _classify_browser(win: WindowInfo, rules: Rules, app: str) -> Classified:
    page_title = _strip_app_suffix(win.title)
    if page_title.lower() in _APP_SUFFIXES:
        page_title = ""  # a new/blank tab: the title is just the browser's name
    project = _site_project(page_title)
    if not project:
        lowered = page_title.lower()
        for needle, label in rules.sites.items():
            if _site_matches(needle, lowered):
                project = label
                break
    return Classified(category="browser", app=app, project=project, detail=page_title, ext="")


#: Invisible bidi controls (LRM/RLM, embeddings, isolates). Chrome wraps
#: right-to-left titles in them, which would hide the " - Google Chrome"
#: suffix from an ``endswith`` check.
_BIDI_CONTROLS_RE = re.compile("[‎‏‪-‮⁦-⁩]")


def _classify(win: WindowInfo, rules: Rules) -> Classified:
    wm_class = (win.wm_class or "").lower()
    app = rules.app_labels.get(wm_class, win.wm_class or "Unknown")

    is_jetbrains = wm_class.startswith("jetbrains-")
    if is_jetbrains and wm_class not in rules.app_labels:
        app = "JetBrains " + wm_class[len("jetbrains-"):].replace("-", " ").title()
    if wm_class in rules.editor_classes or is_jetbrains:
        return _classify_editor(win, rules, app, project_first=is_jetbrains)
    if wm_class in rules.terminal_classes:
        return _classify_terminal(win, app if app != "Unknown" else "Terminal")
    if wm_class in rules.browser_classes:
        return _classify_browser(win, rules, app if app != "Unknown" else "Browser")
    project = rules.app_projects.get(wm_class, "")
    if project and wm_class not in rules.app_labels:
        app = project
    return Classified(category="other", app=app, project=project, detail=win.title.strip(), ext="")


def classify_window(win: WindowInfo, rules: Optional[Rules] = None) -> Optional[Classified]:
    """Classify a raw window into a category, app, project, file/page, and extension.

    Returns ``None`` for a window the rules say to ignore. A redacted window
    keeps its category and app (so the time still counts) but loses
    everything derived from its title.
    """
    rules = rules or Rules()
    win = WindowInfo(title=_BIDI_CONTROLS_RE.sub("", win.title or "").strip(), wm_class=win.wm_class)
    action = privacy_action(win, rules)
    if action == "ignore":
        return None
    classified = _classify(win, rules)
    if action == "redact":
        return Classified(category=classified.category, app=classified.app, project="", detail="", ext="")
    canonical = rules.projects.get(classified.project.lower()) if classified.project else None
    if canonical:
        classified = Classified(
            category=classified.category,
            app=classified.app,
            project=canonical,
            detail=classified.detail,
            ext=classified.ext,
        )
    return classified
