"""Refusing to hand a password to a host we cannot identify.

``sshpass`` answers the password prompt and nothing else -- it cannot answer a
host-key prompt, which is why driving ssh with it normally means turning host
key checking down to ``accept-new``. That combination sends your password to
whoever answers, so the commands that run remote code check first and stop,
naming the command that fixes it.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import Optional

import click

#: What ssh prints when a stored key no longer matches.
CHANGED_KEY_MARKER = "REMOTE HOST IDENTIFICATION HAS CHANGED"

#: ssh-keygen -F does no network I/O; this only guards a pathological file.
LOOKUP_TIMEOUT_SECONDS = 10


def _known_hosts_key(host: str, port: int = 22) -> str:
    """How ``known_hosts`` files a host. A non-default port is bracketed."""
    return host if port in (None, 22) else f"[{host}]:{port}"


def is_known(host: str, port: int = 22) -> bool:
    """Whether ``known_hosts`` already holds a key for this host.

    Being unable to check is not the same as being unsafe: without
    ``ssh-keygen`` we report the host as known and let ssh do its own
    checking, rather than blocking every command on a missing binary.
    """
    if shutil.which("ssh-keygen") is None:
        return True
    try:
        completed = subprocess.run(
            ["ssh-keygen", "-F", _known_hosts_key(host, port)],
            capture_output=True,
            text=True,
            timeout=LOOKUP_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError):  # pragma: no cover - defensive
        return True
    return completed.returncode == 0


def unknown_host_message(name: str, host: str) -> str:
    """What to tell someone whose host is not in known_hosts.

    It names plain ``ssh`` deliberately: ``pyssh connect`` would supply the
    stored password and hit this same refusal, and only an interactive ssh can
    show a fingerprint to check.
    """
    return (
        f"The host key for {name} is not in ~/.ssh/known_hosts, so pyssh will not\n"
        "send your password to it. Verify the fingerprint out of band, then connect\n"
        "once with plain ssh and accept it:\n"
        f"    ssh {name}\n"
        "or trust it without a prompt:\n"
        f"    ssh-keyscan -H {host} >> ~/.ssh/known_hosts"
    )


def changed_key_message(name: str, host: str, address: Optional[str] = None) -> str:
    """What to tell someone whose host key no longer matches."""
    lines = [
        f"The host key for {name} has changed. This is what a machine-in-the-middle",
        "attack looks like; it is also what rebuilding a server looks like.",
        "If you rebuilt it, remove the stale key and reconnect:",
        f"    ssh-keygen -R '{host}'",
    ]
    if address and address != host:
        lines.append(f"    ssh-keygen -R '{address}'      # the resolved address, keyed separately")
    return "\n".join(lines)


def require_known(name: str, host: str, port: int = 22) -> None:
    """Stop unless the host is already in known_hosts."""
    if is_known(host, port):
        return
    raise click.ClickException(unknown_host_message(name, host))


def failure_hint(
    stderr: str, name: str, host: str, address: Optional[str] = None
) -> Optional[str]:
    """A remediation for an ssh failure, or ``None`` if it is not a key problem."""
    if CHANGED_KEY_MARKER in stderr:
        return changed_key_message(name, host, address)
    return None
