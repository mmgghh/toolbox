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
    path = pyssh._password_file("secret", "test")
    assert path is not None
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8") == "secret"


def test_no_password_file_for_key_authentication():
    assert pyssh._password_file(None, "test") is None


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


def test_rsync_dir_uses_sshpass_for_a_password_spec(runner, fake_rsync, monkeypatch):
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "./site", "-d", "me:hunter2@host:/srv"]
    )
    assert result.exit_code == 0, result.output
    cmd = fake_rsync["cmd"]
    assert cmd[0] == "sshpass"
    assert "hunter2" not in " ".join(cmd)
    assert cmd[-1] == "me@host:/srv"


def test_rsync_dir_removes_the_password_file_afterwards(runner, fake_rsync, monkeypatch):
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    runner.invoke(ssh_management, ["rsync-dir", "-s", "./site", "-d", "me:pw@host:/srv"])
    pass_file = pyssh.Path(fake_rsync["cmd"][2])
    assert not pass_file.exists()


def test_rsync_dir_refuses_a_password_to_an_unknown_host(runner, fake_rsync, monkeypatch):
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: False)
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "./site", "-d", "me:hunter2@host:/srv"]
    )
    assert result.exit_code != 0
    assert "known_hosts" in result.stderr
    assert "cmd" not in fake_rsync


def test_rsync_dir_uses_strict_host_keys_with_a_password(runner, fake_rsync, monkeypatch):
    """A password disables the usual accept-new leniency, same as connect/exec/copy-id."""
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "./site", "-d", "me:hunter2@host:/srv"]
    )
    assert result.exit_code == 0, result.output
    ssh_command = fake_rsync["cmd"][fake_rsync["cmd"].index("-e") + 1]
    assert "StrictHostKeyChecking=yes" in ssh_command


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


def test_tunnel_refuses_a_password_to_an_unknown_host(runner, monkeypatch):
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: False)
    result = runner.invoke(ssh_management, ["tunnel", "-s", "me:hunter2@host", "-p", "9998"])
    assert result.exit_code != 0
    assert "known_hosts" in result.stderr


def test_tunnel_uses_strict_host_keys_with_a_password(runner, monkeypatch):
    captured = {}

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    monkeypatch.setattr(
        pyssh.subprocess, "Popen", lambda cmd, **kw: (captured.update(cmd=cmd), FakeProcess())[1]
    )
    monkeypatch.setattr(pyssh, "_wait_for_listener", lambda *a, **k: None)
    result = runner.invoke(ssh_management, ["tunnel", "-s", "me:hunter2@host", "-p", "9998", "-b"])
    assert result.exit_code == 0, result.output
    assert "StrictHostKeyChecking=yes" in captured["cmd"]


# ── targets in command construction ─────────────────────────────────

def test_build_ssh_command_accepts_a_config_name_without_forcing_a_port():
    """-p 22 would override the Port set in ~/.ssh/config."""
    target = pyssh.hosts.Target.from_name("mpars-bi")
    cmd = pyssh.build_ssh_command(target, ["-D", "127.0.0.1:9998"])
    assert cmd[-1] == "mpars-bi"
    assert "-p" not in cmd


def test_build_ssh_command_still_sets_the_port_for_a_spec():
    cmd = pyssh.build_ssh_command(pyssh.parse_server("me@host:2222"), ["-D", "127.0.0.1:1"])
    assert cmd[cmd.index("-p") + 1] == "2222"


def test_build_ssh_command_ends_option_parsing_before_the_destination():
    """A destination beginning with '-' must never be parsed as an option, and
    nothing may follow the destination except a remote command."""
    cmd = pyssh.build_ssh_command(pyssh.hosts.Target.from_name("prod"), [])
    assert cmd[-2:] == ["--", "prod"]


def test_no_ssh_option_is_appended_after_the_destination(tmp_path):
    """Everything after -- is host-then-command, so a trailing -o would be sent
    to the remote shell as the command instead of configuring ssh."""
    password_file = tmp_path / "pass"
    password_file.write_text("secret", encoding="utf-8")
    cmd = pyssh.build_ssh_command(
        pyssh.hosts.Target.from_name("prod"), [], password_file=password_file
    )
    assert cmd[-2:] == ["--", "prod"]
    assert "StrictHostKeyChecking=accept-new" in cmd
    assert cmd.index("StrictHostKeyChecking=accept-new") < cmd.index("--")


def test_build_ssh_command_can_leave_out_dash_n():
    cmd = pyssh.build_ssh_command(
        pyssh.hosts.Target.from_name("prod"), [], no_command=False
    )
    assert "-N" not in cmd


def test_tunnel_accepts_a_config_name(runner, monkeypatch):
    """-s prod must reach ssh as 'prod', not be rejected as a bad spec."""
    captured = {}

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return FakeProcess()

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(pyssh, "_wait_for_listener", lambda *a, **k: None)

    result = runner.invoke(ssh_management, ["tunnel", "-s", "prod", "-p", "9998", "-b"])
    assert result.exit_code == 0, result.output
    assert captured["cmd"][-1] == "prod"
    assert "-p" not in captured["cmd"]


# ── config names in the other commands ──────────────────────────────

@pytest.mark.parametrize(
    ("spec", "host"),
    [
        ("./my dir", None),
        ("/srv/site", None),
        ("me@host:/srv", "host"),
        ("host:/srv", "host"),
        ("mpars-bi:/srv/site", "mpars-bi"),
        ("me:secret@host:/srv", "host"),
    ],
)
def test_rsync_host_of(spec, host):
    assert pyssh.rsync_host_of(spec) == host


def test_rsync_dir_passes_a_config_name_through_verbatim(runner, fake_rsync):
    """ssh resolves the name for rsync; pyssh must not rewrite the target."""
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "./site", "-d", "mpars-bi:/srv/site"]
    )
    assert result.exit_code == 0, result.output
    assert fake_rsync["cmd"][-1] == "mpars-bi:/srv/site"


def test_double_tunnel_resolves_a_config_name_for_the_first_hop(runner, monkeypatch):
    captured = []

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.subprocess, "Popen", lambda cmd, **kw: (captured.append(cmd), FakeProcess())[1]
    )
    monkeypatch.setattr(pyssh, "_wait_for_listener", lambda *a, **k: None)
    monkeypatch.setattr(
        pyssh.hosts,
        "resolve_config",
        lambda name, options=(): pyssh.hosts.ResolvedConfig(
            name=name, hostname="10.0.0.5", user="deploy", port=2222
        ),
    )

    result = runner.invoke(
        ssh_management,
        ["double-tunnel", "--server1", "me@bridge", "--server2", "target", "-b"],
    )
    assert result.exit_code == 0, result.output
    assert "-L" in captured[0]
    assert captured[0][captured[0].index("-L") + 1] == "127.0.0.1:9998:10.0.0.5:2222"
    assert captured[1][-1] == "deploy@127.0.0.1"


def test_double_tunnel_reports_an_unresolvable_name(runner, monkeypatch):
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.hosts, "resolve_config", lambda name, options=(): None)
    result = runner.invoke(
        ssh_management, ["double-tunnel", "--server1", "me@bridge", "--server2", "target"]
    )
    assert result.exit_code != 0
    assert "target" in result.stderr


def test_double_tunnel_refuses_a_password_to_an_unknown_first_hop(runner, monkeypatch):
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: False)
    result = runner.invoke(
        ssh_management,
        ["double-tunnel", "--server1", "me:hunter2@bridge", "--server2", "me@target"],
    )
    assert result.exit_code != 0
    assert "known_hosts" in result.stderr


def test_double_tunnel_refuses_a_password_to_an_unknown_second_hop(runner, monkeypatch):
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: False)
    result = runner.invoke(
        ssh_management,
        ["double-tunnel", "--server1", "me@bridge", "--server2", "me:hunter2@target"],
    )
    assert result.exit_code != 0
    assert "known_hosts" in result.stderr


def test_double_tunnel_uses_strict_host_keys_for_the_first_hop_with_a_password(runner, monkeypatch):
    captured = []

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    monkeypatch.setattr(
        pyssh.subprocess, "Popen", lambda cmd, **kw: (captured.append(cmd), FakeProcess())[1]
    )
    monkeypatch.setattr(pyssh, "_wait_for_listener", lambda *a, **k: None)
    result = runner.invoke(
        ssh_management,
        ["double-tunnel", "--server1", "me:hunter2@bridge", "--server2", "me@target", "-b"],
    )
    assert result.exit_code == 0, result.output
    assert "StrictHostKeyChecking=yes" in captured[0]


def test_double_tunnel_keeps_accept_new_for_the_second_hop_bridge(runner, monkeypatch):
    """Hop 2 dials the local loopback bridge, which has no known_hosts entry of
    its own -- `_guard_host_key` already verified the real remote key up front,
    so forcing strict checking here would only break on the bridge's synthetic,
    per-run address."""
    captured = []

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    monkeypatch.setattr(
        pyssh.subprocess, "Popen", lambda cmd, **kw: (captured.append(cmd), FakeProcess())[1]
    )
    monkeypatch.setattr(pyssh, "_wait_for_listener", lambda *a, **k: None)
    result = runner.invoke(
        ssh_management,
        ["double-tunnel", "--server1", "me@bridge", "--server2", "me:hunter2@target", "-b"],
    )
    assert result.exit_code == 0, result.output
    assert "StrictHostKeyChecking=accept-new" in captured[1]


# ── secrets and tags ────────────────────────────────────────────────

@pytest.fixture
def working_keyring(monkeypatch):
    saved = {}
    fake = type(
        "FakeKeyring",
        (),
        {
            "set_password": staticmethod(lambda s, n, p: saved.__setitem__((s, n), p)),
            "get_password": staticmethod(lambda s, n: saved.get((s, n))),
            "delete_password": staticmethod(lambda s, n: saved.pop((s, n), None)),
        },
    )()
    monkeypatch.setattr(pyssh.store, "_keyring", lambda: fake)
    return saved


def test_secret_set_prompts_and_reports_the_tier(runner, working_keyring):
    result = runner.invoke(ssh_management, ["secret", "set", "prod-web"], input="hunter2\nhunter2\n")
    assert result.exit_code == 0, result.output
    assert "keyring" in result.output
    assert working_keyring[(pyssh.store.KEYRING_SERVICE, "prod-web")] == "hunter2"


def test_secret_list_never_prints_a_password(runner, working_keyring):
    runner.invoke(ssh_management, ["secret", "set", "prod-web"], input="hunter2\nhunter2\n")
    result = runner.invoke(ssh_management, ["secret", "list"])
    assert result.exit_code == 0, result.output
    assert "prod-web" in result.stdout
    assert "hunter2" not in result.output


def test_secret_list_as_json(runner, working_keyring):
    runner.invoke(ssh_management, ["secret", "set", "prod-web"], input="hunter2\nhunter2\n")
    result = runner.invoke(ssh_management, ["secret", "list", "--json"])
    payload = json.loads(result.stdout)
    assert payload[0]["name"] == "prod-web"
    assert payload[0]["tier"] == "keyring"
    assert "value" not in payload[0]


def test_secret_rm_forgets_it(runner, working_keyring):
    runner.invoke(ssh_management, ["secret", "set", "prod-web"], input="hunter2\nhunter2\n")
    result = runner.invoke(ssh_management, ["secret", "rm", "prod-web"])
    assert result.exit_code == 0, result.output
    assert working_keyring == {}


def test_secret_rm_for_an_unknown_host(runner, working_keyring):
    result = runner.invoke(ssh_management, ["secret", "rm", "nope"])
    assert result.exit_code != 0


def test_hosts_tag_add_and_list(runner, monkeypatch, tmp_path):
    config = tmp_path / "config"
    config.write_text("", encoding="utf-8")
    monkeypatch.setattr(pyssh.hosts, "default_config_path", lambda: config)
    runner.invoke(ssh_management, ["hosts", "tag", "add", "prod", "web1", "web2"])
    result = runner.invoke(ssh_management, ["hosts", "--tag", "prod", "--json"])
    assert result.exit_code == 0, result.output
    assert [row["name"] for row in json.loads(result.stdout)] == ["web1", "web2"]


def test_hosts_tag_rm(runner, monkeypatch, tmp_path):
    config = tmp_path / "config"
    config.write_text("", encoding="utf-8")
    monkeypatch.setattr(pyssh.hosts, "default_config_path", lambda: config)
    runner.invoke(ssh_management, ["hosts", "tag", "add", "prod", "web1"])
    runner.invoke(ssh_management, ["hosts", "tag", "rm", "prod", "web1"])
    result = runner.invoke(ssh_management, ["hosts", "--tag", "prod", "--json"])
    assert json.loads(result.stdout) == []


def test_hosts_lists_ssh_config_names(runner, monkeypatch, tmp_path):
    config = tmp_path / "config"
    config.write_text("Host alpha\nHost beta\n", encoding="utf-8")
    monkeypatch.setattr(pyssh.hosts, "default_config_path", lambda: config)
    result = runner.invoke(ssh_management, ["hosts", "--json"])
    assert [row["name"] for row in json.loads(result.stdout)] == ["alpha", "beta"]


def test_hosts_marks_which_names_have_a_secret(runner, monkeypatch, tmp_path, working_keyring):
    config = tmp_path / "config"
    config.write_text("Host alpha\nHost beta\n", encoding="utf-8")
    monkeypatch.setattr(pyssh.hosts, "default_config_path", lambda: config)
    runner.invoke(ssh_management, ["secret", "set", "alpha"], input="pw\npw\n")
    rows = {row["name"]: row for row in json.loads(runner.invoke(ssh_management, ["hosts", "--json"]).stdout)}
    assert rows["alpha"]["secret"] == "keyring"
    assert rows["beta"]["secret"] == "none"


@pytest.mark.parametrize(
    ("prefix", "resolves_to"),
    [("t", "tunnel"), ("d", "double-tunnel"), ("rs", "rsync-dir")],
)
def test_existing_abbreviations_still_resolve(runner, prefix, resolves_to):
    """Adding commands must not break an abbreviation that works today."""
    result = runner.invoke(ssh_management, [prefix, "--help"], prog_name="pyssh")
    assert result.exit_code == 0, result.output
    assert f"Usage: pyssh {resolves_to}" in result.stdout


# ── using a stored secret ────────────────────────────────────────────

def test_a_stored_password_is_used_for_a_config_name(runner, monkeypatch, working_keyring):
    captured = {}

    class FakeProcess:
        pid = 4242

        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.subprocess, "Popen", lambda cmd, **kw: (captured.update(cmd=cmd), FakeProcess())[1]
    )
    monkeypatch.setattr(pyssh, "_wait_for_listener", lambda *a, **k: None)
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    monkeypatch.setattr(
        pyssh.hosts,
        "resolve_config",
        lambda name, options=(): pyssh.hosts.ResolvedConfig(
            name=name, hostname="10.0.0.5", user="me", port=22
        ),
    )
    runner.invoke(ssh_management, ["secret", "set", "prod-web"], input="hunter2\nhunter2\n")

    result = runner.invoke(ssh_management, ["tunnel", "-s", "prod-web", "-p", "9998", "-b"])
    assert result.exit_code == 0, result.output
    assert captured["cmd"][0] == "sshpass"
    assert "hunter2" not in " ".join(captured["cmd"])


def test_an_inline_password_wins_over_a_stored_one(runner, monkeypatch, working_keyring):
    monkeypatch.setattr(pyssh.store, "get_secret", lambda name: "stored")
    target = pyssh.apply_stored_secret(pyssh.hosts.resolve_target("me:inline@host"))
    assert target.password == "inline"


def test_a_spec_without_a_password_does_not_consult_the_store(monkeypatch):
    """An inline user@host is not a stored name; looking it up would be wrong."""
    monkeypatch.setattr(
        pyssh.store, "get_secret", lambda name: pytest.fail("store consulted for a spec")
    )
    assert pyssh.apply_stored_secret(pyssh.hosts.resolve_target("me@host")).password is None


def test_rsync_dir_uses_a_stored_password_for_a_config_name(
    runner, fake_rsync, working_keyring, monkeypatch
):
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    runner.invoke(ssh_management, ["secret", "set", "prod-web"], input="hunter2\nhunter2\n")
    result = runner.invoke(
        ssh_management, ["rsync-dir", "-s", "./site", "-d", "prod-web:/srv/site"]
    )
    assert result.exit_code == 0, result.output
    assert fake_rsync["cmd"][0] == "sshpass"
    assert fake_rsync["cmd"][-1] == "prod-web:/srv/site"
    assert "hunter2" not in " ".join(fake_rsync["cmd"])


# ── connect ─────────────────────────────────────────────────────────

@pytest.fixture
def fake_background(monkeypatch):
    """Capture the argv of a backgrounded ssh, and report success."""
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = "Master running (pid=4242)"

    def fake_run_background(cmd, verbose=0):
        captured["cmd"] = list(cmd)
        return FakeCompleted()

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.session, "run_background", fake_run_background)
    monkeypatch.setattr(pyssh.session, "master_pid", lambda sock, dest: 4242)
    monkeypatch.setattr(pyssh.session, "master_alive", lambda sock, dest: True)
    monkeypatch.setattr(pyssh, "_wait_for_listeners", lambda *a, **k: None)
    # The pre-flight port check binds a real local socket; a machine that
    # happens to be running its own service on the test's chosen port (e.g. a
    # local Postgres on 5432) must not make this fail.
    monkeypatch.setattr(pyssh, "port_is_free", lambda port, host="127.0.0.1": True)
    return captured


def test_connect_backgrounds_a_local_forward(runner, fake_background):
    result = runner.invoke(
        ssh_management, ["connect", "prod", "-L", "5432:db.internal:5432", "-b"]
    )
    assert result.exit_code == 0, result.output
    cmd = fake_background["cmd"]
    assert "-L" in cmd and cmd[cmd.index("-L") + 1] == "127.0.0.1:5432:db.internal:5432"
    assert "-f" in cmd and "-N" in cmd
    assert cmd[-1] == "prod"


def test_connect_backgrounds_a_reverse_forward(runner, fake_background):
    result = runner.invoke(ssh_management, ["connect", "prod", "-R", "8080:localhost:3000", "-b"])
    assert result.exit_code == 0, result.output
    cmd = fake_background["cmd"]
    assert cmd[cmd.index("-R") + 1] == "8080:localhost:3000"


def test_a_failed_reverse_forward_is_reported(runner, monkeypatch):
    """ssh -f exits non-zero when the server could not bind; that is the signal."""

    class FakeCompleted:
        returncode = 255
        stdout = ""
        stderr = "Warning: remote port forwarding failed for listen port 8080"

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.session, "run_background", lambda cmd, verbose=0: FakeCompleted())
    result = runner.invoke(ssh_management, ["connect", "prod", "-R", "8080:localhost:3000", "-b"])
    assert result.exit_code != 0
    assert "8080" in result.stderr


def test_connect_combines_forwards_in_one_connection(runner, fake_background):
    result = runner.invoke(
        ssh_management,
        ["connect", "prod", "-L", "5432:db:5432", "-R", "8080:localhost:3000", "-D", "1080", "-b"],
    )
    assert result.exit_code == 0, result.output
    cmd = fake_background["cmd"]
    assert cmd.count("-L") == 1 and cmd.count("-R") == 1 and cmd.count("-D") == 1


def test_connect_records_the_session_for_status(runner, fake_background):
    runner.invoke(ssh_management, ["connect", "prod", "-L", "5432:db:5432", "-b"])
    states = pyssh.load_states()
    assert len(states) == 1
    assert states[0]["kind"] == "connect"
    assert states[0]["destination"] == "prod"


def test_connect_refuses_a_password_to_an_unknown_host(runner, monkeypatch, working_keyring):
    runner.invoke(ssh_management, ["secret", "set", "prod"], input="hunter2\nhunter2\n")
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: False)
    monkeypatch.setattr(
        pyssh.hosts,
        "resolve_config",
        lambda name, options=(): pyssh.hosts.ResolvedConfig(
            name=name, hostname="10.0.0.5", user="me", port=22
        ),
    )
    result = runner.invoke(ssh_management, ["connect", "prod", "-L", "1:h:1", "-b"])
    assert result.exit_code != 0
    assert "known_hosts" in result.stderr
    assert "ssh prod" in result.stderr


def test_key_auth_to_an_unknown_host_is_not_blocked(runner, fake_background, monkeypatch):
    """Only a password triggers the refusal; ssh does its own checking otherwise."""
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: False)
    result = runner.invoke(ssh_management, ["connect", "prod", "-L", "1:h:1", "-b"])
    assert result.exit_code == 0, result.output


def test_connect_refuses_a_taken_local_port(runner, monkeypatch):
    """Pins the pre-flight port check; nothing here should ever reach ssh."""
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh, "port_is_free", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(
        pyssh.session,
        "run_background",
        lambda cmd, verbose=0: pytest.fail("ssh should never be reached: the port check must stop it first"),
    )
    result = runner.invoke(ssh_management, ["connect", "prod", "-L", "5432:db:5432", "-b"])
    assert result.exit_code != 0
    assert "5432" in result.stderr
    assert "already in use" in result.stderr


def test_connect_uses_strict_host_keys_when_a_password_is_present(
    runner, fake_background, monkeypatch, working_keyring
):
    """A password disables sshpass's usual accept-new leniency."""
    runner.invoke(ssh_management, ["secret", "set", "prod"], input="hunter2\nhunter2\n")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    monkeypatch.setattr(
        pyssh.hosts,
        "resolve_config",
        lambda name, options=(): pyssh.hosts.ResolvedConfig(
            name=name, hostname="10.0.0.5", user="me", port=22
        ),
    )
    result = runner.invoke(ssh_management, ["connect", "prod", "-L", "5432:db:5432", "-b"])
    assert result.exit_code == 0, result.output
    cmd = fake_background["cmd"]
    assert "StrictHostKeyChecking=yes" in cmd
    assert "hunter2" not in " ".join(cmd)


def test_connect_forces_no_host_key_policy_without_a_password(runner, fake_background):
    """No sshpass in play means no forced override; ssh does its own prompting."""
    result = runner.invoke(ssh_management, ["connect", "prod", "-L", "5432:db:5432", "-b"])
    assert result.exit_code == 0, result.output
    assert not any(c.startswith("StrictHostKeyChecking=") for c in fake_background["cmd"])


def test_a_session_with_no_control_socket_is_not_falsely_trackable(
    runner, fake_background, monkeypatch
):
    """Without a socket ssh -f never reports a pid, so nothing trustworthy can be
    saved; pyssh must say so and must not promise a `stop` that cannot work."""
    monkeypatch.setattr(pyssh.session, "control_path", lambda name: None)
    result = runner.invoke(ssh_management, ["connect", "prod", "-L", "5432:db:5432", "-b"])
    assert result.exit_code == 0, result.output
    assert pyssh.load_states() == []
    assert "cannot track" in result.stderr
    assert "Stop it with:" not in result.stderr


def test_wait_for_listeners_times_out_when_nothing_is_listening(monkeypatch):
    monkeypatch.setattr(pyssh, "port_is_listening", lambda port, host="127.0.0.1", timeout=1.0: False)
    monkeypatch.setattr(pyssh.time, "sleep", lambda seconds: None)
    with pytest.raises(click.ClickException, match="9999"):
        pyssh._wait_for_listeners([("127.0.0.1", 9999)], timeout=0)


def test_wait_for_listeners_probes_0_0_0_0_via_loopback(monkeypatch):
    seen = []

    def fake_port_is_listening(port, host="127.0.0.1", timeout=1.0):
        seen.append((port, host))
        return True

    monkeypatch.setattr(pyssh, "port_is_listening", fake_port_is_listening)
    pyssh._wait_for_listeners([("0.0.0.0", 1234)], timeout=1)
    assert seen == [(1234, "127.0.0.1")]


def test_forward_is_a_local_forward(runner, fake_background):
    result = runner.invoke(ssh_management, ["forward", "prod", "-L", "5432:db:5432", "-b"])
    assert result.exit_code == 0, result.output
    cmd = fake_background["cmd"]
    assert cmd[cmd.index("-L") + 1] == "127.0.0.1:5432:db:5432"
    assert "-N" in cmd


def test_reverse_is_a_remote_forward(runner, fake_background):
    result = runner.invoke(ssh_management, ["reverse", "prod", "-R", "8080:localhost:3000", "-b"])
    assert result.exit_code == 0, result.output
    cmd = fake_background["cmd"]
    assert cmd[cmd.index("-R") + 1] == "8080:localhost:3000"


def test_reverse_accepts_a_bare_port_as_a_remote_socks_proxy(runner, fake_background):
    result = runner.invoke(ssh_management, ["reverse", "prod", "-R", "1080", "-b"])
    assert result.exit_code == 0, result.output
    assert fake_background["cmd"][fake_background["cmd"].index("-R") + 1] == "1080"


def test_reverse_warns_that_public_needs_gatewayports(runner, fake_background):
    result = runner.invoke(
        ssh_management, ["reverse", "prod", "-R", "8080:localhost:3000", "--public", "-b"]
    )
    assert result.exit_code == 0, result.output
    assert "GatewayPorts" in result.stderr


def test_status_keeps_a_session_whose_master_answers(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pyssh.session, "master_alive", lambda sock, dest: True)
    pyssh.save_state(
        "connect-5432",
        {"kind": "connect", "pids": [], "control": str(tmp_path / "ctl"), "destination": "prod"},
    )
    result = runner.invoke(ssh_management, ["status", "--json"])
    assert len(json.loads(result.stdout)) == 1


def test_status_drops_a_session_whose_master_is_gone(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pyssh.session, "master_alive", lambda sock, dest: False)
    pyssh.save_state(
        "connect-5432",
        {"kind": "connect", "pids": [], "control": str(tmp_path / "ctl"), "destination": "prod"},
    )
    result = runner.invoke(ssh_management, ["status", "--json"])
    assert json.loads(result.stdout) == []


def test_status_table_shows_forwards_when_there_is_no_socks_port(runner, monkeypatch, tmp_path):
    """A connect/forward/reverse session has no socks_port; the socks column
    must fall back to the forwards string instead of a broken socks5://host:."""
    monkeypatch.setattr(pyssh.session, "master_alive", lambda sock, dest: True)
    pyssh.save_state(
        "connect-5432",
        {
            "kind": "connect",
            "pids": [],
            "control": str(tmp_path / "ctl"),
            "destination": "prod",
            "forwards": ["-L", "127.0.0.1:5432:db:5432"],
        },
    )
    result = runner.invoke(ssh_management, ["status"])
    assert result.exit_code == 0, result.output
    assert "-L 127.0.0.1:5432:db:5432" in result.stdout
    assert "socks5://" not in result.stdout


def test_stop_asks_the_master_to_exit(runner, monkeypatch, tmp_path):
    asked = {}
    killed = {}
    monkeypatch.setattr(pyssh.session, "master_alive", lambda sock, dest: True)
    monkeypatch.setattr(
        pyssh.session, "stop_master", lambda sock, dest: asked.update(dest=dest) or True
    )
    monkeypatch.setattr(
        pyssh, "terminate", lambda pids, timeout=5.0: killed.update(pids=list(pids)) or 0
    )
    pyssh.save_state(
        "connect-5432",
        {
            "kind": "connect",
            "pids": [999999999],
            "control": str(tmp_path / "ctl"),
            "destination": "prod",
        },
    )
    result = runner.invoke(ssh_management, ["stop", "connect-5432"])
    assert result.exit_code == 0, result.output
    assert asked["dest"] == "prod"
    assert killed == {}


def test_stop_still_kills_a_pid_only_session(runner, monkeypatch):
    """Windows and over-long socket paths fall back to PIDs.

    terminate is stubbed so the test does not signal its own process.
    """
    killed = {}
    monkeypatch.setattr(
        pyssh, "terminate", lambda pids, timeout=5.0: killed.update(pids=list(pids)) or 1
    )
    pyssh.save_state("tunnel-9998", {"kind": "tunnel", "pids": [os.getpid()], "socks_port": 9998})
    result = runner.invoke(ssh_management, ["stop", "tunnel-9998"])
    assert result.exit_code == 0, result.output
    assert killed["pids"] == [os.getpid()]


# ── exec ────────────────────────────────────────────────────────────

@pytest.fixture
def fake_exec(monkeypatch):
    """Capture the ssh argv exec would run, and report success."""
    captured = {"cmds": []}

    def fake_run(cmd, name, capture):
        captured["cmds"].append(list(cmd))
        return pyssh.remote.ExecResult(name=name, returncode=0, stdout="ok\n")

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.remote, "run", fake_run)
    return captured


def test_exec_runs_a_command_on_one_host(runner, fake_exec):
    result = runner.invoke(ssh_management, ["exec", "prod", "uptime"])
    assert result.exit_code == 0, result.output
    cmd = fake_exec["cmds"][0]
    assert cmd[-1] == "uptime"
    assert cmd[-2] == "prod"
    assert "-N" not in cmd


def test_exec_joins_its_arguments_like_ssh_does(runner, fake_exec):
    runner.invoke(ssh_management, ["exec", "prod", "ls", "-la", "/srv"])
    assert fake_exec["cmds"][0][-1] == "ls -la /srv"


def test_exec_applies_cd_and_env_and_sudo(runner, fake_exec):
    runner.invoke(
        ssh_management,
        ["exec", "prod", "--cd", "/srv/app", "--env", "CI=1", "--sudo", "make"],
    )
    # shlex.quote only adds quotes when a character needs them; see test_ssh_remote.py.
    assert fake_exec["cmds"][0][-1] == "export CI=1; cd -- /srv/app && sudo -n make"


def test_exec_tty_adds_a_single_dash_t(runner, fake_exec):
    """Never -tt: that forces remote pty allocation even without a local
    terminal, which would risk mixing pty-formatted output into --json/--tag's
    captured stream. A single -t is what ssh silently ignores when captured."""
    runner.invoke(ssh_management, ["exec", "prod", "--tty", "top"])
    cmd = fake_exec["cmds"][0]
    assert "-t" in cmd
    assert "-tt" not in cmd


def test_exec_tty_with_json_still_captures_cleanly(runner, fake_exec):
    """--tty is accepted with --json (which always captures); see
    docs/pyssh.md for why this is inert rather than corrupting."""
    result = runner.invoke(ssh_management, ["exec", "prod", "--tty", "--json", "uptime"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload[0]["stdout"] == "ok\n"


def test_exec_tty_with_tag_adds_a_single_dash_t_per_host(runner, monkeypatch):
    """--tty is accepted with --tag (always captured); see docs/pyssh.md for
    why this is inert rather than corrupting."""
    calls = []
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.remote,
        "run_many",
        lambda jobs, parallel=1: [
            calls.append(cmd)
            or pyssh.remote.ExecResult(name=name, returncode=0, stdout="up\n")
            for name, cmd in jobs
        ],
    )
    runner.invoke(ssh_management, ["hosts", "tag", "add", "prod", "web1"])
    result = runner.invoke(ssh_management, ["exec", "--tag", "prod", "--tty", "uptime"])
    assert result.exit_code == 0, result.output
    assert "-t" in calls[0]
    assert "-tt" not in calls[0]


def test_exec_propagates_the_remote_exit_code(runner, monkeypatch):
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.remote,
        "run",
        lambda cmd, name, capture: pyssh.remote.ExecResult(name=name, returncode=42),
    )
    result = runner.invoke(ssh_management, ["exec", "prod", "false"])
    assert result.exit_code == 42


def test_exec_across_a_tag_hits_every_host(runner, monkeypatch):
    calls = []
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.remote,
        "run_many",
        lambda jobs, parallel=1: [
            calls.append(name)
            or pyssh.remote.ExecResult(name=name, returncode=0, stdout="up\n")
            for name, _ in jobs
        ],
    )
    runner.invoke(ssh_management, ["hosts", "tag", "add", "prod", "web1", "web2"])
    result = runner.invoke(ssh_management, ["exec", "--tag", "prod", "uptime"])
    assert result.exit_code == 0, result.output
    assert calls == ["web1", "web2"]


def test_group_output_is_prefixed_with_the_host(runner, monkeypatch):
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.remote,
        "run_many",
        lambda jobs, parallel=1: [
            pyssh.remote.ExecResult(name=name, returncode=0, stdout="up 3 days\n")
            for name, _ in jobs
        ],
    )
    runner.invoke(ssh_management, ["hosts", "tag", "add", "prod", "web1"])
    result = runner.invoke(ssh_management, ["exec", "--tag", "prod", "uptime"])
    assert "web1 | up 3 days" in result.stdout


def test_a_group_exits_non_zero_if_any_host_failed(runner, monkeypatch):
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.remote,
        "run_many",
        lambda jobs, parallel=1: [
            pyssh.remote.ExecResult(name=name, returncode=0 if name == "web1" else 1)
            for name, _ in jobs
        ],
    )
    runner.invoke(ssh_management, ["hosts", "tag", "add", "prod", "web1", "web2"])
    result = runner.invoke(ssh_management, ["exec", "--tag", "prod", "uptime"])
    assert result.exit_code != 0


def test_group_json_reports_every_host(runner, monkeypatch):
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.remote,
        "run_many",
        lambda jobs, parallel=1: [
            pyssh.remote.ExecResult(name=name, returncode=0, stdout="ok\n") for name, _ in jobs
        ],
    )
    runner.invoke(ssh_management, ["hosts", "tag", "add", "prod", "web1"])
    result = runner.invoke(ssh_management, ["exec", "--tag", "prod", "uptime", "--json"])
    payload = json.loads(result.stdout)
    assert payload[0]["name"] == "web1"
    assert payload[0]["returncode"] == 0


def test_an_unused_tag_is_an_error(runner):
    result = runner.invoke(ssh_management, ["exec", "--tag", "nope", "uptime"])
    assert result.exit_code != 0
    assert "nope" in result.stderr


def test_a_group_uses_batch_mode_so_a_prompt_cannot_hang_it(runner, monkeypatch):
    seen = {}
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.remote,
        "run_many",
        lambda jobs, parallel=1: [
            seen.update(cmd=list(cmd))
            or pyssh.remote.ExecResult(name=name, returncode=0)
            for name, cmd in jobs
        ],
    )
    runner.invoke(ssh_management, ["hosts", "tag", "add", "prod", "web1"])
    runner.invoke(ssh_management, ["exec", "--tag", "prod", "uptime"])
    assert "BatchMode=yes" in seen["cmd"]


def test_exec_needs_a_target(runner):
    result = runner.invoke(ssh_management, ["exec"])
    assert result.exit_code != 0


def test_exec_without_a_double_dash_still_passes_short_remote_flags_through(runner, fake_exec):
    """exec has no short options of its own, so -o survives untouched with no -- needed."""
    runner.invoke(ssh_management, ["exec", "prod", "sort", "-o", "out.txt", "data.txt"])
    assert fake_exec["cmds"][0][-1] == "sort -o out.txt data.txt"


def test_a_double_dash_lets_the_remote_command_use_pysshs_own_flags(runner, fake_exec):
    """`--` still escapes a remote command that collides with one of pyssh's
    own long options, e.g. one that also uses `-o` for its own purposes."""
    runner.invoke(ssh_management, ["exec", "prod", "--", "grep", "-o", "pat", "file"])
    assert fake_exec["cmds"][0][-1] == "grep -o pat file"


def test_exec_refuses_a_password_to_an_unknown_host(runner, monkeypatch, working_keyring):
    runner.invoke(ssh_management, ["secret", "set", "prod"], input="hunter2\nhunter2\n")
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: False)
    monkeypatch.setattr(
        pyssh.hosts,
        "resolve_config",
        lambda name, options=(): pyssh.hosts.ResolvedConfig(
            name=name, hostname="10.0.0.5", user="me", port=22
        ),
    )
    result = runner.invoke(ssh_management, ["exec", "prod", "uptime"])
    assert result.exit_code != 0
    assert "known_hosts" in result.stderr


def test_exec_uses_strict_host_keys_when_a_password_is_present(
    runner, fake_exec, monkeypatch, working_keyring
):
    """A password disables sshpass's usual accept-new leniency."""
    runner.invoke(ssh_management, ["secret", "set", "prod"], input="hunter2\nhunter2\n")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    monkeypatch.setattr(
        pyssh.hosts,
        "resolve_config",
        lambda name, options=(): pyssh.hosts.ResolvedConfig(
            name=name, hostname="10.0.0.5", user="me", port=22
        ),
    )
    result = runner.invoke(ssh_management, ["exec", "prod", "uptime"])
    assert result.exit_code == 0, result.output
    cmd = fake_exec["cmds"][0]
    assert "StrictHostKeyChecking=yes" in cmd
    assert "hunter2" not in " ".join(cmd)


def test_exec_removes_the_password_file_afterwards(
    runner, fake_exec, monkeypatch, working_keyring
):
    runner.invoke(ssh_management, ["secret", "set", "prod"], input="hunter2\nhunter2\n")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: True)
    monkeypatch.setattr(
        pyssh.hosts,
        "resolve_config",
        lambda name, options=(): pyssh.hosts.ResolvedConfig(
            name=name, hostname="10.0.0.5", user="me", port=22
        ),
    )
    runner.invoke(ssh_management, ["exec", "prod", "uptime"])
    pass_file = pyssh.Path(fake_exec["cmds"][0][2])
    assert not pass_file.exists()


def test_exec_passes_parallel_through_to_run_many(runner, monkeypatch):
    captured = {}
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.remote,
        "run_many",
        lambda jobs, parallel=1: captured.update(parallel=parallel) or [
            pyssh.remote.ExecResult(name=name, returncode=0) for name, _ in jobs
        ],
    )
    runner.invoke(ssh_management, ["hosts", "tag", "add", "prod", "web1", "web2"])
    result = runner.invoke(ssh_management, ["exec", "--tag", "prod", "--parallel", "8", "uptime"])
    assert result.exit_code == 0, result.output
    assert captured["parallel"] == 8


# ── onboarding ──────────────────────────────────────────────────────

def test_keygen_makes_an_ed25519_key_by_default(runner, monkeypatch, tmp_path):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.subprocess, "run", lambda cmd, **kw: (captured.update(cmd=cmd), FakeCompleted())[1]
    )
    key = tmp_path / "id_test"
    result = runner.invoke(ssh_management, ["keygen", "-f", str(key)])
    assert result.exit_code == 0, result.output
    assert captured["cmd"][:3] == ["ssh-keygen", "-t", "ed25519"]
    assert str(key) in captured["cmd"]


def test_keygen_names_the_identityfile_line_to_add(runner, monkeypatch, tmp_path):
    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    result = runner.invoke(ssh_management, ["keygen", "prod", "-f", str(tmp_path / "id_test")])
    assert "IdentityFile" in result.output
    assert "Host prod" in result.output


def test_keygen_will_not_overwrite_an_existing_key(runner, monkeypatch, tmp_path):
    def _explode(*args, **kwargs):
        raise AssertionError("ssh-keygen must not run when the key already exists")

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    # A pass-through subprocess.run would let a removed guard silently invoke
    # the real ssh-keygen (it would then prompt to overwrite and exit 1 on
    # EOF, passing the assertions below for the wrong reason). Make the guard
    # load-bearing: any call at all is a hard failure, not a green test.
    monkeypatch.setattr(pyssh.subprocess, "run", _explode)
    key = tmp_path / "id_test"
    key.write_text("existing", encoding="utf-8")
    result = runner.invoke(ssh_management, ["keygen", "-f", str(key)])
    assert result.exit_code != 0
    assert key.read_text(encoding="utf-8") == "existing"
    assert "already exists" in result.output


def test_copy_id_prefers_ssh_copy_id(runner, monkeypatch, tmp_path):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    key = tmp_path / "id_test.pub"
    key.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.subprocess, "run", lambda cmd, **kw: (captured.update(cmd=cmd), FakeCompleted())[1]
    )
    result = runner.invoke(ssh_management, ["copy-id", "prod", "-i", str(key)])
    assert result.exit_code == 0, result.output
    assert captured["cmd"][0] == "ssh-copy-id"
    assert captured["cmd"][-1] == "prod"


def test_copy_id_falls_back_when_ssh_copy_id_is_missing(runner, monkeypatch, tmp_path):
    """Not every OpenSSH build ships ssh-copy-id."""
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    key = tmp_path / "id_test.pub"
    key.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
    monkeypatch.setattr(
        pyssh.shutil, "which", lambda name: None if name == "ssh-copy-id" else f"/usr/bin/{name}"
    )
    monkeypatch.setattr(
        pyssh.subprocess, "run", lambda cmd, **kw: (captured.update(cmd=cmd), FakeCompleted())[1]
    )
    result = runner.invoke(ssh_management, ["copy-id", "prod", "-i", str(key)])
    assert result.exit_code == 0, result.output
    assert captured["cmd"][0] == "ssh"
    assert "authorized_keys" in captured["cmd"][-1]


def test_check_reports_a_reachable_host(runner, monkeypatch):
    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    monkeypatch.setattr(
        pyssh.hosts,
        "resolve_config",
        lambda name, options=(): pyssh.hosts.ResolvedConfig(
            name=name, hostname="10.0.0.5", user="me", port=2222
        ),
    )
    result = runner.invoke(ssh_management, ["check", "prod", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["reachable"] is True
    assert payload["name"] == "prod"
    # The report names the endpoint ssh actually reaches, not the name typed.
    assert (payload["hostname"], payload["port"]) == ("10.0.0.5", 2222)


def test_check_reports_an_unreachable_host(runner, monkeypatch):
    class FakeCompleted:
        returncode = 255
        stdout = ""
        stderr = "Permission denied (publickey)."

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    monkeypatch.setattr(pyssh.hosts, "resolve_config", lambda name, options=(): None)
    result = runner.invoke(ssh_management, ["check", "prod", "--json"])
    assert result.exit_code != 0
    assert json.loads(result.stdout)["reachable"] is False


def test_check_uses_batch_mode(runner, monkeypatch):
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        pyssh.subprocess, "run", lambda cmd, **kw: (captured.update(cmd=cmd), FakeCompleted())[1]
    )
    monkeypatch.setattr(pyssh.hosts, "resolve_config", lambda name, options=(): None)
    runner.invoke(ssh_management, ["check", "prod"])
    assert "BatchMode=yes" in captured["cmd"]


# ── onboarding: fix round ────────────────────────────────────────────

def test_copy_id_refuses_a_stored_password_to_an_unknown_host(
    runner, monkeypatch, tmp_path, working_keyring
):
    """copy-id must guard exactly like connect and exec: no verified host, no password."""
    key = tmp_path / "id_test.pub"
    key.write_text("ssh-ed25519 AAAA test\n", encoding="utf-8")
    runner.invoke(ssh_management, ["secret", "set", "prod"], input="hunter2\nhunter2\n")
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.knownhosts, "is_known", lambda host, port=22: False)
    monkeypatch.setattr(
        pyssh.hosts,
        "resolve_config",
        lambda name, options=(): pyssh.hosts.ResolvedConfig(
            name=name, hostname="10.0.0.5", user="me", port=22
        ),
    )
    monkeypatch.setattr(
        pyssh.subprocess,
        "run",
        lambda *a, **k: pytest.fail(
            "subprocess.run must not be called: the host key was never verified"
        ),
    )
    result = runner.invoke(ssh_management, ["copy-id", "prod", "-i", str(key)])
    assert result.exit_code != 0
    assert "known_hosts" in result.stderr
    assert "ssh prod" in result.stderr


def test_copy_id_fallback_quotes_a_hostile_key_safely(runner, monkeypatch, tmp_path):
    """The installer is a shell one-liner; an unquoted key would be code execution."""
    captured = {}

    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    dangerous_key = "ssh-ed25519 AAAA test$(touch pwned) `touch pwned2` ' ; rm -rf / #"
    key = tmp_path / "id_test.pub"
    key.write_text(dangerous_key + "\n", encoding="utf-8")
    monkeypatch.setattr(
        pyssh.shutil, "which", lambda name: None if name == "ssh-copy-id" else f"/usr/bin/{name}"
    )
    monkeypatch.setattr(
        pyssh.subprocess, "run", lambda cmd, **kw: (captured.update(cmd=cmd), FakeCompleted())[1]
    )
    result = runner.invoke(ssh_management, ["copy-id", "prod", "-i", str(key)])
    assert result.exit_code == 0, result.output
    installer = captured["cmd"][-1]
    # shlex.split undoes shlex.quote's escaping; if the key round-trips intact
    # as a single token, nothing in it broke out to run as a shell command.
    tokens = pyssh.shlex.split(installer)
    printf_index = tokens.index("printf")
    assert tokens[printf_index + 2] == dangerous_key


def test_copy_id_prefers_the_name_specific_key(runner, monkeypatch, tmp_path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    named = ssh_dir / "id_ed25519_prod.pub"
    named.write_text("ssh-ed25519 AAAA named\n", encoding="utf-8")
    generic = ssh_dir / "id_ed25519.pub"
    generic.write_text("ssh-ed25519 AAAA generic\n", encoding="utf-8")
    monkeypatch.setattr(pyssh.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.subprocess, "run", lambda cmd, **kw: type(
        "FakeCompleted", (), {"returncode": 0, "stdout": "", "stderr": ""}
    )())
    result = runner.invoke(ssh_management, ["copy-id", "prod"])
    assert result.exit_code == 0, result.output
    assert "id_ed25519_prod.pub" in result.output


def test_copy_id_falls_back_to_the_generic_key(runner, monkeypatch, tmp_path):
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    generic = ssh_dir / "id_ed25519.pub"
    generic.write_text("ssh-ed25519 AAAA generic\n", encoding="utf-8")
    monkeypatch.setattr(pyssh.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.subprocess, "run", lambda cmd, **kw: type(
        "FakeCompleted", (), {"returncode": 0, "stdout": "", "stderr": ""}
    )())
    result = runner.invoke(ssh_management, ["copy-id", "prod"])
    assert result.exit_code == 0, result.output
    assert "id_ed25519.pub" in result.output


def test_copy_id_names_both_paths_when_neither_key_exists(runner, monkeypatch, tmp_path):
    monkeypatch.setattr(pyssh.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    result = runner.invoke(ssh_management, ["copy-id", "prod"])
    assert result.exit_code != 0
    assert "id_ed25519_prod.pub" in result.output
    assert "id_ed25519.pub" in result.output


def test_keygen_reports_a_directory_it_cannot_create(runner, monkeypatch, tmp_path):
    """A user-supplied -f path can name a parent that is not a directory at all."""
    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    key = blocker / "id_test"
    result = runner.invoke(ssh_management, ["keygen", "-f", str(key)])
    assert result.exit_code != 0
    assert "Could not create" in result.output
    assert str(blocker) in result.output


def test_check_reports_a_reachable_host_without_json(runner, monkeypatch):
    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    monkeypatch.setattr(pyssh.hosts, "resolve_config", lambda name, options=(): None)
    result = runner.invoke(ssh_management, ["check", "prod"])
    assert result.exit_code == 0, result.output
    assert "prod is reachable" in result.stdout
    assert "is reachable" not in result.stderr


def test_check_reports_an_unreachable_host_without_json(runner, monkeypatch):
    class FakeCompleted:
        returncode = 255
        stdout = ""
        stderr = "Permission denied (publickey)."

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    monkeypatch.setattr(pyssh.hosts, "resolve_config", lambda name, options=(): None)
    result = runner.invoke(ssh_management, ["check", "prod"])
    assert result.exit_code != 0
    assert "not reachable" in result.stderr
    assert "not reachable" not in result.stdout


def test_check_prints_the_changed_host_key_remediation(runner, monkeypatch):
    class FakeCompleted:
        returncode = 255
        stdout = ""
        stderr = (
            "@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@\n"
            "REMOTE HOST IDENTIFICATION HAS CHANGED!\n"
        )

    monkeypatch.setattr(pyssh.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(pyssh.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    monkeypatch.setattr(pyssh.hosts, "resolve_config", lambda name, options=(): None)
    result = runner.invoke(ssh_management, ["check", "prod"])
    assert result.exit_code != 0
    assert "ssh-keygen -R" in result.stderr


def test_every_command_is_documented():
    """A command missing from docs/pyssh.md is a command nobody will find."""
    from pathlib import Path

    doc = Path(__file__).resolve().parents[1] / "docs" / "pyssh.md"
    text = doc.read_text(encoding="utf-8")
    documented = [
        name
        for name in ssh_management.list_commands(click.Context(ssh_management))
        if name not in text
    ]
    assert documented == [], f"undocumented commands: {documented}"


def test_every_command_has_an_examples_block(runner):
    """The repo convention: each docstring ends with an Examples: block."""
    ctx = click.Context(ssh_management)
    missing = []
    for name in ssh_management.list_commands(ctx):
        cmd = ssh_management.get_command(ctx, name)
        if isinstance(cmd, click.Group):
            for sub in cmd.list_commands(ctx):
                child = cmd.get_command(ctx, sub)
                if "Examples:" not in (child.help or ""):
                    missing.append(f"{name} {sub}")
        elif "Examples:" not in (cmd.help or ""):
            missing.append(name)
    assert missing == [], f"commands without an Examples block: {missing}"
