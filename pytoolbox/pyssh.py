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
from pathlib import Path
from typing import Optional, Union

import click

from pytoolbox.core import console, paths, rsync
from pytoolbox.core.options import (
    CONTEXT_SETTINGS,
    AliasedGroup,
    dry_run_option,
    json_option,
    verbose_option,
    version_option,
    yes_option,
)
from pytoolbox.ssh import command, hosts, knownhosts, remote, session, store
from pytoolbox.ssh.hosts import (  # noqa: F401 - re-exported for backwards compatibility
    SERVER_SPEC_RE,
    Server,
    load_server_conf,
    parse_server,
    resolve_server,
)

#: Endpoint used to verify that the proxy actually reaches the internet.
#: Returns HTTP 204 with an empty body, so the check is fast and cheap.
DEFAULT_CHECK_URL = "https://www.gstatic.com/generate_204"

#: How often ``--reconnect`` re-tests the proxy.
HEALTH_INTERVAL_SECONDS = 15

#: Seconds to wait for ssh to establish a listener before declaring failure.
STARTUP_TIMEOUT_SECONDS = 20

#: A backgrounded ssh captures its output, so a stalled connect would hang
#: with nothing on screen. Bound it; the user's own -o wins if they set one.
CONNECT_TIMEOUT_SECONDS = 15


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
        if not state_is_alive(state):
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


def state_is_alive(state: dict) -> bool:
    """Whether a recorded session is still running.

    A control socket is authoritative -- ``ssh -O check`` asks the master
    itself. PIDs are the fallback for Windows and for paths too long to hold a
    Unix socket.
    """
    control = state.get("control")
    destination = state.get("destination")
    if control and destination:
        return session.master_alive(Path(control), destination)
    return pid_alive(state.get("pids", []))


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


def _password_file(password: Optional[str], slot: str) -> Optional[Path]:
    """Write a password to an owner-only file for sshpass, or return None."""
    if not password:
        return None
    _require(
        "sshpass",
        "Password authentication needs sshpass (Debian/Ubuntu: `sudo apt install sshpass`, "
        "Termux: `pkg install sshpass`). Alternatively use key authentication with --identity.",
    )
    path = paths.runtime_dir() / f"pyssh-{slot}-{os.getpid()}.pass"
    return paths.write_private_file(path, password)


def apply_stored_secret(target: hosts.Target) -> hosts.Target:
    """Attach a stored password to a target that has none of its own.

    Only config names are looked up: an inline ``user@host`` spec names a
    connection directly, and is not a key in the store.
    """
    if target.password or not target.is_config_name:
        return target
    return target.with_password(store.get_secret(target.spec))


def _target_endpoint(target: hosts.Target) -> tuple[str, int]:
    """The host and port a target actually connects to.

    A config name is resolved through ``ssh -G``; an inline spec carries its
    own. known_hosts keys its entries by this pair rather than by the name the
    user typed, so anything that names a key back to the user must use it.
    """
    if target.is_config_name:
        resolved = hosts.resolve_config(target.spec)
        if resolved is None:
            return target.spec, 22
        return resolved.hostname, resolved.port
    _, _, host = target.spec.partition("@")
    return host, target.port or 22


def _guard_host_key(target: hosts.Target) -> None:
    """Refuse to hand a password to a host we cannot identify.

    Only password authentication is guarded: with a key, ssh's own host-key
    prompting already works, and there is no secret to leak to an impostor.
    """
    if not target.password:
        return
    host, port = _target_endpoint(target)
    knownhosts.require_known(target.spec, host, port)


def _wait_for_listeners(listeners: Sequence[tuple[str, int]], timeout: float) -> None:
    """Block until every local listener accepts connections."""
    deadline = time.monotonic() + timeout
    for host, port in listeners:
        probe_host = "127.0.0.1" if host == "0.0.0.0" else host
        while not port_is_listening(port, probe_host):
            if time.monotonic() > deadline:
                raise click.ClickException(
                    f"Timed out waiting for the forward to listen on port {port}."
                )
            time.sleep(0.3)


def _open_background_session(
    target: hosts.Target,
    forwards: Sequence[str],
    listeners: Sequence[tuple[str, int]],
    name: str,
    kind: str,
    identity: Optional[str],
    ssh_options: Sequence[str],
    verbose: int,
) -> Optional[dict]:
    """Start a backgrounded ssh and record it, returning the saved state.

    ``ssh -f`` returns only after authentication and after every remote
    forward is up, so a non-zero exit is an authoritative failure -- including
    for ``-R``, whose listener lives where no local probe can reach it.

    Returns ``None``, saving nothing, when there is no control socket:
    ``ssh -f`` forks and never reports the daemon's pid, so without a socket
    there is no pid and no handle for a later ``pyssh status``/``stop`` to act
    on -- a state with neither would just be pruned the moment it is read.
    """
    socket_path = session.control_path(name)
    password_file = _password_file(target.password, "c")
    try:
        cmd = build_ssh_command(
            target,
            [*forwards, *command.background_args(socket_path)],
            identity=identity,
            password_file=password_file,
            extra_opts=[f"ConnectTimeout={CONNECT_TIMEOUT_SECONDS}", *ssh_options],
            strict_host_keys=bool(target.password),
        )
        completed = session.run_background(cmd, verbose)
    finally:
        if password_file is not None:
            password_file.unlink(missing_ok=True)

    if completed.returncode != 0:
        endpoint_host, endpoint_port = _target_endpoint(target)
        hint = knownhosts.failure_hint(
            completed.stderr, target.spec, endpoint_host, port=endpoint_port
        )
        detail = completed.stderr.strip() or f"ssh exited with code {completed.returncode}"
        raise click.ClickException(f"{detail}\n{hint}" if hint else detail)

    _wait_for_listeners(listeners, STARTUP_TIMEOUT_SECONDS)

    if socket_path is None:
        console.warn(
            "This connection has no control socket (unsupported on Windows, or "
            "the runtime path would be too long), so pyssh cannot track it with "
            "`pyssh status` or `pyssh stop`. It is running in the background "
            "regardless -- you will need to stop it yourself."
        )
        return None

    pid = session.master_pid(socket_path, target.spec)
    state = {
        "kind": kind,
        "pids": [pid] if pid else [],
        "control": str(socket_path),
        "destination": target.spec,
        "forwards": list(forwards),
        "server": str(target),
        "started_at": time.time(),
    }
    save_state(name, state)
    return state


def build_ssh_command(
    server: Union[Server, hosts.Target],
    forward_args: Sequence[str],
    identity: Optional[str] = None,
    password_file: Optional[Path] = None,
    extra_opts: Sequence[str] = (),
    keepalive: bool = True,
    no_command: bool = True,
    strict_host_keys: bool = False,
) -> list[str]:
    """Assemble the ssh command line for a tunnel or a session.

    A target built from an ssh config name carries no port, so no ``-p`` is
    emitted and the config's own ``Port`` survives.
    """
    target = server if isinstance(server, hosts.Target) else hosts.Target.from_server(server)
    cmd: list[str] = ["ssh"]
    if no_command:
        cmd.append("-N")
    cmd.extend(forward_args)
    if target.port is not None:
        cmd += ["-p", str(target.port)]
    if keepalive:
        # Without these a dropped link leaves the tunnel silently dead.
        cmd[1:1] = [
            "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3",
            "-o", "ExitOnForwardFailure=yes",
        ]
    if identity:
        cmd[1:1] = ["-i", str(Path(identity).expanduser())]
    for opt in extra_opts:
        cmd[1:1] = ["-o", opt]
    if password_file is not None:
        # sshpass answers the password prompt and nothing else, so a host-key
        # prompt would hang. The new commands verify the key up front instead
        # and ask for strictness here; the older ones keep accept-new.
        policy = "yes" if strict_host_keys else "accept-new"
        cmd[1:1] = ["-o", f"StrictHostKeyChecking={policy}"]
    # ``--`` ends option parsing. Without it a destination beginning with ``-`` is read
    # by ssh as an option, and whatever follows becomes the host -- which is how
    # -oProxyCommand=... turns into remote code execution on the commands that append a
    # remote command. Nothing may be appended after the destination but that command.
    cmd += ["--", target.spec]
    if password_file is not None:
        cmd = ["sshpass", "-f", str(password_file)] + cmd
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
    target = apply_stored_secret(hosts.resolve_connection(server, server_conf, "-s/--server"))
    bind_host = "0.0.0.0" if public else "127.0.0.1"

    if not port_is_free(local_port, bind_host):
        raise click.ClickException(
            f"Local port {local_port} is already in use. Pick another with -p, "
            "or run `pyssh status` to see tunnels started by pyssh."
        )

    session = TunnelSession(name=f"tunnel-{local_port}", verbose=verbose)

    def start() -> None:
        password_file = session.track_secret(_password_file(target.password, "t"))
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


def _second_hop_address(target: hosts.Target) -> hosts.Server:
    """Where hop 1 should forward to, and who hop 2 logs in as.

    An inline spec already says. A config name does not, so ssh is asked --
    the first hop needs a real address and port to build its ``-L``.
    """
    if not target.is_config_name:
        user, _, host = target.spec.partition("@")
        return Server(user=user, host=host, port=target.port or 22, password=target.password)
    resolved = hosts.resolve_config(target.spec)
    if resolved is None or not resolved.user:
        raise click.ClickException(
            f"Could not work out where {target.spec!r} points. Add it to ~/.ssh/config "
            "with a HostName and a User, or pass --server2 as 'user@host:port'."
        )
    return Server(
        user=resolved.user,
        host=resolved.hostname,
        port=resolved.port,
        password=target.password,
    )


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
    first = apply_stored_secret(hosts.resolve_connection(server1, server1_conf, "--server1"))
    second = apply_stored_secret(hosts.resolve_connection(server2, server2_conf, "--server2"))
    second_address = _second_hop_address(second)
    bind_host = "0.0.0.0" if public else "127.0.0.1"

    for port in (lp1, lp2):
        if not port_is_free(port, "127.0.0.1" if port == lp1 else bind_host):
            raise click.ClickException(f"Local port {port} is already in use.")

    session = TunnelSession(name=f"double-{lp2}", verbose=verbose)

    def start() -> None:
        pass1 = session.track_secret(_password_file(first.password, "d1"))
        hop1 = build_ssh_command(
            first,
            ["-L", f"127.0.0.1:{lp1}:{second_address.host}:{second_address.port}"],
            identity=identity,
            password_file=pass1,
            extra_opts=ssh_options,
        )
        process1 = session.spawn(hop1)
        _wait_for_listener(lp1, "127.0.0.1", process1, STARTUP_TIMEOUT_SECONDS)

        # Second hop dials the forwarded local port, so it always talks to localhost.
        via_local = Server(
            user=second_address.user,
            host="127.0.0.1",
            port=lp1,
            password=second_address.password,
        )
        pass2 = session.track_secret(_password_file(via_local.password, "d2"))
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


#: ``[user[:password]@]host:path`` -- the host part of a remote rsync target.
RSYNC_HOST_RE = re.compile(r"^(?:(?P<user>[^@:/]+)(?::[^@]*)?@)?(?P<host>[^@:/]+):(?!/{2})")


def rsync_host_of(spec: str) -> Optional[str]:
    """The host an rsync target refers to, or ``None`` for a local path.

    Used to look up a stored secret. The target string itself is never
    rewritten: ssh resolves config host names for rsync already.
    """
    match = RSYNC_HOST_RE.match(spec)
    return match.group("host") if match else None


def _rsync_ssh_command(
    ssh_port: int, identity: Optional[str], ssh_options: tuple[str, ...], password: Optional[str]
) -> str:
    """Build the ``-e`` transport rsync should use, as one shell-quoted string."""
    parts = ["ssh", "-p", str(ssh_port)]
    if identity:
        parts += ["-i", str(Path(identity).expanduser())]
    for option in ssh_options:
        parts += ["-o", option]
    if password:
        # Host-key prompts cannot be answered when sshpass drives ssh.
        parts += ["-o", "StrictHostKeyChecking=accept-new"]
    return shlex.join(parts)


def _run_rsync(cmd: list[str], password: Optional[str], verbose: int) -> None:
    """Run a built rsync command, feeding sshpass a password file when needed."""
    password_file = None
    if password:
        password_file = _password_file(password, "rsync")
        cmd = ["sshpass", "-f", str(password_file)] + cmd

    console.info(f"$ {' '.join(cmd)}", verbose, threshold=0)
    try:
        result = subprocess.run(cmd, check=False)
    finally:
        if password_file is not None:
            password_file.unlink(missing_ok=True)
    if result.returncode != 0:
        raise click.ClickException(f"rsync exited with code {result.returncode}.")


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
    if password is None:
        for spec in (source, destination):
            host = rsync_host_of(spec)
            if host and "@" not in spec.split(":", 1)[0]:
                password = store.get_secret(host)
                if password:
                    break

    if exclude_from:
        exclude = (*exclude, *rsync.read_pattern_file(Path(exclude_from)))
    if match_from:
        match = (*match, *rsync.read_pattern_file(Path(match_from)))

    plan = rsync.RsyncOptions(
        source=source,
        destination=destination,
        ssh_command=_rsync_ssh_command(ssh_port, identity, ssh_options, password),
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

    _run_rsync(cmd, password, verbose)


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
        forwards = " ".join(state.get("forwards", []))
        socks = (
            f"socks5://{state.get('bind', '127.0.0.1')}:{state['socks_port']}"
            if state.get("socks_port")
            else forwards
        )
        rows.append(
            {
                "name": Path(state["_file"]).stem,
                "kind": state.get("kind", ""),
                "socks": socks,
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
        control = state.get("control")
        destination = state.get("destination")
        if not (control and destination and session.stop_master(Path(control), destination)):
            terminate(state.get("pids", []))
        Path(state["_file"]).unlink(missing_ok=True)
        console.result(f"Stopped {Path(state['_file']).stem}.")


# ═══════════════════════════════════════════════════════════════════
# Secrets and tags
# ═══════════════════════════════════════════════════════════════════


@ssh_management.group(cls=AliasedGroup)
def secret() -> None:
    """Passwords for hosts in your ~/.ssh/config.

    \b
    Key authentication is always preferable; this is for the servers that
    will not have it. Passwords go to the OS keyring when there is one,
    and never onto a command line.

    \b
    Examples:
      pyssh secret set prod-web
      pyssh secret list
      pyssh secret rm prod-web
    """


@secret.command("set")
@click.argument("name")
@click.option(
    "--insecure-plaintext",
    is_flag=True,
    help="Store the password in the 0600 config file when no keyring works.",
)
def secret_set(name: str, insecure_plaintext: bool) -> None:
    """Store the password for NAME, prompting for it.

    \b
    Examples:
      pyssh secret set prod-web
      pyssh secret set termux-box --insecure-plaintext
    """
    password = click.prompt("Password", hide_input=True, confirmation_prompt=True, err=True)
    tier = store.set_secret(name, password, allow_plaintext=insecure_plaintext)
    if tier == store.TIER_PLAINTEXT:
        console.warn(f"{name}'s password is stored in plain text at {store.store_path()}.")
    console.success(f"Stored {name}'s password ({tier}).")


@secret.command("rm")
@click.argument("name")
def secret_rm(name: str) -> None:
    """Forget the password for NAME.

    \b
    Examples:
      pyssh secret rm prod-web
    """
    if not store.remove_secret(name):
        raise click.ClickException(f"No password is stored for {name!r}.")
    console.success(f"Forgot {name}'s password.")


@secret.command("list")
@json_option
def secret_list(as_json: bool) -> None:
    """List hosts with a stored password, and where it is kept.

    \b
    Examples:
      pyssh secret list
      pyssh secret list --json
    """
    rows = [
        {"name": item.name, "tier": item.tier}
        for item in store.entries()
        if item.tier != store.TIER_NONE
    ]
    if not rows and not as_json:
        console.result("No passwords are stored.")
        return
    console.print_rows(rows, ["name", "tier"], as_json=as_json)


@ssh_management.group(cls=AliasedGroup, name="hosts", invoke_without_command=True)
@click.option("-t", "--tag", "tag_filter", help="Only hosts carrying this tag.")
@json_option
@click.pass_context
def hosts_cmd(ctx: click.Context, tag_filter: Optional[str], as_json: bool) -> None:
    """List the hosts pyssh can reach, and manage their tags.

    \b
    Names come from ~/.ssh/config; tags and stored passwords come from
    pyssh. A name pyssh has a tag or password for is listed even when it
    is not in your ssh config.

    \b
    Examples:
      pyssh hosts
      pyssh hosts --tag prod --json
      pyssh hosts tag add prod web1 web2
    """
    if ctx.invoked_subcommand is not None:
        return

    stored = {item.name: item for item in store.entries()}
    names = list(hosts.config_host_names())
    names += [name for name in sorted(stored) if name not in names]

    rows = []
    for name in names:
        item = stored.get(name)
        tags = list(item.tags) if item else []
        if tag_filter and tag_filter not in tags:
            continue
        rows.append(
            {
                "name": name,
                "tags": ",".join(tags),
                "secret": item.tier if item else store.TIER_NONE,
            }
        )

    if not rows and not as_json:
        console.result("No hosts found. Add one to ~/.ssh/config.")
        return
    console.print_rows(rows, ["name", "tags", "secret"], as_json=as_json)


@hosts_cmd.group(cls=AliasedGroup, name="tag")
def hosts_tag() -> None:
    """Group hosts so `pyssh exec --tag` can address them together.

    \b
    Examples:
      pyssh hosts tag add prod web1 web2
      pyssh hosts tag rm prod web2
    """


@hosts_tag.command("add")
@click.argument("tag_name")
@click.argument("names", nargs=-1, required=True)
def hosts_tag_add(tag_name: str, names: tuple[str, ...]) -> None:
    """Tag each of NAMES with TAG_NAME.

    \b
    Examples:
      pyssh hosts tag add prod web1 web2
      pyssh hosts tag add db mpars-bi
    """
    for name in names:
        store.add_tags(name, [tag_name])
    console.success(f"Tagged {console.plural(len(names), 'host')} with {tag_name!r}.")


@hosts_tag.command("rm")
@click.argument("tag_name")
@click.argument("names", nargs=-1, required=True)
def hosts_tag_rm(tag_name: str, names: tuple[str, ...]) -> None:
    """Remove TAG_NAME from each of NAMES.

    \b
    Examples:
      pyssh hosts tag rm prod web2
    """
    for name in names:
        store.remove_tags(name, [tag_name])
    console.success(f"Removed {tag_name!r} from {console.plural(len(names), 'host')}.")


# ═══════════════════════════════════════════════════════════════════
# Connect
# ═══════════════════════════════════════════════════════════════════


@ssh_management.command()
@click.argument("name")
@click.option("-L", "--local", "local_forwards", multiple=True, metavar="SPEC",
              help="Forward a local port to the remote side: port:host:hostport. Repeatable.")
@click.option("-R", "--remote", "remote_forwards", multiple=True, metavar="SPEC",
              help="Forward a remote port back here: port:host:hostport, or a bare "
                   "port for a SOCKS proxy the server can use. Repeatable.")
@click.option("-D", "--dynamic", "dynamic_forwards", multiple=True, metavar="SPEC",
              help="Open a local SOCKS5 proxy on PORT. Repeatable.")
@click.option("-N", "--no-command", is_flag=True, help="Forward only; do not start a shell.")
@click.option("-t", "--tty", is_flag=True, help="Force a TTY (for interactive remote programs).")
@click.option("-b", "--background", is_flag=True,
              help="Return once the forwards are up, leaving them running.")
@click.option("--public", is_flag=True,
              help="Bind local forwards to 0.0.0.0 so the LAN can reach them.")
@click.option("-i", "--identity", type=click.Path(dir_okay=False), help="Private key file.")
@click.option("-o", "--ssh-option", "ssh_options", multiple=True,
              help="Extra `ssh -o` option, repeatable.")
@verbose_option
def connect(
    name: str,
    local_forwards: tuple[str, ...],
    remote_forwards: tuple[str, ...],
    dynamic_forwards: tuple[str, ...],
    no_command: bool,
    tty: bool,
    background: bool,
    public: bool,
    identity: Optional[str],
    ssh_options: tuple[str, ...],
    verbose: int,
) -> None:
    """Open a connection to NAME, with any forwards you need.

    \b
    NAME is a host in your ~/.ssh/config, or a 'user@host:port' spec. With no
    forwards this is an interactive shell; -L, -R and -D can be combined and
    repeated in one connection.

    \b
    Examples:
      pyssh connect prod
      pyssh connect prod -L 5432:db.internal:5432 -b
      pyssh connect prod -R 8080:localhost:3000 -D 1080 -b
    """
    _require("ssh", "Install OpenSSH (Termux: `pkg install openssh`).")
    target = apply_stored_secret(hosts.resolve_target(name))
    _guard_host_key(target)

    forwards = command.forward_args(
        local=local_forwards, remote=remote_forwards, dynamic=dynamic_forwards, public=public
    )
    listeners = command.local_listeners(
        local=local_forwards, dynamic=dynamic_forwards, public=public
    )

    if public and remote_forwards:
        console.warn(
            "A remote forward binds the server's loopback unless its sshd sets "
            "'GatewayPorts yes' or 'clientspecified'. Check there if other machines "
            "cannot reach it."
        )

    for host, port in listeners:
        if not port_is_free(port, host):
            raise click.ClickException(
                f"Local port {port} is already in use. Pick another, or run "
                "`pyssh status` to see sessions started by pyssh."
            )

    if background:
        session_name = f"connect-{listeners[0][1] if listeners else os.getpid()}"
        state = _open_background_session(
            target, forwards, listeners, session_name, "connect",
            identity, ssh_options, verbose,
        )
        console.success(f"Connected to {target.spec} in the background.", verbose)
        if state is not None:
            console.echo(f"Stop it with: pyssh stop {session_name}", err=True)
        return

    password_file = _password_file(target.password, "c")
    try:
        cmd = build_ssh_command(
            target,
            [*forwards, *(["-t"] if tty else [])],
            identity=identity,
            password_file=password_file,
            extra_opts=ssh_options,
            no_command=no_command or bool(forwards and not tty),
            strict_host_keys=bool(target.password),
        )
        console.info(f"$ {' '.join(cmd)}", verbose, threshold=1)
        raise SystemExit(subprocess.call(cmd))
    finally:
        if password_file is not None:
            password_file.unlink(missing_ok=True)


def _preset_session(
    name: str,
    local: Sequence[str],
    remote: Sequence[str],
    dynamic: Sequence[str],
    public: bool,
    background: bool,
    identity: Optional[str],
    ssh_options: Sequence[str],
    verbose: int,
) -> None:
    """Shared body for the forward/reverse presets."""
    ctx = click.get_current_context()
    ctx.invoke(
        connect,
        name=name,
        local_forwards=local,
        remote_forwards=remote,
        dynamic_forwards=dynamic,
        no_command=True,
        tty=False,
        background=background,
        public=public,
        identity=identity,
        ssh_options=ssh_options,
        verbose=verbose,
    )


@ssh_management.command()
@click.argument("name")
@click.option("-L", "--local", "local_forwards", multiple=True, required=True, metavar="SPEC",
              help="port:host:hostport, or bind:port:host:hostport. Repeatable.")
@click.option("-b", "--background", is_flag=True, help="Return once the forward is up.")
@click.option("--public", is_flag=True, help="Bind 0.0.0.0 so the LAN can reach it.")
@click.option("-i", "--identity", type=click.Path(dir_okay=False), help="Private key file.")
@click.option("-o", "--ssh-option", "ssh_options", multiple=True, help="Extra `ssh -o` option.")
@verbose_option
def forward(
    name: str,
    local_forwards: tuple[str, ...],
    background: bool,
    public: bool,
    identity: Optional[str],
    ssh_options: tuple[str, ...],
    verbose: int,
) -> None:
    """Bring a remote service to a local port.

    \b
    Examples:
      pyssh forward prod -L 5432:db.internal:5432
      pyssh forward prod -L 6379:cache:6379 -b
    """
    _preset_session(
        name, local_forwards, (), (), public, background, identity, ssh_options, verbose
    )


@ssh_management.command()
@click.argument("name")
@click.option("-R", "--remote", "remote_forwards", multiple=True, required=True, metavar="SPEC",
              help="port:host:hostport, or a bare port for a SOCKS proxy the server can use.")
@click.option("-b", "--background", is_flag=True, help="Return once the forward is up.")
@click.option("--public", is_flag=True,
              help="Ask the server to bind 0.0.0.0. Needs GatewayPorts there.")
@click.option("-i", "--identity", type=click.Path(dir_okay=False), help="Private key file.")
@click.option("-o", "--ssh-option", "ssh_options", multiple=True, help="Extra `ssh -o` option.")
@verbose_option
def reverse(
    name: str,
    remote_forwards: tuple[str, ...],
    background: bool,
    public: bool,
    identity: Optional[str],
    ssh_options: tuple[str, ...],
    verbose: int,
) -> None:
    """Expose a local service on the remote server.

    \b
    A bare port -- `-R 1080` -- gives the server a SOCKS proxy through your
    machine instead, which is how a locked-down box gets internet access.

    \b
    Examples:
      pyssh reverse prod -R 8080:localhost:3000
      pyssh reverse prod -R 1080 -b
    """
    _preset_session(
        name, (), remote_forwards, (), public, background, identity, ssh_options, verbose
    )


# ═══════════════════════════════════════════════════════════════════
# Exec
# ═══════════════════════════════════════════════════════════════════


@ssh_management.command(
    "exec", context_settings={**CONTEXT_SETTINGS, "ignore_unknown_options": True}
)
@click.argument("args", nargs=-1, required=True)
@click.option("--tag", help="Run on every host carrying this tag instead of one NAME.")
@click.option("-P", "--parallel", default=1, show_default=True, type=click.IntRange(1, 64),
              help="How many hosts to run on at once.")
@click.option("--cd", "workdir", metavar="DIR", help="Run the command in DIR.")
@click.option("--env", "env_pairs", multiple=True, metavar="NAME=VALUE",
              help="Export a variable before running. Repeatable.")
@click.option("--sudo", is_flag=True, help="Run as root with `sudo -n`. Needs passwordless sudo.")
@click.option("-t", "--tty", is_flag=True, help="Force a TTY, for interactive remote programs.")
@click.option("-i", "--identity", type=click.Path(dir_okay=False), help="Private key file.")
@click.option("-o", "--ssh-option", "ssh_options", multiple=True, help="Extra `ssh -o` option.")
@json_option
@verbose_option
def exec_command(
    args: tuple[str, ...],
    tag: Optional[str],
    parallel: int,
    workdir: Optional[str],
    env_pairs: tuple[str, ...],
    sudo: bool,
    tty: bool,
    identity: Optional[str],
    ssh_options: tuple[str, ...],
    as_json: bool,
    verbose: int,
) -> None:
    """Run a command on one host, or on every host with a tag.

    \b
    Usage is `pyssh exec NAME COMMAND...` or `pyssh exec --tag TAG COMMAND...`.
    Arguments are joined with spaces and interpreted by the remote shell,
    exactly as `ssh host cmd` does, so quote anything with pipes or globs you
    want the far side to expand. One host's output passes straight through, so
    it can be piped and redirected; a group's is prefixed with the host name.

    \b
    Examples:
      pyssh exec prod 'uptime'
      pyssh exec prod --cd /srv/app --env CI=1 'git pull && make'
      pyssh exec --tag prod -P 8 'systemctl is-active nginx'
    """
    _require("ssh", "Install OpenSSH (Termux: `pkg install openssh`).")

    if tag:
        names = store.names_with_tag(tag)
        if not names:
            raise click.ClickException(
                f"No hosts are tagged {tag!r}. Tag some with `pyssh hosts tag add {tag} NAME`."
            )
        remote_command = " ".join(args)
    else:
        if len(args) < 2:
            raise click.ClickException(
                "Provide a host and a command, or use --tag. Example: pyssh exec prod 'uptime'."
            )
        names = [args[0]]
        remote_command = " ".join(args[1:])

    wrapped = remote.wrap_command(remote_command, workdir=workdir, env=env_pairs, sudo=sudo)
    grouped = bool(tag)

    jobs: list[tuple[str, list[str]]] = []
    secret_files: list[Path] = []
    try:
        for name in names:
            target = apply_stored_secret(hosts.resolve_target(name))
            _guard_host_key(target)
            password_file = _password_file(target.password, f"x{len(secret_files)}")
            if password_file is not None:
                secret_files.append(password_file)
            # A captured worker cannot answer a prompt, so a group refuses to be
            # asked. A stored password goes through sshpass and never prompts.
            options = list(ssh_options)
            if grouped and not target.password:
                options.append("BatchMode=yes")
            cmd = build_ssh_command(
                target,
                ["-t"] if tty else [],
                identity=identity,
                password_file=password_file,
                extra_opts=options,
                no_command=False,
                strict_host_keys=bool(target.password),
            )
            cmd.append(wrapped)
            console.info(f"$ {' '.join(cmd)}", verbose, threshold=1)
            jobs.append((name, cmd))

        if not grouped:
            result = remote.run(jobs[0][1], jobs[0][0], capture=as_json)
            if as_json:
                console.emit_json([_exec_row(result)])
            raise SystemExit(result.returncode)

        results = remote.run_many(jobs, parallel=parallel)
    finally:
        for path in secret_files:
            with suppress(OSError):
                path.unlink(missing_ok=True)

    if as_json:
        console.emit_json([_exec_row(item) for item in results])
    else:
        for item in results:
            for line in item.stdout.splitlines():
                console.result(f"{item.name} | {line}")
            for line in item.stderr.splitlines():
                console.echo(f"{item.name} | {line}", err=True)
            if not item.ok:
                console.error(f"{item.name} exited with code {item.returncode}.")

    failed = [item.name for item in results if not item.ok]
    if failed:
        raise SystemExit(1)
    console.success(f"Ran on {console.plural(len(results), 'host')}.", verbose)


def _exec_row(result: remote.ExecResult) -> dict:
    return {
        "name": result.name,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


if __name__ == "__main__":  # pragma: no cover
    ssh_management()
