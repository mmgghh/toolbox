"""Tests for the pyssh secrets-and-tags store.

Nothing here touches a real keyring: the backend is faked, and every file
lands under PYTOOLBOX_HOME inside tmp_path.
"""

from __future__ import annotations

import os
import stat

import click
import pytest

from pytoolbox.ssh import store


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTOOLBOX_HOME", str(tmp_path / "home"))


def test_an_empty_store_reads_as_empty():
    assert store.entries() == []


def test_tags_round_trip():
    store.add_tags("prod-web", ["prod", "web"])
    assert store.entry("prod-web").tags == ("prod", "web")


def test_tags_are_deduplicated_and_sorted():
    store.add_tags("prod-web", ["web", "prod", "web"])
    assert store.entry("prod-web").tags == ("prod", "web")


def test_removing_a_tag_leaves_the_others():
    store.add_tags("prod-web", ["prod", "web"])
    store.remove_tags("prod-web", ["web"])
    assert store.entry("prod-web").tags == ("prod",)


def test_names_with_tag_finds_every_host():
    store.add_tags("web1", ["prod"])
    store.add_tags("web2", ["prod"])
    store.add_tags("staging1", ["staging"])
    assert store.names_with_tag("prod") == ["web1", "web2"]


def test_names_with_an_unused_tag_is_empty():
    store.add_tags("web1", ["prod"])
    assert store.names_with_tag("nope") == []


@pytest.mark.parametrize("name", ["", "   ", "me@host", "two words", "-oProxyCommand=id", "-4"])
def test_validate_name_rejects_unusable_names(name):
    with pytest.raises(click.ClickException):
        store.validate_name(name)


def test_a_name_can_never_collide_with_a_spec():
    """An @ in a stored name would make resolve_target ambiguous."""
    with pytest.raises(click.ClickException):
        store.add_tags("me@host", ["prod"])


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_store_file_is_owner_only():
    store.add_tags("prod-web", ["prod"])
    assert stat.S_IMODE(store.store_path().stat().st_mode) == 0o600


def test_a_corrupt_store_is_reported_not_silently_dropped():
    store.add_tags("prod-web", ["prod"])
    store.store_path().write_text("{not json", encoding="utf-8")
    with pytest.raises(click.ClickException):
        store.entries()
