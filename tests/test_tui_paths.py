"""Tests for PathSuggester, the filesystem ghost-text completer for TUI path fields."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("textual", reason="TUI is an optional extra")

from pytoolbox.tui.paths import PathSuggester  # noqa: E402


def suggest(suggester: PathSuggester, value: str):
    return asyncio.run(suggester.get_suggestion(value))


@pytest.fixture
def tree(tmp_path, monkeypatch):
    (tmp_path / "alpha.txt").write_text("")
    (tmp_path / "alphabet").mkdir()
    (tmp_path / ".hidden").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "nested.txt").write_text("")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_no_suggestion_for_empty_input(tree):
    assert suggest(PathSuggester(), "") is None


def test_suggests_a_matching_name_in_the_current_directory(tree):
    # "alpha.txt" sorts before "alphabet", so it wins as the first match.
    assert suggest(PathSuggester(), "al") == "alpha.txt"


def test_directory_suggestion_gets_a_trailing_slash(tree):
    assert suggest(PathSuggester(), "sub") == "sub/"


def test_descends_into_a_typed_directory(tree):
    assert suggest(PathSuggester(), "sub/ne") == "sub/nested.txt"


def test_hidden_entries_are_skipped_unless_asked_for(tree):
    assert suggest(PathSuggester(), "") is None
    assert suggest(PathSuggester(), ".") == ".hidden"


def test_dirs_only_skips_files(tree):
    assert suggest(PathSuggester(dirs_only=True), "al") == "alphabet/"


def test_absolute_root_lists_root_directory():
    # "/" has no head after rpartition -- must list "/" itself, not cwd.
    suggestion = suggest(PathSuggester(), "/")
    assert suggestion is not None
    assert suggestion.startswith("/")


def test_no_match_returns_none(tree):
    assert suggest(PathSuggester(), "zzz-does-not-exist") is None


def test_nonexistent_directory_returns_none(tree):
    assert suggest(PathSuggester(), "no-such-dir/x") is None


def test_home_expansion_is_used_only_for_the_filesystem_lookup(tree, monkeypatch):
    monkeypatch.setenv("HOME", str(tree))
    suggestion = suggest(PathSuggester(), "~/al")
    # The suggestion keeps the user's "~" spelling rather than expanding it.
    assert suggestion == "~/alpha.txt"
