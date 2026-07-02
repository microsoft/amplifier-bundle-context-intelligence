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
| `close_drain_timeout` | `10.0` s | The **shutdown flush window** — how long `close()` waits for still-queued events to finish before the worker is cancelled. | You see `… shutdown: N undelivered event(s)`. |
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
      close_drain_timeout: 15      # let the tail flush at shutdown
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

### `<dest> shutdown: N undelivered event(s)`

- **Cause:** at session end the drain window (`close_drain_timeout`) elapsed before the
  last few queued events finished their remote round-trip. This is the single most common
  remote symptom. (The default is now `10.0 s`; older configs that pinned it to `0.5 s`
  will see this constantly against a remote server.)
- **Not data loss:** the `N` events remain durable in `events.jsonl`.
- **Fix:** raise `close_drain_timeout` (e.g. `15`–`20`) so the tail flushes; or accept the
  warning and replay the tail with `context-intelligence-upload`.

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

After a genuinely persistent `401`, the hook **gives up on that event after 10 attempts**
(it stays durable in `events.jsonl`) and moves on, rather than looping forever — so a bad
key no longer blocks the whole queue. Fix the key, restart, and replay if needed.

### Which `auth_mode` for Azure?

| Situation | Use |
|-----------|-----|
| Non-interactive: CI/CD pipeline or a cloud-hosted service that cannot run `az login` | `auth_mode: static` with an `api_key` |
| Interactive developer against an Entra-protected server | `auth_mode: entra` + `auth_resource: api://<server-app-client-id>` (uses your `az login` identity) |

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
| `shutdown: N undelivered event(s)` | Raise `close_drain_timeout`; tail is safe in `events.jsonl`. |
| `unreachable, retrying with backoff` | One-off → ignore. Sustained → raise `dispatch_read_timeout`, check the path. |
| `still rejecting auth (HTTP 401)` | Probe the key (`422` = OK); check `auth_mode`; if key was rotated, **restart** the session. |
| A key was rotated mid-session | Restart the session — the old key is cached until then. |
| Events piled up while the server was down | Replay with `context-intelligence-upload`. |
