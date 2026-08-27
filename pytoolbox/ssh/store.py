"""Secrets and tags for ssh config hosts.

``~/.ssh/config`` already says how to reach a host. What it cannot hold is a
password, and it has no notion of grouping -- so this store adds exactly those
two things, keyed by ssh config host name, and nothing else. It is deliberately
not a server inventory: there is already one of those.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import click

from pytoolbox.core import paths

#: Bumped only for a format change that needs migrating.
STORE_VERSION = 1

#: Where a keyring-backed password is filed.
KEYRING_SERVICE = "pytoolbox-ssh"

TIER_NONE = "none"
TIER_KEYRING = "keyring"
TIER_PLAINTEXT = "plaintext"


def store_path() -> Path:
    """The store file. Owner-only, alongside the other pytoolbox config."""
    return paths.config_dir() / "ssh.json"


def load() -> dict:
    """Read the store, or an empty one if it does not exist yet."""
    path = store_path()
    if not path.is_file():
        return {"version": STORE_VERSION, "hosts": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise click.ClickException(
            f"Could not read {path}: {exc}. Fix or delete the file and try again."
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("hosts", {}), dict):
        raise click.ClickException(
            f"{path} is not a pyssh store. Fix or delete the file and try again."
        )
    data.setdefault("version", STORE_VERSION)
    data.setdefault("hosts", {})
    return data


def save(data: dict) -> Path:
    """Write the store back with owner-only permissions."""
    return paths.write_private_file(
        store_path(), json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    )


def validate_name(name: str) -> str:
    """Check a host name can be stored, and return it stripped.

    A name containing ``@`` would parse as an inline spec instead, so the two
    forms could no longer be told apart.
    """
    raw = (name or "").strip()
    if not raw or "@" in raw or raw.startswith("-") or any(char.isspace() for char in raw):
        raise click.ClickException(
            f"{name!r} is not a usable host name. Use the name from ~/.ssh/config, "
            "without '@', spaces, or a leading '-' (ssh would read that as an option)."
        )
    return raw


@dataclass(frozen=True)
class HostEntry:
    """What pyssh stores about one host."""

    name: str
    tags: tuple[str, ...] = ()
    tier: str = TIER_NONE


def _record_to_entry(name: str, record: dict) -> HostEntry:
    secret = record.get("secret") or {}
    return HostEntry(
        name=name,
        tags=tuple(record.get("tags") or ()),
        tier=secret.get("tier", TIER_NONE),
    )


def entry(name: str) -> HostEntry:
    """The stored entry for ``name``, empty if there is none."""
    name = validate_name(name)
    return _record_to_entry(name, load()["hosts"].get(name, {}))


def entries() -> list[HostEntry]:
    """Every stored entry, by name."""
    hosts = load()["hosts"]
    return [_record_to_entry(name, hosts[name]) for name in sorted(hosts)]


def _update(name: str, mutate) -> HostEntry:
    name = validate_name(name)
    data = load()
    record = data["hosts"].setdefault(name, {})
    mutate(record)
    if not record.get("tags") and not record.get("secret"):
        del data["hosts"][name]
        save(data)
        return HostEntry(name=name)
    save(data)
    return _record_to_entry(name, record)


def add_tags(name: str, tags: Sequence[str]) -> HostEntry:
    """Add tags to a host, keeping them sorted and unique."""
    wanted = {tag.strip() for tag in tags if tag.strip()}
    if not wanted:
        raise click.ClickException("Provide at least one tag.")

    def mutate(record: dict) -> None:
        record["tags"] = sorted(set(record.get("tags") or ()) | wanted)

    return _update(name, mutate)


def remove_tags(name: str, tags: Sequence[str]) -> HostEntry:
    """Remove tags from a host, dropping the entry when nothing is left."""
    unwanted = {tag.strip() for tag in tags if tag.strip()}

    def mutate(record: dict) -> None:
        remaining = sorted(set(record.get("tags") or ()) - unwanted)
        if remaining:
            record["tags"] = remaining
        else:
            record.pop("tags", None)

    return _update(name, mutate)


def names_with_tag(tag: str) -> list[str]:
    """Every host carrying ``tag``, by name."""
    return [item.name for item in entries() if tag in item.tags]
