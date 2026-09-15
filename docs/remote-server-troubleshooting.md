# Troubleshooting remote / Azure-deployed Context Intelligence servers

This guide covers the context-intelligence **hook** (the client that forwards session
events) talking to a **remote** Context Intelligence Server — in particular one deployed
on Azure behind **API Management (APIM)** with a per-request **Microsoft Entra** token.
Everything here is configured under `overrides.hook-context-intelligence.config` in
`~/.amplifier/settings.yaml` (the same keys the `mount()` config dict uses).

## Two things that are always true

Read these first — they reframe most "errors" as tuning, not incidents:

1. **Local capture never depends on the server.** Every event is written to the local
   `events.jsonl` *before* any forwarding is attempted. Forwarding is best-effort. A
   forwarding failure is therefore **never data loss** — undelivered events stay durable
   on disk and can be replayed later with `context-intelligence-upload` (see
   [Recovering undelivered events](#recovering-undelivered-events)).
2. **The hook is quiet unless it needs you.** It retries transient failures with capped
   exponential backoff and stays silent. When it *does* log a `WARNING`, that line is the
   signal — read it literally, then use the tables below.

## Why remote is different from `localhost:8000`

Against a local server every POST is a sub-millisecond loopback. Against a remote server
each POST pays **connect + TLS + APIM + Entra token + graph write** — often hundreds of
milliseconds to a couple of seconds per request. Timeout budgets that are generous for
localhost are too tight for that round-trip. The knobs below widen them.

## Tuning knobs

All optional, all under `overrides.hook-context-intelligence.config`. Bad or unparseable
values fall back to the default and are clamped to a `0.1 s` floor (they never crash
startup).

| Key | Default | What it bounds | Bump it for remote/Azure when… |
|-----|---------|----------------|-------------------------------|
| `close_drain_timeout` | `20.0` s | The **shutdown flush window** — how long `close()` waits for still-queued events to finish before the worker is cancelled. A **ceiling, not a fixed wait**: `close()` returns the moment the queue empties. | You see `… shutdown: N undelivered event(s)` with a **small** `queued=` count. |
| `dispatch_read_timeout` | `10.0` s | The HTTP **read** phase — waiting for the server's response after the request is sent. | You see `… unreachable, retrying with backoff`; APIM + graph-write latency is high. |
| `dispatch_timeout` | `10.0` s | The HTTP **write** phase — sending the request body. | Large event bodies over a slow uplink. |
| `dispatch_failure_threshold` | `3` | Consecutive failures before the escalation warning fires. | Rarely — raise only to quiet a flaky-but-recovering link. |
| `dispatch_queue_capacity` | `256` | In-memory queue depth before events overflow (to durable `events.jsonl`). | Very bursty sessions against a slow server. |

> The **connect** and **pool** timeouts are fixed at `0.5 s` and are intentionally not
> configurable — they fail fast on a genuinely unreachable host rather than hang. They are
> not the knob to touch for a slow-but-reachable remote server; `dispatch_read_timeout` is.

Example — a generous profile for an Azure/APIM destination:

```yaml
# ~/.amplifier/settings.yaml
overrides:
  hook-context-intelligence:
    config:
      close_drain_timeout: 30      # let the tail flush at shutdown (default 20.0)
      dispatch_read_timeout: 20    # APIM + Entra + graph write can be slow
      destinations:
        azure-team:
          url: "https://ci.example.com"
          api_key: "${AZURE_TEAM_KEY}"     # static mode
          # or, for an Entra-protected server:
          # auth_mode: entra
          # auth_resource: "api://<server-app-client-id>"
```

## Symptom → cause → fix

### `<dest> shutdown: N undelivered event(s) (queued=Q in-flight=F overflow-dropped=D)`

**Read the breakdown before you reach for a knob — it names two different problems.**

- **Not data loss, in either case:** all `N` events remain durable in `events.jsonl` and
  replay with `context-intelligence-upload`.

**Case 1 — SHORT tail (`queued=` single digits, `overflow-dropped=0`).**

- **Cause:** the drain window (`close_drain_timeout`) elapsed before the last few queued
  events finished their remote round-trip.
- **Fix:** raise `close_drain_timeout` (default `20.0`; try `30`). The value is a
  **ceiling, not a fixed wait** — `close()` returns the instant the queue empties, so
  raising it costs nothing on a healthy drain.

**Case 2 — DEEP queue (`queued=` in the dozens/hundreds, and/or `overflow-dropped>0`).**

- **Cause:** this is a **throughput** problem, not a drain-window problem. The dispatcher
  posts **serially** — one in-flight event at a time — so its ceiling is roughly one event
  per round-trip. Against a remote destination costing several hundred ms per POST, a busy
  session enqueues faster than it drains, all session long. The queue is already deep long
  before shutdown; `overflow-dropped>0` means it also hit `dispatch_queue_capacity` (256)
  and shed the newest events.
- **Tell-tale:** `breaker_open=False` and `degraded_seconds=0` in the matching
  `shutdown_undelivered` record in `forwarding-YYYY-MM-DD.jsonl` — the destination is
  **healthy**, just slower than you produce.
- **Do NOT** just raise `close_drain_timeout`: no drain window empties a 150-deep queue,
  and you will only lengthen shutdown.
- **Fix — now automatic.** The next session's backlog sweep replays this from the
  delivery watermark with no action from you. Confirm it is working by reading
  `<session_dir>/delivery/<destination>.json`: `offset` should be climbing toward the
  size of `events.jsonl`, with `last_outcome: delivered`. The signals that mean
  something is genuinely wrong are `last_outcome: no_progress`, a non-null
  `last_error`, or a rising `consecutive_sweeps_without_progress`.
- Replay is duplicate-free because the server MERGEs on a deterministic `node_id`
  under a `(node_id, workspace)` uniqueness constraint — not because of
  `idempotency_key`, which is an in-memory 7-day cache that a restart clears.
- Events older than `sweep_max_age_hours` (default 48) are **not** swept; they are
  reported, and need `context-intelligence-upload` or `context-intelligence-recover`.
- Raising `dispatch_queue_capacity` converts *overflow drops* into *queued* events —
  it does not make them deliver.

To see which case you are in across all sessions, aggregate the durable records:

```bash
python3 - <<'EOF'
import json, glob, os, re, statistics
pat = re.compile(r"queued=(\d+) in_flight=(\d+) overflow_dropped=(\d+)")
rows = []
for f in glob.glob(os.path.expanduser("~/.amplifier/context-intelligence-logs/forwarding-*.jsonl")):
    for line in open(f):
        try: r = json.loads(line)
        except Exception: continue
        if r.get("kind") != "shutdown_undelivered": continue
        m = pat.search(r.get("detail", ""))
        if m: rows.append(tuple(int(x) for x in m.groups()))
if rows:
    q = [r[0] for r in rows]
    print(f"{len(rows)} shutdowns | median queued={statistics.median(q)} max={max(q)} "
          f"| with overflow drops: {sum(1 for r in rows if r[2])}")
EOF
```

Real output from a workstation forwarding to an Azure/APIM destination — a textbook Case 2:

```
215 shutdowns | median queued=157 max=256 | with overflow drops: 77
```

A median `queued` in the dozens or higher is Case 2.

### Why did my session take a couple of seconds longer to exit?

- **Cause:** `sweep_close_grace_seconds` (default `2.0`) let a catch-up sweep keep running
  at session teardown instead of being cancelled immediately. This only happens when a
  sweep was still mid-delivery at the moment the session ended — a normal exit with
  nothing in flight is unaffected (measured `0.000s` added). If you saw the delay, it
  means the bundle was actively recovering backlog, not that something is wrong.
- **Shared budget:** the grace period is one shared budget across **all** destinations
  for that session, not one per destination — several slow destinations still cost a
  single grace window, not several.
- **Fix (if you want immediate exit instead):**
  ```yaml
  overrides:
    hook-context-intelligence:
      config:
        sweep_close_grace_seconds: 0.0
  ```
  **Tradeoff:** with `0.0`, a sweep that gets cancelled mid-delivery simply resumes from
  its last committed chunk on the next session — nothing is lost, it just takes more
  sessions for the backlog to fully catch up.

### `<dest> unreachable, retrying with backoff — events still captured locally`

- **Cause:** connect/read timeouts or transient network errors. The hook is retrying with
  backoff. A **one-off** line is a blip and self-heals (you'll see `Reconnected to <dest>`).
  A **sustained** stream means the read budget is too tight, or the server is slow/unreachable.
- **Fix:** raise `dispatch_read_timeout`; confirm the destination `url` scheme is `https`
  and the network path is open; run the [probes](#diagnostic-probe-cookbook) to confirm the
  server is up.

### `<dest> still rejecting auth (HTTP <status>) after N auth failures — Check credentials.`

**Confirm it is *really* auth before you rotate a key.** A genuine, fresh `401` is required
to raise this warning. Verify against the server rather than trusting the log:

1. **Is the server even returning 401s?** Check its access log for `POST /events` statuses
   over your window. If it shows `202`s (accepted) and **no** `401`s, your key is fine and
   the noise came from elsewhere — older hook versions miscounted **timeouts** as `401`s;
   upgrade the hook.
2. **Does *your* key authenticate?** Use the [auth probe](#does-my-key-authenticate) — a
   `422` (body-validation) response means the token was **accepted**; a `401` means the key
   is **rejected**.
3. **`static` vs `entra` mismatch.** A `static` bearer sent to a server that expects an
   Entra delegated token (or vice-versa) is rejected `401` on every attempt. Match
   `auth_mode` to what the server enforces (see [Which auth_mode for Azure](#which-auth_mode-for-azure)).
4. **A rotated static key is not re-read by a running session.** The auth strategy is built
   once when the session starts. If you fix/rotate the key mid-session, **restart the
   session** — the running process keeps sending the old key until it does.

After **sustained** `401`s (a high hard-failure rate over a rolling window, held for at
least ~30 s — not one blip, not a brief token-rotation window), the hook trips a
**circuit breaker** for that destination: it emits **one** warning naming the destination
and its URL, then **pauses forwarding** to that destination and goes quiet (no more
per-event spam). Every event still lands in `events.jsonl`. While paused it retries a
**single probe roughly every 5 minutes**; the moment the destination recovers (you fix the
key/URL), the probe succeeds and **forwarding auto-resumes with no restart** ("Reconnected
… resuming delivery"). A `403` is treated as a per-event authorization skip and does **not**
trip the breaker (it won't disable a destination that authenticates fine). Transient
failures (network, timeout, 5xx, 429) never trip it — they retry forever.

**Recovery has two parts:** fixing the credential/URL auto-resumes delivery of **new**
events (no restart), but the **backlog** that accumulated while paused only drains when you
replay it: `context-intelligence-upload --path <events.jsonl dir>` (see
[Recovering undelivered events](#recovering-undelivered-events)).

> **The warning names the URL — believe the URL, not the destination label.** A `401` can
> come from a *misrouted* URL (an auth gateway, a reverse proxy, the wrong port) that is not
> the CI server at all — the named server may have rejected nothing. The warning now prints
> both the destination name **and** its `url`; confirm that `url` actually targets the CI
> server before you touch credentials.
>
> **Look at the durable forwarding log for after-the-fact diagnosis.** These auth failures,
> give-ups, permanent rejects, and token-unavailable events are also written — with URL, HTTP
> status, and session id — to a per-day `forwarding-YYYY-MM-DD.jsonl` under
> `forwarding_log_dir` (default `~/.amplifier/context-intelligence-logs`), a **separate sink
> from `events.jsonl`**. Grep it once the noisy session has ended:
> `jq 'select(.kind=="auth_failure")' ~/.amplifier/context-intelligence-logs/forwarding-*.jsonl`.

### `<dest> auth token unavailable (run \`az login\` to refresh) — retrying with backoff; events remain durable in events.jsonl.`

**This is a different failure from the `401` above** — it fires *before* a request is even
sent, when `auth_mode: entra` and token acquisition itself fails (a genuinely expired/absent
`az login` session causes azure-identity to raise rather than return a token). It is **not**
a silent drop and it does **not** count toward the 401 give-up ceiling described above (those
are unrelated failure modes and are tracked separately) — it is treated as any other transient
dispatch outcome and retried with the same capped backoff.

- **Cause:** `az login` session expired or absent in the environment Amplifier runs in.
- **Confirm:** `az account show` fails.
- **Fix:** re-run `az login`. Nothing is cached on a failed token acquisition, so the very
  next retry (no restart required) picks up the refreshed credential and resumes delivery.
- **Not data loss:** events queued in the meantime stay durable in `events.jsonl` regardless,
  same as every other transient path.

### Which `auth_mode` for Azure?

| Situation | Use |
|-----------|-----|
| Interactive developer against an Entra-protected server | `auth_mode: entra` + `auth_resource: api://<server-app-client-id>` (`DefaultAzureCredential` falls through to your `az login` identity) |
| Non-interactive app-to-app: a hosted service with a managed identity / workload identity / service principal | `auth_mode: entra` + `auth_resource: api://<server-app-client-id>`. The hosted identity must hold an **application** app-role (e.g. `Contributor`) on the server's App Registration. `DefaultAzureCredential` picks up the ambient credential — no `az login` needed. |
| A host with no Azure identity at all (e.g. a locked-down CI runner) | `auth_mode: static` with an `api_key` |

A misconfigured target fails loud at mount (naming the offending target): `entra` with an
empty `auth_resource`, or `static` with an empty `api_key` — evaluated **after** `${VAR}`
expansion. The hook never sends a blank bearer.

## Diagnostic probe cookbook

Replace `https://<server>` with your destination URL. These print no secrets.

**Is the server up, and what version?** (`/version` is unauthenticated.)

```bash
curl -s https://<server>/version        # -> {"version":"6.0.0"}
```

<a id="does-my-key-authenticate"></a>
**Does my key authenticate?** (send your bearer to `POST /events` with an empty body):

```bash
KEY="${AZURE_TEAM_KEY:-}"   # the same env var your destination references
curl -s -o /dev/null -w '%{http_code}\n' \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -X POST https://<server>/events -d '{}'
```

Interpret the status code:

| Status | Meaning |
|--------|---------|
| `422` | **Auth OK.** The token was accepted; the request reached body-validation and was rejected only because the body is empty (expected). |
| `401` | **Key rejected.** Wrong static key, or an `auth_mode`/token-type mismatch. |
| `403` | Authenticated but not authorized (identity not mapped on the server). |
| connect/timeout error | Network/URL/TLS problem, not auth — see the *unreachable* row above. |

**Server-side ground truth.** The decisive evidence for an auth dispute is the CI server's
own access log: count `POST /events` responses over your window. All `202`s with zero
`401`s means the server accepted everything and any client-side "401" warning was spurious.

## Recovering undelivered events

Nothing forwarded is ever lost — replay a session's durable log to any server:

```bash
context-intelligence-upload --path <session-dir> \
  --server-url https://<server> --api-key "$KEY"
# (flags also read from env/config; see --help)
```

Use this after fixing a credential, after a `shutdown: N undelivered` warning, or any time
you want to backfill a destination that was down.

## Quick reference

| You see… | Do this |
|----------|---------|
| `shutdown: N undelivered event(s)` | Read `queued=`. Small → raise `close_drain_timeout`. Deep / `overflow-dropped>0` → throughput, not the drain window; the next session's sweep now recovers it automatically (confirm via the watermark's `offset`/`last_outcome`). Safe in `events.jsonl` either way. |
| `N session(s) older than the sweep window` | Those events won't be swept automatically — recover with `context-intelligence-upload` or `context-intelligence-recover`. |
| Session exit took a couple seconds longer | Normal when a sweep was mid-delivery at teardown, bounded by `sweep_close_grace_seconds` (default `2.0`). Set `0.0` for immediate exit. |
| `unreachable, retrying with backoff` | One-off → ignore. Sustained → raise `dispatch_read_timeout`, check the path. |
| `still rejecting auth (HTTP 401)` | Probe the key (`422` = OK); check `auth_mode`; if key was rotated, **restart** the session. |
| A key was rotated mid-session | Restart the session — the old key is cached until then. |
| Events piled up while the server was down | Replay with `context-intelligence-upload`. |
