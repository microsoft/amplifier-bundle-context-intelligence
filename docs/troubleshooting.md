# Troubleshooting

Observed symptom → root cause → fix for the context-intelligence hook's local capture
and server forwarding. Work from the exact log line, not from guesses — each dispatch
warning is a precise signal (see the [Auto-recovery dispatch](../README.md#auto-recovery-dispatch)
state machine and [Server dispatch](../README.md#server-dispatch) in the README).

For **remote / Azure-deployed** servers (APIM, Entra, cross-region tuning, the auth probe
cookbook, and recovering undelivered events), see
[`remote-server-troubleshooting.md`](remote-server-troubleshooting.md).

---

## `team-shared unreachable, retrying with backoff — events still captured locally in events.jsonl, no action needed`

**What it means.** A configured destination (here named `team-shared`) hit a **TRANSIENT**
dispatch outcome and the worker entered DEGRADED state. Local capture to `events.jsonl` is
unaffected — **no events are lost**; they can be back-filled later with
`context-intelligence-upload`. This warning fires **only after** a request was actually
attempted, which — for `auth_mode: entra` — means **a bearer token was already acquired and
attached.** So this specific message does **not** indicate broken credentials.

**Root causes, most to least common:**

| Root cause | How to confirm | Fix |
|---|---|---|
| **Connect timeout too tight** for a cross-region / VPN / proxy path — the default was historically a hardcoded 0.5 s, which manufactures spurious `httpx.ConnectTimeout` against a perfectly healthy server. | Warning recurs across sessions even though a manual POST to the server URL succeeds. | Raise **`dispatch_connect_timeout`** (default now `3.0` s; see the [config table](../README.md#other-config-keys)). Set `5.0`+ on slow links. |
| **Transient network / server blip** (real timeout, 5xx, 429, or a 401 during token refresh). | Warning appears briefly then a `Reconnected — resuming delivery` INFO follows. | None needed — auto-recovery handles it. |
| **Expired `az login` session** (Entra mode). | `az account show` fails in the environment Amplifier runs in. Note: a *fully* expired session more often produces a different, louder failure (see below), not this graceful warning. | Re-run `az login`; the in-memory token cache refreshes on the next dispatch. |
| **Server not reachable at all** (wrong `url`, server down). | `curl -sS <url>` fails; DNS/route error. | Correct the destination `url`; confirm the server is up. |

**Quick verification that the destination actually works** (rules out "credentials are broken" by construction):

```bash
az account show                         # Entra mode: confirms an active login
az account get-access-token --resource <auth_resource>   # confirms a token can be minted
# then POST a probe event to <url>/events with that Bearer token — a 202 proves network + auth + server are all healthy
```

If the probe returns **202** but sessions still log the warning, the cause is almost always
the **connect timeout** — raise `dispatch_connect_timeout`.

---

## `<dest> auth token unavailable (run \`az login\` to refresh) — retrying with backoff; events remain durable in events.jsonl.`

If `auth_mode: entra` and your `az login` session is genuinely expired/absent, token
acquisition can fail **before** the request is even sent (azure-identity raises rather than
returning a token). This is **not** a silent drop — the dispatcher catches the failure,
treats it as a transient outcome, and retries with the same capped backoff as a network blip.
Because a fresh token is requested on every retry (nothing is cached on failure), simply
**re-running `az login`** mid-session lets the very next retry succeed and resume delivery —
no restart needed. Events queued in the meantime stay durable in `events.jsonl` regardless.
See [Token caching & refresh (Entra)](../README.md#token-caching--refresh-entra).

---

## `<dest> (<url>) forwarding paused after sustained auth failures (HTTP 401) …`

After **sustained** `401`s (a high hard-failure rate over a rolling window, held for at least
~30 s — not one blip, not a brief token-rotation window), the hook trips a **circuit breaker**
for that destination: it emits **one** warning naming the destination and its URL, then
**pauses forwarding** to that destination and goes quiet. Every event still lands in
`events.jsonl`.

- While paused it retries a **single probe roughly every 5 minutes**; the moment the
  destination recovers (you fix the key/URL), the probe succeeds and **forwarding auto-resumes
  with no restart** (`Reconnected … resuming delivery`).
- A `403` is a **per-event authorization skip** and does **not** trip the breaker (it won't
  disable a destination that authenticates fine). Transient failures (network, timeout, 5xx,
  429) never trip it — they retry forever.
- **Believe the URL, not the label.** The warning prints both the destination name and its
  `url`; a `401` can come from a misrouted URL (an auth gateway, wrong port) that is not the CI
  server at all. Confirm the `url` targets the CI server before touching credentials.

**Recovery has two parts:** fixing the credential/URL auto-resumes delivery of **new** events
(no restart); the **backlog** that accumulated while paused only drains when you replay it:
`context-intelligence-upload --path <events.jsonl dir>` (see
[Recovering undelivered events](remote-server-troubleshooting.md#recovering-undelivered-events)).

The full per-attempt trace (every status, url, session id, and the pause/resume transitions)
is written to the durable **forwarding diagnostics log** — a per-day
`forwarding-YYYY-MM-DD.jsonl` under `forwarding_log_dir` (default
`~/.amplifier/context-intelligence-logs`), a **separate sink from `events.jsonl`**. Grep it
after the fact:

```bash
jq 'select(.kind=="breaker_open" or .kind=="auth_failure")' \
  ~/.amplifier/context-intelligence-logs/forwarding-*.jsonl
```
