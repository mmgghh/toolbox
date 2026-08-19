"""SSH helpers: SOCKS tunnels (single and chained) and an rsync wrapper.

These are thin, predictable wrappers around the system ``ssh`` and ``rsync``
binaries -- not an SSH implementation. Passwords, when used at all, are handed
to ``sshpass`` through an owner-only file under the user's runtime directory
and removed when the command exits.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import time
from collections.abc import Sequence
from contextlib import closing, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import click

from pytoolbox.core import console, paths, rsync
from pytoolbox.core.options import (
    CONTEXT_SETTINGS,
    AliasedGroup,
    dry_run_option,
    verbose_option,
    version_option,
    yes_option,
)

#: Endpoint used to verify that the proxy actually reaches the internet.
#: Returns HTTP 204 with an empty body, so the check is fast and cheap.
DEFAULT_CHECK_URL = "https://www.gstatic.com/generate_204"

#: How often ``--reconnect`` re-tests the proxy.
HEALTH_INTERVAL_SECONDS = 15

#: Seconds to wait for ssh to establish a listener before declaring failure.
STARTUP_TIMEOUT_SECONDS = 20

SERVER_SPEC_RE = re.compile(r"^(?P<user>[^@:/]+)(?::(?P<password>[^@]*))?@(?P<host>[^@:/]+)(?::(?P<port>\d+))?$")


# ═══════════════════════════════════════════════════════════════════
# Server specs
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class Server:
    """A parsed ``user[:password]@host[:port]`` target."""

    user: str
    host: str
    port: int = 22
    password: Optional[str] = None

    @property
    def target(self) -> str:
        """The ``user@host`` string ssh expects."""
        return f"{self.user}@{self.host}"

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.user}@{self.host}:{self.port}"


def parse_server(spec: str) -> Server:
    """Parse ``user@host``, ``user@host:port`` or ``user:password@host:port``."""
    raw = spec.strip()
    match = SERVER_SPEC_RE.match(raw)
    if not match:
        raise click.ClickException(
            f"Invalid server spec: {spec!r}. Expected 'user@host', 'user@host:port', "
            "or 'user:password@host:port'."
        )
    password = match.group("password")
    return Server(
        user=match.group("user"),
        host=match.group("host"),
        port=int(match.group("port") or 22),
        password=password or None,
    )


def load_server_conf(path: Path) -> Server:
    """Read a server spec from the first non-empty, non-comment line of a file."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise click.ClickException(f"Could not read {path}: {exc}") from exc
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return parse_server(line)
    raise click.ClickException(f"{path} contains no server spec.")


def resolve_server(spec: Optional[str], conf: Optional[str], label: str) -> Server:
    """Resolve a server from either an inline spec or a config file."""
    if spec and conf:
        raise click.ClickException(f"Use either {label} or {label}-conf, not both.")
    if spec:
        return parse_server(spec)
    if conf:
        return load_server_conf(Path(conf))
    raise click.ClickException(f"Provide {label} or {label}-conf.")


# ═══════════════════════════════════════════════════════════════════
# Ports and health checks
# ═══════════════════════════════════════════════════════════════════


def port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """Whether a local TCP port can be bound.

    Binding is the right test: ``connect()`` only proves nobody is *accepting*
    connections, which is also true for a port held by a socket in TIME_WAIT.
    """
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def port_is_listening(port: int, host: str = "127.0.0.1", timeout: float = 1.0) -> bool:
    """Whether something accepts TCP connections on ``host:port``."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def check_socks5_proxy(host: str, port: int, address: str = DEFAULT_CHECK_URL) -> bool:
    """Whether a SOCKS5 proxy at ``host:port`` can reach ``address``."""
    try:
        import requests
    except ImportError:  # pragma: no cover - requests is a hard dependency
        return port_is_listening(port, host)

    proxy = f"socks5h://{host}:{port}"
    try:
        response = requests.get(
            address, timeout=10, proxies={"http": proxy, "https": proxy}
        )
    except Exception as exc:  # noqa: BLE001 - any failure means "not reachable"
        if "Missing dependencies for SOCKS support" in str(exc):
            raise click.ClickException(
                "SOCKS proxy support needs PySocks. Install it with `pip install 'requests[socks]'`."
            ) from exc
        return False
    return response.status_code < 400


# ═══════════════════════════════════════════════════════════════════
# Tunnel processes
# ═══════════════════════════════════════════════════════════════════


def tunnels_dir() -> Path:
    """Directory holding one state file per running tunnel."""
    return paths.ensure_dir(paths.runtime_dir() / "tunnels", private=True)


def _state_path(name: str) -> Path:
    return tunnels_dir() / f"{name}.json"


def save_state(name: str, payload: dict) -> Path:
    """Record a running tunnel so ``pyssh status``/``stop`` can find it."""
    path = _state_path(name)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def load_states() -> list[dict]:
    """Return the state of every recorded tunnel, dropping stale entries."""
    states: list[dict] = []
    for path in sorted(tunnels_dir().glob("*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        state["_file"] = str(path)
        if not pid_alive(state.get("pids", [])):
            path.unlink(missing_ok=True)
            continue
        states.append(state)
    return states


def pid_alive(pids: Sequence[int]) -> bool:
    """Whether any of ``pids`` is still running."""
    for pid in pids:
        try:
            os.kill(int(pid), 0)
        except (OSError, ValueError, TypeError):
            continue
        return True
    return False


def terminate(pids: Sequence[int], timeout: float = 5.0) -> int:
    """Terminate processes gracefully, escalating to SIGKILL. Returns count killed."""
    killed = 0
    for pid in pids:
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            continue
        with suppress(OSError):
            os.kill(pid, signal.SIGTERM)
            killed += 1
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and pid_alive(pids):
        time.sleep(0.2)
    for pid in pids:
        with suppress(OSError, TypeError, ValueError):
            os.kill(int(pid), signal.SIGKILL)
    return killed


def _require(binary: str, hint: str) -> None:
    if shutil.which(binary) is None:
        raise click.ClickException(f"`{binary}` was not found on PATH. {hint}")


def _password_file(server: Server, slot: str) -> Optional[Path]:
    """Write a password to an owner-only file for sshpass, or return None."""
    if not server.password:
        return None
    _require(
        "sshpass",
        "Password authentication needs sshpass (Debian/Ubuntu: `sudo apt install sshpass`, "
        "Termux: `pkg install sshpass`). Alternatively use key authentication with --identity.",
    )
    path = paths.runtime_dir() / f"pyssh-{slot}-{os.getpid()}.pass"
    return paths.write_private_file(path, server.password)


def build_ssh_command(
    server: Server,
    forward_args: Sequence[str],
    identity: Optional[str] = None,
    password_file: Optional[Path] = None,
    extra_opts: Sequence[str] = (),
    keepalive: bool = True,
) -> list[str]:
    """Assemble the ssh command line for a tunnel."""
    cmd: list[str] = ["ssh", "-N", *forward_args, "-p", str(server.port), server.target]
    if keepalive:
        # Without these a dropped link leaves the tunnel silently dead.
        cmd[1:1] = ["-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3", "-o", "ExitOnForwardFailure=yes"]
    if identity:
        cmd[1:1] = ["-i", str(Path(identity).expanduser())]
    for opt in extra_opts:
        cmd[1:1] = ["-o", opt]
    if password_file is not None:
        # Host-key prompts cannot be answered when sshpass drives ssh non-interactively.
        cmd = ["sshpass", "-f", str(password_file)] + cmd
        cmd.extend(["-o", "StrictHostKeyChecking=accept-new"])
    return cmd


def _wait_for_listener(port: int, host: str, process: subprocess.Popen, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise click.ClickException(
                f"ssh exited with code {process.returncode} before the tunnel came up. "
                "Run with -v to see ssh's own output."
            )
        if port_is_listening(port, probe_host):
            return
        time.sleep(0.3)
    raise click.ClickException(f"Timed out waiting for the tunnel to listen on port {port}.")


class TunnelSession:
    """Owns the ssh child processes for a tunnel and cleans them up on exit."""

    def __init__(self, name: str, verbose: int = 0) -> None:
        self.name = name
        self.verbose = verbose
        self.processes: list[subprocess.Popen] = []
        self.secret_files: list[Path] = []

    def spawn(self, cmd: Sequence[str]) -> subprocess.Popen:
        """Start an ssh process, hiding its chatter unless ``-v`` was given."""
        console.info(f"$ {' '.join(cmd)}", self.verbose, threshold=1)
        stream = None if self.verbose else subprocess.DEVNULL
        try:
            process = subprocess.Popen(list(cmd), stdout=stream, stderr=stream)
        except OSError as exc:
            raise click.ClickException(f"Could not start ssh: {exc}") from exc
        self.processes.append(process)
        return process

    def track_secret(self, path: Optional[Path]) -> Optional[Path]:
        """Remember a password file so it gets shredded on cleanup."""
        if path is not None:
            self.secret_files.append(path)
        return path

    @property
    def pids(self) -> list[int]:
        """PIDs of the ssh processes started by this session."""
        return [process.pid for process in self.processes]

    def alive(self) -> bool:
        """Whether every child process is still running."""
        return bool(self.processes) and all(p.poll() is None for p in self.processes)

    def clear_secrets(self) -> None:
        """Delete the password files.

        Safe to call as soon as the tunnel is up: sshpass reads the file once,
        at authentication time, and never reopens it.
        """
        for path in self.secret_files:
            with suppress(OSError):
                path.unlink(missing_ok=True)
        self.secret_files.clear()

    def cleanup(self) -> None:
        """Kill the ssh processes, delete secrets and drop the state file."""
        for process in self.processes:
            if process.poll() is None:
                with suppress(OSError):
                    process.terminate()
        for process in self.processes:
            with suppress(subprocess.TimeoutExpired):
                process.wait(timeout=5)
            if process.poll() is None:
                with suppress(OSError):
                    process.kill()
        self.processes.clear()
        self.clear_secrets()
        with suppress(OSError):
            _state_path(self.name).unlink(missing_ok=True)


def _serve_until_interrupt(
    session: TunnelSession,
    restart: Optional[callable],
    reconnect: bool,
    check_host: str,
    check_port_number: int,
    check_url: str,
    verbose: int,
) -> None:
    """Block until Ctrl-C, optionally re-establishing a dead tunnel."""
    console.echo("Press Ctrl-C to stop.", err=True)
    try:
        while True:
            time.sleep(1 if not reconnect else HEALTH_INTERVAL_SECONDS)
            if not reconnect:
                if not session.alive():
                    console.error("ssh exited; stopping.")
                    return
                continue
            healthy = session.alive() and check_socks5_proxy(check_host, check_port_number, check_url)
            if healthy:
                console.info("proxy healthy", verbose, threshold=2)
                continue
            console.warn(f"{check_url} is not reachable through the proxy; restarting the tunnel.")
            session.cleanup()
            time.sleep(1)
            if restart is not None:
                restart()
    except KeyboardInterrupt:
        console.echo("", err=True)
        console.info("stopping tunnel", verbose, threshold=0)


# ═══════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════


@click.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
@version_option
def ssh_management() -> None:
    """SSH tunnels and transfers.

    \b
    Examples:
      pyssh tunnel -s user@vps.example.com -p 9998
      pyssh tunnel --server-conf ~/.config/pytoolbox/vps.conf --reconnect
      pyssh double-tunnel --server1 me@bridge:22 --server2 me@target:22
      pyssh rsync-dir -s ./site -d me@vps:/srv/site -p 22 --dry-run
      pyssh status
      pyssh stop --all
    """


def _tunnel_options(func):
    func = verbose_option(func)
    func = click.option(
        "--check-url",
        default=DEFAULT_CHECK_URL,
        show_default=True,
        help="URL used by --reconnect to verify the proxy really works.",
    )(func)
    func = click.option(
        "--reconnect",
        "reconnect",
        is_flag=True,
        help="Watch the proxy and rebuild the tunnel when it stops working.",
    )(func)
    func = click.option(
        "-b",
        "--background",
        is_flag=True,
        help="Return immediately, leaving the tunnel running (see `pyssh status`).",
    )(func)
    func = click.option(
        "-o",
        "--ssh-option",
        "ssh_options",
        multiple=True,
        help="Extra `ssh -o` option, repeatable (e.g. -o Compression=yes).",
    )(func)
    func = click.option(
        "-i",
        "--identity",
        type=click.Path(dir_okay=False),
        help="Private key file to authenticate with.",
    )(func)
    func = click.option(
        "--public",
        is_flag=True,
        help="Bind the SOCKS port to 0.0.0.0 so other devices on the LAN can use it.",
    )(func)
    return func


@ssh_management.command()
@click.option(
    "-s",
    "--server",
    help="Target as 'user@host', 'user@host:port' or 'user:password@host:port'.",
)
@click.option(
    "--server-conf",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="File whose first non-comment line holds the server spec.",
)
@click.option(
    "-p",
    "--local-port",
    default=9998,
    show_default=True,
    type=click.IntRange(1, 65535),
    help="Local port the SOCKS5 proxy listens on.",
)
@_tunnel_options
def tunnel(
    server: Optional[str],
    server_conf: Optional[str],
    local_port: int,
    public: bool,
    identity: Optional[str],
    ssh_options: tuple[str, ...],
    background: bool,
    reconnect: bool,
    check_url: str,
    verbose: int,
) -> None:
    """Open a SOCKS5 proxy through one remote server.

    \b
    Useful when your machine has restricted internet access but can reach a
    server that does not. Point your browser or shell at
    socks5://localhost:<local-port> afterwards.

    \b
    Examples:
      pyssh tunnel -s me@vps.example.com -p 9998
      pyssh tunnel -s me@vps.example.com -i ~/.ssh/id_ed25519 --background
      pyssh tunnel --server-conf ~/vps.conf --reconnect --public
    """
    _require("ssh", "Install OpenSSH (Termux: `pkg install openssh`).")
    target = resolve_server(server, server_conf, "-s/--server")
    bind_host = "0.0.0.0" if public else "127.0.0.1"

    if not port_is_free(local_port, bind_host):
        raise click.ClickException(
            f"Local port {local_port} is already in use. Pick another with -p, "
            "or run `pyssh status` to see tunnels started by pyssh."
        )

    session = TunnelSession(name=f"tunnel-{local_port}", verbose=verbose)

    def start() -> None:
        password_file = session.track_secret(_password_file(target, "t"))
        cmd = build_ssh_command(
            target,
            ["-D", f"{bind_host}:{local_port}"],
            identity=identity,
            password_file=password_file,
            extra_opts=ssh_options,
        )
        process = session.spawn(cmd)
        _wait_for_listener(local_port, bind_host, process, STARTUP_TIMEOUT_SECONDS)
        save_state(
            session.name,
            {
                "kind": "tunnel",
                "pids": session.pids,
                "socks_port": local_port,
                "bind": bind_host,
                "server": str(target),
                "started_at": time.time(),
            },
        )
        # The password has been consumed by sshpass at this point; nothing
        # reopens the file, so it should not outlive the handshake.
        session.clear_secrets()

    detached = False
    try:
        start()
        console.success(f"SOCKS5 proxy ready on socks5://{bind_host}:{local_port}", verbose)
        if background:
            console.echo(f"Running in the background (pids: {', '.join(map(str, session.pids))}).", err=True)
            console.echo(f"Stop it with: pyssh stop {session.name}", err=True)
            detached = True
            return
        _serve_until_interrupt(
            session, start, reconnect, "127.0.0.1", local_port, check_url, verbose
        )
    finally:
        session.clear_secrets()
        if not detached:
            session.cleanup()


@ssh_management.command("double-tunnel")
@click.option("--server1", help="First hop, as 'user[:password]@host[:port]'.")
@click.option("--server2", help="Second hop, as 'user[:password]@host[:port]'.")
@click.option(
    "--server1-conf",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="File holding the first hop's server spec.",
)
@click.option(
    "--server2-conf",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="File holding the second hop's server spec.",
)
@click.option(
    "--lp1",
    default=9998,
    show_default=True,
    type=click.IntRange(1, 65535),
    help="Local port forwarded to server 2's SSH port.",
)
@click.option(
    "--lp2",
    default=9999,
    show_default=True,
    type=click.IntRange(1, 65535),
    help="Local port the SOCKS5 proxy listens on.",
)
@_tunnel_options
def double_tunnel(
    server1: Optional[str],
    server2: Optional[str],
    server1_conf: Optional[str],
    server2_conf: Optional[str],
    lp1: int,
    lp2: int,
    public: bool,
    identity: Optional[str],
    ssh_options: tuple[str, ...],
    background: bool,
    reconnect: bool,
    check_url: str,
    verbose: int,
) -> None:
    """Open a SOCKS5 proxy to server 2, reached through server 1.

    \b
    For the case where your machine can reach server 1 but not server 2, and
    only server 2 has unrestricted access. Traffic then flows
    you -> server1 -> server2 -> internet, exposed as socks5://localhost:<lp2>.

    \b
    Example:
      pyssh double-tunnel --server1 me@bridge.example.com:22 \\
                          --server2 me@target.example.com:22 --lp1 9998 --lp2 9999
    """
    _require("ssh", "Install OpenSSH (Termux: `pkg install openssh`).")
    first = resolve_server(server1, server1_conf, "--server1")
    second = resolve_server(server2, server2_conf, "--server2")
    bind_host = "0.0.0.0" if public else "127.0.0.1"

    for port in (lp1, lp2):
        if not port_is_free(port, "127.0.0.1" if port == lp1 else bind_host):
            raise click.ClickException(f"Local port {port} is already in use.")

    session = TunnelSession(name=f"double-{lp2}", verbose=verbose)

    def start() -> None:
        pass1 = session.track_secret(_password_file(first, "d1"))
        hop1 = build_ssh_command(
            first,
            ["-L", f"127.0.0.1:{lp1}:{second.host}:{second.port}"],
            identity=identity,
            password_file=pass1,
            extra_opts=ssh_options,
        )
        process1 = session.spawn(hop1)
        _wait_for_listener(lp1, "127.0.0.1", process1, STARTUP_TIMEOUT_SECONDS)

        # Second hop dials the forwarded local port, so it always talks to localhost.
        via_local = Server(user=second.user, host="127.0.0.1", port=lp1, password=second.password)
        pass2 = session.track_secret(_password_file(via_local, "d2"))
        hop2 = build_ssh_command(
            via_local,
            ["-D", f"{bind_host}:{lp2}"],
            identity=identity,
            password_file=pass2,
            extra_opts=ssh_options,
        )
        process2 = session.spawn(hop2)
        _wait_for_listener(lp2, bind_host, process2, STARTUP_TIMEOUT_SECONDS)
        save_state(
            session.name,
            {
                "kind": "double-tunnel",
                "pids": session.pids,
                "socks_port": lp2,
                "bridge_port": lp1,
                "bind": bind_host,
                "server": f"{first} -> {second}",
                "started_at": time.time(),
            },
        )
        session.clear_secrets()

    detached = False
    try:
        start()
        console.success(f"SOCKS5 proxy ready on socks5://{bind_host}:{lp2}", verbose)
        if background:
            console.echo(f"Running in the background (pids: {', '.join(map(str, session.pids))}).", err=True)
            console.echo(f"Stop it with: pyssh stop {session.name}", err=True)
            detached = True
            return
        _serve_until_interrupt(session, start, reconnect, "127.0.0.1", lp2, check_url, verbose)
    finally:
        session.clear_secrets()
        if not detached:
            session.cleanup()


#: ``user[:password]@host:/path``. Anything else -- a local path, a bare
#: ``host:/path`` -- is handed to rsync untouched.
RSYNC_TARGET_RE = re.compile(
    r"^(?P<user>[^@:/]+)(?::(?P<password>[^@]*))?@(?P<host>[^@:/]+):(?P<path>.*)$"
)

RSYNC_EPILOG = """\
\b
Patterns are globs, not regex:
  *      any characters, stops at /
  **     any characters, crosses /
  ?      one character, not /
  [a-z]  character class
  foo/   directories only

\b
A pattern with no slash matches the basename at any depth, so '*.jpg'
finds photos/2024/a.jpg. A pattern containing a slash is matched from
the transfer root, and a leading / anchors it there.

\b
Excludes are always applied before matches, whatever order you type
them in, so -e node_modules --match '*.js' skips node_modules. {a,b}
is expanded for you; rsync itself cannot, and would match nothing.
Use --raw-patterns for verbatim rsync semantics.
"""


def split_rsync_target(spec: str) -> tuple[str, Optional[str]]:
    """Split an inline password out of an rsync target.

    Returns the target as rsync should see it and the password, if any. Specs
    that are not ``user[:password]@host:path`` -- local paths, bare
    ``host:path`` -- come back unchanged.
    """
    match = RSYNC_TARGET_RE.match(spec)
    if not match or match.group("password") is None:
        return spec, None
    user, host, path = match.group("user"), match.group("host"), match.group("path")
    return f"{user}@{host}:{path}", match.group("password") or None


@ssh_management.command("rsync-dir", epilog=RSYNC_EPILOG)
@click.option(
    "-s",
    "--source",
    required=True,
    prompt=True,
    help="Local path, or 'user[:password]@host:/remote/path'.",
)
@click.option(
    "-d",
    "--destination",
    required=True,
    prompt=True,
    help="Local path, or 'user[:password]@host:/remote/path'.",
)
# ── matching and filtering ──
@click.option(
    "--match",
    multiple=True,
    metavar="GLOB",
    help="Transfer only files matching GLOB, at any depth. Repeatable. Becomes "
    "rsync's --include '*/' --include GLOB --exclude '*' --prune-empty-dirs.",
)
@click.option(
    "-e",
    "--exclude",
    multiple=True,
    metavar="GLOB",
    help="Skip files matching GLOB. Repeatable, and applied before --match. "
    "Becomes rsync's --exclude.",
)
@click.option(
    "--match-from",
    type=click.Path(dir_okay=False),
    help="Read --match patterns from a file, one per line; '#' and ';' start a comment.",
)
@click.option(
    "--exclude-from",
    type=click.Path(dir_okay=False),
    help="Read --exclude patterns from a file, one per line; '#' and ';' start a comment.",
)
@click.option(
    "--gitignore",
    is_flag=True,
    help="Honour .gitignore files in the tree (rsync's --filter=':- .gitignore'). "
    "Does not exclude .git/ itself -- add -e '.git' if you want that too.",
)
@click.option(
    "--files-from",
    type=click.Path(dir_okay=False),
    help="Transfer exactly the paths listed in this file (rsync's --files-from). "
    "Cannot be combined with --match or --gitignore; listed directories are not "
    "recursed into.",
)
@click.option("--min-size", metavar="SIZE", help="Skip files smaller than SIZE, e.g. 1k.")
@click.option("--max-size", metavar="SIZE", help="Skip files larger than SIZE, e.g. 10m.")
@click.option(
    "--raw-patterns",
    is_flag=True,
    help="Pass patterns to rsync verbatim: no {a,b} expansion, no regex check.",
)
# ── safety ──
@click.option(
    "--delete",
    is_flag=True,
    help="Delete destination files missing from the source. Asks first.",
)
@click.option(
    "--mirror",
    is_flag=True,
    help="Make the destination identical to the source: rsync's --delete plus "
    "--delete-excluded. Asks first. With --match this deletes everything at the "
    "destination that does not match.",
)
@click.option(
    "--backup-dir",
    metavar="DIR",
    type=click.Path(file_okay=False),
    help="Move deleted and overwritten files here instead of losing them "
    "(rsync's --backup --backup-dir). A relative DIR is resolved against the "
    "destination directory.",
)
@click.option("--stats", is_flag=True, help="Print rsync's transfer summary at the end.")
# ── transport ──
@click.option(
    "-p",
    "--ssh-port",
    default=22,
    show_default=True,
    type=click.IntRange(1, 65535),
    help="SSH port of the remote side.",
)
@click.option(
    "--identity",
    type=click.Path(dir_okay=False),
    help="Private key file to authenticate with.",
)
@click.option(
    "-o",
    "--ssh-option",
    "ssh_options",
    multiple=True,
    metavar="OPT",
    help="Extra 'ssh -o' option, repeatable. Example: -o ConnectTimeout=5.",
)
@click.option("--bwlimit", metavar="RATE", help="Cap the transfer rate, e.g. 500k or 1.5m.")
@click.option(
    "--no-compress",
    is_flag=True,
    help="Drop the 'z' from -azP. Worth it on a LAN, or for already-compressed data.",
)
@click.option(
    "--sudo",
    is_flag=True,
    help="Run rsync as root on the remote side (--rsync-path='sudo rsync'). "
    "Needs passwordless sudo there.",
)
# ── comparison ──
@click.option(
    "-i",
    "--ignore-existing",
    is_flag=True,
    help="Never touch files that already exist at the destination.",
)
@click.option(
    "--existing",
    is_flag=True,
    help="Update only files already at the destination; never create new ones.",
)
@click.option(
    "-c",
    "--checksum",
    is_flag=True,
    help="Compare by file contents rather than size and timestamp. Slower, exact.",
)
@click.option(
    "--size-only",
    is_flag=True,
    help="Treat files of equal size as identical, ignoring timestamps.",
)
@dry_run_option
@yes_option
@verbose_option
def rsync_dir(
    source: str,
    destination: str,
    match: tuple[str, ...],
    exclude: tuple[str, ...],
    match_from: Optional[str],
    exclude_from: Optional[str],
    gitignore: bool,
    files_from: Optional[str],
    min_size: Optional[str],
    max_size: Optional[str],
    raw_patterns: bool,
    delete: bool,
    mirror: bool,
    backup_dir: Optional[str],
    stats: bool,
    ssh_port: int,
    identity: Optional[str],
    ssh_options: tuple[str, ...],
    bwlimit: Optional[str],
    no_compress: bool,
    sudo: bool,
    ignore_existing: bool,
    existing: bool,
    checksum: bool,
    size_only: bool,
    dry_run: bool,
    assume_yes: bool,
    verbose: int,
) -> None:
    """Copy a directory over SSH with rsync.

    \b
    Wraps `rsync -azP -e "ssh -p <port>"`. Arguments are passed to rsync
    directly (no shell), so paths with spaces or quotes are safe. Without
    --ignore-existing, --update is used: only newer source files transfer.

    \b
    Examples:
      pyssh rsync-dir -s ./site -d me@vps:/srv/site -p 22
      pyssh rsync-dir -s ./photos -d me@vps:/srv/pics --match '*.{jpg,png}'
      pyssh rsync-dir -s ./repo -d me@vps:/srv/repo --gitignore -e '.git'
      pyssh rsync-dir -s ./site -d me@vps:/srv/site --mirror --dry-run
      pyssh rsync-dir -s me@vps:/srv/site -d ./backup --bwlimit 500k
    """
    _require("rsync", "Install rsync (Termux: `pkg install rsync`).")

    source, source_password = split_rsync_target(source)
    destination, destination_password = split_rsync_target(destination)
    if source_password and destination_password:
        raise click.ClickException(
            "Only one side can carry a password; rsync opens a single SSH connection."
        )
    password = source_password or destination_password

    ssh_parts = ["ssh", "-p", str(ssh_port)]
    if identity:
        ssh_parts += ["-i", str(Path(identity).expanduser())]
    for option in ssh_options:
        ssh_parts += ["-o", option]
    if password:
        # Host-key prompts cannot be answered when sshpass drives ssh.
        ssh_parts += ["-o", "StrictHostKeyChecking=accept-new"]

    if exclude_from:
        exclude = (*exclude, *rsync.read_pattern_file(Path(exclude_from)))
    if match_from:
        match = (*match, *rsync.read_pattern_file(Path(match_from)))

    plan = rsync.RsyncOptions(
        source=source,
        destination=destination,
        ssh_command=shlex.join(ssh_parts),
        ignore_existing=ignore_existing,
        existing=existing,
        checksum=checksum,
        size_only=size_only,
        compress=not no_compress,
        bwlimit=bwlimit,
        sudo=sudo,
        delete=delete,
        mirror=mirror,
        backup_dir=backup_dir,
        stats=stats,
        dry_run=dry_run,
        exclude=tuple(exclude),
        match=tuple(match),
        gitignore=gitignore,
        files_from=files_from,
        min_size=min_size,
        max_size=max_size,
        raw_patterns=raw_patterns,
        verbose=verbose,
    )
    cmd = rsync.build_rsync_command(plan)

    if plan.deletes and not dry_run:
        what = "everything not matching" if match else "files missing from the source"
        if not console.confirm(
            f"Delete {what} under {destination}?", assume_yes, default=False
        ):
            raise click.Abort()

    password_file = None
    if password:
        password_file = _password_file(
            Server(user="rsync", host="rsync", password=password), "rsync"
        )
        cmd = ["sshpass", "-f", str(password_file)] + cmd

    console.info(f"$ {' '.join(cmd)}", verbose, threshold=0)
    try:
        result = subprocess.run(cmd, check=False)
    finally:
        if password_file is not None:
            password_file.unlink(missing_ok=True)
    if result.returncode != 0:
        raise click.ClickException(f"rsync exited with code {result.returncode}.")


@ssh_management.command()
@click.option("--json", "as_json", is_flag=True, help="Print state as JSON.")
def status(as_json: bool) -> None:
    """List tunnels started by pyssh that are still running."""
    states = load_states()
    if as_json:
        console.emit_json(states)
        return
    if not states:
        console.result("No pyssh tunnels are running.")
        return
    rows = []
    for state in states:
        rows.append(
            {
                "name": Path(state["_file"]).stem,
                "kind": state.get("kind", ""),
                "socks": f"socks5://{state.get('bind', '127.0.0.1')}:{state.get('socks_port', '')}",
                "server": state.get("server", ""),
                "pids": ",".join(str(pid) for pid in state.get("pids", [])),
                "uptime": _uptime(state.get("started_at")),
            }
        )
    console.print_rows(rows, ["name", "kind", "socks", "server", "pids", "uptime"])


def _uptime(started_at: Optional[float]) -> str:
    if not started_at:
        return ""
    from pytoolbox.core.intervals import format_duration

    return format_duration(time.time() - float(started_at))


@ssh_management.command()
@click.argument("name", required=False)
@click.option("-a", "--all", "stop_all", is_flag=True, help="Stop every running tunnel.")
def stop(name: Optional[str], stop_all: bool) -> None:
    """Stop a background tunnel by NAME (see `pyssh status`), or all of them."""
    states = load_states()
    if not states:
        console.result("No pyssh tunnels are running.")
        return
    if not name and not stop_all:
        raise click.ClickException("Provide a tunnel NAME or use --all. See `pyssh status`.")

    targets = states if stop_all else [s for s in states if Path(s["_file"]).stem == name]
    if not targets:
        raise click.ClickException(f"No running tunnel named {name!r}.")

    for state in targets:
        terminate(state.get("pids", []))
        Path(state["_file"]).unlink(missing_ok=True)
        console.result(f"Stopped {Path(state['_file']).stem}.")


if __name__ == "__main__":  # pragma: no cover
    ssh_management()
