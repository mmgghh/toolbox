"""Tests for pynet.

Everything that would touch the network is either exercised against a local
socket or monkeypatched, so the suite runs offline.
"""

from __future__ import annotations

import json
import socket
import threading
from contextlib import closing

import click
import pytest

from pytoolbox import pynet
from pytoolbox.pynet import net_cli


@pytest.fixture
def open_port():
    """A listening TCP socket on localhost; yields its port number."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]

    def accept_loop():
        try:
            while True:
                conn, _ = server.accept()
                conn.close()
        except OSError:
            return

    thread = threading.Thread(target=accept_loop, daemon=True)
    thread.start()
    yield port
    server.close()


@pytest.fixture
def closed_port():
    """A port number nothing is listening on."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ── parse_ports ─────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("80", [80]),
        ("80,443", [80, 443]),
        ("8000-8003", [8000, 8001, 8002, 8003]),
        ("443,80", [80, 443]),
        ("8003-8000", [8000, 8001, 8002, 8003]),
        ("80, 443", [80, 443]),
        ("80,80", [80]),
    ],
)
def test_parse_ports(spec, expected):
    assert pynet.parse_ports(spec) == expected


@pytest.mark.parametrize("spec", ["", "abc", "0", "70000", "1-70000", "5-abc"])
def test_parse_ports_rejects_bad_input(spec):
    with pytest.raises(click.ClickException):
        pynet.parse_ports(spec)


# ── probing ─────────────────────────────────────────────────────────

def test_probe_port_open(open_port):
    result = pynet.probe_port("127.0.0.1", open_port, timeout=2)
    assert result.open
    assert result.latency_ms is not None


def test_probe_port_closed(closed_port):
    result = pynet.probe_port("127.0.0.1", closed_port, timeout=0.5)
    assert not result.open
    assert result.latency_ms is None


def test_probe_ports_returns_sorted_results(open_port, closed_port):
    results = pynet.probe_ports("127.0.0.1", [closed_port, open_port], timeout=0.5)
    assert [r.port for r in results] == sorted([closed_port, open_port])


def test_port_result_row_names_known_services():
    row = pynet.PortResult(port=443, open=True, latency_ms=1.0).as_row()
    assert row["service"] == "https"
    assert row["state"] == "open"


def test_resolve_localhost():
    assert "127.0.0.1" in pynet.resolve_host("localhost", socket.AF_INET)


def test_resolve_unknown_host_raises():
    with pytest.raises(click.ClickException):
        pynet.resolve_host("no-such-host.invalid")


def test_tcp_ping_counts_replies(open_port):
    timings = pynet.tcp_ping("127.0.0.1", open_port, count=2, timeout=1)
    assert len(timings) == 2
    assert all(t is not None for t in timings)


def test_referral_server_extraction():
    assert pynet._referral_server("refer: whois.verisign-grs.com\n") == "whois.verisign-grs.com"
    assert pynet._referral_server("no referral here") is None


# ── CLI ─────────────────────────────────────────────────────────────

def test_port_command_open(runner, open_port):
    result = runner.invoke(net_cli, ["port", "127.0.0.1", str(open_port), "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert rows[0]["state"] == "open"


def test_port_command_exits_nonzero_when_all_closed(runner, closed_port):
    result = runner.invoke(net_cli, ["port", "127.0.0.1", str(closed_port), "-t", "0.5"])
    assert result.exit_code == 1


def test_dns_command_for_an_ip(runner):
    result = runner.invoke(net_cli, ["dns", "127.0.0.1", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.output)["address"] == "127.0.0.1"


def test_dns_command_for_a_name(runner):
    result = runner.invoke(net_cli, ["dns", "localhost", "-4"])
    assert result.exit_code == 0
    assert "127.0.0.1" in result.output


def test_dns_rejects_both_families(runner):
    result = runner.invoke(net_cli, ["dns", "localhost", "-4", "-6"])
    assert result.exit_code != 0


def test_url_encode_and_decode(runner):
    assert runner.invoke(net_cli, ["url", "a b"]).output.strip() == "a%20b"
    assert runner.invoke(net_cli, ["url", "a%20b", "-d"]).output.strip() == "a b"


def test_url_parse_json(runner):
    result = runner.invoke(net_cli, ["url", "https://example.com:8443/p?q=1#f", "--parse", "--json"])
    payload = json.loads(result.output)
    assert payload["host"] == "example.com"
    assert payload["port"] == 8443
    assert payload["query"] == {"q": "1"}


def test_ip_local_only(runner):
    result = runner.invoke(net_cli, ["ip", "--local"])
    assert result.exit_code == 0


def test_ip_rejects_conflicting_flags(runner):
    result = runner.invoke(net_cli, ["ip", "--local", "--public"])
    assert result.exit_code != 0


def test_ip_public_uses_the_first_working_endpoint(runner, monkeypatch):
    monkeypatch.setattr(pynet, "public_ip", lambda timeout=5.0: "203.0.113.7")
    result = runner.invoke(net_cli, ["ip", "--public", "--json"])
    assert json.loads(result.output)["public"] == "203.0.113.7"


def test_ping_tcp_mode(runner, open_port):
    result = runner.invoke(
        net_cli, ["ping", "127.0.0.1", "--tcp", "-p", str(open_port), "-c", "2", "--json"]
    )
    assert result.exit_code == 0, result.output
    # Progress notes go to stderr; only stdout carries the payload.
    payload = json.loads(result.stdout)
    assert payload["received"] == 2


def test_ping_tcp_failure_exits_nonzero(runner, closed_port):
    result = runner.invoke(
        net_cli, ["ping", "127.0.0.1", "--tcp", "-p", str(closed_port), "-c", "1", "-t", "0.5"]
    )
    assert result.exit_code == 1


def test_scan_reports_open_ports(runner, open_port):
    result = runner.invoke(
        net_cli, ["scan", "127.0.0.1", "-p", str(open_port), "-t", "1", "--json"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["state"] == "open"
