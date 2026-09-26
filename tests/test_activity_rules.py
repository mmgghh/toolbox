"""Tests for window-title classification heuristics (``pytime auto``)."""

from __future__ import annotations

import json

import pytest

from pytoolbox.core.activewindow import WindowInfo
from pytoolbox.core.activity_rules import RulesError, classify_window, load_rules


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


def test_load_rules_reports_malformed_override_file(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(RulesError, match="Could not read"):
        load_rules(path)


def test_load_rules_reports_bad_regex(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"ignore": {"titles": ["("]}}), encoding="utf-8")
    with pytest.raises(RulesError, match="ignore.titles"):
        load_rules(path)


def test_project_aliases_map_to_canonical_name(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"projects": {"SAMT": ["samt", "samt-backend"]}}), encoding="utf-8")
    rules = load_rules(path)
    editor = classify_window(WindowInfo("samt – main.py", "jetbrains-pycharm-ce"), rules)
    browser = classify_window(WindowInfo("Fix it · Issue #3 · org/samt-backend - Google Chrome", "google-chrome"), rules)
    assert editor.project == "SAMT"
    assert browser.project == "SAMT"


def test_ignore_rules_drop_the_window(tmp_path):
    path = tmp_path / "rules.json"
    path.write_text(json.dumps({"ignore": {"apps": ["org.keepassxc.KeePassXC"], "titles": ["bank"]}}), encoding="utf-8")
    rules = load_rules(path)
    assert classify_window(WindowInfo("Passwords", "org.keepassxc.KeePassXC"), rules) is None
    assert classify_window(WindowInfo("My Bank - Google Chrome", "google-chrome"), rules) is None
    assert classify_window(WindowInfo("News - Google Chrome", "google-chrome"), rules) is not None


def test_incognito_windows_are_redacted_by_default():
    result = classify_window(WindowInfo("YouTube - Google Chrome (Incognito)", "google-chrome"))
    assert result.category == "browser"
    assert result.app == "Chrome"
    assert (result.project, result.detail, result.ext) == ("", "", "")


def test_github_titles_give_the_repository():
    cases = {
        "Add auto tracking · Pull Request #7 · mmgghh/toolbox - Google Chrome": "toolbox",
        "toolbox/pytoolbox/pytime.py at main · mmgghh/toolbox - Google Chrome": "toolbox",
        "mmgghh/toolbox: Small CLI tools - Google Chrome": "toolbox",
        "GitHub - mmgghh/toolbox: Small CLI tools - Google Chrome": "toolbox",
    }
    for title, repo in cases.items():
        assert classify_window(WindowInfo(title, "google-chrome")).project == repo, title


def test_gitlab_and_jira_titles():
    gitlab = classify_window(WindowInfo("Issues · group / sub / shop · GitLab - Google Chrome", "google-chrome"))
    jira = classify_window(WindowInfo("[SAMT-42] Fix login - Jira - Google Chrome", "google-chrome"))
    assert gitlab.project == "shop"
    assert jira.project == "SAMT"


def test_terminal_shell_init_title_and_claude(tmp_path, monkeypatch):
    repo = tmp_path / "home" / "projects" / "toolbox"
    (repo / ".git").mkdir(parents=True)
    (repo / "pytoolbox" / "core").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    result = classify_window(WindowInfo("claude @ ~/projects/toolbox", "org.gnome.Terminal"))
    assert result.project == "toolbox"
    assert result.app == "Terminal (Claude Code)"
    assert result.detail == "claude"

    nested = classify_window(WindowInfo("mohammad@mg: ~/projects/toolbox/pytoolbox/core", "org.gnome.Terminal"))
    assert nested.project == "toolbox"  # resolved to the git root, not "core"


def test_terminal_editor_title_gives_file_and_extension():
    result = classify_window(WindowInfo("nvim pytime.py @ ~/projects/toolbox", "kitty"))
    assert result.detail == "pytime.py"
    assert result.ext == "py"
    assert result.project == "toolbox"


def test_terminal_directory_named_claude_is_not_claude_code():
    result = classify_window(WindowInfo("ls @ ~/projects/claude-notes", "kitty"))
    assert "Claude Code" not in result.app


def test_terminal_in_home_has_no_project():
    assert classify_window(WindowInfo("mohammad@mg: ~", "org.gnome.Terminal")).project == ""
