"""Tests for pyps.

Uses real ``/proc`` (there is nothing sensible to mock it with) and a
throwaway child process as a stand-in for "some process on the system".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import click
import pytest

from pytoolbox import pyps
from pytoolbox.pyps import ProcessInfo, ps_cli

pytestmark = pytest.mark.skipif(not pyps.PROC.is_dir(), reason="pyps needs /proc (Linux/Termux only)")


@pytest.fixture
def marked_process():
    """A child process whose command line carries a unique marker, for --cmdline matching."""
    marker = f"pyps-test-{os.getpid()}-{time.time_ns()}"
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker])
    try:
        # Give the kernel a moment to populate /proc/<pid>/cmdline.
        deadline = time.time() + 2
        while time.time() < deadline:
            if marker in pyps.read_cmdline(proc.pid):
                break
            time.sleep(0.05)
        yield proc, marker
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


@pytest.fixture
def two_marked_processes():
    """Two child processes sharing one marker, for multi-match tests (e.g. ambiguous `info`)."""
    marker = f"pyps-test-multi-{os.getpid()}-{time.time_ns()}"
    procs = [
        subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", marker, tag])
        for tag in ("a", "b")
    ]
    try:
        deadline = time.time() + 2
        while time.time() < deadline:
            if all(marker in pyps.read_cmdline(p.pid) for p in procs):
                break
            time.sleep(0.05)
        yield procs, marker
    finally:
        for proc in procs:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


# ── /proc parsing ──────────────────────────────────────────────────

def test_list_processes_includes_self():
    pids = {p.pid for p in pyps.list_processes()}
    assert os.getpid() in pids


def test_load_process_returns_none_for_dead_pid():
    # PID 1 always exists on Linux; a huge PID essentially never does.
    assert pyps.load_process(2**30, os.sysconf("SC_CLK_TCK"), pyps.system_uptime()) is None


def test_total_memory_and_meminfo_are_positive():
    assert pyps.total_memory_bytes() > 0
    meminfo = pyps.read_meminfo()
    assert meminfo["MemTotal"] > 0
    assert "SwapTotal" in meminfo


def test_read_cmdline_and_status_for_self():
    cmdline = pyps.read_cmdline(os.getpid())
    assert "pytest" in cmdline or sys.executable.split("/")[-1] in cmdline
    status = pyps.read_status(os.getpid())
    assert "Name" in status and "PPid" in status


# ── matching / signals ─────────────────────────────────────────────

def test_find_matches_substring_and_exact():
    processes = [
        ProcessInfo(1, 0, 0, "sshd", "/usr/sbin/sshd -D", "S", 1, 0, 0, 0, 0.0, 1.0),
        ProcessInfo(2, 0, 0, "bash", "-bash", "S", 1, 0, 0, 0, 0.0, 1.0),
    ]
    assert [p.pid for p in pyps.find_matches(processes, "ssh", exact=False, include_cmdline=False)] == [1]
    assert pyps.find_matches(processes, "ssh", exact=True, include_cmdline=False) == []
    assert [p.pid for p in pyps.find_matches(processes, "sshd", exact=True, include_cmdline=False)] == [1]


def test_find_matches_cmdline():
    processes = [ProcessInfo(1, 0, 0, "python3", "python3 -m http.server", "S", 1, 0, 0, 0, 0.0, 1.0)]
    assert pyps.find_matches(processes, "http.server", exact=False, include_cmdline=False) == []
    assert [p.pid for p in pyps.find_matches(processes, "http.server", exact=False, include_cmdline=True)] == [1]


@pytest.mark.parametrize(("value", "expected"), [("9", 9), ("KILL", 9), ("sigterm", 15), ("TERM", 15)])
def test_parse_signal_accepts_names_and_numbers(value, expected):
    assert pyps.parse_signal(value) == expected


def test_parse_signal_rejects_garbage():
    with pytest.raises(click.ClickException):
        pyps.parse_signal("not-a-signal")


# ── CLI ─────────────────────────────────────────────────────────────

def test_top_json_lists_processes(runner):
    result = runner.invoke(ps_cli, ["top", "-n", "5", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert 1 <= len(rows) <= 5
    assert {"pid", "user", "rss", "swap", "cmd"} <= rows[0].keys()


def test_top_rejects_unknown_sort_key(runner):
    result = runner.invoke(ps_cli, ["top", "--sort", "bogus"])
    assert result.exit_code != 0


def test_sort_key_mem_and_rss_are_equivalent():
    """mem% is rss / total RAM -- a constant divisor -- so both keys must order identically."""
    processes = [
        ProcessInfo(1, 0, 0, "a", "a", "S", 1, 300, 0, 0, 0.0, 1.0),
        ProcessInfo(2, 0, 0, "b", "b", "S", 1, 100, 0, 0, 0.0, 1.0),
        ProcessInfo(3, 0, 0, "c", "c", "S", 1, 200, 0, 0, 0.0, 1.0),
    ]
    by_mem = sorted(processes, key=pyps.SORT_KEY_FUNCS["mem"], reverse=True)
    by_rss = sorted(processes, key=pyps.SORT_KEY_FUNCS["rss"], reverse=True)
    assert [p.pid for p in by_mem] == [p.pid for p in by_rss] == [1, 3, 2]


def test_top_sort_mem_accepted(runner):
    result = runner.invoke(ps_cli, ["top", "-n", "5", "--sort", "mem", "--json"])
    assert result.exit_code == 0, result.output


def test_top_sort_cpu_accepted(runner):
    result = runner.invoke(ps_cli, ["top", "-n", "5", "--sort", "cpu", "--json"])
    assert result.exit_code == 0, result.output


def test_find_locates_marked_process(runner, marked_process):
    proc, marker = marked_process
    result = runner.invoke(ps_cli, ["find", marker, "--cmdline", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert any(row["pid"] == proc.pid for row in rows)


def test_find_no_match_reports_and_exits_zero(runner):
    result = runner.invoke(ps_cli, ["find", "no-such-process-xyz-abc"])
    assert result.exit_code == 0
    assert "No process matches" in result.output


def test_find_no_match_json_is_empty_array(runner):
    result = runner.invoke(ps_cli, ["find", "no-such-process-xyz-abc", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == []


def test_find_sort_by_cpu(runner, marked_process):
    _proc, marker = marked_process
    result = runner.invoke(ps_cli, ["find", marker, "--cmdline", "--sort", "cpu", "--json"])
    assert result.exit_code == 0, result.output


def test_top_table_headers_are_percent_labelled(runner):
    result = runner.invoke(ps_cli, ["top", "-n", "1"])
    assert result.exit_code == 0, result.output
    assert "cpu%" in result.output
    assert "mem%" in result.output


def test_top_json_keeps_plain_field_names(runner):
    result = runner.invoke(ps_cli, ["top", "-n", "1", "--json"])
    assert result.exit_code == 0, result.output
    row = json.loads(result.stdout)[0]
    assert "cpu" in row and "mem" in row
    assert "cpu%" not in row


def test_info_for_self(runner):
    result = runner.invoke(ps_cli, ["info", str(os.getpid()), "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["pid"] == os.getpid()


def test_info_unknown_pid_fails(runner):
    result = runner.invoke(ps_cli, ["info", str(2**30)])
    assert result.exit_code != 0


def test_info_no_name_match_fails(runner):
    result = runner.invoke(ps_cli, ["info", "no-such-process-xyz-abc"])
    assert result.exit_code != 0


def test_info_by_unique_marker_resolves_one_process(runner, marked_process):
    proc, marker = marked_process
    result = runner.invoke(ps_cli, ["info", marker, "--cmdline", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["pid"] == proc.pid


def test_info_ambiguous_name_shows_a_table(runner, two_marked_processes):
    procs, marker = two_marked_processes
    result = runner.invoke(ps_cli, ["info", marker, "--cmdline"])
    assert result.exit_code == 0, result.output
    assert "processes match" in result.output
    for proc in procs:
        assert str(proc.pid) in result.output


def test_info_ambiguous_name_json_is_an_array(runner, two_marked_processes):
    procs, marker = two_marked_processes
    result = runner.invoke(ps_cli, ["info", marker, "--cmdline", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert {p.pid for p in procs} <= {row["pid"] for row in rows}


def test_free_json_has_mem_and_swap(runner):
    result = runner.invoke(ps_cli, ["free", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["mem"]["total"] > 0
    assert "swap" in payload


def test_kill_dry_run_leaves_process_alive(runner, marked_process):
    proc, marker = marked_process
    result = runner.invoke(ps_cli, ["kill", marker, "--cmdline", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert proc.poll() is None


def test_kill_by_pid_with_confirmation(runner, marked_process):
    proc, _marker = marked_process
    result = runner.invoke(ps_cli, ["kill", str(proc.pid), "-y"])
    assert result.exit_code == 0, result.output
    proc.wait(timeout=5)
    assert proc.returncode is not None


def test_kill_by_cmdline_marker(runner, marked_process):
    proc, marker = marked_process
    result = runner.invoke(ps_cli, ["kill", marker, "--cmdline", "-y"])
    assert result.exit_code == 0, result.output
    proc.wait(timeout=5)
    assert proc.returncode is not None


def test_kill_unknown_pid_fails(runner):
    result = runner.invoke(ps_cli, ["kill", str(2**30)])
    assert result.exit_code != 0


def test_kill_no_match_fails(runner):
    result = runner.invoke(ps_cli, ["kill", "no-such-process-xyz-abc"])
    assert result.exit_code != 0


# ── swap ────────────────────────────────────────────────────────────
# swapon/swapoff are never actually exec'd here: shutil.which and
# subprocess.run are monkeypatched so the suite can never touch real swap.

def test_read_swap_devices_returns_a_list():
    assert isinstance(pyps.read_swap_devices(), list)


def test_swap_command_json_is_a_list(runner):
    result = runner.invoke(ps_cli, ["swap", "--json"])
    assert result.exit_code == 0, result.output
    assert isinstance(json.loads(result.stdout), list)


def test_swapon_requires_device_or_all(runner):
    result = runner.invoke(ps_cli, ["swapon"])
    assert result.exit_code != 0
    assert "DEVICE" in result.output


def test_swapon_reports_missing_binary(runner, monkeypatch):
    monkeypatch.setattr(pyps.shutil, "which", lambda name: None)
    monkeypatch.setattr(pyps, "SWAP_BINARY_DIRS", ())
    result = runner.invoke(ps_cli, ["swapon", "--all"])
    assert result.exit_code != 0
    assert "was not found" in result.output


def test_find_swap_binary_falls_back_to_sbin(monkeypatch):
    monkeypatch.setattr(pyps.shutil, "which", lambda name: None)
    monkeypatch.setattr(pyps, "SWAP_BINARY_DIRS", ("/sbin", "/usr/sbin"))
    found = pyps._find_swap_binary("swapon")
    # Exercises the real filesystem: present on a normal Debian/Ubuntu box,
    # absent in a minimal container -- either outcome is a valid, non-crashing result.
    assert found is None or found.endswith("/swapon")


def test_swapon_dry_run_never_calls_subprocess(runner, monkeypatch):
    monkeypatch.setattr(pyps.shutil, "which", lambda name: f"/sbin/{name}")
    monkeypatch.setattr(
        pyps.subprocess, "run", lambda *a, **k: pytest.fail("dry-run must not execute anything")
    )
    result = runner.invoke(ps_cli, ["swapon", "--all", "--dry-run"])
    assert result.exit_code == 0, result.output


def test_swapoff_runs_confirmed_command(runner, monkeypatch):
    monkeypatch.setattr(pyps.shutil, "which", lambda name: f"/sbin/{name}")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(pyps.subprocess, "run", fake_run)
    result = runner.invoke(ps_cli, ["swapoff", "/swapfile", "-y"])
    assert result.exit_code == 0, result.output
    assert captured["cmd"] == ["/sbin/swapoff", "/swapfile"]


def test_swapon_surfaces_command_failure(runner, monkeypatch):
    monkeypatch.setattr(pyps.shutil, "which", lambda name: f"/sbin/{name}")
    monkeypatch.setattr(
        pyps.subprocess,
        "run",
        lambda cmd, **k: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="swapon: Permission denied"),
    )
    result = runner.invoke(ps_cli, ["swapon", "/swapfile", "-y"])
    assert result.exit_code != 0
    assert "Permission denied" in result.output


def test_swapoff_declined_confirmation_does_not_run(runner, monkeypatch):
    monkeypatch.setattr(pyps.shutil, "which", lambda name: f"/sbin/{name}")
    monkeypatch.setattr(
        pyps.subprocess, "run", lambda *a, **k: pytest.fail("must not run when confirmation is declined")
    )
    monkeypatch.setattr(pyps.console, "confirm", lambda *a, **k: False)
    result = runner.invoke(ps_cli, ["swapoff", "/swapfile"])
    assert result.exit_code == 0, result.output
