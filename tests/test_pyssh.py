"""Tests for pyssh.

No SSH connection is ever made: command construction, spec parsing and state
handling are tested directly, and the subprocess boundary is monkeypatched.
"""

from __future__ import annotations

import json
import os
import stat

import click
import pytest

from pytoolbox import pyssh
from pytoolbox.pyssh import ssh_management


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    """Keep PID and password files inside the test's own directory."""
    monkeypatch.setenv("PYTOOLBOX_HOME", str(tmp_path / "home"))


# ── server specs ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("spec", "user", "password", "host", "port"),
    [
        ("me@host", "me", None, "host", 22),
        ("me@host:2222", "me", None, "host", 2222),
        ("me:secret@host:22", "me", "secret", "host", 22),
        ("me:secret@example.com:2222", "me", "secret", "example.com", 2222),
    ],
)
def test_parse_server(spec, user, password, host, port):
    server = pyssh.parse_server(spec)
    assert (server.user, server.password, server.host, server.port) == (user, password, host, port)


@pytest.mark.parametrize("spec", ["", "host", "me@", "@host", "me@host:port", "me@host:22:33"])
def test_parse_server_rejects_bad_specs(spec):
    with pytest.raises(click.ClickException):
        pyssh.parse_server(spec)


def test_load_server_conf_skips_comments(tmp_path):
    conf = tmp_path / "server.conf"
    conf.write_text("# a comment\n\nme:pw@host:2222\n", encoding="utf-8")
    server = pyssh.load_server_conf(conf)
    assert server.host == "host"
    assert server.password == "pw"


def test_load_server_conf_without_a_spec(tmp_path):
    conf = tmp_path / "empty.conf"
    conf.write_text("# nothing here\n", encoding="utf-8")
    with pytest.raises(click.ClickException):
        pyssh.load_server_conf(conf)


def test_resolve_server_rejects_both_sources(tmp_path):
    conf = tmp_path / "s.conf"
    conf.write_text("me@host\n", encoding="utf-8")
    with pytest.raises(click.ClickException):
        pyssh.resolve_server("me@host", str(conf), "-s/--server")


# ── command construction ────────────────────────────────────────────

def test_build_ssh_command_basics():
    server = pyssh.parse_server("me@example.com:2222")
    cmd = pyssh.build_ssh_command(server, ["-D", "127.0.0.1:9998"])
    assert cmd[0] == "ssh"
    assert "-N" in cmd
    assert cmd[-1] == "me@example.com"
    assert "2222" in cmd
    assert "ServerAliveInterval=30" in cmd


def test_build_ssh_command_with_identity_and_options():
    server = pyssh.parse_server("me@host")
    cmd = pyssh.build_ssh_command(
        server, ["-D", "0.0.0.0:1080"], identity="~/.ssh/id_ed25519", extra_opts=["Compression=yes"]
    )
    assert "-i" in cmd
    assert "Compression=yes" in cmd
    assert not any(part.startswith("~") for part in cmd)  # tilde is expanded


def test_build_ssh_command_uses_sshpass_when_a_password_file_is_given(tmp_path):
    password_file = tmp_path / "pass"
    password_file.write_text("secret", encoding="utf-8")
    cmd = pyssh.build_ssh_command(
        pyssh.parse_server("me@host"), ["-D", "127.0.0.1:1"], password_file=password_file
    )
    assert cmd[0] == "sshpass"
    assert str(password_file) in cmd
    assert "StrictHostKeyChecking=accept-new" in cmd


def test_password_never_appears_on_the_command_line(tmp_path):
    password_file = tmp_path / "pass"
    password_file.write_text("hunter2", encoding="utf-8")
    cmd = pyssh.build_ssh_command(
        pyssh.parse_server("me:hunter2@host"), ["-D", "127.0.0.1:1"], password_file=password_file
    )
    assert "hunter2" not in " ".join(cmd)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_password_file_is_owner_only(monkeypatch, tmp_path):
    monkeypatch.setattr(pyssh, "_require", lambda binary, hint: None)
    path = pyssh._password_file(pyssh.parse_server("me:secret@host"), "test")
    assert path is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8") == "secret"


def test_no_password_file_for_key_authentication():
    assert pyssh._password_file(pyssh.parse_server("me@host"), "test") is None


def test_password_files_live_outside_the_package():
    package_dir = pyssh.Path(pyssh.__file__).parent
    assert package_dir not in pyssh.tunnels_dir().parents
    assert pyssh.tunnels_dir() != package_dir


# ── ports and state ─────────────────────────────────────────────────

def test_port_is_free_for_an_unbound_port():
    import socket
    from contextlib import closing

    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    assert pyssh.port_is_free(port)


def test_port_is_not_free_while_bound():
    import socket
    from contextlib import closing

    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert not pyssh.port_is_free(port)


def test_state_is_dropped_when_the_process_is_gone():
    pyssh.save_state("ghost", {"kind": "tunnel", "pids": [999999999], "socks_port": 1})
    assert pyssh.load_states() == []


def test_state_is_kept_while_the_process_lives():
    pyssh.save_state("mine", {"kind": "tunnel", "pids": [os.getpid()], "socks_port": 9998})
    states = pyssh.load_states()
    assert len(states) == 1
    assert states[0]["socks_port"] == 9998


def test_pid_alive_handles_junk():
    assert not pyssh.pid_alive([])
    assert not pyssh.pid_alive(["not-a-pid"])
    assert pyssh.pid_alive([os.getpid()])


# ── CLI ─────────────────────────────────────────────────────────────

def test_status_with_no_tunnels(runner):
    result = runner.invoke(ssh_management, ["status"])
    assert result.exit_code == 0
    assert "No pyssh tunnels are running." in result.output


def test_status_json_lists_running_tunnels(runner):
    pyssh.save_state("mine", {"kind": "tunnel", "pids": [os.getpid()], "socks_port": 9998})
    result = runner.invoke(ssh_management, ["status", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)[0]["socks_port"] == 9998


def test_stop_requires_a_target(runner):
    pyssh.save_state("mine", {"kind": "tunnel", "pids": [os.getpid()], "socks_port": 9998})
    result = runner.invoke(ssh_management, ["stop"])
    assert result.exit_code != 0
    assert "name" in result.stderr.lower()


def test_stop_unknown_name(runner):
    pyssh.save_state("mine", {"kind": "tunnel", "pids": [os.getpid()], "socks_port": 9998})
    result = runner.invoke(ssh_management, ["stop", "nope"])
    assert result.exit_code != 0


def test_rsync_dir_builds_a_safe_argument_list(runner, monkeypatch, tmp_path):
    captured = {}

    class FakeResult:
        returncode = 0

    def fake_run(cmd, check=False):
        captured["cmd"] = cmd
        return FakeResult()

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: "/usr/bin/rsync")
    monkeypatch.setattr(pyssh.subprocess, "run", fake_run)

    result = runner.invoke(
        ssh_management,
        [
            "rsync-dir",
            "-s", "./my dir",
            "-d", "me@host:/srv/site",
            "-p", "2222",
            "--delete",
            "--dry-run",
            "-e", "*.tmp",
        ],
    )
    assert result.exit_code == 0, result.output
    cmd = captured["cmd"]
    assert isinstance(cmd, list)  # never a shell string
    assert cmd[0] == "rsync"
    assert "ssh -p 2222" in cmd  # -e value is one argument, unquoted
    assert "--delete" in cmd
    assert "--dry-run" in cmd
    assert cmd[-2:] == ["./my dir", "me@host:/srv/site"]


def test_rsync_dir_reports_failure(runner, monkeypatch):
    class FakeResult:
        returncode = 23

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: "/usr/bin/rsync")
    monkeypatch.setattr(pyssh.subprocess, "run", lambda cmd, check=False: FakeResult())
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "a", "-d", "b", "-p", "22"]
    )
    assert result.exit_code != 0
    assert "23" in result.stderr


@pytest.fixture
def fake_rsync(monkeypatch):
    """Capture the argument list rsync would have been run with."""
    captured = {}

    class FakeResult:
        returncode = 0

    def fake_run(cmd, check=False):
        captured["cmd"] = cmd
        return FakeResult()

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.subprocess, "run", fake_run)
    return captured


@pytest.mark.parametrize(
    ("spec", "target", "password"),
    [
        ("./my dir", "./my dir", None),
        ("/srv/site", "/srv/site", None),
        ("me@host:/srv", "me@host:/srv", None),
        ("host:/srv", "host:/srv", None),
        ("me:secret@host:/srv", "me@host:/srv", "secret"),
    ],
)
def test_split_rsync_target(spec, target, password):
    assert pyssh.split_rsync_target(spec) == (target, password)


def test_rsync_dir_match_builds_include_rules(runner, fake_rsync):
    result = runner.invoke(
        ssh_management,
        ["rsync-dir", "-s", "./site", "-d", "me@host:/srv", "--match", "*.{jpg,png}"],
    )
    assert result.exit_code == 0, result.output
    cmd = fake_rsync["cmd"]
    assert "*.jpg" in cmd and "*.png" in cmd
    assert cmd[cmd.index("--include")] == "--include"
    assert "-m" in cmd


def test_rsync_dir_reads_patterns_from_a_file(runner, fake_rsync, tmp_path):
    patterns = tmp_path / "ignore.txt"
    patterns.write_text("# junk\n\n*.tmp\n", encoding="utf-8")
    result = runner.invoke(
        ssh_management,
        ["rsync-dir", "-s", "./site", "-d", "./out", "--exclude-from", str(patterns)],
    )
    assert result.exit_code == 0, result.output
    assert "*.tmp" in fake_rsync["cmd"]
    assert "# junk" not in fake_rsync["cmd"]


def test_rsync_dir_rejects_a_regex_shaped_pattern(runner, fake_rsync):
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "a", "-d", "b", "-e", r".*\.log$"]
    )
    assert result.exit_code != 0
    assert "regex" in result.stderr.lower()
    assert "cmd" not in fake_rsync


def test_rsync_dir_quotes_an_identity_path_with_spaces(runner, fake_rsync):
    result = runner.invoke(
        ssh_management,
        ["rsync-dir", "-s", "a", "-d", "b", "--identity", "/my keys/id_ed25519"],
    )
    assert result.exit_code == 0, result.output
    ssh_command = fake_rsync["cmd"][fake_rsync["cmd"].index("-e") + 1]
    assert "'/my keys/id_ed25519'" in ssh_command


def test_rsync_dir_passes_extra_ssh_options(runner, fake_rsync):
    result = runner.invoke(
        ssh_management,
        ["rsync-dir", "-s", "a", "-d", "b", "-o", "Compression=yes"],
    )
    assert result.exit_code == 0, result.output
    ssh_command = fake_rsync["cmd"][fake_rsync["cmd"].index("-e") + 1]
    assert "-o Compression=yes" in ssh_command


def test_rsync_dir_uses_sshpass_for_a_password_spec(runner, fake_rsync):
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "./site", "-d", "me:hunter2@host:/srv"]
    )
    assert result.exit_code == 0, result.output
    cmd = fake_rsync["cmd"]
    assert cmd[0] == "sshpass"
    assert "hunter2" not in " ".join(cmd)
    assert cmd[-1] == "me@host:/srv"


def test_rsync_dir_removes_the_password_file_afterwards(runner, fake_rsync):
    runner.invoke(ssh_management, ["rsync-dir", "-s", "./site", "-d", "me:pw@host:/srv"])
    pass_file = pyssh.Path(fake_rsync["cmd"][2])
    assert not pass_file.exists()


def test_rsync_dir_rejects_a_password_on_both_sides(runner, fake_rsync):
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "a:1@h1:/x", "-d", "b:2@h2:/y"]
    )
    assert result.exit_code != 0
    assert "cmd" not in fake_rsync


def test_rsync_dir_will_not_delete_without_confirmation(runner, fake_rsync):
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "./site", "-d", "me@host:/srv", "--delete"]
    )
    assert result.exit_code != 0
    assert "cmd" not in fake_rsync


def test_rsync_dir_deletes_when_confirmed(runner, fake_rsync):
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "./site", "-d", "me@host:/srv", "--delete", "-y"]
    )
    assert result.exit_code == 0, result.output
    assert "--delete" in fake_rsync["cmd"]


def test_rsync_dir_does_not_prompt_for_a_dry_run(runner, fake_rsync):
    result = runner.invoke(
        ssh_management,
        ["rsync-dir", "-s", "./site", "-d", "me@host:/srv", "--mirror", "--dry-run"],
    )
    assert result.exit_code == 0, result.output
    assert "--delete-excluded" in fake_rsync["cmd"]


def test_tunnel_refuses_a_busy_port(runner, monkeypatch):
    import socket
    from contextlib import closing

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: "/usr/bin/ssh")
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        result = runner.invoke(
            ssh_management, ["tunnel", "-s", "me@host", "-p", str(port)]
        )
    assert result.exit_code != 0
    assert "already in use" in result.stderr
