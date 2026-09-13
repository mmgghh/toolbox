"""Tests for RsyncTargetSuggester, the local+remote completer behind pyssh's
`sync -s/-d` fields.

No real SSH connection is ever made: `_ssh_list` is the sole I/O boundary and
is monkeypatched wherever a remote listing is exercised.
"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual", reason="TUI is an optional extra")

from pytoolbox.tui import rsync_paths  # noqa: E402
from pytoolbox.tui.rsync_paths import (  # noqa: E402
    RsyncTargetSuggester,
    list_remote_target_matches,
    parse_remote_target,
)


def suggest(suggester: RsyncTargetSuggester, value: str):
    return asyncio.run(suggester.get_suggestion(value))


# ── parse_remote_target ──────────────────────────────────────────────

@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("./local/path", None),
        ("relative", None),
        ("", None),
        ("me@host:/sr", ("me@host", "/", "me@host:/", "sr")),
        ("host:/srv/si", ("host", "/srv", "host:/srv/", "si")),
        ("host:", ("host", ".", "host:", "")),
        # The ssh target drops the password (never shells out with it on the
        # argv), but the shown prefix keeps the user's own spelling, same as
        # local completion preserves "~" instead of expanding it.
        ("me:secret@host:/srv", ("me@host", "/", "me:secret@host:/", "srv")),
    ],
)
def test_parse_remote_target(value, expected):
    assert parse_remote_target(value) == expected


# ── local + config-host completion (no remote call) ─────────────────

@pytest.fixture
def tree(tmp_path, monkeypatch):
    (tmp_path / "alpha.txt").write_text("")
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_suggests_a_local_path_when_not_yet_remote(tree):
    assert suggest(RsyncTargetSuggester(), "al") == "alpha.txt"


def test_suggests_a_config_host_name_when_no_colon_yet(tree, monkeypatch):
    monkeypatch.setattr(rsync_paths.hosts, "config_host_names", lambda: ["prod-web", "prod-db"])
    assert suggest(RsyncTargetSuggester(), "prod-w") == "prod-web:"


def test_local_matches_win_over_host_names(tree, monkeypatch):
    # "alpha.txt" exists locally; nothing should override that with a host guess.
    monkeypatch.setattr(rsync_paths.hosts, "config_host_names", lambda: ["alphabet-host"])
    assert suggest(RsyncTargetSuggester(), "al") == "alpha.txt"


def test_host_names_are_not_suggested_once_a_slash_is_typed(tree, monkeypatch):
    monkeypatch.setattr(rsync_paths.hosts, "config_host_names", lambda: ["subversion"])
    assert suggest(RsyncTargetSuggester(), "sub/") is None


# ── remote completion ─────────────────────────────────────────────────

def test_suggests_a_remote_entry(monkeypatch):
    monkeypatch.setattr(rsync_paths, "REMOTE_DEBOUNCE_SECONDS", 0)
    calls = []

    async def fake_ssh_list(ssh_target, remote_dir):
        calls.append((ssh_target, remote_dir))
        return ["alpha.txt", "beta/"]

    monkeypatch.setattr(rsync_paths, "_ssh_list", fake_ssh_list)
    assert suggest(RsyncTargetSuggester(), "me@host:al") == "me@host:alpha.txt"
    assert calls == [("me@host", ".")]


def test_remote_listing_is_cached_per_directory(monkeypatch):
    monkeypatch.setattr(rsync_paths, "REMOTE_DEBOUNCE_SECONDS", 0)
    calls = []

    async def fake_ssh_list(ssh_target, remote_dir):
        calls.append((ssh_target, remote_dir))
        return ["alpha.txt", "alphabet/"]

    monkeypatch.setattr(rsync_paths, "_ssh_list", fake_ssh_list)
    suggester = RsyncTargetSuggester()
    assert suggest(suggester, "me@host:al") == "me@host:alpha.txt"
    assert suggest(suggester, "me@host:alp") == "me@host:alpha.txt"
    assert len(calls) == 1


def test_remote_lookup_fails_silently(monkeypatch):
    monkeypatch.setattr(rsync_paths, "REMOTE_DEBOUNCE_SECONDS", 0)

    async def fake_ssh_list(ssh_target, remote_dir):
        return []

    monkeypatch.setattr(rsync_paths, "_ssh_list", fake_ssh_list)
    assert suggest(RsyncTargetSuggester(), "me@host:al") is None


def test_a_newer_keystroke_suppresses_a_stale_remote_lookup(monkeypatch):
    calls = []

    async def fake_ssh_list(ssh_target, remote_dir):
        calls.append((ssh_target, remote_dir))
        return ["alpha.txt"]

    monkeypatch.setattr(rsync_paths, "_ssh_list", fake_ssh_list)
    suggester = RsyncTargetSuggester()

    async def race():
        first = asyncio.create_task(suggester.get_suggestion("me@host:a"))
        await asyncio.sleep(0)  # let `first` start and begin its debounce wait
        second = asyncio.create_task(suggester.get_suggestion("me@host:al"))
        return await asyncio.gather(first, second)

    first_result, second_result = asyncio.run(race())
    assert first_result is None
    assert second_result == "me@host:alpha.txt"
    assert len(calls) == 1


# ── Ctrl+Space dropdown ──────────────────────────────────────────────

def test_list_matches_local_and_host_names(tree, monkeypatch):
    monkeypatch.setattr(rsync_paths.hosts, "config_host_names", lambda: ["prod-web"])
    (tree / "prod-notes.txt").write_text("")
    matches = asyncio.run(list_remote_target_matches("prod"))
    assert matches == ["prod-notes.txt", "prod-web:"]


def test_list_matches_remote_entries(monkeypatch):
    async def fake_ssh_list(ssh_target, remote_dir):
        return ["alpha.txt", "alphabet/", "beta.txt"]

    monkeypatch.setattr(rsync_paths, "_ssh_list", fake_ssh_list)
    matches = asyncio.run(list_remote_target_matches("me@host:al"))
    assert matches == ["me@host:alpha.txt", "me@host:alphabet/"]


def test_remote_hidden_entries_skipped_unless_asked_for(monkeypatch):
    async def fake_ssh_list(ssh_target, remote_dir):
        return [".secret", "public.txt"]

    monkeypatch.setattr(rsync_paths, "_ssh_list", fake_ssh_list)
    assert asyncio.run(list_remote_target_matches("me@host:")) == ["me@host:public.txt"]
    assert asyncio.run(list_remote_target_matches("me@host:.")) == ["me@host:.secret"]


def test_list_matches_is_empty_for_no_remote_match(monkeypatch):
    async def fake_ssh_list(ssh_target, remote_dir):
        return ["alpha.txt"]

    monkeypatch.setattr(rsync_paths, "_ssh_list", fake_ssh_list)
    assert asyncio.run(list_remote_target_matches("me@host:zzz")) == []
