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


# ── secrets ─────────────────────────────────────────────────────────

class FakeKeyring:
    """A working in-memory keyring backend."""

    def __init__(self):
        self.saved = {}

    def set_password(self, service, name, password):
        self.saved[(service, name)] = password

    def get_password(self, service, name):
        return self.saved.get((service, name))

    def delete_password(self, service, name):
        if (service, name) not in self.saved:
            raise RuntimeError("no such password")
        del self.saved[(service, name)]


class BrokenKeyring:
    """What an unusable backend looks like: importable, but it raises."""

    def set_password(self, service, name, password):
        raise RuntimeError("No recommended backend was available")

    def get_password(self, service, name):
        raise RuntimeError("No recommended backend was available")

    def delete_password(self, service, name):
        raise RuntimeError("No recommended backend was available")


@pytest.fixture
def working_keyring(monkeypatch):
    fake = FakeKeyring()
    monkeypatch.setattr(store, "_keyring", lambda: fake)
    return fake


@pytest.fixture
def no_keyring(monkeypatch):
    monkeypatch.setattr(store, "_keyring", lambda: None)


def test_a_password_goes_to_the_keyring_when_one_works(working_keyring):
    assert store.set_secret("prod-web", "hunter2") == store.TIER_KEYRING
    assert working_keyring.saved[(store.KEYRING_SERVICE, "prod-web")] == "hunter2"


def test_a_keyring_password_is_not_written_to_the_file(working_keyring):
    store.set_secret("prod-web", "hunter2")
    assert "hunter2" not in store.store_path().read_text(encoding="utf-8")


def test_a_keyring_password_reads_back(working_keyring):
    store.set_secret("prod-web", "hunter2")
    assert store.get_secret("prod-web") == "hunter2"


def test_without_a_keyring_storing_a_password_is_refused(no_keyring):
    with pytest.raises(click.ClickException) as excinfo:
        store.set_secret("prod-web", "hunter2")
    message = str(excinfo.value)
    assert "keygen" in message and "copy-id" in message
    assert "--insecure-plaintext" in message


def test_a_broken_backend_is_treated_as_no_keyring(monkeypatch):
    monkeypatch.setattr(store, "_keyring", lambda: BrokenKeyring())
    with pytest.raises(click.ClickException):
        store.set_secret("prod-web", "hunter2")


def test_plaintext_is_stored_only_when_asked(no_keyring):
    assert store.set_secret("prod-web", "hunter2", allow_plaintext=True) == store.TIER_PLAINTEXT
    assert store.get_secret("prod-web") == "hunter2"


def test_reading_a_plaintext_secret_warns(no_keyring, capsys):
    store.set_secret("prod-web", "hunter2", allow_plaintext=True)
    store.get_secret("prod-web")
    assert "plain text" in capsys.readouterr().err


def test_a_host_with_no_secret_reads_as_none(working_keyring):
    store.add_tags("prod-web", ["prod"])
    assert store.get_secret("prod-web") is None


def test_removing_a_secret_clears_both_places(working_keyring):
    store.set_secret("prod-web", "hunter2")
    assert store.remove_secret("prod-web") is True
    assert store.get_secret("prod-web") is None
    assert working_keyring.saved == {}


def test_removing_a_secret_keeps_the_tags(working_keyring):
    store.add_tags("prod-web", ["prod"])
    store.set_secret("prod-web", "hunter2")
    store.remove_secret("prod-web")
    assert store.entry("prod-web").tags == ("prod",)


def test_removing_a_secret_that_is_not_there(working_keyring):
    assert store.remove_secret("prod-web") is False


def test_the_tier_is_visible_without_reading_the_secret(working_keyring):
    store.set_secret("prod-web", "hunter2")
    assert store.entry("prod-web").tier == store.TIER_KEYRING
