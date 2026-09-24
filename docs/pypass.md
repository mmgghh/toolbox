# `pypass` — import/export for the `pass` password manager

Also available as `toolbox pass`.

```
import-chrome   Import a Chrome/Edge password CSV export into `pass`
export          Tar the whole password store, for moving it to another computer
import          Restore a password store from a `pypass export` archive
sync            Sync the password store with a server over SSH
```

`pypass` is not a `pass` wrapper. It doesn't add, edit, generate or look up
individual passwords — `pass` already does all of that well. It only moves
data in and out: a browser export in, or the whole store out and back in
again when you set up a new machine.

---

## `import-chrome`

```shell
pypass import-chrome chrome-passwords.csv
pypass import-chrome chrome-passwords.csv -n     # preview, no changes
pypass import-chrome chrome-passwords.csv -y     # skip the shred prompt's confirmation
pypass import-chrome chrome-passwords.csv --no-shred
```

Reads a Chrome or Edge password export (`chrome://password-manager/passwords`
→ "Export passwords", or the equivalent in Edge) and inserts every row into
`pass` via `pass insert`. Requires `pass` on `PATH`.

Each entry is named `username@host[:port]`, taken from the row's URL and
username — a nonstandard port is kept, the default 80/443 is not, and a
blank username drops the leading `@`. A row whose URL has no host at all
(a blank URL, or one with no scheme/netloc) is skipped and counted in the
summary, and so is a row with an empty password. An `android://` app entry
still gets a host (the app's reversed package name), so it imports like any
other row.

Each entry's body is:

```
<password>
login: <username>
url: <url>
```

— the convention `pass-import`/`browserpass`-style tools already read, so
the imported entries stay usable from other `pass` extensions.

If a computed name collides with an entry that already exists (either one
just imported, or one already in the store), an entry with the exact same
password and URL is treated as a duplicate and skipped; anything else gets
`-2`, `-3`, ... appended. The run ends with a one-line summary of what was
imported, skipped, and suffixed.

On success, unless `--no-shred` or `-n/--dry-run` was given, `pypass` asks
whether to shred the source CSV — it's a plaintext password dump. `-y`
answers yes without asking; the delete uses `shred -u` when available,
otherwise a single random-byte overwrite followed by unlink (which, like any
such overwrite, isn't guaranteed on an SSD or a copy-on-write filesystem).

---

## `export` / `import`

```shell
pypass export                          # -> password-store-<timestamp>.tar.gz
pypass export ~/backups/store.tar.gz
pypass export --no-git                 # leave version history out
pypass import store.tar.gz             # restore into $PASSWORD_STORE_DIR
pypass import store.tar.gz --force     # overwrite a non-empty destination
```

`export` tars the whole password store (`$PASSWORD_STORE_DIR`, or
`~/.password-store` if that's unset, or `--store`) as-is. Every entry stays
GPG-encrypted inside the archive, so the archive itself needs no additional
encryption — its security is exactly the security of the entries it
contains. `.git` is included by default since it's cheap and keeps history;
`--no-git` leaves it out.

The archive does **not** include the GPG private key that decrypts those
entries. Move that separately (`gpg --export-secret-keys <key-id> >
private.key`, kept at least as safe as the archive) — this is the same step
any `pass` migration needs, with or without `pypass`.

`import` extracts an archive into the destination store, refusing to touch
a destination that already exists and is not empty unless `--force` is
given, in which case the existing directory is removed first. Extraction
validates every archive member stays inside the destination before writing
anything, so a crafted or corrupted archive can't write outside the store.

---

## `sync`

```shell
pypass sync prod                                    # two-way, never deletes
pypass sync prod --push                              # local -> server only
pypass sync prod --pull                              # server -> local only
pypass sync prod --push --delete                     # mirror local onto the server
pypass sync me@host:2222 --remote-store ~/other-store
```

Syncs the password store with a server over SSH, using `rsync` under the
hood -- the same wrapper `pyssh sync` uses, so `SERVER` is resolved exactly
the same way: a host in `~/.ssh/config`, or an inline
`user[:password]@host[:port]` spec, with the same stored-secret lookup and
known-hosts guard as `pyssh`'s own commands. Requires `rsync` on `PATH`
(Termux: `pkg install rsync`).

Without `--push` or `--pull`, sync is two-way: entries missing on either
side are copied to the other (server first, then local), and nothing is
ever deleted, no matter what ran before. `--push` sends local entries to
the server only; `--pull` brings the server's entries here only. Files are
compared by content (`--checksum`), not size and mtime, since clocks
across machines (especially Termux) can't be relied on to agree.

`.git` is never synced -- a file-level two-way sync can't safely merge
git's internal state. For history, use `export`/`import` for a one-time
move, or a real git remote.

**`--delete`** removes destination entries missing from the source --
requires `--push` or `--pull`, and is refused in two-way mode: deleting
based on one direction's view could erase an entry the other direction
hasn't synced yet. It never touches `.git`, and asks for confirmation
first unless `-y/--yes` is given; `--dry-run` never prompts.

| Option | Meaning |
| --- | --- |
| `--store PATH` | Local password store directory (default: `$PASSWORD_STORE_DIR` or `~/.password-store`) |
| `--remote-store PATH` | Password store directory on the server (default: `~/.password-store`) |
| `--push` | Copy local entries to the server only |
| `--pull` | Copy the server's entries here only |
| `--delete` | Delete destination entries missing from the source. Needs `--push`/`--pull`. Asks first |
| `-p, --ssh-port` | Remote SSH port (default 22) |
| `--identity` | Private key file |
| `-o, --ssh-option` | Extra `ssh -o` option, repeatable |
| `-n, --dry-run` | Report what would transfer, change nothing |
| `-y, --yes` | Skip the confirmation prompt |
