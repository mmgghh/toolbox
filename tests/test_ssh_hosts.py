"""Tests for pytoolbox.ssh.hosts.

No SSH connection is ever made: parsing and resolution are pure, and the
``ssh -G`` boundary is monkeypatched.
"""

from __future__ import annotations

import click
import pytest

from pytoolbox import pyssh
from pytoolbox.ssh import hosts


def test_hosts_module_owns_the_server_spec():
    server = hosts.parse_server("me:secret@example.com:2222")
    assert (server.user, server.password, server.host, server.port) == (
        "me",
        "secret",
        "example.com",
        2222,
    )


def test_pyssh_still_exports_the_moved_names():
    """The existing CLI and test suite import these from pytoolbox.pyssh."""
    assert pyssh.parse_server is hosts.parse_server
    assert pyssh.Server is hosts.Server
    assert pyssh.load_server_conf is hosts.load_server_conf
    assert pyssh.resolve_server is hosts.resolve_server
    assert pyssh.SERVER_SPEC_RE is hosts.SERVER_SPEC_RE


# ── targets ─────────────────────────────────────────────────────────

def test_target_from_an_inline_spec_carries_its_port():
    target = hosts.resolve_target("me@example.com:2222")
    assert target.spec == "me@example.com"
    assert target.port == 2222
    assert not target.is_config_name


def test_target_from_a_name_leaves_the_port_to_ssh_config():
    target = hosts.resolve_target("mpars-bi")
    assert target.spec == "mpars-bi"
    assert target.port is None
    assert target.is_config_name


def test_a_name_can_never_look_like_a_spec():
    """Anything with an @ must parse as a spec; it is never passed to ssh raw."""
    with pytest.raises(click.ClickException):
        hosts.resolve_target("me@host:not-a-port")


def test_resolve_target_rejects_an_empty_value():
    with pytest.raises(click.ClickException):
        hosts.resolve_target("   ")


def test_target_carries_a_password_from_a_spec():
    assert hosts.resolve_target("me:hunter2@host").password == "hunter2"


def test_with_password_returns_a_new_target():
    target = hosts.resolve_target("mpars-bi")
    assert target.with_password("s3cret").password == "s3cret"
    assert target.password is None  # frozen: the original is untouched


def test_resolve_connection_rejects_both_sources(tmp_path):
    conf = tmp_path / "s.conf"
    conf.write_text("me@host\n", encoding="utf-8")
    with pytest.raises(click.ClickException):
        hosts.resolve_connection("me@host", str(conf), "-s/--server")


def test_resolve_connection_reads_a_conf_file(tmp_path):
    conf = tmp_path / "s.conf"
    conf.write_text("me:pw@host:2222\n", encoding="utf-8")
    target = hosts.resolve_connection(None, str(conf), "-s/--server")
    assert (target.spec, target.port, target.password) == ("me@host", 2222, "pw")
