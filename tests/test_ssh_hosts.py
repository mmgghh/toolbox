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


@pytest.mark.parametrize("name", ["-oProxyCommand=id", "-4", "--version"])
def test_a_name_starting_with_a_dash_is_refused(name):
    """ssh would read it as an option, not a host; with a trailing remote
    command that is how -oProxyCommand=... becomes code execution."""
    with pytest.raises(click.ClickException) as excinfo:
        hosts.resolve_target(name)
    assert "option" in str(excinfo.value)


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


# ── ssh -G ──────────────────────────────────────────────────────────

SSH_G_OUTPUT = """\
user deploy
hostname 10.0.0.5
port 2222
proxyjump bastion
identityfile ~/.ssh/id_ed25519
identityfile ~/.ssh/id_rsa
controlmaster auto
serveraliveinterval 60
"""


def test_parse_ssh_g_reads_the_fields_pyssh_needs():
    resolved = hosts.parse_ssh_g("prod", SSH_G_OUTPUT)
    assert resolved.hostname == "10.0.0.5"
    assert resolved.user == "deploy"
    assert resolved.port == 2222
    assert resolved.proxy_jump == "bastion"
    assert resolved.identity_files == ("~/.ssh/id_ed25519", "~/.ssh/id_rsa")


def test_parse_ssh_g_falls_back_for_an_undefined_name():
    resolved = hosts.parse_ssh_g("whatever", "user me\nhostname whatever\nport 22\n")
    assert resolved.hostname == "whatever"
    assert resolved.port == 22
    assert resolved.proxy_jump is None
    assert resolved.identity_files == ()


def test_parse_ssh_g_ignores_a_none_proxyjump():
    """ssh reports the absence of a jump host as the literal string 'none'."""
    assert hosts.parse_ssh_g("x", "hostname x\nproxyjump none\n").proxy_jump is None


def test_parse_ssh_g_survives_a_junk_port():
    assert hosts.parse_ssh_g("x", "hostname x\nport nonsense\n").port == 22


def test_resolve_config_runs_ssh_dash_g(monkeypatch):
    calls = {}

    class FakeCompleted:
        returncode = 0
        stdout = SSH_G_OUTPUT
        stderr = ""

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return FakeCompleted()

    monkeypatch.setattr(hosts.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(hosts.subprocess, "run", fake_run)

    resolved = hosts.resolve_config("prod")
    assert calls["cmd"] == ["ssh", "-G", "--", "prod"]
    assert resolved is not None
    assert resolved.hostname == "10.0.0.5"


def test_resolve_config_passes_extra_options(monkeypatch):
    calls = {}

    class FakeCompleted:
        returncode = 0
        stdout = SSH_G_OUTPUT
        stderr = ""

    monkeypatch.setattr(hosts.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        hosts.subprocess, "run", lambda cmd, **kw: (calls.update(cmd=cmd), FakeCompleted())[1]
    )
    hosts.resolve_config("prod", ["Port=2200"])
    assert calls["cmd"] == ["ssh", "-G", "-o", "Port=2200", "--", "prod"]


def test_resolve_config_ends_option_parsing_before_the_name(monkeypatch):
    """Without --, a name beginning with - is swallowed by ssh as an option."""
    calls = {}

    class FakeCompleted:
        returncode = 0
        stdout = SSH_G_OUTPUT
        stderr = ""

    monkeypatch.setattr(hosts.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        hosts.subprocess, "run", lambda cmd, **kw: (calls.update(cmd=cmd), FakeCompleted())[1]
    )
    hosts.resolve_config("prod")
    assert calls["cmd"][-2:] == ["--", "prod"]


def test_resolve_config_returns_none_without_ssh(monkeypatch):
    monkeypatch.setattr(hosts.shutil, "which", lambda name: None)
    assert hosts.resolve_config("prod") is None


def test_resolve_config_returns_none_when_ssh_fails(monkeypatch):
    class FakeCompleted:
        returncode = 255
        stdout = ""
        stderr = "bad configuration"

    monkeypatch.setattr(hosts.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(hosts.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    assert hosts.resolve_config("prod") is None


def test_resolve_config_returns_none_on_a_decode_error(monkeypatch):
    """subprocess.run(text=True) decodes stdout itself; a non-UTF-8 IdentityFile
    path can make that raise UnicodeDecodeError, which is a ValueError rather
    than an OSError or SubprocessError. resolve_config must swallow it too."""

    def raise_decode_error(cmd, **kwargs):
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    monkeypatch.setattr(hosts.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(hosts.subprocess, "run", raise_decode_error)
    assert hosts.resolve_config("prod") is None
