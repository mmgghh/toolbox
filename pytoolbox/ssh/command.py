"""Forward specifications, and the ssh arguments they become.

ssh accepts several shapes per forwarding flag and reports a bad one only
after connecting. Checking them here means a typo costs a message instead of
a handshake.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Optional

import click

#: Shapes accepted per flag, longest first, for the error message.
_SHAPES = {
    "-L": "port:host:hostport or bind:port:host:hostport",
    "-R": "port, bind:port, port:host:hostport or bind:port:host:hostport",
    "-D": "port or bind:port",
}


def _port(value: str, spec: str, flag: str) -> int:
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise click.ClickException(
            f"{value!r} in {flag} {spec!r} is not a port. Expected {_SHAPES[flag]}."
        )
    return int(value)


def _split(spec: str, flag: str) -> list[str]:
    parts = spec.split(":")
    if not spec or len(parts) > 4:
        raise click.ClickException(f"Invalid {flag} {spec!r}. Expected {_SHAPES[flag]}.")
    return parts


def _default_bind(public: bool) -> str:
    return "0.0.0.0" if public else "127.0.0.1"


def _local_or_dynamic(spec: str, flag: str, public: bool) -> str:
    """Normalise a -L or -D spec, filling in the local bind address."""
    parts = _split(spec, flag)
    wanted = (3, 4) if flag == "-L" else (1, 2)
    if len(parts) not in wanted:
        raise click.ClickException(f"Invalid {flag} {spec!r}. Expected {_SHAPES[flag]}.")

    if len(parts) == max(wanted):
        bind, rest = parts[0], parts[1:]
    else:
        bind, rest = _default_bind(public), parts

    _port(rest[0], spec, flag)
    if flag == "-L":
        if not rest[1]:
            raise click.ClickException(f"Invalid {flag} {spec!r}. Expected {_SHAPES[flag]}.")
        _port(rest[2], spec, flag)
    return ":".join([bind, *rest])


def _remote(spec: str, flag: str = "-R") -> str:
    """Validate a -R spec. The bind address is the server's business.

    A bare port makes ssh a SOCKS proxy for the server, so unlike -L the
    destination is optional. sshd binds the remote listener to loopback unless
    its GatewayPorts says otherwise, which is why no bind address is filled in
    here.
    """
    parts = _split(spec, flag)
    if len(parts) == 1:
        _port(parts[0], spec, flag)
    elif len(parts) == 2:
        _port(parts[1], spec, flag)
    elif len(parts) == 3:
        _port(parts[0], spec, flag)
        _port(parts[2], spec, flag)
    else:
        _port(parts[1], spec, flag)
        _port(parts[3], spec, flag)
    return spec


def forward_args(
    local: Sequence[str] = (),
    remote: Sequence[str] = (),
    dynamic: Sequence[str] = (),
    public: bool = False,
) -> list[str]:
    """Turn forward specs into ssh arguments, validating each one."""
    args: list[str] = []
    for spec in local:
        args += ["-L", _local_or_dynamic(spec, "-L", public)]
    for spec in remote:
        args += ["-R", _remote(spec)]
    for spec in dynamic:
        args += ["-D", _local_or_dynamic(spec, "-D", public)]
    return args


def local_listeners(
    local: Sequence[str] = (), dynamic: Sequence[str] = (), public: bool = False
) -> list[tuple[str, int]]:
    """The ``(host, port)`` pairs that will listen on this machine.

    Remote forwards are absent by definition: their listener is on the server,
    where no local probe can observe it.
    """
    listeners: list[tuple[str, int]] = []
    for spec, flag in [(s, "-L") for s in local] + [(s, "-D") for s in dynamic]:
        parts = _local_or_dynamic(spec, flag, public).split(":")
        listeners.append((parts[0], int(parts[1])))
    return listeners


def background_args(socket_path: Optional[Path]) -> list[str]:
    """Arguments that put ssh in the background with a control socket.

    ``-f`` makes ssh fork after authentication -- and, with
    ``ExitOnForwardFailure=yes``, only after every remote forward is
    established, so its exit status is a readiness signal. ``-M -S`` gives a
    handle that survives PID reuse.
    """
    args = ["-f"]
    if socket_path is not None:
        args += ["-M", "-S", str(socket_path)]
    return args
