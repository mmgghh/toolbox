"""Tests for pyps.

Uses real ``/proc`` (there is nothing sensible to mock it with) and a
throwaway child process as a stand-in for "some process on the system".
"""

from __future__ import annotations

import json
import os
import socket
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


@pytest.fixture
def listening_process():
    """A child process listening on an OS-assigned TCP port on 127.0.0.1."""
    script = (
        "import socket, time\n"
        "s = socket.socket()\n"
        "s.bind(('127.0.0.1', 0))\n"
        "s.listen(1)\n"
        "print(s.getsockname()[1], flush=True)\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    try:
        port = int(proc.stdout.readline().strip())
        yield proc, port
    finally:
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


def test_parse_signal_rejects_a_digit_lookalike_cleanly():
    """A superscript passes isdigit() but int() cannot parse it; this must
    raise a clean ClickException rather than an unhandled ValueError."""
    with pytest.raises(click.ClickException):
        pyps.parse_signal("²")


def test_parse_signal_rejects_non_ascii_digits():
    """A signal number is always plain ASCII; Persian digits fall through to
    the name lookup and are rejected there, not misread as a signal number."""
    with pytest.raises(click.ClickException):
        pyps.parse_signal("۹")


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


def test_kill_target_that_looks_like_a_digit_fails_cleanly(runner):
    """A superscript passes isdigit() but int() cannot parse it; TARGET must
    fall through to the name match and fail cleanly, not crash with a
    ValueError traceback."""
    result = runner.invoke(ps_cli, ["kill", "²"])
    assert result.exit_code != 0
    assert "No process matches" in result.stderr


def test_info_target_that_looks_like_a_digit_fails_cleanly(runner):
    """A superscript passes isdigit() but int() cannot parse it; TARGET must
    fall through to the name match and fail cleanly, not crash with a
    ValueError traceback."""
    result = runner.invoke(ps_cli, ["info", "²"])
    assert result.exit_code != 0
    assert "No process matches" in result.stderr


def test_kill_no_match_fails(runner):
    result = runner.invoke(ps_cli, ["kill", "no-such-process-xyz-abc"])
    assert result.exit_code != 0


def test_kill_rejects_both_target_and_port(runner):
    result = runner.invoke(ps_cli, ["kill", "12345", "--port", "8080"])
    assert result.exit_code != 0
    assert "not both or neither" in result.output.lower()


def test_kill_rejects_neither_target_nor_port(runner):
    result = runner.invoke(ps_cli, ["kill"])
    assert result.exit_code != 0
    assert "not both or neither" in result.output.lower()


def test_kill_rejects_exact_with_port(runner):
    result = runner.invoke(ps_cli, ["kill", "--port", "3000", "--exact"])
    assert result.exit_code != 0
    assert "--port" in result.output


def test_kill_rejects_cmdline_with_port(runner):
    result = runner.invoke(ps_cli, ["kill", "--port", "3000", "--cmdline"])
    assert result.exit_code != 0
    assert "--port" in result.output


def test_kill_by_port_dry_run_leaves_process_alive(runner, listening_process):
    proc, port = listening_process
    result = runner.invoke(ps_cli, ["kill", "--port", str(port), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert proc.poll() is None


def test_kill_by_port_with_confirmation(runner, listening_process):
    proc, port = listening_process
    result = runner.invoke(ps_cli, ["kill", "--port", str(port), "-y"])
    assert result.exit_code == 0, result.output
    proc.wait(timeout=5)
    assert proc.returncode is not None


def test_kill_by_port_no_match_fails(runner):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    result = runner.invoke(ps_cli, ["kill", "--port", str(port)])
    assert result.exit_code != 0
    assert "no process" in result.output.lower()


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


# ── ports ───────────────────────────────────────────────────────────

def test_decode_hex_address_ipv4():
    assert pyps._decode_hex_address("0100007F") == "127.0.0.1"


def test_decode_hex_address_ipv6_mapped_v4():
    assert pyps._decode_hex_address("0000000000000000FFFF00000100007F") == "::ffff:127.0.0.1"


def test_parse_net_line_tcp_listen():
    line = (
        "   0: 0100007F:0439 00000000:0000 0A 00000000:00000000 00:00000000 "
        "00000000  1000        0 152822844 2 00000000 100 0 0 10 0"
    )
    assert pyps._parse_net_line(line, "tcp") == ("127.0.0.1", 1081, "LISTEN", "152822844")


def test_parse_net_line_udp_has_no_state_label():
    line = (
        " 5218: 1100027F:0035 00000000:0000 07 00000000:00000000 00:00000000 "
        "00000000     0        0 151389978 2 00000000 0"
    )
    assert pyps._parse_net_line(line, "udp") == ("127.2.0.17", 53, "", "151389978")


def test_parse_net_line_returns_none_for_header_row():
    header = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode"
    assert pyps._parse_net_line(header, "tcp") is None


def test_build_inode_pid_map_finds_own_listening_socket():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        inode = str(os.fstat(sock.fileno()).st_ino)
        mapping = pyps._build_inode_pid_map()
        assert os.getpid() in mapping.get(inode, [])
    finally:
        sock.close()


def test_list_ports_finds_own_listening_socket():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        matches = [e for e in pyps.list_ports() if e.protocol == "tcp" and e.port == port]
        assert len(matches) == 1
        assert matches[0].state == "LISTEN"
        assert os.getpid() in matches[0].pids
    finally:
        sock.close()


def test_list_ports_excludes_non_listening_tcp_by_default():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.create_connection(server.getsockname())
    try:
        client_port = client.getsockname()[1]

        deadline = time.time() + 2
        entries_all: list = []
        while time.time() < deadline:
            entries_all = pyps.list_ports(listen_only=False)
            if any(e.port == client_port and e.state == "ESTABLISHED" for e in entries_all):
                break
            time.sleep(0.05)
        assert any(e.port == client_port and e.state == "ESTABLISHED" for e in entries_all)

        assert not any(e.port == client_port for e in pyps.list_ports())
    finally:
        client.close()
        server.close()


def test_list_ports_finds_own_ipv6_listening_socket():
    try:
        sock = socket.socket(socket.AF_INET6)
        sock.bind(("::1", 0))
    except OSError:
        pytest.skip("IPv6 loopback not available")
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        matches = [e for e in pyps.list_ports() if e.protocol == "tcp" and e.port == port]
        assert len(matches) == 1
        assert matches[0].address == "::1"
        assert matches[0].state == "LISTEN"
        assert os.getpid() in matches[0].pids
    finally:
        sock.close()


def test_list_ports_resolves_all_pids_sharing_one_listening_socket():
    """A fork()'d child inherits the parent's listening fd -- both PIDs share one
    socket inode, the same way a preforked server's master and workers do."""
    script = (
        "import os, socket, time\n"
        "s = socket.socket()\n"
        "s.bind(('127.0.0.1', 0))\n"
        "s.listen(1)\n"
        "print(s.getsockname()[1], flush=True)\n"
        "child = os.fork()\n"
        "if child == 0:\n"
        "    time.sleep(60)\n"
        "else:\n"
        "    print(child, flush=True)\n"
        "    time.sleep(60)\n"
    )
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
    child_pid = None
    try:
        port = int(proc.stdout.readline().strip())
        child_pid = int(proc.stdout.readline().strip())

        deadline = time.time() + 2
        pids: set = set()
        while time.time() < deadline:
            matches = [e for e in pyps.list_ports() if e.protocol == "tcp" and e.port == port]
            if matches and {proc.pid, child_pid} <= set(matches[0].pids):
                pids = set(matches[0].pids)
                break
            time.sleep(0.05)
        assert {proc.pid, child_pid} <= pids
    finally:
        if child_pid is not None:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_ports_json_lists_a_bound_port(runner):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        result = runner.invoke(ps_cli, ["ports", str(port), "--json"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)
        assert len(rows) == 1
        assert rows[0]["port"] == port
        assert rows[0]["proto"] == "tcp"
        assert rows[0]["state"] == "LISTEN"
        assert rows[0]["pid"] == str(os.getpid())
    finally:
        sock.close()


def test_ports_not_in_use_is_reported_and_exits_nonzero(runner):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    result = runner.invoke(ps_cli, ["ports", str(port)])
    assert result.exit_code != 0
    assert "not in use" in result.output.lower()


def test_ports_pid_filter_restricts_rows(runner):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        result = runner.invoke(ps_cli, ["ports", "--json", "-p", str(os.getpid())])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)
        assert any(r["port"] == port for r in rows)
        assert all(r["pid"] == str(os.getpid()) for r in rows)
    finally:
        sock.close()


def test_ports_process_filter_matches_by_name(runner):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        name = pyps.read_status(os.getpid())["Name"]
        result = runner.invoke(ps_cli, ["ports", "--json", "--process", name[:3]])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)
        assert any(r["port"] == port for r in rows)
    finally:
        sock.close()


def test_ports_process_filter_excludes_non_matching(runner):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        result = runner.invoke(ps_cli, ["ports", "--json", "--process", "no-such-process-xyz"])
        assert result.exit_code == 0, result.output
        rows = json.loads(result.stdout)
        assert not any(r["port"] == port for r in rows)
    finally:
        sock.close()


def test_ports_tcp_and_udp_are_mutually_exclusive(runner):
    result = runner.invoke(ps_cli, ["ports", "--tcp", "--udp"])
    assert result.exit_code != 0


def test_ports_udp_only_excludes_our_tcp_socket(runner):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        port = sock.getsockname()[1]
        result = runner.invoke(ps_cli, ["ports", str(port), "--udp"])
        assert result.exit_code != 0
    finally:
        sock.close()


def test_ports_all_flag_includes_established(runner):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.create_connection(server.getsockname())
    try:
        client_port = client.getsockname()[1]
        deadline = time.time() + 2
        found = False
        while time.time() < deadline and not found:
            result = runner.invoke(ps_cli, ["ports", str(client_port), "--all", "--json"])
            rows = json.loads(result.stdout) if result.exit_code == 0 else []
            found = any(r["state"] == "ESTABLISHED" for r in rows)
            if not found:
                time.sleep(0.05)
        assert found
    finally:
        client.close()
        server.close()
