# `pyssh` — SSH tunnels and transfers

Also available as `toolbox ssh`.

```
tunnel          SOCKS5 proxy through one remote server
double-tunnel   SOCKS5 proxy to server 2, reached through server 1
connect         Open a connection, with any combination of forwards
forward         Bring a remote service to a local port
reverse         Expose a local service on the remote server
rsync-dir       Copy a directory over SSH with rsync
status          List tunnels started by pyssh
stop            Stop a background tunnel
secret          Store, list and forget host passwords
hosts           List hosts pyssh can reach, and manage their tags
```

These are thin wrappers around the system `ssh` and `rsync` binaries, not an
SSH implementation. Arguments are passed as argument lists, never through a
shell, so paths with spaces and quotes are safe.

---

## Server specs

```
user@host
user@host:port
user:password@host:port
```

Or use the name of a host in your `~/.ssh/config`:

```shell
pyssh tunnel -s mpars-bi -p 9998
pyssh rsync-dir -s ./site -d mpars-bi:/srv/site
```

A value containing `@` is parsed as a spec; anything else is handed to ssh as a
host name, so `~/.ssh/config` decides the hostname, user, port, identity file
and `ProxyJump`. pyssh does not parse your ssh config — it asks `ssh -G`.

Or keep it in a file and pass `--server-conf`; the first non-empty,
non-comment line is used:

```text
# ~/.config/pytoolbox/vps.conf
me:secret@vps.example.com:22
```

**Authentication.** Key authentication is the default path — pass
`-i/--identity` or rely on your agent and `~/.ssh/config`. When a spec
includes a password, `sshpass` is required; the password is written to an
owner-only (`0600`) file under `$XDG_RUNTIME_DIR/pytoolbox`, handed to
`sshpass -f`, and deleted as soon as the handshake completes. It never appears
in the process list.

## Secrets and tags

`~/.ssh/config` says how to reach a host. It cannot hold a password and has
no notion of grouping, so pyssh stores exactly those two things, keyed by ssh
config host name, in an owner-only `ssh.json` beside its other config.

```shell
pyssh secret set prod-web        # prompts, stores in the OS keyring
pyssh secret list
pyssh hosts tag add prod web1 web2
pyssh hosts --tag prod
```

| Tier | Where the password lives | When |
| --- | --- | --- |
| `none` | nowhere — key file, agent or PKCS#11 | the default, and always preferable |
| `keyring` | the OS keyring | whenever a backend works |
| `plaintext` | the 0600 `ssh.json` | only with `--insecure-plaintext` |

The keyring needs the extra: `pip install 'pytoolbox[secrets]'`.

**Termux.** There is no keyring backend on Termux — `keyring`'s Linux backend
is D-Bus SecretService, which Termux does not run, and `termux-keystore`
cannot store arbitrary data (it does `generate`, `sign`, `verify`, `list` and
`delete`, and nothing else). Use key authentication instead. Android's
hardware keystore can hold the key itself through
[tergent](https://github.com/aeolwyr/tergent), a PKCS#11 provider — set
`PKCS11Provider` in `~/.ssh/config` and pyssh picks it up like any other
setting.

## `tunnel`

Opens a SOCKS5 proxy through one server. Useful when your machine has
restricted internet access but can reach a server that does not.

```shell
pyssh tunnel -s me@vps.example.com -p 9998
pyssh tunnel -s me@vps.example.com -i ~/.ssh/id_ed25519 --background
pyssh tunnel --server-conf ~/.config/pytoolbox/vps.conf --reconnect --public
```

| Option | Meaning |
| --- | --- |
| `-p, --local-port` | Port the proxy listens on (default 9998) |
| `--public` | Bind `0.0.0.0` so other devices on the LAN can use it |
| `-i, --identity` | Private key file |
| `-o, --ssh-option` | Extra `ssh -o` option, repeatable |
| `-b, --background` | Return immediately, leaving the tunnel running |
| `--reconnect` | Re-test the proxy and rebuild it when it stops working |
| `--check-url` | URL used by `--reconnect` (default: a 204 endpoint) |

Then point clients at `socks5://localhost:9998`.

The command waits until the port is actually listening before reporting
success, so a failed login is reported instead of a silently dead tunnel. In
the foreground it stays up until Ctrl-C and cleans up its child processes on
exit.

`--reconnect` checks connectivity through the proxy every 15 seconds and
rebuilds the tunnel when the check fails. It needs the `socks` extra
(`pip install 'pytoolbox[socks]'`). To test whether a *specific* site is
reachable, point `--check-url` at it.

## `double-tunnel`

For when your machine can reach server 1 but not server 2, and only server 2
has unrestricted access. Traffic flows you → server 1 → server 2 → internet.

```shell
pyssh double-tunnel \
  --server1 me@bridge.example.com:22 \
  --server2 me@target.example.com:22 \
  --lp1 9998 --lp2 9999
```

`--lp1` is the local port forwarded to server 2's SSH port; `--lp2` is where
the SOCKS proxy listens. It takes the same options as `tunnel`.

## `connect`, `forward` and `reverse`

`connect` is the general command; `forward` and `reverse` are presets over it.

```shell
pyssh connect prod                                   # interactive shell
pyssh connect prod -L 5432:db.internal:5432 -b       # background local forward
pyssh connect prod -R 8080:localhost:3000 -D 1080 -b # several at once
pyssh forward prod -L 6379:cache:6379
pyssh reverse prod -R 1080                           # SOCKS proxy for the server
```

**Backgrounding.** `-b` uses ssh's own `-f`, so the command returns only once
authentication has succeeded — and, for a `-R` forward, only once the server
has actually bound the port. A failure is reported instead of leaving a dead
tunnel behind. The session is tracked by a control socket, so `pyssh stop`
closes it cleanly with `ssh -O exit` rather than signalling a PID.

**`--public` on a reverse forward** asks the *server* to bind `0.0.0.0`, which
sshd refuses unless its `GatewayPorts` is `yes` or `clientspecified`. The
default is `no`, so pyssh warns rather than reporting a public listener that
is not reachable.

## `exec`

```shell
pyssh exec prod 'uptime'
pyssh exec prod --cd /srv/app --env CI=1 'git pull && make'
pyssh exec prod --sudo 'systemctl restart nginx'
pyssh exec --tag prod -P 8 'systemctl is-active nginx'
pyssh exec --tag prod 'uptime' --json
```

Arguments are joined with spaces and interpreted by the **remote** shell,
exactly as `ssh host cmd` does — quote anything with pipes, globs or
semicolons that the far side should expand.

| Option | Meaning |
| --- | --- |
| `--tag TAG` | Run on every host carrying TAG instead of one NAME |
| `-P, --parallel N` | Run on up to N hosts at once (default 1) |
| `--cd DIR` | Run the command in DIR |
| `--env NAME=VALUE` | Export a variable first. Repeatable |
| `--sudo` | Run as root with `sudo -n`; needs passwordless sudo there |
| `-t, --tty` | Force a TTY, for interactive remote programs |
| `--json` | One record per host: name, exit code, stdout, stderr |

**Output and exit codes.** One host's output passes straight through, so
`pyssh exec prod 'cat log' > log` works. A group's output is prefixed with the
host name and grouped per host. A single host propagates the remote command's
own exit code; a group exits non-zero if any host failed.

`--sudo` applies to the command as written, so on a pipeline it covers only the
first stage — wrap the whole thing in `sh -c '...'` if you need more. Group runs
add `BatchMode=yes` unless a stored password is in play, so a host that would
prompt fails instead of hanging.

**Host keys.** When a password is used, `exec` refuses to connect to a host
that is not already in `~/.ssh/known_hosts`, and prints the command that fixes
it — sending a password to an unverified host and then running commands on it
is exactly the machine-in-the-middle case. Key authentication is unaffected.

## `status` and `stop`

Background tunnels record their state under `$XDG_RUNTIME_DIR/pytoolbox`.
Entries whose processes have died are pruned automatically.

```shell
pyssh status
pyssh status --json
pyssh stop tunnel-9998
pyssh stop --all
```

```
name         | kind   | socks                    | server              | pids  | uptime
-------------+--------+--------------------------+---------------------+-------+-------
tunnel-9998  | tunnel | socks5://127.0.0.1:9998  | me@vps.example.com  | 40312 | 12m 3s
```

## `rsync-dir`

Wraps `rsync -azP -e "ssh -p <port>"`.

```shell
pyssh rsync-dir -s ./site -d me@vps:/srv/site -p 22
pyssh rsync-dir -s ./photos -d me@vps:/srv/pics --match '*.{jpg,png}'
pyssh rsync-dir -s ./repo -d me@vps:/srv/repo --gitignore -e '.git'
pyssh rsync-dir -s ./site -d me@vps:/srv/site --mirror --dry-run
pyssh rsync-dir -s me@vps:/srv/site -d ./backup --bwlimit 500k --no-compress
```

Either side may be `user@host:/path`, and — unlike plain rsync — also
`user:password@host:/path`, which routes through `sshpass` exactly as the
tunnel commands do. Only one side may carry a password; rsync opens a single
SSH connection.

### Patterns

Patterns are **shell globs, not regex**:

| | |
| --- | --- |
| `*` | any characters, stops at `/` |
| `**` | any characters, crosses `/` |
| `?` | one character, not `/` |
| `[a-z]`, `[!0-9]` | character class |
| `foo/` | directories only |
| `\*` | a literal asterisk |

Two rules decide what a pattern applies to:

- **No slash → matches the basename, at any depth.** `--match '*.jpg'` finds
  `photos/2024/a.jpg`.
- **A slash anywhere → matches the path from the transfer root**, and a leading
  `/` anchors it there (not at the filesystem root). `--match 'src/*.js'` is one
  level down; `--match '**/*.js'` is any depth.

`{a,b}` is expanded before rsync sees it, because rsync has no brace expansion
of its own and a quoted `*.{jpg,png}` would silently match nothing. For the same
reason, a regex-shaped pattern is rejected with a suggestion rather than
transferring zero files:

```console
$ pyssh rsync-dir -s ./site -d ./backup -e '.*\.log$'
Error: '.*\.log$' looks like a regex. rsync matches shell globs, so this would
silently match nothing -- did you mean '*.log'? Pass --raw-patterns to send it
through unchanged.
```

`--raw-patterns` turns off both behaviours and gives verbatim rsync semantics.

### Rule order

rsync applies filter rules first-match-wins, so the order is fixed rather than
following the order you type:

```
--exclude          -e, then --exclude-from lines
--filter           --gitignore
--include '*/'     \
--include <match>  |  only when --match/--match-from is used
--exclude '*'      /
--prune-empty-dirs
```

Excludes therefore always beat matches: `-e node_modules --match '*.js'` skips
`node_modules` even though its files match. The `--include '*/'` rule is what
makes rsync descend into subdirectories at all, and `--prune-empty-dirs` clears
the empty skeleton it would otherwise leave behind.

### Options

**Matching and filtering**

| Option | Meaning |
| --- | --- |
| `--match GLOB` | Transfer *only* files matching GLOB, at any depth. Repeatable |
| `-e, --exclude GLOB` | Skip files matching GLOB. Repeatable, applied before `--match` |
| `--match-from FILE` | Read `--match` patterns from a file, one per line |
| `--exclude-from FILE` | Read `--exclude` patterns from a file, one per line |
| `--gitignore` | Honour `.gitignore` files in the tree |
| `--files-from FILE` | Transfer exactly the listed paths |
| `--min-size`, `--max-size` | Skip files below/above a size, e.g. `1k`, `10m` |
| `--raw-patterns` | Pass patterns to rsync verbatim |

In pattern files, blank lines and lines starting with `#` or `;` are ignored.
`--files-from` cannot be combined with `--match` or `--gitignore`, and does not
recurse into directories named in the list.

**Safety**

| Option | Meaning |
| --- | --- |
| `--delete` | Delete destination files missing from the source |
| `--mirror` | `--delete` plus `--delete-excluded` |
| `--backup-dir DIR` | Move deleted and overwritten files here instead of losing them |
| `--stats` | Print rsync's transfer summary |
| `-n, --dry-run` | Report what would transfer, change nothing |
| `-y, --yes` | Skip the confirmation prompt |

`--delete` and `--mirror` ask before running, naming the destination. A
non-interactive session (pipe, cron, CI) takes the safe answer and aborts, so
pass `-y` when you mean it. `--dry-run` never prompts.

Note that `--mirror` together with `--match` deletes everything at the
destination that does not match the pattern — consistent, and the reason the
prompt exists. A relative `--backup-dir` resolves against the destination
directory, which is rsync's own rule.

**Transport**

| Option | Meaning |
| --- | --- |
| `-p, --ssh-port` | Remote SSH port (default 22) |
| `--identity` | Private key file |
| `-o, --ssh-option` | Extra `ssh -o` option, repeatable |
| `--bwlimit RATE` | Cap the transfer rate, e.g. `500k` |
| `--no-compress` | Drop the `z` from `-azP` |
| `--sudo` | Run rsync as root remotely; needs passwordless sudo there |

Compression costs CPU to save bandwidth. On a LAN, or for video, images and
archives that are already compressed, `--no-compress` is usually faster.

**Comparison**

| Option | Meaning |
| --- | --- |
| `-i, --ignore-existing` | Never touch files already at the destination |
| `--existing` | Update only files already there; never create new ones |
| `-c, --checksum` | Compare by contents rather than size and timestamp |
| `--size-only` | Treat equal-sized files as identical |

Without `--ignore-existing`, `--update` is used: only newer source files are
transferred.

Combinations that would cancel each other out — `--checksum` with
`--size-only`, `--existing` with `--ignore-existing` — are rejected up front
instead of being handed to rsync.

## Termux

```shell
pkg install openssh rsync
pkg install sshpass     # only if you use password authentication
```

Everything else works unchanged. Key authentication is strongly preferred on a
shared device.
