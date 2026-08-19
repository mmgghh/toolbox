"""Network diagnostics that work anywhere, including unrooted Termux.

Everything here uses ordinary user-space TCP/UDP sockets plus ``requests``.
Nothing needs root, raw sockets or extra packages, so the same commands behave
identically on a laptop and on a phone. Where a system binary would be nicer
(``ping``, ``whois``) it is used when present and transparently replaced by a
pure-Python fallback when it is not.
"""

from __future__ import annotations

import ipaddress
import shutil
import socket
import subprocess
import time
import urllib.parse
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import click

from pytoolbox.core import console
from pytoolbox.core.options import (
    CONTEXT_SETTINGS,
    AliasedGroup,
    json_option,
    verbose_option,
    version_option,
)

#: Queried in order until one answers; each returns the caller's IP as plain text.
PUBLIC_IP_ENDPOINTS = (
    "https://api.ipify.org",
    "https://ifconfig.me/ip",
    "https://icanhazip.com",
)

#: Ports probed by ``pynet scan`` when no explicit list is given.
COMMON_PORTS: dict[int, str] = {
    21: "ftp",
    22: "ssh",
    23: "telnet",
    25: "smtp",
    53: "dns",
    80: "http",
    110: "pop3",
    143: "imap",
    443: "https",
    445: "smb",
    587: "smtp-tls",
    993: "imaps",
    995: "pop3s",
    1080: "socks",
    1433: "mssql",
    3306: "mysql",
    3389: "rdp",
    5432: "postgres",
    5900: "vnc",
    6379: "redis",
    8000: "http-alt",
    8080: "http-proxy",
    8443: "https-alt",
    9998: "pyssh-tunnel",
    27017: "mongodb",
}

DEFAULT_TIMEOUT = 3.0
DEFAULT_WORKERS = 64
WHOIS_PORT = 43
IANA_WHOIS = "whois.iana.org"


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class PortResult:
    """Outcome of one TCP connection attempt."""

    port: int
    open: bool
    latency_ms: Optional[float]
    service: str = ""

    def as_row(self) -> dict:
        """Row form used by table/JSON output."""
        return {
            "port": self.port,
            "service": self.service or COMMON_PORTS.get(self.port, ""),
            "state": "open" if self.open else "closed",
            "latency_ms": f"{self.latency_ms:.1f}" if self.latency_ms is not None else "",
        }


def parse_ports(spec: str) -> list[int]:
    """Expand ``"80,443,8000-8010"`` into a sorted list of port numbers."""
    ports: set[int] = set()
    for chunk in spec.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            start_raw, _, end_raw = chunk.partition("-")
            try:
                start, end = int(start_raw), int(end_raw)
            except ValueError as exc:
                raise click.ClickException(f"Invalid port range: {chunk!r}") from exc
            if start > end:
                start, end = end, start
            if not (1 <= start <= 65535 and 1 <= end <= 65535):
                raise click.ClickException(f"Port range out of bounds: {chunk!r}")
            ports.update(range(start, end + 1))
        else:
            try:
                port = int(chunk)
            except ValueError as exc:
                raise click.ClickException(f"Invalid port: {chunk!r}") from exc
            if not 1 <= port <= 65535:
                raise click.ClickException(f"Port out of bounds: {port}")
            ports.add(port)
    if not ports:
        raise click.ClickException("No ports given.")
    return sorted(ports)


def probe_port(host: str, port: int, timeout: float = DEFAULT_TIMEOUT) -> PortResult:
    """Try to open a TCP connection, measuring how long it took."""
    started = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = (time.perf_counter() - started) * 1000
            return PortResult(port=port, open=True, latency_ms=elapsed)
    except OSError:
        return PortResult(port=port, open=False, latency_ms=None)


def probe_ports(
    host: str,
    ports: Sequence[int],
    timeout: float = DEFAULT_TIMEOUT,
    workers: int = DEFAULT_WORKERS,
) -> list[PortResult]:
    """Probe many ports concurrently.

    The worker count is capped by the number of ports so that a two-port check
    does not spin up 64 threads on a phone.
    """
    pool_size = max(1, min(workers, len(ports)))
    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        results = list(pool.map(lambda p: probe_port(host, p, timeout), ports))
    return sorted(results, key=lambda r: r.port)


def resolve_host(host: str, family: int = 0) -> list[str]:
    """Return every IP address a hostname resolves to, without duplicates."""
    try:
        infos = socket.getaddrinfo(host, None, family=family, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise click.ClickException(f"Could not resolve {host!r}: {exc.strerror or exc}") from exc
    seen: list[str] = []
    for info in infos:
        address = info[4][0]
        if address not in seen:
            seen.append(address)
    return seen


def reverse_lookup(address: str) -> Optional[str]:
    """PTR lookup for an IP address, or ``None`` when there is no record."""
    try:
        return socket.gethostbyaddr(address)[0]
    except (OSError, socket.herror):
        return None


def local_addresses() -> list[dict]:
    """Best-effort list of this machine's own IP addresses.

    Opening a UDP socket toward a public address makes the kernel pick the
    interface it would really use, which is the only portable way to learn the
    outbound address -- ``gethostbyname(gethostname())`` returns 127.0.1.1 on
    most Linux systems and fails outright on Android.
    """
    addresses: list[dict] = []
    for family, probe in ((socket.AF_INET, ("8.8.8.8", 80)), (socket.AF_INET6, ("2001:4860:4860::8888", 80))):
        sock = socket.socket(family, socket.SOCK_DGRAM)
        try:
            sock.settimeout(1.0)
            sock.connect(probe)
            addresses.append({"family": "IPv4" if family == socket.AF_INET else "IPv6", "address": sock.getsockname()[0]})
        except OSError:
            continue
        finally:
            sock.close()
    return addresses


def public_ip(timeout: float = 5.0) -> Optional[str]:
    """Ask a public echo service for this machine's outside address."""
    import requests

    for url in PUBLIC_IP_ENDPOINTS:
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
        except Exception:  # noqa: BLE001 - try the next endpoint
            continue
        candidate = response.text.strip()
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return candidate
    return None


def _network_name(asn, name) -> str:
    """``"AS13335 Cloudflare"`` from whichever halves a provider supplied."""
    asn_text = f"AS{asn}" if isinstance(asn, int) else str(asn or "").strip()
    return " ".join(part for part in (asn_text, str(name or "").strip()) if part)


def _parse_ipwho(data: dict) -> Optional[dict]:
    """Normalise a response from ipwho.is."""
    if data.get("success") is False:
        return None
    connection = data.get("connection") or {}
    zone = data.get("timezone") or {}
    return {
        "ip": data.get("ip") or "",
        "city": data.get("city") or "",
        "region": data.get("region") or "",
        "country": data.get("country") or "",
        "country_code": data.get("country_code") or "",
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": (zone.get("id") if isinstance(zone, dict) else zone) or "",
        "network": _network_name(connection.get("asn"), connection.get("isp") or connection.get("org")),
    }


def _parse_ipapi_co(data: dict) -> Optional[dict]:
    """Normalise a response from ipapi.co."""
    if data.get("error"):
        return None
    return {
        "ip": data.get("ip") or "",
        "city": data.get("city") or "",
        "region": data.get("region") or "",
        "country": data.get("country_name") or "",
        "country_code": data.get("country_code") or "",
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": data.get("timezone") or "",
        "network": _network_name(data.get("asn"), data.get("org")),
    }


def _parse_freeipapi(data: dict) -> Optional[dict]:
    """Normalise a response from freeipapi.com."""
    if not data.get("ipAddress"):
        return None
    return {
        "ip": data.get("ipAddress") or "",
        "city": data.get("cityName") or "",
        "region": data.get("regionName") or "",
        "country": data.get("countryName") or "",
        "country_code": data.get("countryCode") or "",
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": data.get("timeZone") or "",
        "network": "",
    }


#: Geolocation providers, tried in order. All free, keyless and HTTPS-only, so
#: the address being looked up is not sent in the clear.
GEO_ENDPOINTS = (
    ("https://ipwho.is/{ip}", _parse_ipwho),
    ("https://ipapi.co/{ip}/json/", _parse_ipapi_co),
    ("https://freeipapi.com/api/json/{ip}", _parse_freeipapi),
)


def geo_lookup(address: str, timeout: float = 5.0) -> Optional[dict]:
    """Approximate location of ``address``, or ``None`` when nobody answers.

    Returns rather than raises when every provider fails: an offline machine
    should still see the addresses the caller has already collected. The answer
    is where the address block is *registered*, which is regularly the ISP's
    city rather than anyone's actual location -- treat it as a hint.
    """
    import requests

    target = urllib.parse.quote(address.strip(), safe="")
    if not target:
        return None
    for template, parse in GEO_ENDPOINTS:
        try:
            response = requests.get(template.format(ip=target), timeout=timeout)
            response.raise_for_status()
            data = response.json()
        except Exception:  # noqa: BLE001 - try the next provider
            continue
        if not isinstance(data, dict):
            continue
        located = parse(data)
        # A provider that answers "unknown" for a private or reserved address
        # returns a well-formed payload with nothing in it; keep looking.
        if located and (located["country"] or located["country_code"]):
            return located
    return None


def format_location(located: dict) -> list[str]:
    """Printable lines for one geolocation result, omitting what is unknown."""
    place = ", ".join(part for part in (located["city"], located["region"], located["country"]) if part)
    if place and located["country_code"]:
        place = f"{place} ({located['country_code']})"

    lines = []
    if place:
        lines.append(("location", place))
    latitude, longitude = located["latitude"], located["longitude"]
    if latitude not in (None, "") and longitude not in (None, ""):
        lines.append(("coords", f"{latitude}, {longitude}"))
    if located["timezone"]:
        lines.append(("timezone", located["timezone"]))
    if located["network"]:
        lines.append(("network", located["network"]))
    return [f"{label:<8} {value}" for label, value in lines]


def tcp_ping(host: str, port: int, count: int, timeout: float) -> list[Optional[float]]:
    """Time repeated TCP handshakes -- the fallback when ICMP is unavailable."""
    timings: list[Optional[float]] = []
    for index in range(count):
        result = probe_port(host, port, timeout)
        timings.append(result.latency_ms if result.open else None)
        if index + 1 < count:
            time.sleep(0.3)
    return timings


def whois_query(domain: str, server: str = IANA_WHOIS, timeout: float = 10.0) -> str:
    """Run a WHOIS query over the raw port-43 protocol."""
    # Internationalised domains have to be punycoded; WHOIS servers speak ASCII.
    query = domain if domain.isascii() else domain.encode("idna").decode("ascii")
    try:
        with socket.create_connection((server, WHOIS_PORT), timeout=timeout) as sock:
            sock.sendall(f"{query}\r\n".encode("ascii"))
            chunks = []
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
    except OSError as exc:
        raise click.ClickException(f"WHOIS query to {server} failed: {exc}") from exc
    return b"".join(chunks).decode("utf-8", errors="replace")


def _referral_server(response: str) -> Optional[str]:
    for line in response.splitlines():
        lowered = line.lower()
        if lowered.startswith(("refer:", "whois:", "registrar whois server:")):
            _, _, value = line.partition(":")
            value = value.strip()
            if value:
                return value
    return None


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


@click.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
@version_option
def net_cli() -> None:
    """Network diagnostics: addresses, DNS, ports, HTTP, WHOIS, file serving.

    \b
    Examples:
      pynet ip
      pynet dns example.com
      pynet port example.com 80 443
      pynet scan 192.168.1.1
      pynet ping example.com
      pynet http https://example.com -v
      pynet serve ./share --port 8000
      pynet whois example.com
    """


@net_cli.command("ip")
@click.argument("address", required=False)
@click.option("--local", "local_only", is_flag=True, help="Show only local interface addresses.")
@click.option("--public", "public_only", is_flag=True, help="Show only the public address.")
@click.option("--geo", is_flag=True, help="Also look up where the address is (needs internet).")
@click.option("--timeout", default=5.0, show_default=True, help="Seconds to wait for the lookup.")
@json_option
def ip_command(
    address: Optional[str], local_only: bool, public_only: bool, geo: bool, timeout: float, as_json: bool
) -> None:
    """Show this machine's local and public IP addresses.

    \b
    With ADDRESS (an IP or a hostname), locate that address instead. The
    location lookup is opt-in and never required: without --geo, and whenever
    the lookup fails, the addresses are still printed.

    \b
    Examples:
      pynet ip
      pynet ip --local
      pynet ip --public --json
      pynet ip --geo
      pynet ip 1.1.1.1
    """
    if local_only and public_only:
        raise click.ClickException("Use either --local or --public, not both.")
    if local_only and (geo or address):
        raise click.ClickException("--local shows this machine's own interfaces; there is nothing to locate.")
    if address and public_only:
        raise click.ClickException("Pass an address or --public, not both.")

    payload: dict = {}
    if address:
        # A hostname is resolved first, so `pynet ip --geo example.com` works
        # the same way every other pynet command accepts a name.
        try:
            ipaddress.ip_address(address)
            target = address
        except ValueError:
            target = resolve_host(address)[0]
        payload["address"] = target
        payload["location"] = geo_lookup(target, timeout)
    else:
        if not public_only:
            payload["local"] = local_addresses()
        if not local_only:
            payload["public"] = public_ip(timeout)
        if geo and payload.get("public"):
            payload["location"] = geo_lookup(payload["public"], timeout)

    if as_json:
        console.emit_json(payload)
    else:
        for entry in payload.get("local", []):
            console.result(f"{entry['family']:<5} {entry['address']}")
        if "address" in payload:
            console.result(f"address  {payload['address']}")
        if "public" in payload:
            if payload["public"]:
                console.result(f"public {payload['public']}")
            else:
                console.warn("Could not determine the public IP address (no internet?).")
        if payload.get("location"):
            for line in format_location(payload["location"]):
                console.result(line)
        elif "location" in payload:
            where = payload.get("address") or payload.get("public") or "this address"
            console.warn(f"Could not look up the location of {where} (no internet?).")

    # Only an explicit lookup that produced nothing is a failure: --geo is an
    # addition to `pynet ip`, and must not break it when there is no internet.
    if address and not payload.get("location"):
        raise SystemExit(1)


@net_cli.command()
@click.argument("host")
@click.option("-4", "ipv4_only", is_flag=True, help="Resolve IPv4 addresses only.")
@click.option("-6", "ipv6_only", is_flag=True, help="Resolve IPv6 addresses only.")
@click.option("-r", "--reverse", is_flag=True, help="Also do a PTR lookup for each address.")
@json_option
def dns(host: str, ipv4_only: bool, ipv6_only: bool, reverse: bool, as_json: bool) -> None:
    """Resolve HOST to IP addresses (or an IP back to a name).

    \b
    Examples:
      pynet dns example.com
      pynet dns example.com -4 --reverse
      pynet dns 1.1.1.1
    """
    if ipv4_only and ipv6_only:
        raise click.ClickException("Use either -4 or -6, not both.")

    try:
        ipaddress.ip_address(host)
        is_address = True
    except ValueError:
        is_address = False

    if is_address:
        name = reverse_lookup(host)
        if as_json:
            console.emit_json({"address": host, "hostname": name})
        elif name:
            console.result(f"{host} -> {name}")
        else:
            console.result(f"{host} has no PTR record")
        return

    family = socket.AF_INET if ipv4_only else socket.AF_INET6 if ipv6_only else 0
    addresses = resolve_host(host, family)
    rows = []
    for address in addresses:
        row = {"address": address, "family": "IPv6" if ":" in address else "IPv4"}
        if reverse:
            row["hostname"] = reverse_lookup(address) or ""
        rows.append(row)

    headers = ["family", "address"] + (["hostname"] if reverse else [])
    console.print_rows(rows, headers, as_json=as_json)


@net_cli.command("port")
@click.argument("host")
@click.argument("ports", nargs=-1, required=True)
@click.option("-t", "--timeout", default=DEFAULT_TIMEOUT, show_default=True, help="Connect timeout in seconds.")
@click.option("--open-only", is_flag=True, help="Hide closed ports.")
@json_option
def port_command(host: str, ports: tuple[str, ...], timeout: float, open_only: bool, as_json: bool) -> None:
    """Check whether TCP PORTS are reachable on HOST.

    \b
    PORTS accepts single values, comma lists and ranges.

    \b
    Examples:
      pynet port example.com 443
      pynet port 192.168.1.10 22,80,443
      pynet port localhost 8000-8010 --open-only
    """
    port_list = parse_ports(",".join(ports))
    results = probe_ports(host, port_list, timeout)
    rows = [r.as_row() for r in results if r.open or not open_only]
    console.print_rows(rows, ["port", "service", "state", "latency_ms"], as_json=as_json)
    if not as_json and not any(r.open for r in results):
        raise SystemExit(1)


@net_cli.command()
@click.argument("host")
@click.option(
    "-p",
    "--ports",
    default=None,
    help="Ports to scan (default: a list of common service ports).",
)
@click.option("-t", "--timeout", default=1.0, show_default=True, help="Connect timeout in seconds.")
@click.option(
    "-w", "--workers", default=DEFAULT_WORKERS, show_default=True, help="Concurrent connection attempts."
)
@click.option("--all", "show_all", is_flag=True, help="Show closed ports too.")
@json_option
def scan(
    host: str, ports: Optional[str], timeout: float, workers: int, show_all: bool, as_json: bool
) -> None:
    """Scan HOST for open TCP ports.

    \b
    Only scan hosts you are responsible for. Defaults to a short list of
    well-known service ports; pass -p for anything else.

    \b
    Examples:
      pynet scan 192.168.1.1
      pynet scan example.com -p 1-1024
      pynet scan localhost -p 8000-9000 --all
    """
    port_list = parse_ports(ports) if ports else sorted(COMMON_PORTS)
    console.info(f"Scanning {len(port_list)} ports on {host}...", verbose=1)
    results = probe_ports(host, port_list, timeout, workers)
    rows = [r.as_row() for r in results if r.open or show_all]
    if not rows:
        console.result(f"No open ports found on {host}.")
        return
    console.print_rows(rows, ["port", "service", "state", "latency_ms"], as_json=as_json)


@net_cli.command()
@click.argument("host")
@click.option("-c", "--count", default=4, show_default=True, help="Number of probes to send.")
@click.option("-p", "--port", default=443, show_default=True, help="Port used by the TCP fallback.")
@click.option("-t", "--timeout", default=DEFAULT_TIMEOUT, show_default=True, help="Per-probe timeout in seconds.")
@click.option("--tcp", is_flag=True, help="Force TCP probing instead of the system ping.")
@json_option
def ping(host: str, count: int, port: int, timeout: float, tcp: bool, as_json: bool) -> None:
    """Measure round-trip time to HOST.

    \b
    Uses the system `ping` when it is available; otherwise (and always with
    --tcp) it times TCP handshakes instead, which needs no special privileges
    and therefore works on unrooted Android/Termux.

    \b
    Examples:
      pynet ping example.com
      pynet ping example.com --tcp -p 80 -c 10
    """
    use_icmp = not tcp and shutil.which("ping") is not None
    if use_icmp:
        cmd = ["ping", "-c", str(count), "-W", str(int(max(timeout, 1))), host]
        console.info(f"$ {' '.join(cmd)}", verbose=1, threshold=2)
        result = subprocess.run(cmd, check=False)
        raise SystemExit(result.returncode)

    console.info(f"TCP ping {host}:{port}", verbose=1)
    timings = tcp_ping(host, port, count, timeout)
    received = [t for t in timings if t is not None]
    if as_json:
        console.emit_json(
            {
                "host": host,
                "port": port,
                "sent": count,
                "received": len(received),
                "min_ms": min(received) if received else None,
                "avg_ms": sum(received) / len(received) if received else None,
                "max_ms": max(received) if received else None,
            }
        )
    else:
        for index, timing in enumerate(timings, 1):
            if timing is None:
                console.result(f"seq={index} no response")
            else:
                console.result(f"seq={index} time={timing:.1f} ms")
        loss = 100 * (count - len(received)) / count
        console.result(f"--- {host}:{port} ---")
        console.result(f"{count} probes, {len(received)} replies, {loss:.0f}% loss")
        if received:
            console.result(
                f"min/avg/max = {min(received):.1f}/{sum(received) / len(received):.1f}/{max(received):.1f} ms"
            )
    if not received:
        raise SystemExit(1)


@net_cli.command()
@click.argument("url")
@click.option("-X", "--method", default="GET", show_default=True, help="HTTP method to use.")
@click.option("-H", "--header", "headers", multiple=True, help="Extra request header 'Name: value', repeatable.")
@click.option("-t", "--timeout", default=10.0, show_default=True, help="Request timeout in seconds.")
@click.option("--no-redirects", is_flag=True, help="Do not follow redirects.")
@click.option("--body", is_flag=True, help="Print the response body as well.")
@json_option
@verbose_option
def http(
    url: str,
    method: str,
    headers: tuple[str, ...],
    timeout: float,
    no_redirects: bool,
    body: bool,
    as_json: bool,
    verbose: int,
) -> None:
    """Request URL and report status, timing and the redirect chain.

    \b
    Examples:
      pynet http https://example.com
      pynet http https://example.com -v --no-redirects
      pynet http https://api.example.com -X HEAD -H 'Authorization: Bearer x'
    """
    import requests

    if "://" not in url:
        url = f"https://{url}"

    request_headers = {}
    for item in headers:
        name, sep, value = item.partition(":")
        if not sep:
            raise click.ClickException(f"Invalid header {item!r}. Use 'Name: value'.")
        request_headers[name.strip()] = value.strip()

    started = time.perf_counter()
    try:
        response = requests.request(
            method.upper(),
            url,
            headers=request_headers,
            timeout=timeout,
            allow_redirects=not no_redirects,
        )
    except requests.RequestException as exc:
        raise click.ClickException(f"Request failed: {exc}") from exc
    elapsed_ms = (time.perf_counter() - started) * 1000

    payload = {
        "url": response.url,
        "status": response.status_code,
        "reason": response.reason,
        "elapsed_ms": round(elapsed_ms, 1),
        "size_bytes": len(response.content),
        "redirects": [{"status": r.status_code, "url": r.url} for r in response.history],
        "headers": dict(response.headers),
    }
    if body:
        payload["body"] = response.text

    if as_json:
        console.emit_json(payload)
        return

    for redirect in payload["redirects"]:
        console.result(f"{redirect['status']} -> {redirect['url']}")
    console.result(f"{payload['status']} {payload['reason']}  {payload['elapsed_ms']} ms  {payload['size_bytes']} bytes")
    console.result(f"url: {payload['url']}")
    if verbose:
        for name, value in payload["headers"].items():
            console.result(f"{name}: {value}")
    if body:
        console.result("")
        console.result(response.text)
    if response.status_code >= 400:
        raise SystemExit(1)


@net_cli.command()
@click.argument("directory", required=False, default=".", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("-p", "--port", default=8000, show_default=True, type=click.IntRange(1, 65535), help="Port to listen on.")
@click.option("--bind", default="0.0.0.0", show_default=True, help="Address to bind to ('127.0.0.1' for local only).")
def serve(directory: Path, port: int, bind: str) -> None:
    """Serve DIRECTORY over HTTP on the local network.

    \b
    Handy for moving a file between a phone and a laptop without a cable or
    an account anywhere. Serves read-only; press Ctrl-C to stop.

    \b
    Examples:
      pynet serve
      pynet serve ~/downloads -p 8080
      pynet serve . --bind 127.0.0.1
    """
    import functools
    import http.server

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    try:
        server = http.server.ThreadingHTTPServer((bind, port), handler)
    except OSError as exc:
        raise click.ClickException(f"Could not listen on {bind}:{port}: {exc}") from exc

    console.result(f"Serving {directory.resolve()}")
    for entry in local_addresses() if bind == "0.0.0.0" else []:
        if entry["family"] == "IPv4":
            console.result(f"  http://{entry['address']}:{port}")
    console.result(f"  http://127.0.0.1:{port}")
    console.echo("Press Ctrl-C to stop.", err=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.echo("", err=True)
    finally:
        server.server_close()


@net_cli.command()
@click.argument("domain")
@click.option("--server", default=None, help="WHOIS server to query (default: follow IANA referrals).")
@click.option("--raw", is_flag=True, help="Print the first response without following referrals.")
@json_option
def whois(domain: str, server: Optional[str], raw: bool, as_json: bool) -> None:
    """Look up WHOIS registration data for DOMAIN.

    \b
    Speaks the port-43 WHOIS protocol directly, so no `whois` binary is needed.

    \b
    Examples:
      pynet whois example.com
      pynet whois example.com --server whois.verisign-grs.com
      pynet whois example.com --json
    """
    target = server or IANA_WHOIS
    response = whois_query(domain, target)
    if not raw and server is None:
        referral = _referral_server(response)
        # One hop only: IANA points at the registry, which is where the real
        # record lives. Chasing further referrals loops on some TLDs.
        if referral and referral != target:
            console.info(f"Referred to {referral}", verbose=1)
            response = whois_query(domain, referral)
            target = referral
    if as_json:
        # A WHOIS record is free-form text, so the response stays one string;
        # JSON only makes it safe to embed and says which server answered.
        console.emit_json({"domain": domain, "server": target, "response": response.strip()})
        return
    console.result(response.strip())


@net_cli.command("url")
@click.argument("value")
@click.option("-d", "--decode", is_flag=True, help="Decode instead of encode.")
@click.option("--parse", is_flag=True, help="Split a URL into its components.")
@json_option
def url_command(value: str, decode: bool, parse: bool, as_json: bool) -> None:
    """Percent-encode, decode or parse a URL.

    \b
    Examples:
      pynet url 'a b&c=d'
      pynet url 'a%20b' --decode
      pynet url 'https://example.com/p?q=1#f' --parse
    """
    if parse:
        parts = urllib.parse.urlsplit(value)
        payload = {
            "scheme": parts.scheme,
            "host": parts.hostname or "",
            "port": parts.port or "",
            "path": parts.path,
            "query": dict(urllib.parse.parse_qsl(parts.query)),
            "fragment": parts.fragment,
        }
        if as_json:
            console.emit_json(payload)
        else:
            for key, item in payload.items():
                console.result(f"{key}: {item}")
        return

    console.result(urllib.parse.unquote(value) if decode else urllib.parse.quote(value, safe=""))


if __name__ == "__main__":  # pragma: no cover
    net_cli()
