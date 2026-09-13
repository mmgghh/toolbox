"""Local + remote path autocomplete for pyssh's rsync target fields (`sync -s/-d`).

A typed value is either a local path -- completed the same way any other
path field is, plus a guess at `~/.ssh/config` host names once there's no
`/` yet to make it ambiguous -- or `[user[:password]@]host:path` /
`confighost:path`, completed by asking the real remote host over ssh once
the target is unambiguous. Remote listings are debounced and cached per
directory, since each one is a real network round trip rather than a local
directory read.
"""

from __future__ import annotations

import asyncio
import itertools
import shlex
from collections.abc import Iterable, Iterator
from typing import Optional

from textual import on
from textual.actions import SkipAction
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.suggester import Suggester
from textual.widgets import Input, OptionList

from pytoolbox.pyssh import RSYNC_HOST_RE
from pytoolbox.ssh import hosts
from pytoolbox.tui.paths import MAX_DROPDOWN_MATCHES, _matching_entries, _PathOptionList

#: How long to wait after the last keystroke before asking the remote host,
#: so typing out a whole path doesn't open one ssh connection per character.
REMOTE_DEBOUNCE_SECONDS = 0.4

#: How long ssh gets to establish the connection before this gives up on it.
SSH_CONNECT_TIMEOUT_SECONDS = 5

#: Overall budget for the connection *and* the remote `ls`, so a host that
#: accepts the connection but hangs afterwards can't stall the suggester.
_LS_TIMEOUT_SECONDS = SSH_CONNECT_TIMEOUT_SECONDS + 5


def parse_remote_target(value: str) -> Optional[tuple[str, str, str, str]]:
    """Split a typed value into an ssh target and the remote path being typed.

    Returns ``(ssh_target, remote_dir, shown_prefix, remote_prefix)``, or
    ``None`` if `value` isn't (yet) shaped like a remote rsync target -- in
    which case it should be completed as a local path instead. Mirrors
    `pytoolbox.tui.paths._matching_entries`'s split, just operating on the
    substring after the ``user@host:`` prefix.
    """
    match = RSYNC_HOST_RE.match(value)
    if match is None:
        return None
    host_end = match.end()
    user = match["user"]
    ssh_target = f"{user}@{match['host']}" if user else match["host"]
    remote_partial = value[host_end:]
    head, sep, remote_prefix = remote_partial.rpartition("/")
    if sep:
        remote_dir = head or "/"
        shown_prefix = f"{value[:host_end]}{head}/"
    else:
        remote_dir = "."
        shown_prefix = value[:host_end]
    return ssh_target, remote_dir, shown_prefix, remote_prefix


def _local_and_host_matches(value: str) -> Iterator[str]:
    """Local filesystem entries, plus matching `~/.ssh/config` host names.

    Host names only make sense before a "/" is typed -- once there's a
    slash, `value` is unambiguously a filesystem path.
    """
    yield from _matching_entries(value, dirs_only=False)
    if "/" not in value:
        for name in hosts.config_host_names():
            if name.startswith(value):
                yield f"{name}:"


def _filtered_entries(entries: Iterable[str], prefix: str) -> Iterator[str]:
    """`entries` starting with `prefix`, hiding dotfiles unless asked for."""
    show_hidden = prefix.startswith(".")
    for name in entries:
        if not name.startswith(prefix):
            continue
        if name.startswith(".") and not show_hidden:
            continue
        yield name


async def _ssh_list(ssh_target: str, remote_dir: str) -> list[str]:
    """Directory entries at `remote_dir` on `ssh_target`, or `[]` on any failure.

    `-o BatchMode=yes` means a host that would need a password prompt fails
    immediately instead of leaving the TUI waiting on input that never comes;
    autocomplete is best-effort, so any other failure (unreachable host, bad
    path, timeout) is swallowed the same way rather than surfaced.
    """
    remote_command = "ls -1Ap -- " + shlex.quote(remote_dir)
    cmd = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={SSH_CONNECT_TIMEOUT_SECONDS}",
        "--",
        ssh_target,
        remote_command,
    ]
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=_LS_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        if process is not None:
            process.kill()
        return []
    except OSError:
        return []
    if process.returncode != 0:
        return []
    return [line for line in stdout.decode("utf-8", "replace").splitlines() if line]


class RsyncTargetSuggester(Suggester):
    """Ghost-text completion for an rsync target: local path (or config host
    name) until the value is unambiguously remote, then a real directory
    listing on that host.
    """

    def __init__(self) -> None:
        # Every keystroke's value differs while debouncing is in flight, so
        # Suggester's own cache would just grow unboundedly; caching is done
        # here instead, per remote directory rather than per typed prefix.
        super().__init__(use_cache=False, case_sensitive=True)
        self._remote_cache: dict[tuple[str, str], list[str]] = {}
        self._generation = 0

    async def get_suggestion(self, value: str) -> Optional[str]:
        remote = parse_remote_target(value)
        if remote is None:
            return next(_local_and_host_matches(value), None)

        ssh_target, remote_dir, shown_prefix, remote_prefix = remote
        self._generation += 1
        my_generation = self._generation
        await asyncio.sleep(REMOTE_DEBOUNCE_SECONDS)
        if my_generation != self._generation:
            # A newer keystroke arrived while this one was debouncing --
            # let that one talk to the host instead.
            return None

        entries = await self._listing(ssh_target, remote_dir)
        return next(
            (f"{shown_prefix}{name}" for name in _filtered_entries(entries, remote_prefix)),
            None,
        )

    async def _listing(self, ssh_target: str, remote_dir: str) -> list[str]:
        key = (ssh_target, remote_dir)
        if key not in self._remote_cache:
            self._remote_cache[key] = await _ssh_list(ssh_target, remote_dir)
        return self._remote_cache[key]


async def list_remote_target_matches(value: str) -> list[str]:
    """Every matching entry for the Ctrl+Space dropdown.

    Unlike ghost-text, this runs once per explicit user action rather than
    per keystroke, so it always fetches a fresh remote listing rather than
    debouncing or sharing the suggester's cache.
    """
    remote = parse_remote_target(value)
    if remote is None:
        return list(itertools.islice(_local_and_host_matches(value), MAX_DROPDOWN_MATCHES))
    ssh_target, remote_dir, shown_prefix, remote_prefix = remote
    entries = await _ssh_list(ssh_target, remote_dir)
    matches = (f"{shown_prefix}{name}" for name in _filtered_entries(entries, remote_prefix))
    return list(itertools.islice(matches, MAX_DROPDOWN_MATCHES))


class RsyncTargetInput(Vertical):
    """An rsync-target `Input` plus a Ctrl+Space dropdown, completing locally
    or against the real remote host depending on what's typed so far.

    Same shell as `pytoolbox.tui.paths.PathInput`, but the dropdown listing
    is async (a remote lookup is a network call, not a local directory read).
    """

    BINDINGS = [
        # See PathInput's BINDINGS for why both key names are listed.
        Binding("ctrl+@,ctrl+space", "show_options", "Path options", show=False),
        Binding("escape", "close_options", "Close options", show=False),
    ]

    def __init__(self, **input_kwargs) -> None:
        super().__init__()
        self._input_kwargs = input_kwargs

    def compose(self) -> ComposeResult:
        yield Input(suggester=RsyncTargetSuggester(), **self._input_kwargs)
        yield _PathOptionList()

    def on_mount(self) -> None:
        self.query_one(_PathOptionList).display = False

    @property
    def value(self) -> str:
        return self.query_one(Input).value

    @value.setter
    def value(self, new_value: str) -> None:
        self.query_one(Input).value = new_value

    async def action_show_options(self) -> None:
        matches = await list_remote_target_matches(self.query_one(Input).value)
        if not matches:
            return
        options = self.query_one(_PathOptionList)
        options.clear_options()
        options.add_options(matches)
        options.highlighted = 0
        options.display = True
        options.focus()

    def action_close_options(self) -> None:
        options = self.query_one(_PathOptionList)
        if not options.display:
            # Nothing to close -- let Escape fall through to "back" as usual.
            raise SkipAction()
        options.display = False
        self.query_one(Input).focus()

    @on(OptionList.OptionSelected)
    def _pick(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        input_widget = self.query_one(Input)
        input_widget.value = str(event.option.prompt)
        input_widget.action_end()
        self.query_one(_PathOptionList).display = False
        input_widget.focus()
