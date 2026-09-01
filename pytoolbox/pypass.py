"""Import/export for the `pass` password manager.

Not a `pass` wrapper: it only moves data in and out of an existing `pass`
store -- a Chrome/Edge CSV export in, or the whole store out and back in
again for an OS/computer migration. Everything else (adding, editing,
looking up an entry) is `pass`'s job, not this tool's.
"""

from __future__ import annotations

import csv
import datetime
import os
import secrets
import shutil
import subprocess
import tarfile
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import click

from pytoolbox.core import console
from pytoolbox.core.options import (
    CONTEXT_SETTINGS,
    AliasedGroup,
    dry_run_option,
    verbose_option,
    version_option,
    yes_option,
)

#: Ports that don't need to be spelled out in an entry name because the
#: scheme already implies them.
DEFAULT_PORTS = {"http": 80, "https": 443}


def store_dir(explicit: Optional[str] = None) -> Path:
    """Resolve the password store directory.

    Priority: an explicit ``--store`` value, then ``$PASSWORD_STORE_DIR``
    (the same variable `pass` itself honours), then ``~/.password-store``.
    """
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("PASSWORD_STORE_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".password-store"


def entry_name(url: str, username: str) -> Optional[str]:
    """Build a `pass` entry name ``username@host[:port]`` from a Chrome row.

    Returns ``None`` when ``url`` has no parseable hostname (a blank URL, or
    a non-http(s) scheme like ``android://...``), since there is nothing
    sensible to name the entry after.
    """
    parsed = urllib.parse.urlparse(url)
    host = parsed.hostname
    if not host:
        return None
    scheme = (parsed.scheme or "").lower()
    default_port = DEFAULT_PORTS.get(scheme)
    port = parsed.port
    suffix = f":{port}" if port and port != default_port else ""
    user = username.replace("/", "_").strip()
    return f"{user}@{host}{suffix}" if user else f"{host}{suffix}"


@dataclass
class ChromeRow:
    url: str
    username: str
    password: str


def read_chrome_csv(path: Path) -> list[ChromeRow]:
    """Read a Chrome/Edge password export.

    Chrome and Edge both export ``name,url,username,password[,note]``.
    Only ``url``, ``username`` and ``password`` are used; header matching
    is case-insensitive so an edited or re-saved export still works.
    """
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fields = {(name or "").strip().lower(): name for name in reader.fieldnames or []}
        missing = {"url", "username", "password"} - fields.keys()
        if missing:
            found = ", ".join(reader.fieldnames or []) or "(none)"
            raise click.ClickException(
                f"CSV is missing column(s): {', '.join(sorted(missing))}. Found: {found}."
            )
        return [
            ChromeRow(
                url=(row.get(fields["url"]) or "").strip(),
                username=(row.get(fields["username"]) or "").strip(),
                password=row.get(fields["password"]) or "",
            )
            for row in reader
        ]


def existing_entries(store: Path) -> set[str]:
    """Every entry name already in ``store``, as `pass` would address it."""
    if not store.is_dir():
        return set()
    return {p.relative_to(store).with_suffix("").as_posix() for p in store.rglob("*.gpg")}


def _require(binary: str, hint: str) -> None:
    if shutil.which(binary) is None:
        raise click.ClickException(f"`{binary}` was not found on PATH. {hint}")


def _pass_show(name: str) -> Optional[str]:
    """The decrypted body of an existing entry, or ``None`` if it can't be read."""
    result = subprocess.run(
        ["pass", "show", name], text=True, capture_output=True, check=False
    )
    return result.stdout if result.returncode == 0 else None


def resolve_name(
    base: str, password: str, url: str, taken: set[str], show=_pass_show
) -> tuple[Optional[str], str]:
    """Decide the entry name to use for ``base``, given names already ``taken``.

    Returns ``(None, "duplicate")`` when an existing entry at ``base`` has
    the same password and URL already -- nothing to write. Returns
    ``(base, "new")`` when there's no collision, or ``(f"{base}-N", "suffixed")``
    the first time there is one but the content differs.
    """
    if base not in taken:
        return base, "new"
    existing = show(base)
    if existing is not None:
        lines = existing.splitlines()
        existing_password = lines[0] if lines else ""
        if existing_password == password and f"url: {url}" in lines[1:]:
            return None, "duplicate"
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}", "suffixed"


def _shred(path: Path) -> None:
    if shutil.which("shred") is not None:
        subprocess.run(["shred", "-u", "-n", "3", str(path)], check=False)
        if not path.exists():
            console.success(f"Shredded {path}.")
            return
    try:
        size = path.stat().st_size
        with path.open("r+b") as handle:
            handle.write(secrets.token_bytes(size))
            handle.flush()
            os.fsync(handle.fileno())
        path.unlink()
        console.warn(
            f"Overwrote and deleted {path}. This does not guarantee removal on an "
            f"SSD, copy-on-write, or journaled filesystem."
        )
    except OSError as exc:
        console.error(f"Could not shred {path}: {exc}")


def _print_import_summary(imported, suffixed, duplicates, failed, unparseable, empty, dry_run) -> None:
    verb = "Would import" if dry_run else "Imported"
    console.result(f"{verb} {console.plural(len(imported), 'entry', 'entries')}.")
    if suffixed:
        console.info(f"  {len(suffixed)} name collision(s), suffixed: {', '.join(suffixed)}", threshold=0)
    if duplicates:
        console.info(f"  {len(duplicates)} duplicate(s) skipped: {', '.join(duplicates)}", threshold=0)
    if unparseable:
        console.info(f"  {unparseable} row(s) skipped: no usable URL.", threshold=0)
    if empty:
        console.info(f"  {empty} row(s) skipped: empty password.", threshold=0)
    if failed:
        console.error(f"  {len(failed)} row(s) failed: {', '.join(failed)}")


def _safe_members(tar: tarfile.TarFile, dest: Path) -> list:
    dest_resolved = dest.resolve()
    members = []
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if target != dest_resolved and dest_resolved not in target.parents:
            raise click.ClickException(f"Refusing to extract unsafe archive member: {member.name!r}")
        members.append(member)
    return members


@click.group(cls=AliasedGroup, context_settings=CONTEXT_SETTINGS)
@version_option
def pass_cli() -> None:
    """Import/export for the `pass` password manager.

    Not a `pass` wrapper -- only moves data in (a Chrome/Edge CSV export)
    and out (the whole store, for an OS/computer migration). Everything
    else is `pass`'s job.
    """


@pass_cli.command("import-chrome")
@click.argument("csv_file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@yes_option
@dry_run_option
@click.option("--no-shred", is_flag=True, help="Never offer to delete the source CSV.")
@verbose_option
def import_chrome(csv_file, assume_yes, dry_run, no_shred, verbose) -> None:
    """Import a Chrome/Edge password CSV export into `pass`."""
    _require("pass", "Install pass (e.g. `apt install pass` / `pkg install pass`).")
    console.dry_run_notice(dry_run)

    rows = read_chrome_csv(csv_file)
    taken = existing_entries(store_dir())

    imported: list[str] = []
    suffixed: list[str] = []
    duplicates: list[str] = []
    failed: list[str] = []
    unparseable = 0
    empty = 0

    for row in rows:
        if not row.password:
            empty += 1
            continue
        base = entry_name(row.url, row.username)
        if base is None:
            unparseable += 1
            continue
        name, status = resolve_name(base, row.password, row.url, taken)
        if status == "duplicate":
            duplicates.append(base)
            continue
        taken.add(name)
        if status == "suffixed":
            suffixed.append(name)
        if dry_run:
            imported.append(name)
            continue
        body = f"{row.password}\nlogin: {row.username}\nurl: {row.url}\n"
        result = subprocess.run(
            ["pass", "insert", "-m", name], input=body, text=True, capture_output=True, check=False
        )
        if result.returncode == 0:
            imported.append(name)
            console.info(f"Inserted {name}", verbose=verbose)
        else:
            failed.append(name)

    _print_import_summary(imported, suffixed, duplicates, failed, unparseable, empty, dry_run)

    if dry_run or no_shred or not imported:
        return
    if console.confirm(
        f"Shred the source CSV {csv_file}? It contains plaintext passwords.",
        assume_yes=assume_yes,
    ):
        _shred(csv_file)


@pass_cli.command("export")
@click.argument("output", required=False, type=click.Path(path_type=Path))
@click.option(
    "--store",
    "store_path",
    type=click.Path(path_type=Path),
    help="Password store directory (default: $PASSWORD_STORE_DIR or ~/.password-store).",
)
@click.option("--no-git", is_flag=True, help="Exclude .git from the archive.")
@verbose_option
def export_store(output, store_path, no_git, verbose) -> None:
    """Tar the whole password store, for moving it to another computer."""
    store = store_dir(str(store_path) if store_path else None)
    if not store.is_dir():
        raise click.ClickException(f"No password store at {store}.")
    if output is None:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = Path(f"password-store-{stamp}.tar.gz")

    with tarfile.open(output, "w:gz") as tar:
        for child in sorted(store.iterdir()):
            if no_git and child.name == ".git":
                continue
            tar.add(child, arcname=child.name)
            console.info(f"Added {child.name}", verbose=verbose)

    console.success(f"Exported {store} to {output}.")
    console.info(
        "This archive does not include your GPG private key. Move it separately, "
        "e.g. `gpg --export-secret-keys <key-id> > private.key` (keep that file at "
        "least as safe as the archive).",
        threshold=0,
    )


@pass_cli.command("import")
@click.argument("archive", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--store",
    "store_path",
    type=click.Path(path_type=Path),
    help="Destination store directory (default: $PASSWORD_STORE_DIR or ~/.password-store).",
)
@click.option("--force", is_flag=True, help="Overwrite an existing, non-empty destination.")
@verbose_option
def import_store(archive, store_path, force, verbose) -> None:
    """Restore a password store from a `pypass export` archive."""
    dest = store_dir(str(store_path) if store_path else None)
    if dest.exists() and any(dest.iterdir()):
        if not force:
            raise click.ClickException(
                f"{dest} already exists and is not empty. Pass --force to overwrite it."
            )
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    with tarfile.open(archive, "r:*") as tar:
        members = _safe_members(tar, dest)
        tar.extractall(dest, members=members)

    console.success(f"Restored {archive} to {dest}.")
