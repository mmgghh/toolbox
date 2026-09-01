# `pypass` — import/export for the `pass` password manager

Also available as `toolbox pass`.

```
import-chrome   Import a Chrome/Edge password CSV export into `pass`
export          Tar the whole password store, for moving it to another computer
import          Restore a password store from a `pypass export` archive
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
