# `pyssh` — SSH tunnels and transfers

Also available as `toolbox ssh`.

```
tunnel          SOCKS5 proxy through one remote server
double-tunnel   SOCKS5 proxy to server 2, reached through server 1
rsync-dir       Copy a directory over SSH with rsync
status          List tunnels started by pyssh
stop            Stop a background tunnel
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
pyssh rsync-dir -s me@vps:/srv/site -d ./backup --ignore-existing
pyssh rsync-dir -s ./site -d me@vps:/srv/site --delete --dry-run
pyssh rsync-dir -s ./site -d me@vps:/srv/site -e '*.tmp' -e 'node_modules'
```

| Option | Meaning |
| --- | --- |
| `-p, --ssh-port` | Remote SSH port (default 22) |
| `--identity` | Private key file |
| `-i, --ignore-existing` | Never touch files already at the destination |
| `--delete` | Delete destination files missing from the source |
| `-e, --exclude` | Exclude pattern, repeatable |
| `-n, --dry-run` | Ask rsync to report what it would transfer |

Without `--ignore-existing`, `--update` is used: only newer source files are
transferred.

## Termux

```shell
pkg install openssh rsync
pkg install sshpass     # only if you use password authentication
```

Everything else works unchanged. Key authentication is strongly preferred on a
shared device.
