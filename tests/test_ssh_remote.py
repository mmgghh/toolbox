"""Tests for remote command execution. No ssh is ever run."""

from __future__ import annotations

import click
import pytest

from pytoolbox.ssh import remote


def test_a_plain_command_is_untouched():
    assert remote.wrap_command("uptime") == "uptime"


def test_cd_runs_the_command_in_a_directory():
    # shlex.quote adds quotes only when a character needs them, so a plain
    # path passes through bare. The guarantee is shell-safety, not quote marks.
    assert remote.wrap_command("git pull", workdir="/srv/app") == "cd /srv/app && git pull"


def test_a_directory_with_spaces_is_quoted():
    assert remote.wrap_command("ls", workdir="/srv/my app") == "cd '/srv/my app' && ls"


def test_env_is_exported_so_a_pipeline_sees_it():
    assert remote.wrap_command("printenv FOO", env=["FOO=bar"]) == "export FOO=bar; printenv FOO"


def test_env_values_are_quoted():
    assert remote.wrap_command("x", env=["FOO=a b"]) == "export FOO='a b'; x"


def test_several_env_pairs_keep_their_order():
    assert remote.wrap_command("x", env=["A=1", "B=2"]) == "export A=1; export B=2; x"


def test_sudo_is_non_interactive():
    """Prompting would hang a captured-output worker."""
    assert remote.wrap_command("systemctl restart app", sudo=True) == "sudo -n systemctl restart app"


def test_cd_and_env_and_sudo_compose():
    wrapped = remote.wrap_command("make", workdir="/srv", env=["CI=1"], sudo=True)
    assert wrapped == "export CI=1; cd /srv && sudo -n make"


@pytest.mark.parametrize("pair", ["FOO", "=bar", "1FOO=bar", "FO O=bar"])
def test_a_malformed_env_pair_is_rejected(pair):
    with pytest.raises(click.ClickException):
        remote.wrap_command("x", env=[pair])


def test_an_empty_command_is_rejected():
    with pytest.raises(click.ClickException):
        remote.wrap_command("   ")


def test_run_captures_output(monkeypatch):
    class FakeCompleted:
        returncode = 3
        stdout = "hello\n"
        stderr = "oops\n"

    monkeypatch.setattr(remote.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    result = remote.run(["ssh", "prod", "true"], "prod", capture=True)
    assert (result.name, result.returncode, result.stdout, result.stderr) == (
        "prod",
        3,
        "hello\n",
        "oops\n",
    )


def test_run_without_capture_passes_streams_through(monkeypatch):
    """A single host must be able to pipe: pyssh exec x 'cat f' > f."""
    monkeypatch.setattr(remote.subprocess, "call", lambda cmd: 0)
    result = remote.run(["ssh", "prod", "cat f"], "prod", capture=False)
    assert result.returncode == 0
    assert result.stdout == ""


def test_run_many_returns_one_result_per_host(monkeypatch):
    class FakeCompleted:
        returncode = 0
        stdout = "ok\n"
        stderr = ""

    monkeypatch.setattr(remote.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    results = remote.run_many(
        [("web1", ["ssh", "web1", "uptime"]), ("web2", ["ssh", "web2", "uptime"])], parallel=2
    )
    assert sorted(item.name for item in results) == ["web1", "web2"]


def test_run_many_keeps_the_order_it_was_given(monkeypatch):
    class FakeCompleted:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(remote.subprocess, "run", lambda cmd, **kw: FakeCompleted())
    jobs = [(name, ["ssh", name, "x"]) for name in ["c", "a", "b"]]
    assert [item.name for item in remote.run_many(jobs, parallel=3)] == ["c", "a", "b"]
