"""Tests for window-title classification heuristics (``pytime auto``)."""

from __future__ import annotations

import json

from pytoolbox.core.activewindow import WindowInfo
from pytoolbox.core.activity_rules import Rules, classify_window, load_rules


def test_classify_vscode_title_with_em_dash():
    win = WindowInfo(title="pytime.py — toolbox", wm_class="code")
    result = classify_window(win)
    assert result.category == "editor"
    assert result.app == "VS Code"
    assert result.project == "toolbox"
    assert result.detail == "pytime.py"
    assert result.ext == "py"


def test_classify_vscode_title_strips_dirty_marker_and_app_suffix():
    win = WindowInfo(title="● pytime.py — toolbox - Visual Studio Code", wm_class="code")
    result = classify_window(win)
    assert result.project == "toolbox"
    assert result.detail == "pytime.py"
    assert result.ext == "py"


def test_classify_pycharm_ce_project_first_title():
    win = WindowInfo(title="toolbox – pytime.py", wm_class="jetbrains-pycharm-ce")
    result = classify_window(win)
    assert result.category == "editor"
    assert result.app == "PyCharm CE"
    assert result.project == "toolbox"
    assert result.detail == "pytime.py"
    assert result.ext == "py"


def test_classify_jetbrains_title_with_path_bracket():
    win = WindowInfo(title="toolbox [~/projects/toolbox] – pytoolbox/pytime.py", wm_class="jetbrains-pycharm")
    result = classify_window(win)
    assert result.project == "toolbox"
    assert result.detail == "pytime.py"
    assert result.ext == "py"


def test_classify_unknown_jetbrains_product_is_still_an_editor():
    win = WindowInfo(title="shop – main.go", wm_class="jetbrains-fleet")
    result = classify_window(win)
    assert result.category == "editor"
    assert result.app == "JetBrains Fleet"
    assert result.project == "shop"
    assert result.ext == "go"


def test_classify_jetbrains_without_open_file_uses_project_first():
    win = WindowInfo(title="toolbox – Settings", wm_class="jetbrains-pycharm-ce")
    result = classify_window(win)
    assert result.project == "toolbox"
    assert result.detail == "Settings"
    assert result.ext == ""


def test_classify_terminal_extracts_project_from_path():
    win = WindowInfo(title="user@host: ~/toolbox", wm_class="gnome-terminal-server")
    result = classify_window(win)
    assert result.category == "terminal"
    assert result.app == "Terminal"
    assert result.project == "toolbox"
    assert result.ext == ""


def test_classify_terminal_flags_claude_code():
    win = WindowInfo(title="running claude in ~/toolbox", wm_class="alacritty")
    result = classify_window(win)
    assert "Claude Code" in result.app
    assert result.project == "toolbox"


def test_classify_browser_matches_known_site():
    win = WindowInfo(title="ChatGPT - Google Chrome", wm_class="google-chrome")
    result = classify_window(win)
    assert result.category == "browser"
    assert result.app == "Chrome"
    assert result.project == "ChatGPT"
    assert result.detail == "ChatGPT"


def test_classify_browser_matches_claude_code_page():
    win = WindowInfo(title="Claude Code - Google Chrome", wm_class="google-chrome")
    assert classify_window(win).project == "Claude"


def test_classify_browser_site_needs_whole_word():
    win = WindowInfo(title="Reading about claudetown - Google Chrome", wm_class="google-chrome")
    assert classify_window(win).project == ""


def test_classify_browser_strips_bidi_marks_around_rtl_title():
    win = WindowInfo(title="‎‫خانه - Google Chrome‬‎", wm_class="google-chrome")
    result = classify_window(win)
    assert result.detail == "خانه"


def test_classify_browser_new_tab_has_empty_detail():
    win = WindowInfo(title="Google Chrome", wm_class="google-chrome")
    assert classify_window(win).detail == ""


def test_classify_browser_unknown_site_leaves_project_blank():
    win = WindowInfo(title="Some Random Page - Google Chrome", wm_class="google-chrome")
    result = classify_window(win)
    assert result.category == "browser"
    assert result.project == ""
    assert result.detail == "Some Random Page"


def test_classify_claude_desktop_app_uses_app_as_project():
    win = WindowInfo(title="Claude", wm_class="com.anthropic.Claude")
    result = classify_window(win)
    assert result.category == "other"
    assert result.app == "Claude"
    assert result.project == "Claude"


def test_app_projects_override(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"app_projects": {"com.example.Notes": "Notes"}}), encoding="utf-8")
    result = classify_window(WindowInfo(title="x", wm_class="com.example.Notes"), load_rules(path))
    assert result.project == "Notes"


def test_classify_unknown_app_falls_back_to_other():
    win = WindowInfo(title="whatever", wm_class="some-random-app")
    result = classify_window(win)
    assert result.category == "other"
    assert result.app == "some-random-app"
    assert result.project == ""
    assert result.detail == "whatever"


def test_load_rules_without_override_file_returns_defaults(tmp_path):
    rules = load_rules(tmp_path / "missing.json")
    assert "code" in rules.editor_classes
    assert "chatgpt" in rules.sites


def test_load_rules_merges_override_file(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps(
            {
                "editor_classes": ["my-editor"],
                "app_labels": {"my-editor": "My Editor"},
                "sites": {"my-site.com": "My Site"},
            }
        ),
        encoding="utf-8",
    )
    rules = load_rules(path)
    assert "my-editor" in rules.editor_classes
    assert "code" in rules.editor_classes  # defaults still present
    assert rules.app_labels["my-editor"] == "My Editor"
    assert rules.sites["my-site.com"] == "My Site"
    assert "chatgpt" in rules.sites  # defaults still present

    win = WindowInfo(title="notes.txt — scratch", wm_class="my-editor")
    result = classify_window(win, rules)
    assert result.category == "editor"
    assert result.app == "My Editor"


def test_load_rules_ignores_malformed_override_file(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text("{not valid json", encoding="utf-8")
    rules = load_rules(path)
    assert rules.editor_classes == Rules().editor_classes
