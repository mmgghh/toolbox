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


# ── geolocation ─────────────────────────────────────────────────────

#: One response per provider, all describing the same address.
GEO_RESPONSES = {
    "ipwho.is": {
        "success": True,
        "ip": "1.1.1.1",
        "city": "Sydney",
        "region": "New South Wales",
        "country": "Australia",
        "country_code": "AU",
        "latitude": -33.86,
        "longitude": 151.2,
        "timezone": {"id": "Australia/Sydney"},
        "connection": {"asn": 13335, "isp": "Cloudflare"},
    },
    "ipapi.co": {
        "ip": "1.1.1.1",
        "city": "Sydney",
        "region": "New South Wales",
        "country_name": "Australia",
        "country_code": "AU",
        "latitude": -33.86,
        "longitude": 151.2,
        "timezone": "Australia/Sydney",
        "asn": "AS13335",
        "org": "Cloudflare",
    },
    "freeipapi.com": {
        "ipAddress": "1.1.1.1",
        "cityName": "Sydney",
        "regionName": "New South Wales",
        "countryName": "Australia",
        "countryCode": "AU",
        "latitude": -33.86,
        "longitude": 151.2,
        "timeZone": "Australia/Sydney",
    },
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _fake_get(payloads):
    """A requests.get stand-in serving one payload per host, 500ing otherwise."""
    calls = []

    def get(url, timeout=None):
        calls.append(url)
        for host, payload in payloads.items():
            if host in url:
                return _FakeResponse(payload)
        raise OSError("unreachable")

    get.calls = calls
    return get


@pytest.mark.parametrize("provider", sorted(GEO_RESPONSES))
def test_every_provider_yields_the_same_shape(monkeypatch, provider):
    """Whichever service answers, the caller sees one vocabulary."""
    monkeypatch.setattr("requests.get", _fake_get({provider: GEO_RESPONSES[provider]}))
    located = pynet.geo_lookup("1.1.1.1")
    assert located["city"] == "Sydney"
    assert located["country"] == "Australia"
    assert located["country_code"] == "AU"
    assert located["timezone"] == "Australia/Sydney"
    assert (located["latitude"], located["longitude"]) == (-33.86, 151.2)


def test_geo_lookup_falls_through_to_the_next_provider(monkeypatch):
    get = _fake_get({"freeipapi.com": GEO_RESPONSES["freeipapi.com"]})
    monkeypatch.setattr("requests.get", get)
    assert pynet.geo_lookup("1.1.1.1")["country"] == "Australia"
    assert len(get.calls) == len(pynet.GEO_ENDPOINTS)


def test_geo_lookup_skips_a_provider_that_answers_nothing_useful(monkeypatch):
    """A private address gets a well-formed payload with no location in it."""
    empty = {"success": True, "ip": "10.0.0.1", "country": "", "country_code": ""}
    get = _fake_get({"ipwho.is": empty, "ipapi.co": GEO_RESPONSES["ipapi.co"]})
    monkeypatch.setattr("requests.get", get)
    assert pynet.geo_lookup("10.0.0.1")["country"] == "Australia"


def test_geo_lookup_returns_none_when_offline(monkeypatch):
    monkeypatch.setattr("requests.get", _fake_get({}))
    assert pynet.geo_lookup("1.1.1.1") is None


def test_ip_geo_reports_the_location_of_an_address(runner, monkeypatch):
    monkeypatch.setattr("requests.get", _fake_get({"ipwho.is": GEO_RESPONSES["ipwho.is"]}))
    result = runner.invoke(net_cli, ["ip", "1.1.1.1"])
    assert result.exit_code == 0, result.output
    assert "Sydney, New South Wales, Australia (AU)" in result.stdout
    assert "AS13335 Cloudflare" in result.stdout


def test_ip_geo_adds_a_location_to_the_public_address(runner, monkeypatch):
    monkeypatch.setattr(pynet, "public_ip", lambda timeout=5.0: "1.1.1.1")
    monkeypatch.setattr("requests.get", _fake_get({"ipwho.is": GEO_RESPONSES["ipwho.is"]}))
    payload = json.loads(runner.invoke(net_cli, ["ip", "--public", "--geo", "--json"]).stdout)
    assert payload["public"] == "1.1.1.1"
    assert payload["location"]["city"] == "Sydney"


def test_ip_still_works_when_the_location_lookup_fails(runner, monkeypatch):
    """--geo is an addition; losing it must not cost you the addresses."""
    monkeypatch.setattr(pynet, "public_ip", lambda timeout=5.0: "203.0.113.7")
    monkeypatch.setattr("requests.get", _fake_get({}))
    result = runner.invoke(net_cli, ["ip", "--geo"])
    assert result.exit_code == 0, result.output
    assert "203.0.113.7" in result.stdout
    assert "Could not look up the location" in result.stderr


def test_ip_geo_of_an_explicit_address_exits_nonzero_when_it_fails(runner, monkeypatch):
    monkeypatch.setattr("requests.get", _fake_get({}))
    assert runner.invoke(net_cli, ["ip", "1.1.1.1"]).exit_code == 1


def test_ip_geo_resolves_a_hostname_first(runner, monkeypatch):
    monkeypatch.setattr(pynet, "resolve_host", lambda host, family=0: ["1.1.1.1"])
    monkeypatch.setattr("requests.get", _fake_get({"ipwho.is": GEO_RESPONSES["ipwho.is"]}))
    payload = json.loads(runner.invoke(net_cli, ["ip", "one.one.one.one", "--json"]).stdout)
    assert payload["address"] == "1.1.1.1"
    assert payload["location"]["country_code"] == "AU"


def test_ip_rejects_locating_a_local_interface(runner):
    result = runner.invoke(net_cli, ["ip", "--local", "--geo"])
    assert result.exit_code != 0


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
