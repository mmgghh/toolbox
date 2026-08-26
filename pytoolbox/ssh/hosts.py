"""Turning a name into something ssh can be pointed at.

pyssh keeps no server inventory of its own: ``~/.ssh/config`` already is one.
A name that is not an inline ``user@host`` spec is handed to ssh unchanged, and
``ssh -G`` is consulted only when pyssh itself needs to know what that name
resolves to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
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
