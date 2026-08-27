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
from typing import Optional

import click

from pytoolbox.core import console, paths

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


NO_KEYRING_MESSAGE = """\
No usable keyring on this system, so pyssh will not store a password for {name}.
Use key authentication instead:
    pyssh keygen {name} && pyssh copy-id {name}
On Termux, Android's hardware keystore can hold an SSH key via tergent:
    https://github.com/aeolwyr/tergent
To store it in plain text anyway (mode 0600, readable by anyone who reads your
home directory), re-run with --insecure-plaintext."""


def _keyring():
    """The keyring module, or ``None`` when the extra is not installed.

    Isolated in one function so tests can substitute a backend, and so the
    import cost is paid only by the commands that need a secret.
    """
    try:
        import keyring
    except ImportError:
        return None
    return keyring


def set_secret(name: str, password: str, allow_plaintext: bool = False) -> str:
    """Store a password at the strongest tier available, and name that tier.

    Whether a backend works is decided by trying it: a keyring can be
    importable and still unusable, which is the normal case on Termux and on
    headless servers.
    """
    name = validate_name(name)
    backend = _keyring()
    if backend is not None:
        try:
            backend.set_password(KEYRING_SERVICE, name, password)
        except Exception:  # noqa: BLE001 - any failure means "no usable keyring"
            pass
        else:
            _set_secret_record(name, {"tier": TIER_KEYRING})
            return TIER_KEYRING

    if not allow_plaintext:
        raise click.ClickException(NO_KEYRING_MESSAGE.format(name=name))
    _set_secret_record(name, {"tier": TIER_PLAINTEXT, "value": password})
    return TIER_PLAINTEXT


def _set_secret_record(name: str, secret: dict) -> None:
    data = load()
    data["hosts"].setdefault(name, {})["secret"] = secret
    save(data)


def get_secret(name: str) -> Optional[str]:
    """The stored password for ``name``, or ``None`` if there is none."""
    name = validate_name(name)
    record = load()["hosts"].get(name) or {}
    secret = record.get("secret") or {}
    tier = secret.get("tier", TIER_NONE)

    if tier == TIER_PLAINTEXT:
        console.warn(
            f"{name}: using a password stored in plain text at {store_path()}. "
            "Move to key authentication when you can: pyssh copy-id " + name
        )
        return secret.get("value")

    if tier == TIER_KEYRING:
        backend = _keyring()
        if backend is None:
            raise click.ClickException(
                f"{name}'s password is in the OS keyring, but the keyring package is "
                "not installed here. Install it with `pip install 'pytoolbox[secrets]'`."
            )
        try:
            return backend.get_password(KEYRING_SERVICE, name)
        except Exception as exc:  # noqa: BLE001 - surfaced with what to do next
            raise click.ClickException(
                f"Could not read {name}'s password from the keyring: {exc}. "
                f"Re-add it with `pyssh secret set {name}`."
            ) from exc

    return None


def remove_secret(name: str) -> bool:
    """Forget a host's password. Returns whether there was one."""
    name = validate_name(name)
    data = load()
    record = data["hosts"].get(name) or {}
    had_secret = bool(record.get("secret"))

    backend = _keyring()
    if backend is not None:
        try:
            backend.delete_password(KEYRING_SERVICE, name)
        except Exception:  # noqa: BLE001 - nothing stored there is fine
            pass

    if not had_secret:
        return False

    record.pop("secret", None)
    if not record.get("tags"):
        del data["hosts"][name]
    save(data)
    return True
