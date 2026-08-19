# `pynet` — network diagnostics

Also available as `toolbox net`.

```
ip      Local and public IP addresses, and where an address is
dns     Resolve a name to addresses, or an address back to a name
port    Check whether TCP ports are reachable
scan    Scan a host for open TCP ports
ping    Measure round-trip time
http    Request a URL and report status, timing and redirects
serve   Serve a directory over HTTP on the local network
whois   Look up domain registration data
url     Percent-encode, decode or parse a URL
```

Everything uses ordinary user-space sockets plus `requests`. Nothing needs
root or raw sockets, so all of it works on an unrooted Android device. Most
commands accept `--json`.

---

## `ip`

```shell
pynet ip
pynet ip --local
pynet ip --public --json
pynet ip --geo                  # ...and where it thinks you are
pynet ip 1.1.1.1                # locate any address
pynet ip example.com --json     # a hostname is resolved first
```

Local addresses are discovered by opening a UDP socket toward a public address
and asking the kernel which interface it picked — the only portable way to
learn the outbound address (`gethostbyname(gethostname())` returns `127.0.1.1`
on most Linux systems and fails on Android).

The public address comes from the first responding echo service among
api.ipify.org, ifconfig.me and icanhazip.com.

### Location

`--geo` adds city, region, country, coordinates, timezone and network to the
public address; passing an `ADDRESS` locates that one instead.

```
address  1.1.1.1
location Brisbane, Queensland, Australia (AU)
coords   -27.4675408, 153.028092
timezone Australia/Brisbane
network  AS13335 Cloudflare, Inc.
```

The lookup is **opt-in and never load-bearing**: `pynet ip` on its own touches
no geolocation service, and when the lookup fails — no internet, a blocked
provider — the addresses are still printed and the exit code stays 0. Only an
explicit `pynet ip ADDRESS`, where the location *is* the answer, exits 1 when
nothing comes back.

Providers are tried in order until one answers: [ipwho.is](https://ipwho.is),
[ipapi.co](https://ipapi.co), [freeipapi.com](https://freeipapi.com). All are
free, need no key and are queried over HTTPS, so the address being looked up is
not sent in the clear. Whichever one replies, the fields come back under the
same names.

A geolocation database says where an address *block was registered*, which is
regularly the ISP's city rather than anywhere a person is; addresses behind a
VPN or a mobile carrier are routinely off by hundreds of kilometres. Read it as
a hint, not as a location for someone.

## `dns`

```shell
pynet dns example.com
pynet dns example.com -4 --reverse
pynet dns 1.1.1.1               # PTR lookup
```

`-4`/`-6` restrict the address family; `-r/--reverse` adds a PTR lookup for
each resolved address.

## `port`

```shell
pynet port example.com 443
pynet port 192.168.1.10 22,80,443
pynet port localhost 8000-8010 --open-only
pynet port example.com 443 --json
```

`PORTS` accepts single values, comma lists and ranges, in any combination.
Exits non-zero when nothing is open, so it works in shell conditionals:

```shell
if pynet port db.internal 5432 >/dev/null; then echo "database is up"; fi
```

Each result includes the handshake latency, which makes this a decent
"is this service slow or down?" check.

## `scan`

```shell
pynet scan 192.168.1.1
pynet scan example.com -p 1-1024
pynet scan localhost -p 8000-9000 --all -w 128
```

Without `-p` it probes a list of well-known service ports. Probes run
concurrently; the worker count is capped by the number of ports, so a two-port
check does not spin up 64 threads on a phone.

Only scan hosts you are responsible for.

## `ping`

```shell
pynet ping example.com
pynet ping example.com --tcp -p 80 -c 10
pynet ping example.com --tcp --json
```

Uses the system `ping` when available. Otherwise — and always with `--tcp` —
it times TCP handshakes instead, which needs no privileges and therefore works
on unrooted Android where ICMP is unavailable. Exits non-zero when nothing
replies.

## `http`

```shell
pynet http https://example.com
pynet http https://example.com -v --no-redirects
pynet http https://api.example.com -X HEAD -H 'Authorization: Bearer token'
pynet http https://example.com --json
```

Reports the final status, elapsed time, response size and the redirect chain;
`-v` adds response headers and `--body` prints the body. Exits non-zero on
4xx/5xx. The scheme may be omitted (`pynet http example.com` assumes https).

## `serve`

```shell
pynet serve
pynet serve ~/downloads -p 8080
pynet serve . --bind 127.0.0.1
```

Serves a directory read-only over HTTP and prints the LAN URLs to use — the
easiest way to move a file between a phone and a laptop with no cable and no
account anywhere. Ctrl-C stops it.

It binds `0.0.0.0` by default, so anyone on the same network can read the
served directory. Use `--bind 127.0.0.1` when that is not what you want.

## `whois`

```shell
pynet whois example.com
pynet whois example.com --server whois.verisign-grs.com
pynet whois example.com --raw
pynet whois example.com --json
```

Speaks the port-43 WHOIS protocol directly, so no `whois` binary is needed.
It queries IANA first and follows one referral to the registry that holds the
real record; `--raw` stops at the first response. Internationalised domain
names are punycoded automatically. A WHOIS record is free-form text, so
`--json` keeps the response as one string and adds the server that answered.

## `url`

```shell
pynet url 'a b&c'                                  # a%20b%26c
pynet url 'a%20b' --decode                         # a b
pynet url 'https://example.com/p?q=1#f' --parse
pynet url 'https://example.com/p?q=1' --parse --json
```
