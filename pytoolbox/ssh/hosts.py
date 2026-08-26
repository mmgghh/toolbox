"""Turning a name into something ssh can be pointed at.

pyssh keeps no server inventory of its own: ``~/.ssh/config`` already is one.
A name that is not an inline ``user@host`` spec is handed to ssh unchanged, and
``ssh -G`` is consulted only when pyssh itself needs to know what that name
resolves to.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import click

SERVER_SPEC_RE = re.compile(
    r"^(?P<user>[^@:/]+)(?::(?P<password>[^@]*))?@(?P<host>[^@:/]+)(?::(?P<port>\d+))?$"
)


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


@dataclass(frozen=True)
class Target:
    """A destination ssh can be pointed at.

    ``spec`` is handed to ssh as its destination argument: either a
    ``user@host`` built from an inline spec, or an ``~/.ssh/config`` host name
    passed through untouched. ``port`` is ``None`` for a config name, so that
    pyssh never overrides the ``Port`` the config already sets.
    """

    spec: str
    port: Optional[int] = None
    password: Optional[str] = None

    @classmethod
    def from_server(cls, server: Server) -> Target:
        """Build a target from a parsed inline spec."""
        return cls(spec=server.target, port=server.port, password=server.password)

    @classmethod
    def from_name(cls, name: str) -> Target:
        """Build a target from an ssh config host name."""
        return cls(spec=name)

    @property
    def is_config_name(self) -> bool:
        """Whether ssh config, rather than pyssh, decides how to connect."""
        return "@" not in self.spec

    def with_password(self, password: Optional[str]) -> Target:
        """Return a copy carrying ``password``."""
        return replace(self, password=password)

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.spec if self.port is None else f"{self.spec}:{self.port}"


def resolve_target(value: str) -> Target:
    """Resolve a ``-s/--server`` value to a target.

    A value containing ``@`` must parse as an inline spec; anything else is an
    ssh config host name and is handed to ssh unchanged. The two can never
    collide, because :data:`SERVER_SPEC_RE` requires an ``@``.
    """
    raw = value.strip()
    if not raw:
        raise click.ClickException("Provide a server name or a 'user@host' spec.")
    if "@" in raw:
        return Target.from_server(parse_server(raw))
    return Target.from_name(raw)


def resolve_connection(spec: Optional[str], conf: Optional[str], label: str) -> Target:
    """Resolve a target from an inline value or a config file."""
    if spec and conf:
        raise click.ClickException(f"Use either {label} or {label}-conf, not both.")
    if spec:
        return resolve_target(spec)
    if conf:
        return Target.from_server(load_server_conf(Path(conf)))
    raise click.ClickException(f"Provide {label} or {label}-conf.")


#: How long to wait for ``ssh -G``. It does no network I/O, so this only
#: guards against a pathological config.
CONFIG_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class ResolvedConfig:
    """What ssh would use for a host name, as reported by ``ssh -G``."""

    name: str
    hostname: str
    user: str
    port: int
    identity_files: tuple[str, ...] = ()
    proxy_jump: Optional[str] = None


def parse_ssh_g(name: str, text: str) -> ResolvedConfig:
    """Parse ``ssh -G`` output.

    Keys are lowercase and repeat only for ``identityfile``; for everything
    else ssh prints the winning value first, so later lines are ignored.
    """
    values: dict[str, str] = {}
    identities: list[str] = []
    for line in text.splitlines():
        key, _, value = line.strip().partition(" ")
        key = key.lower()
        value = value.strip()
        if not key or not value:
            continue
        if key == "identityfile":
            identities.append(value)
        elif key not in values:
            values[key] = value

    port = values.get("port", "22")
    jump = values.get("proxyjump")
    return ResolvedConfig(
        name=name,
        hostname=values.get("hostname", name),
        user=values.get("user", ""),
        port=int(port) if port.isdigit() else 22,
        identity_files=tuple(identities),
        proxy_jump=None if jump in (None, "none") else jump,
    )


def resolve_config(name: str, options: Sequence[str] = ()) -> Optional[ResolvedConfig]:
    """Ask ssh what ``name`` resolves to, or ``None`` if it cannot be asked.

    Returning ``None`` rather than raising is deliberate: pyssh only needs this
    when it must know a hostname or port for itself. Connecting works without
    it, because ssh does the same resolution again anyway.
    """
    if shutil.which("ssh") is None:
        return None
    cmd = ["ssh", "-G"]
    for option in options:
        cmd += ["-o", option]
    cmd.append(name)
    try:
        completed = subprocess.run(
            cmd, capture_output=True, text=True, timeout=CONFIG_TIMEOUT_SECONDS
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return parse_ssh_g(name, completed.stdout)
