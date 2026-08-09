"""Shared pytest fixtures."""

from __future__ import annotations

import inspect

import pytest
from click.testing import CliRunner

# Click 8.2 always keeps the two streams apart. Older releases (the newest
# Click available on Python 3.9) fold stderr into stdout unless asked not to.
_MIXES_STDERR = "mix_stderr" in inspect.signature(CliRunner.__init__).parameters


@pytest.fixture
def runner() -> CliRunner:
    """A Click test runner that keeps stdout and stderr apart.

    ``result.stdout`` holds only real output, so JSON payloads stay parseable
    even when a command writes progress notes to stderr.
    """
    if _MIXES_STDERR:
        return CliRunner(mix_stderr=False)
    return CliRunner()


@pytest.fixture
def tree(tmp_path):
    """A small directory tree used by the filesystem tests.

    Layout::

        root/a/one.txt      "alpha"
        root/a/two.txt      "beta"
        root/b/three.txt    "alpha"   (duplicate of one.txt)
        root/b/notes.md     "# title"
    """
    root = tmp_path / "root"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "one.txt").write_text("alpha", encoding="utf-8")
    (root / "a" / "two.txt").write_text("beta", encoding="utf-8")
    (root / "b" / "three.txt").write_text("alpha", encoding="utf-8")
    (root / "b" / "notes.md").write_text("# title", encoding="utf-8")
    return root


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point every pytoolbox directory at a throwaway location."""
    monkeypatch.setenv("PYTOOLBOX_HOME", str(tmp_path / "pytoolbox-home"))
    monkeypatch.setenv("PYTIME_DB", str(tmp_path / "pytime.db"))
    return tmp_path
