"""Shared pytest fixtures."""

from __future__ import annotations

import pytest
from click.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    """A Click test runner.

    ``result.stdout`` holds only real output; ``result.output`` also includes
    the progress and warning text pytoolbox sends to stderr.
    """
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
