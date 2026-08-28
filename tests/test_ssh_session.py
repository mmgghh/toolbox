"""Tests for background ssh sessions. No ssh is ever run."""

from __future__ import annotations

import pytest

from pytoolbox.ssh import session


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTOOLBOX_HOME", str(tmp_path / "home"))


def _fake_ssh(monkeypatch, returncode, stderr="", stdout=""):
    class FakeCompleted:
        pass

    FakeCompleted.returncode = returncode
    FakeCompleted.stdout = stdout
    FakeCompleted.stderr = stderr
    calls = {}
    monkeypatch.setattr(session.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        session.subprocess, "run", lambda cmd, **kw: (calls.update(cmd=cmd), FakeCompleted())[1]
    )
    return calls


def test_control_path_lives_under_the_runtime_dir():
    path = session.control_path("tunnel-9998")
    assert path is not None
    assert path.name == "tunnel-9998"
    assert path.parent.is_dir()


def test_control_path_is_none_when_it_would_be_too_long(monkeypatch):
    """A Unix socket path is capped; callers fall back to tracking PIDs."""
    monkeypatch.setattr(session, "control_dir", lambda: session.Path("/" + "x" * 120))
    assert session.control_path("tunnel-9998") is None


def test_control_path_is_none_on_windows(monkeypatch):
    """Windows OpenSSH has no ControlMaster."""
    monkeypatch.setattr(session.paths, "is_windows", lambda: True)
    assert session.control_path("tunnel-9998") is None


def test_master_pid_is_read_from_the_check_reply(monkeypatch, tmp_path):
    calls = _fake_ssh(monkeypatch, 0, stderr="Master running (pid=12345)\r\n")
    socket = tmp_path / "ctl"
    assert session.master_pid(socket, "prod") == 12345
    assert calls["cmd"] == ["ssh", "-S", str(socket), "-O", "check", "prod"]


def test_master_pid_is_none_when_the_master_is_gone(monkeypatch, tmp_path):
    _fake_ssh(monkeypatch, 255, stderr="Control socket connect: No such file or directory")
    assert session.master_pid(tmp_path / "ctl", "prod") is None


def test_master_pid_is_none_when_returncode_is_nonzero_even_with_a_pid_in_the_reply(
    monkeypatch, tmp_path
):
    """Isolates the returncode guard from the regex-miss path tested above."""
    _fake_ssh(monkeypatch, 1, stderr="Master running (pid=42)")
    assert session.master_pid(tmp_path / "ctl", "prod") is None


def test_master_pid_ignores_pid_embedded_in_a_longer_word(monkeypatch, tmp_path):
    """A bare ``pid=`` search would false-positive on "stupid=5" or "rapid=42"."""
    _fake_ssh(monkeypatch, 0, stderr="stupid=5 rapid=42")
    assert session.master_pid(tmp_path / "ctl", "prod") is None


def test_master_pid_prefers_stderr_over_stdout(monkeypatch, tmp_path):
    """ssh -O check answers on stderr; stdout is not authoritative."""
    _fake_ssh(monkeypatch, 0, stderr="Master running (pid=111)", stdout="pid=999")
    assert session.master_pid(tmp_path / "ctl", "prod") == 111


def test_master_alive_follows_the_pid(monkeypatch, tmp_path):
    _fake_ssh(monkeypatch, 0, stderr="Master running (pid=1)")
    assert session.master_alive(tmp_path / "ctl", "prod") is True


def test_master_alive_is_false_when_the_master_is_gone(monkeypatch, tmp_path):
    _fake_ssh(monkeypatch, 255, stderr="Control socket connect: No such file or directory")
    assert session.master_alive(tmp_path / "ctl", "prod") is False


def test_stop_master_asks_ssh_to_exit(monkeypatch, tmp_path):
    calls = _fake_ssh(monkeypatch, 0)
    socket = tmp_path / "ctl"
    assert session.stop_master(socket, "prod") is True
    assert calls["cmd"] == ["ssh", "-S", str(socket), "-O", "exit", "prod"]


def test_stop_master_reports_a_master_that_was_already_gone(monkeypatch, tmp_path):
    _fake_ssh(monkeypatch, 255)
    assert session.stop_master(tmp_path / "ctl", "prod") is False


def test_run_background_returns_the_exit_status(monkeypatch):
    """ssh -f exits non-zero when a remote forward could not be established."""
    _fake_ssh(monkeypatch, 255, stderr="remote port forwarding failed for listen port 8080")
    completed = session.run_background(["ssh", "-f", "-N", "-R", "8080:localhost:80", "prod"])
    assert completed.returncode == 255
    assert "remote port forwarding failed" in completed.stderr
