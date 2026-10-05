"""Tests for `pytime auto service` with systemctl faked out."""

from __future__ import annotations

import subprocess
import sys

import pytest

from pytoolbox.core import activity_service
from pytoolbox.pytime import time_cli


@pytest.fixture
def fake_systemd(tmp_path, monkeypatch):
    """Record systemctl calls; report the unit enabled/active once started."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PYTIME_DB", str(tmp_path / "pytime.db"))
    for name in activity_service.SESSION_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.setattr(activity_service.shutil, "which", lambda name: f"/usr/bin/{name}")
    calls: list[list[str]] = []
    state = {"enabled": "disabled", "active": "inactive"}

    def run(args, **kwargs):
        calls.append(args[2:])
        verb = args[2]
        if verb == "enable":
            state["enabled"] = "enabled"
            if "--now" in args:
                state["active"] = "active"
        elif verb == "disable":
            state["enabled"] = "disabled"
            if "--now" in args:
                state["active"] = "inactive"
        elif verb in ("start", "restart"):
            state["active"] = "active"
        elif verb == "stop":
            state["active"] = "inactive"
        elif verb == "is-enabled":
            return subprocess.CompletedProcess(args, 0, state["enabled"] + "\n", "")
        elif verb == "is-active":
            return subprocess.CompletedProcess(args, 0, state["active"] + "\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(activity_service.subprocess, "run", run)
    return calls


def test_render_unit_pins_python_db_and_path(tmp_path):
    text = activity_service.render_unit(tmp_path / "my db.db", 5, 10, python="/venv/bin/python", path_env="/a:/b")
    assert f'ExecStart=/venv/bin/python -m pytoolbox.pytime --db "{tmp_path / "my db.db"}" auto watch' in text
    assert "--interval 5 --idle-timeout 10" in text
    assert "Environment=PATH=/a:/b" in text
    assert "WantedBy=graphical-session.target" in text
    assert "StartLimitIntervalSec=0" in text


def test_render_unit_escapes_percent():
    text = activity_service.render_unit("/tmp/100%.db", 5, 5, python="/py", path_env="")
    assert "100%%.db" in text


def test_install_now_writes_unit_and_starts(runner, fake_systemd, tmp_path):
    result = runner.invoke(time_cli, ["auto", "service", "install", "--now", "--interval", "7"])
    assert result.exit_code == 0, result.output
    unit = (tmp_path / "config" / "systemd" / "user" / "pytime-auto.service").read_text()
    assert sys.executable in unit
    assert str(tmp_path / "pytime.db") in unit
    assert "--interval 7" in unit
    assert ["daemon-reload"] in fake_systemd
    assert ["import-environment", "WAYLAND_DISPLAY", "XDG_CURRENT_DESKTOP"] in fake_systemd
    assert ["enable", "--now", "pytime-auto.service"] in fake_systemd
    assert "active, enabled at login" in result.output


def test_install_refuses_to_overwrite_without_force(runner, fake_systemd):
    runner.invoke(time_cli, ["auto", "service", "install"])
    again = runner.invoke(time_cli, ["auto", "service", "install"])
    assert again.exit_code != 0
    assert "--force" in again.stderr
    assert runner.invoke(time_cli, ["auto", "service", "install", "--force"]).exit_code == 0


def test_lifecycle_commands(runner, fake_systemd):
    runner.invoke(time_cli, ["auto", "service", "install"])
    assert "enabled at login" in runner.invoke(time_cli, ["auto", "service", "enable"]).output
    assert "active," in runner.invoke(time_cli, ["auto", "service", "start"]).output
    assert "inactive," in runner.invoke(time_cli, ["auto", "service", "stop"]).output
    assert "active," in runner.invoke(time_cli, ["auto", "service", "restart"]).output
    disabled = runner.invoke(time_cli, ["auto", "service", "disable", "--now"])
    assert "inactive, disabled at login" in disabled.output
    status = runner.invoke(time_cli, ["auto", "service", "status"])
    assert "Unit file:" in status.output


def test_commands_need_the_unit_installed(runner, fake_systemd):
    result = runner.invoke(time_cli, ["auto", "service", "start"])
    assert result.exit_code != 0
    assert "install" in result.stderr
    assert "Not installed" in runner.invoke(time_cli, ["auto", "service", "status"]).output


def test_uninstall_disables_and_removes(runner, fake_systemd, tmp_path):
    runner.invoke(time_cli, ["auto", "service", "install", "--now"])
    result = runner.invoke(time_cli, ["auto", "service", "uninstall"])
    assert result.exit_code == 0
    assert ["disable", "--now", "pytime-auto.service"] in fake_systemd
    assert not (tmp_path / "config" / "systemd" / "user" / "pytime-auto.service").exists()


def test_without_systemctl_explains(runner, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("PYTIME_DB", str(tmp_path / "pytime.db"))
    monkeypatch.setattr(activity_service.shutil, "which", lambda name: None)
    result = runner.invoke(time_cli, ["auto", "service", "install"])
    assert result.exit_code != 0
    assert "needs systemd" in result.stderr


def test_systemctl_failure_is_reported(runner, fake_systemd, monkeypatch):
    runner.invoke(time_cli, ["auto", "service", "install"])

    def failing(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "Unit pytime-auto.service failed.")

    monkeypatch.setattr(activity_service.subprocess, "run", failing)
    result = runner.invoke(time_cli, ["auto", "service", "start"])
    assert result.exit_code != 0
    assert "failed" in result.stderr
