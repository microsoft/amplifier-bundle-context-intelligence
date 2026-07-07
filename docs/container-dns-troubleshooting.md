# Setting up and troubleshooting DTU / Incus container reachability to a Context Intelligence server

This guide covers the layer **below** the hook: how a session running **inside a DTU /
Incus container** reaches the Context Intelligence server *at all*. The
[remote-server troubleshooting guide](remote-server-troubleshooting.md) assumes the
client can already open a connection to the server and focuses on dispatch, timeouts,
and auth. This guide is what you read when the connection itself never forms **from
inside a container** — a `context_intelligence_server_url` is only as good as the
container's ability to **resolve the name** and **route to the address** behind it.

Everything the hook does still applies once the bytes flow: local capture to
`events.jsonl` happens first and is **never** contingent on reachability, so a container
that can't reach the server loses **no data** — the events sit durable on disk and replay
later with `context-intelligence-upload`. This guide is about restoring *forwarding*, not
rescuing data.

> **The one rule.** Never let a container's reachability depend solely on **MagicDNS**.
> Containers don't run Tailscale, so they borrow the host's MagicDNS — a path that breaks
> whenever the host's DNS wobbles. Give the container a **LAN floor** it can resolve on its
> own. This is the container-layer extension of the host `/etc/hosts` LAN-floor doctrine.

---

## Pick your topology first

There are exactly two ways a containerised session reaches a CI server, and they fail for
**completely different reasons**. Decide which one you're in before touching anything.

| Topology | The CI server is… | How the container reaches it | The failure mode | Section |
|---|---|---|---|---|
| **A — Local** | the **same host** that runs the DTU | `localhost` → **Incus-bridge default gateway** rewrite (baked in by the DTU profile) | wrong gateway IP / server not on the expected port | [Topology A](#topology-a--ci-server-on-the-dtu-host-localhost) |
| **B — Remote** | a **different tailnet host** | resolves a tailnet name (`<name>.ts.net`) — but has **no MagicDNS** of its own | borrowed host MagicDNS wobbles → recurring dispatch failures | [Topology B](#topology-b--ci-server-on-a-remote-tailnet-host-the-magicdns-trap) |

If your server URL is `http://localhost:<port>` (or a `127.0.0.1` address) you are in
**Topology A**. If it is a tailnet name like `https://dyad.tail09557f.ts.net:8448` you are
in **Topology B**.

---

## Why containers are different (the borrowed-resolver trap)

A DTU/Incus container is **not on the tailnet**. It has no `tailscale0` interface and no
MagicDNS. When a session inside it tries to resolve a tailnet name, the query takes a
borrowed path:

```
  ┌─────────────────────────── DTU / Incus container ───────────────────────────┐
  │  session → hook → resolve "dyad.tail09557f.ts.net"                           │
  │                       │                                                      │
  │                       ▼  (container has NO Tailscale, NO MagicDNS)           │
  └───────────────────────┼──────────────────────────────────────────────────────┘
                          │ forwarded to the Incus bridge (incusbrN) dnsmasq
                          ▼
                 ┌──────────────── host ────────────────┐
                 │  bridge dnsmasq (runs with --no-hosts)│
                 │        │                              │
                 │        ▼  forwards to host resolver   │
                 │  host resolver ── *.ts.net ──▶ MagicDNS (tailscale0)          │
                 │                                 │ answers 100.x tailnet IP    │
                 │                                 ▼ reached via host tailnet    │
                 └───────────────────────── routing ───────────────────────────┘
```

That borrowed path is only as healthy as the **host's** MagicDNS — which keeps breaking in
practice (NetworkManager DNS fights, node-key expiry). When it wobbles, **every container
on that host loses CI reachability at once**, even though the host itself is fine (the
host's own `/etc/hosts` LAN floor does **not** reach containers — the bridge dnsmasq runs
`--no-hosts` and ignores it).

The fix in Topology B is to stop borrowing: give the bridge dnsmasq a **static answer** for
the CI name that points at its **LAN IP**, so the container resolves and routes over the LAN
and never touches MagicDNS.

---

## Topology A — CI server on the DTU host (`localhost`)

When the CI server runs on the **same host** as the DTU, there is **no tailnet name and no
DNS to fix**. The container can't use `localhost` (that's the container's own loopback), so
the **DTU launch profile** rewrites it to the host, detected via the Incus bridge's default
gateway:

```bash
# from the DTU profile (example-dtu-external-server.yaml) — runs at container start:
HOST_IP=$(ip route | grep default | awk '{print $3}')   # the Incus bridge gateway = the host
CI_URL="http://$HOST_IP:$CI_PORT"
# exported as AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL before the hook starts
```

> **There is no "localhost → gateway" knob in the hook.** The hook only ever receives a
> final URL. The rewrite is a **DTU-profile** concern, not a
> `overrides.hook-context-intelligence.config` setting — don't go hunting for a hook config
> key that doesn't exist. If the rewrite is wrong, fix it in the **profile**, not the hook.

**Verify (from inside the container):**

```bash
# what did the profile bake in?
incus exec <container> -- printenv AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL   # -> http://<gateway-ip>:<port>
# is the server actually there?
incus exec <container> -- curl -s http://<gateway-ip>:<port>/version           # -> {"version":"..."}
```

If `/version` returns `200`, you're done — hand off to the
[remote-server troubleshooting guide](remote-server-troubleshooting.md) for anything about
dispatch or auth. If it hangs or refuses, the server isn't listening on that host/port, or
the gateway IP is wrong for this bridge — re-check `ip route` inside the container against
the host's `incusbrN` address.

---

## Topology B — CI server on a remote tailnet host (the MagicDNS trap)

This is the case this guide exists for. The CI server
lives on a **different** tailnet host; the containerised session must resolve its tailnet
name; and — per the borrowed-resolver trap above — it does so through the host's MagicDNS,
which is the recurring point of failure.

### The fix: a LAN floor on the bridge dnsmasq

One `raw.dnsmasq` entry on the host's Incus bridge points the CI server's tailnet **name**
at its **LAN IP**. The **name is unchanged**, which is the whole trick — SNI still matches
the server's vhost (e.g. Caddy on the CI host) and the existing Tailscale-issued
certificate still validates. **No cert, URL, domain, or hook-config change.**

```bash
# worked example: CI server dyad, LAN IP 192.168.1.112, bridge incusbr0
incus network set incusbr0 raw.dnsmasq 'address=/dyad.tail09557f.ts.net/192.168.1.112'
```

Generalised: `address=/<ci-tailnet-name>/<ci-lan-ip>` on the DTU host's `<incusbrN>`.

**Apply it append-safe** — never clobber an existing `raw.dnsmasq`:

```bash
line='address=/dyad.tail09557f.ts.net/192.168.1.112'
cur=$(incus network get incusbr0 raw.dnsmasq 2>/dev/null)
printf '%s' "$cur" | grep -qF "$line" || { \
  [ -n "$cur" ] && new="$cur"$'\n'"$line" || new="$line"; \
  incus network set incusbr0 raw.dnsmasq "$new"; }
```

Setting the network config makes Incus reload **that bridge's dnsmasq** — a brief DNS blip
for containers on that bridge; **nothing restarts the containers themselves**, and captured
events are untouched.

### Where to apply it (and where NOT to)

Apply the LAN floor **only on a host that runs DTUs reaching a REMOTE CI server**. The
decision, per host:

| Host runs DTUs? | CI server is… | Apply the floor? | Why |
|---|---|---|---|
| yes | remote tailnet host | **YES** | the container must resolve the tailnet name without MagicDNS |
| yes | **this host** (`localhost`) | **NO** | Topology A — the container reaches CI via the profile's `localhost`→gateway rewrite, never the tailnet name. The override would be an unused no-op. |
| **no** (roams, no containers) | anything | **NO** | no containers to serve; the host keeps using its own MagicDNS from wherever it is |

Worked example — a real fleet, **verified 2026-07-07**:

| Host | `incusbrN` IP | Applied | Why |
|---|---|---|---|
| monad | `10.56.154.1` | YES | runs resolve DTUs; CI URL is remote `dyad.tail09557f.ts.net:8448` |
| ambrose | `10.87.34.1` | YES | runs Incus containers; CI URL is remote `dyad.tail09557f.ts.net:8448` |
| dyad | (n/a) | NO | dyad **is** the CI server (`http://localhost:8000`) — Topology A |
| machen | (n/a) | NO | roams off-LAN, runs no DTUs — keeps MagicDNS |

### Verify

```bash
# from inside a running container — the name must resolve to the LAN IP, not a 100.x tailnet IP:
incus exec <container> -- getent hosts dyad.tail09557f.ts.net          # expect 192.168.1.112
incus exec <container> -- curl -s -o /dev/null -w '%{http_code}\n' \
    https://dyad.tail09557f.ts.net:8448/version                        # expect 200

# or, no container needed — query the bridge dnsmasq directly (use the incusbrN IPv4):
dig @10.56.154.1 +short dyad.tail09557f.ts.net                         # expect 192.168.1.112
```

A `getent` that returns the LAN IP **and** a `/version` `200` means the floor is in place
and the full path (LAN route + SNI + cert) is healthy.

---

## Driving the setup with Amplifier

This whole topology can be stood up and verified from an Amplifier session — you don't hand
a human a checklist, you drive it.

1. **Launch the environment through the DTU bundle.** Delegate to the Digital Twin agent
   (or load the `digital-twin-universe` skill) to bring up the container from a profile:
   `delegate(agent="digital-twin-universe:dtu-profile-builder", instruction="launch a DTU
   on this host whose session forwards to the remote CI server <name>:<port>", ...)`. For a
   remote CI target, the profile is the `example-dtu-external-server.yaml` shape — but the
   `localhost`→gateway auto-detect only covers **Topology A**, so for **Topology B** the LAN
   floor below is still required.
2. **Apply the LAN floor as a host step**, not inside the container — run the append-safe
   `incus network set incusbr0 raw.dnsmasq …` block above via `bash`. It's idempotent, so an
   agent can run it every launch without fear of duplicating or clobbering.
3. **Let Amplifier verify end-to-end**, not by eye: run the three `getent` / `curl` / `dig`
   probes and assert `192.168.1.112` and `200`. Treat anything else as a failed gate and
   route to the [symptom table](#symptom--cause--fix) below.
4. **Confirm the loop actually closed** by reading the hook's own durable diagnostics after
   a short session: any connect failure the container hit is recorded — **with the real URL
   it contacted** — in the per-day
   `forwarding-YYYY-MM-DD.jsonl` under `forwarding_log_dir` (default
   `~/.amplifier/context-intelligence-logs`):
   `jq 'select(.kind=="give_up" or .kind=="auth_failure") | {url, http_status, session_id}'
   ~/.amplifier/context-intelligence-logs/forwarding-*.jsonl`. **Believe the URL, not the
   label** — a DNS misresolution shows up here as the wrong address behind the right name.

---

## Symptom → cause → fix

### Dispatch works on the host but **fails from inside the container**

- **Cause:** the container is on MagicDNS's borrowed path (Topology B) and the host's
  MagicDNS is wobbling — the host is fine because it has its own LAN floor / working
  `tailscale0`; the container does not. This is the signature symptom.
- **Fix:** apply the [LAN floor](#the-fix-a-lan-floor-on-the-bridge-dnsmasq) on this host's
  `incusbrN`. Re-run the [verify probes](#verify).
- **Not data loss:** everything the container captured is durable in `events.jsonl`; replay
  the gap with `context-intelligence-upload` once forwarding is restored.

### `getent hosts <name>` inside the container returns a `100.x` address (a tailnet IP)

- **Cause:** the floor isn't in effect — either never applied on this bridge, applied to a
  **different** `incusbrN` than the one this container uses, or the container cached the old
  answer before the dnsmasq reload.
- **Fix:** confirm the container's bridge (`incus exec <c> -- ip route | grep default` → the
  gateway is the bridge you must set the floor on). Re-apply append-safe, then re-resolve.
  If it still returns the tailnet IP, the container's resolver has a stale entry — a fresh
  `getent` after the reload should pick it up; if not, restart just the container.

### Name resolves to the LAN IP but `curl https://<name>:<port>/version` returns `000`

- **Cause:** resolution is fixed but the **LAN route** to that IP is down, the server isn't
  listening on that port, or a firewall blocks the container subnet. This is a routing/host
  problem, not DNS.
- **Fix:** from the host, `curl https://<name>:<port>/version` to isolate whether the server
  is up at all; check the CI host is reachable on the LAN and listening on `<port>`.

### `curl` against the **bare IP** returns `000` even with `-k`

- **This is expected — do not "fix" it by switching the URL to the IP.** See
  [why the alternatives don't work](#why-these-alternatives-do-not-work-so-we-dont-re-litigate).
  The name is load-bearing for both SNI and the certificate; keep the name, fix the
  resolution.

### A new DTU host was added and its containers can't reach CI

- **Cause:** the LAN floor is **per host** — a new host that runs DTUs against a remote CI
  server needs its own `raw.dnsmasq` line.
- **Fix:** apply the floor on the new host's `incusbrN` (see [New hosts](#new-hosts-and-other-services)).

---

## Why these alternatives do NOT work (so we don't re-litigate)

These have all been tried and proven to fail; documented here so nobody burns hours
rediscovering it.

- **Host `/etc/hosts`.** The Incus bridge dnsmasq runs with `--no-hosts`, so it **ignores
  the host's hosts file entirely**. This is exactly why the host-level LAN floor never helped
  containers — the container never consults it.
- **LAN DNS (UniFi / router) override.** The host resolver sends `*.<tailnet>.ts.net` to
  Tailscale MagicDNS via a **per-domain route on `tailscale0`**, never to the LAN DNS — so a
  UniFi record for that name is never consulted. `raw.dnsmasq` on the bridge is the **one
  layer the container queries that can override it**.
- **Point the CI URL at the bare IP** (`https://<ci-lan-ip>:<port>`). Fails **two** ways,
  both proven: (1) the CI host's vhost is keyed on **SNI = the hostname**, so an IP SNI
  matches no site (`HTTP 000` even with `-k`); and (2) the Tailscale certificate's SAN is
  `DNS:<name>` only — **no IP** — so TLS validation fails. **The name is load-bearing for
  both SNI and the cert;** keep the name and fix resolution instead.

---

## New hosts and other services

**New DTU host.** When a new host starts running DTUs/Incus containers that must reach a
**remote** CI server, apply the [LAN floor](#the-fix-a-lan-floor-on-the-bridge-dnsmasq) on
its `incusbrN`. Not needed on the CI-server host itself (that's Topology A) or on roaming
hosts that run no containers.

**Other tailnet services.** The same pattern generalises: one
`address=/<name>/<lan-ip>` line per service on the bridge. Only worth doing for names that
**containers must resolve** and that map to a **LAN-reachable IP with a cert valid for that
name**. Anything that fails either half (no LAN path, or a cert that doesn't cover the name)
is not a candidate for the floor.

---

## Quick reference

| You see… | Do this |
|---|---|
| Dispatch works on host, fails inside the container | Topology B — apply the `raw.dnsmasq` LAN floor on this host's `incusbrN`. |
| `getent hosts <name>` → `100.x` tailnet IP | Floor not in effect on this container's bridge — re-apply append-safe, confirm the right `incusbrN`, re-resolve. |
| `getent` → LAN IP but `curl …/version` → `000` | Routing/port, not DNS — check the CI host is up and listening on `<port>` over the LAN. |
| `curl` against the **bare IP** → `000` | Expected (SNI + cert need the name). Keep the name; fix resolution. Don't switch the URL to an IP. |
| URL is `http://localhost:<port>` and it fails | Topology A — fix the DTU **profile** `localhost`→gateway rewrite, not the hook config. |
| New DTU host can't reach CI | Per-host floor — apply the `raw.dnsmasq` line on the new host's `incusbrN`. |
| Events piled up while the container was cut off | No data lost — replay with `context-intelligence-upload`. |

See also: [`docs/troubleshooting.md`](troubleshooting.md) (the local symptom → cause → fix
guide) and [`docs/remote-server-troubleshooting.md`](remote-server-troubleshooting.md)
(dispatch, timeouts, and auth **once the connection forms**).
