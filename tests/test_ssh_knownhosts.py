"""Tests for host key verification.

ssh-keygen is never actually run: the subprocess boundary is monkeypatched.
"""

from __future__ import annotations

import click
import pytest

from pytoolbox.ssh import knownhosts


def _fake_keygen(monkeypatch, returncode):
    class FakeCompleted:
        pass

    FakeCompleted.returncode = returncode
    FakeCompleted.stdout = ""
    FakeCompleted.stderr = ""
    monkeypatch.setattr(knownhosts.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls = {}
    monkeypatch.setattr(
        knownhosts.subprocess,
        "run",
        lambda cmd, **kw: (calls.update(cmd=cmd), FakeCompleted())[1],
    )
    return calls


def test_a_known_host_is_known(monkeypatch):
    _fake_keygen(monkeypatch, 0)
    assert knownhosts.is_known("example.com") is True


def test_an_unknown_host_is_not(monkeypatch):
    _fake_keygen(monkeypatch, 1)
    assert knownhosts.is_known("example.com") is False


def test_a_nonstandard_port_is_looked_up_in_brackets(monkeypatch):
    """known_hosts keys a non-22 port as [host]:port."""
    calls = _fake_keygen(monkeypatch, 0)
    knownhosts.is_known("example.com", 2222)
    assert calls["cmd"] == ["ssh-keygen", "-F", "[example.com]:2222"]


def test_port_22_is_looked_up_bare(monkeypatch):
    calls = _fake_keygen(monkeypatch, 0)
    knownhosts.is_known("example.com", 22)
    assert calls["cmd"] == ["ssh-keygen", "-F", "example.com"]


def test_without_ssh_keygen_we_do_not_block(monkeypatch):
    """Unable to check is not the same as unsafe; ssh still does its own check."""
    monkeypatch.setattr(knownhosts.shutil, "which", lambda name: None)
    assert knownhosts.is_known("example.com") is True


def test_the_unknown_host_message_points_at_plain_ssh():
    """pyssh connect would supply the password and hit the same refusal."""
    message = knownhosts.unknown_host_message("prod", "10.0.0.5")
    assert "ssh prod" in message
    assert "ssh-keyscan -H 10.0.0.5" in message
    assert "pyssh connect" not in message


def test_the_unknown_host_message_makes_recording_conditional_on_verifying():
    """'record it without a prompt' must read as conditional, not an alternative."""
    message = knownhosts.unknown_host_message("prod", "10.0.0.5")
    assert "once you have verified the fingerprint" in message
    assert "or trust it without a prompt" not in message


def test_unknown_host_message_scans_a_nonstandard_port():
    message = knownhosts.unknown_host_message("prod", "10.0.0.5", port=2222)
    assert "ssh-keyscan -p 2222 -H 10.0.0.5" in message


def test_unknown_host_message_default_port_has_no_dash_p():
    message = knownhosts.unknown_host_message("prod", "10.0.0.5", port=22)
    assert "ssh-keyscan -H 10.0.0.5" in message
    assert "-p" not in message


def test_the_changed_key_message_says_how_to_remove_it():
    message = knownhosts.changed_key_message("prod", "10.0.0.5")
    assert "ssh-keygen -R '10.0.0.5'" in message


def test_the_changed_key_message_covers_the_address_too():
    """known_hosts keys the name and the address separately."""
    message = knownhosts.changed_key_message("prod", "prod.example.com", "203.0.113.9")
    assert "ssh-keygen -R 'prod.example.com'" in message
    assert "ssh-keygen -R '203.0.113.9'" in message


def test_changed_key_message_brackets_a_nonstandard_port():
    message = knownhosts.changed_key_message("prod", "10.0.0.5", port=2222)
    assert "ssh-keygen -R '[10.0.0.5]:2222'" in message


def test_changed_key_message_keeps_the_default_port_bare():
    message = knownhosts.changed_key_message("prod", "10.0.0.5", port=22)
    assert "ssh-keygen -R '10.0.0.5'" in message
    assert "[10.0.0.5]" not in message


def test_changed_key_message_brackets_the_address_line_too():
    message = knownhosts.changed_key_message("prod", "prod.example.com", "203.0.113.9", port=2222)
    assert "ssh-keygen -R '[prod.example.com]:2222'" in message
    assert "ssh-keygen -R '[203.0.113.9]:2222'" in message


def test_changed_key_message_has_no_second_line_when_address_is_none():
    message = knownhosts.changed_key_message("prod", "10.0.0.5")
    assert message.count("ssh-keygen -R") == 1


def test_changed_key_message_has_no_second_line_when_address_equals_host():
    message = knownhosts.changed_key_message("prod", "10.0.0.5", "10.0.0.5")
    assert message.count("ssh-keygen -R") == 1


def test_require_known_raises_for_an_unknown_host(monkeypatch):
    _fake_keygen(monkeypatch, 1)
    with pytest.raises(click.ClickException) as excinfo:
        knownhosts.require_known("prod", "10.0.0.5")
    assert "known_hosts" in str(excinfo.value)


def test_require_known_propagates_the_port_into_the_message(monkeypatch):
    _fake_keygen(monkeypatch, 1)
    with pytest.raises(click.ClickException) as excinfo:
        knownhosts.require_known("prod", "10.0.0.5", port=2222)
    assert "ssh-keyscan -p 2222 -H 10.0.0.5" in str(excinfo.value)


def test_require_known_passes_for_a_known_host(monkeypatch):
    _fake_keygen(monkeypatch, 0)
    knownhosts.require_known("prod", "10.0.0.5")  # does not raise


def test_failure_hint_recognises_a_changed_key():
    stderr = (
        "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
        "@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @\n"
    )
    hint = knownhosts.failure_hint(stderr, "prod", "10.0.0.5")
    assert hint is not None
    assert "ssh-keygen -R '10.0.0.5'" in hint


def test_failure_hint_is_silent_about_unrelated_errors():
    assert knownhosts.failure_hint("Permission denied (publickey).", "prod", "10.0.0.5") is None


def test_failure_hint_propagates_the_port():
    stderr = "WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!"
    hint = knownhosts.failure_hint(stderr, "prod", "10.0.0.5", port=2222)
    assert hint is not None
    assert "ssh-keygen -R '[10.0.0.5]:2222'" in hint
