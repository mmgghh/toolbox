"""Running a command on one remote host, or on several at once.

The command is handed to ssh as a single argument and interpreted by the
*remote* shell -- the same contract as ``ssh host 'cmd'``. That is why pipes
and redirections work, and why the command itself is never quoted here.
"""

from __future__ import annotations

import re
import shlex
import subprocess
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

import click

#: A shell variable name: letters, digits and underscores, not starting with a digit.
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def wrap_command(
    remote_command: str,
    workdir: Optional[str] = None,
    env: Sequence[str] = (),
    sudo: bool = False,
) -> str:
    """Wrap a command with the working directory, environment and sudo asked for.

    ``export`` is used rather than ``env VAR=x cmd`` so that a pipeline sees
    the variable too, and ``sudo -n`` never prompts -- a prompt would hang a
    worker whose output is being captured.
    """
    body = (remote_command or "").strip()
    if not body:
        raise click.ClickException("Provide a command to run.")

    prefix: list[str] = []
    for pair in env:
        name, sep, value = pair.partition("=")
        if not sep or not ENV_NAME_RE.fullmatch(name):
            raise click.ClickException(
                f"{pair!r} is not a NAME=VALUE pair. Example: --env DEPLOY_ENV=staging."
            )
        prefix.append(f"export {name}={shlex.quote(value)};")

    if workdir:
        prefix.append(f"cd -- {shlex.quote(workdir)} &&")
    if sudo:
        body = f"sudo -n {body}"
    return " ".join([*prefix, body])


@dataclass(frozen=True)
class ExecResult:
    """What one host did with the command."""

    name: str
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        """Whether the remote command succeeded."""
        return self.returncode == 0


def run(cmd: Sequence[str], name: str, capture: bool) -> ExecResult:
    """Run one ssh command line.

    Without ``capture`` the child inherits this process's streams, so a single
    host can be piped and redirected exactly like plain ssh. A local failure to
    even spawn the child (missing binary, EMFILE under a high --parallel) is
    turned into a normal ExecResult rather than left to propagate, so one bad
    host in run_many cannot discard every other host's result.
    """
    try:
        if not capture:
            return ExecResult(name=name, returncode=subprocess.call(list(cmd)))
        completed = subprocess.run(list(cmd), capture_output=True, text=True)
    except (OSError, subprocess.SubprocessError) as exc:
        return ExecResult(name=name, returncode=255, stderr=str(exc))
    return ExecResult(
        name=name,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_many(jobs: Sequence[tuple[str, Sequence[str]]], parallel: int = 1) -> list[ExecResult]:
    """Run a command on several hosts, at most ``parallel`` at a time.

    Results come back in the order the jobs were given, whatever order they
    finished in, so output is stable between runs.
    """
    if parallel <= 1:
        return [run(cmd, name, capture=True) for name, cmd in jobs]
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = [pool.submit(run, cmd, name, True) for name, cmd in jobs]
        return [future.result() for future in futures]
