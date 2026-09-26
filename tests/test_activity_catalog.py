"""Tests for the built-in app/site catalog behind ``pytime auto``."""

from __future__ import annotations

import json
from collections import Counter

import pytest

from pytoolbox.core import activity_catalog
from pytoolbox.core.activewindow import WindowInfo
from pytoolbox.core.activity_rules import Rules, classify_window, load_rules, rules_to_json
from pytoolbox.pytime import time_cli

KINDS = {"editor", "terminal", "browser", "app"}


def test_catalog_is_well_formed():
    counts = Counter(cls for app in activity_catalog.APPS for cls in app.classes)
    duplicates = sorted(cls for cls, n in counts.items() if n > 1)
    assert duplicates == [], "a window class may belong to one app only"
    for app in activity_catalog.APPS:
        assert app.kind in KINDS, app
        assert app.classes, app
        assert all(cls == cls.lower().strip() and cls for cls in app.classes), app
        assert all(suffix == suffix.lower() for suffix in app.suffixes), app
        assert not (app.redact and app.project), app
    assert all(key == key.lower() for key in activity_catalog.SITES)


def test_suffixes_longest_first():
    suffixes = activity_catalog.title_suffixes()
    assert suffixes.index("firefox developer edition") < suffixes.index("firefox")
    assert suffixes.index("pycharm community edition") < suffixes.index("pycharm")


@pytest.mark.parametrize(
    ("title", "wm_class", "expected"),
    [
        # editors: (category, app, project, detail, ext)
        ("main.rs — shop", "dev.zed.Zed", ("editor", "Zed", "shop", "main.rs", "rs")),
        ("app.ts - web - Visual Studio Code - Insiders", "Code - Insiders", ("editor", "VS Code Insiders", "web", "app.ts", "ts")),
        ("shop – MainActivity.kt", "jetbrains-studio", ("editor", "Android Studio", "shop", "MainActivity.kt", "kt")),
        ("lib.rs - shop - Cursor", "Cursor", ("editor", "Cursor", "shop", "lib.rs", "rs")),
        ("notes.md — Kate", "org.kde.kate", ("editor", "Kate", "", "notes.md", "md")),
        # browsers
        ("Array - JavaScript | MDN — LibreWolf", "librewolf", ("browser", "LibreWolf", "MDN", "Array - JavaScript | MDN", "")),
        ("requests · PyPI - Microsoft​ Edge", "microsoft-edge", ("browser", "Edge", "PyPI", "requests · PyPI", "")),
        ("How to rebase - Stack Overflow — Firefox Developer Edition", "firefoxdeveloperedition",
         ("browser", "Firefox Developer Edition", "Stack Overflow", "How to rebase - Stack Overflow", "")),
        ("Deployments – Vercel - Brave", "brave-browser", ("browser", "Brave", "Vercel", "Deployments – Vercel", "")),
        # apps
        ("prod-db - DBeaver 24.1", "DBeaver", ("other", "DBeaver", "", "prod-db - DBeaver 24.1", "")),
        ("Postman", "com.getpostman.Postman", ("other", "Postman", "", "Postman", "")),
        ("general - Slack", "Slack", ("other", "Slack", "Slack", "general - Slack", "")),
        ("Inbox - Mozilla Thunderbird", "thunderbird", ("other", "Thunderbird", "Mail", "Inbox - Mozilla Thunderbird", "")),
    ],
)
def test_catalog_classifications(title, wm_class, expected):
    result = classify_window(WindowInfo(title, wm_class))
    assert (result.category, result.app, result.project, result.detail, result.ext) == expected


def test_terminals_from_the_catalog():
    for wm_class, label in (("com.mitchellh.ghostty", "Ghostty"), ("org.kde.yakuake", "Yakuake"), ("dev.warp.Warp", "Warp")):
        result = classify_window(WindowInfo("user@host: ~/projects/toolbox", wm_class))
        assert (result.category, result.app, result.project) == ("terminal", label, "toolbox")


def test_password_managers_are_redacted():
    result = classify_window(WindowInfo("work.kdbx - KeePassXC", "org.keepassxc.KeePassXC"))
    assert (result.app, result.project, result.detail) == ("KeePassXC", "", "")


def test_ambiguous_words_are_not_sites():
    result = classify_window(WindowInfo("Linear algebra notes - Google Chrome", "google-chrome"))
    assert result.project == ""


def test_dump_round_trips_through_load_rules(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps({"projects": {"SAMT": ["samt", "samt-backend"]}, "ignore": {"titles": ["bank"]}}),
        encoding="utf-8",
    )
    rules = load_rules(source)
    dumped = tmp_path / "dumped.json"
    dumped.write_text(json.dumps(rules_to_json(rules)), encoding="utf-8")
    assert rules_to_json(load_rules(dumped)) == rules_to_json(rules)


def test_dump_command_prints_the_catalog(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("PYTIME_DB", str(tmp_path / "pytime.db"))
    from pytoolbox.core import activity_rules

    monkeypatch.setattr(activity_rules, "default_rules_path", lambda: tmp_path / "missing.json")
    result = runner.invoke(time_cli, ["auto", "rules", "--dump"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["app_labels"]["dbeaver"] == "DBeaver"
    assert "org.gnome.ptyxis" in payload["terminal_classes"]
    assert payload == rules_to_json(Rules())
