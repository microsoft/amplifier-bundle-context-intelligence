# Configuration & Integration Reference

> Deep reference for configuring, embedding, and operating the
> `amplifier-bundle-context-intelligence` hook and query tools.
> New here? Start with the [README Quick start](../README.md#quick-start) first —
> this document is the exhaustive reference you graduate to once the bundle is installed.

**Contents**

- [Understanding workspace](#understanding-workspace)
- [Configuring via the Amplifier app-cli](#configuring-via-the-amplifier-app-cli)
- [Embedding in an Amplifier application](#embedding-in-an-amplifier-application)
- [Configuration reference](#configuration-reference)
- [Server dispatch](#server-dispatch)

---

## Understanding workspace

**Workspace** is the primary organizing label for event data. It is written into every local file and every server POST, and it is how you scope a query to one project's sessions. Typical uses: separate projects (`my-api`, `frontend`), separate environments (`dev`, `staging`, `prod`), or separate teams on a shared server.

> **Workspace is not a security boundary.** It is a label the *client* chooses, not an access control. The server does not bind a credential to a workspace, does not validate the `workspace` a client sends, and `graph_query` accepts `workspace: "*"` to read across all of them — as does hand-written Cypher that simply omits the filter. On a shared server, every credential can read every workspace: separation is by convention, not enforcement. If you need one contributor to be unable to read another's data, that has to be enforced at the server or network layer — see [remote-access-sharing.md](https://github.com/microsoft/amplifier-context-intelligence/blob/main/docs/remote-access-sharing.md) in the server repo.

### How workspace appears in data

`workspace` is a top-level field in all three places data is written:

**`events.jsonl`** — every line:
```json
{"event":"session:start","workspace":"my-project","timestamp":"2026-01-15T10:23:44.123Z","data":{"session_id":"abc-123","working_dir":"/home/user/myapp",...}}
```

**`metadata.json`** — session-level record:
```json
{"format":"context-intelligence","version":"1.0.0","session_id":"abc-123","workspace":"my-project","parent_id":"","started_at":"2026-01-15T10:23:44.123Z","last_event_at":"2026-01-15T10:23:59.789Z","status":"completed","ended_at":"2026-01-15T10:24:01.456Z","working_dir":"/home/user/myapp"}
```

**Server POST** (`POST /events`) — forwarded to the CI server:
```json
{"event":"session:start","workspace":"my-project","idempotency_key":"aci-event-v1:<sha256>","data":{...}}
```

### Resolution priority

The hook resolves `workspace` using the same `config → coordinator → default` pattern as all other properties:

| Priority | Source | Notes |
|----------|--------|-------|
| **1 (highest)** | `config["workspace"]` | From `settings.yaml` or env var — see [Configuration reference](#configuration-reference) |
| **2** | `coordinator.config["workspace"]` | Set programmatically before the session starts — see [Embedding](#embedding-in-an-amplifier-application) |
| **3 (lowest)** | `project_slug` derived from working directory | Automatic — `/home/user/my-api` becomes `-home-user-my-api` |


---

## Configuring via the Amplifier app-cli

When this bundle is loaded through the [Amplifier app-cli](https://github.com/microsoft/amplifier-app-cli) (`amplifier bundle add`), the app-cli provides a `settings.yaml` override mechanism for passing configuration values that differ from the bundle's defaults — for example, a different server URL or workspace name, or when your organisation names secrets differently in `keys.env`.

### The override pattern

`~/.amplifier/settings.yaml` is the app-cli's knob for bundle configuration. It is safe to commit to version control **as long as secrets are referenced via `${VAR_NAME}` interpolation**, never as literal values. The actual secrets stay exclusively in `~/.amplifier/keys.env`. The `${...}` placeholder is resolved by the app-cli **before** the value reaches the hook — the hook reads only its mount config dict and never the environment directly, so the variable name in `keys.env` is entirely your choice (it never has to match any `AMPLIFIER_*` convention).

The actual `destinations` configuration — its sub-keys, routing rules, pattern semantics, defaults, validation, and per-project overrides — lives in one place: [Server forwarding — `destinations`](#server-forwarding--destinations) in the Configuration reference.

---

## Embedding in an Amplifier application

When integrating this hook from Python rather than through the bundle CLI, call `mount()` directly.

```python
from amplifier_module_hook_context_intelligence import mount
```

Signature:

```python
async def mount(coordinator, config: dict) -> cleanup_fn
```

`mount()` returns an async cleanup coroutine that **must** be awaited when the session ends — it drains in-flight HTTP dispatches and closes the persistent HTTP client.

### Minimal — JSONL only

```python
cleanup = await mount(coordinator, config={})
# ... session runs ...
await cleanup()
```

With an empty config dict the hook resolves everything from `coordinator.config` and the working-directory slug.

### Full config dict

All keys are optional. Omitted keys fall through to `coordinator.config` then to built-in defaults:

```python
config = {
    # Server forwarding — omit entirely to disable
    "context_intelligence_server_url": "http://localhost:8000",
    "context_intelligence_api_key": "your-api-key",

    # Workspace — written into every events.jsonl record and metadata.json
    # Omit to fall back to coordinator.config["workspace"], then project_slug
    "workspace": "my-project",

    # Storage (default: ~/.amplifier/projects)
    "base_path": "/var/data/amplifier/projects",

    # Durable server-forwarding diagnostics log — a SEPARATE sink from events.jsonl.
    # Per-day forwarding-YYYY-MM-DD.jsonl recording auth failures / give-ups /
    # permanent rejects (with destination name + url + HTTP status + session id).
    # Default: ~/.amplifier/context-intelligence-logs
    "forwarding_log_dir": "/var/log/amplifier/context-intelligence-logs",

    # Tuning — all optional
    "log_level": "WARNING",
    "exclude_events": ["context:compaction"],
    "dispatch_timeout": 30,          # HTTP write-phase budget (s), default 10.0
    "dispatch_read_timeout": 20,     # HTTP read-phase budget (s), default 10.0 — raise for slow/remote servers
    "close_drain_timeout": 15,       # shutdown flush window (s), default 10.0 — raise for remote (Azure/APIM) drains
    "dispatch_failure_threshold": 3,
}
```

> **Deploying against a remote or Azure-hosted server?** For the tuning knobs above,
> auth diagnosis, and a probe cookbook, see
> [Troubleshooting remote / Azure-deployed servers](remote-server-troubleshooting.md).

```python

cleanup = await mount(coordinator, config=config)
await cleanup()
```

### Workspace via coordinator.config

When workspace varies at runtime (e.g., multi-tenant apps), omit `workspace` from the config dict and set it on the coordinator instead. It is consulted as the middle fallback:

```python
coordinator.config["workspace"] = tenant_id   # priority 2 — used when config["workspace"] is absent

cleanup = await mount(coordinator, config={
    "context_intelligence_server_url": "http://localhost:8000",
    "context_intelligence_api_key": "your-api-key",
})
```

### Accessing resolved values

`mount()` registers a `HookConfigResolver` as the `context_intelligence.hook_config_resolver` capability:

```python
resolver = coordinator.get_capability("context_intelligence.hook_config_resolver")
resolver.workspace                  # resolved workspace string
resolver.base_path                  # resolved Path object
resolver.session_dir("abc-123")     # Path to a session's CI directory
```

---

## Configuration reference

The `config` dict passed to `mount()` uses the same keys as the `overrides.hook-context-intelligence.config` block in `settings.yaml`. The hook is a **pure mount-config consumer**: it reads only this config dict (plus coordinator capabilities) and does **not** read environment variables or `settings.yaml` itself. Environment variables reach the config only because the shipped behavior YAML (and any `settings.yaml` override) reference them through `${VAR}` / `${VAR:default}` placeholders, which the Amplifier app-cli expands before `mount()` is called. There is **no** automatic `AMPLIFIER_CONTEXT_INTELLIGENCE_<KEY>` → config-key mapping — an env var with no corresponding `${VAR}` placeholder in the active config never reaches the hook. The **Env var** column below names the variable each shipped placeholder reads.

#### Server forwarding — `destinations`

`destinations` is a dict keyed by destination name, under `overrides.hook-context-intelligence.config`. Each value is a dict with these keys:

| Sub-key | Required | Default | Description |
|---------|----------|---------|-------------|
| `url` | yes | — | Base URL of the CI server for this destination. |
| `api_key` | yes | — | Bearer token for this destination. Sent as `Authorization: Bearer <value>` on that destination's POSTs only — because each destination references its own `${VAR}`, distinct keys never cross between servers. |
| `include` | no | `["**"]` | `.gitignore`-style patterns matched against the session's working directory. The destination is a candidate when any pattern matches. |
| `exclude` | no | `[]` | `.gitignore`-style patterns; if any matches, the destination is dropped for that session (**exclude wins**). |

```yaml
# ~/.amplifier/settings.yaml — route different projects to different servers
overrides:
  hook-context-intelligence:
    config:
      workspace: "my-project"          # optional — auto-resolved if omitted
      destinations:
        personal:
          url: "http://localhost:8000"
          api_key: "${PERSONAL_CI_KEY}"   # secret lives in keys.env, referenced here
          include: ["**"]                 # all sessions...
          exclude: ["**/client-*/"]       # ...except any client-* project dir and below
        team:
          url: "https://ci.team.example"
          api_key: "${TEAM_CI_KEY}"
          include: ["**/work/"]           # only sessions under a "work" directory
```

**Routing.** For each session the hook derives a match key from the session's working directory (the `session.working_dir` capability) and tests it against every destination. A destination is **active** for a session iff the working dir matches an `include` pattern **and** does not match an `exclude` pattern — **exclude wins, per destination**. The session's events are sent to **every** active destination (true fan-out): a session can match zero, one, or several. **Local JSONL is always written**, regardless of how many destinations match.

**Pattern semantics — `.gitignore` rules.** `include` / `exclude` patterns use `.gitignore` (gitwildmatch) semantics, matched against the session's working **directory**:

| Pattern | Matches |
|---------|---------|
| `foo/`, `foo`, `**/foo/`, `**/foo` | the directory `foo` **and everything beneath it** |
| `**` | every session |
| empty `include` list | nothing (the destination is inactive) |

Prefer the trailing-slash directory form (e.g. `**/work/`) to mean "this project and all its sessions" — it matches whether the session is launched from the project **root** or any subdirectory. (A pattern that targets only contents, like `**/work/**`, still also matches the directory itself here, because the match key is a directory.)

**Defaults & validation.** Omitted `include` defaults to `["**"]` (match everything); omitted `exclude` defaults to none. After `${VAR}` expansion, a `destinations` entry whose `url` **or** `api_key` is empty is a **mount error** (fail-fast, naming the offending destination). With no `destinations` configured, the hook is local-JSONL-only. (The legacy scalar path is intentionally more lenient — see [Deprecated — legacy single-server scalars](#deprecated--legacy-single-server-scalars) below.)

**Per-project override.** Because `destinations` is keyed by name, a project `.amplifier/settings.yaml` can override a single destination's `include`/`exclude` (e.g. `destinations.team.include`) without restating the others — the app-cli deep-merges user → project settings.

> **Secrets:** keep `api_key` values in `~/.amplifier/keys.env` and reference them via `${VAR}` in `settings.yaml`. Never write a literal key into `settings.yaml`.

#### Authentication — `auth_mode` / `auth_resource`

Each **target** chooses its authentication mode **independently** — every hook `destination` (the write path) and every query-tool `source` (the read path). A mixed fleet is fine: reach one server with a static key and another with a Microsoft Entra bearer token. A target is **never** both at once.

| Mode | Selected by | Credential |
|------|-------------|------------|
| **`static`** | `auth_mode: static` — the **default**, unchanged | the target's `api_key`, sent as `Authorization: Bearer <api_key>` |
| **`entra`** | `auth_mode: entra` | a Microsoft Entra bearer token via `DefaultAzureCredential`, requested for the audience named by `auth_resource`. Works both interactively (developer `az login`) and non-interactively (managed identity / workload identity / service principal) with no config change |

Two new per-target keys (valid on both `destinations` entries and `sources` entries):

| Sub-key | Required | Default | Description |
|---------|----------|---------|-------------|
| `auth_mode` | no | `static` | `static` (use `api_key`) or `entra` (use a Microsoft Entra bearer token). |
| `auth_resource` | **only when `auth_mode: entra`** | — | The Entra audience the token is requested for — `api://<server-app-client-id>`. |

Both values support `${VAR}` / `${VAR:default}` environment substitution, exactly like `api_key` — e.g. `auth_resource: "${CI_AUTH_RESOURCE}"`.

```yaml
# ~/.amplifier/settings.yaml — a mixed fleet: one static destination, one Entra destination,
# plus an Entra read source. auth_mode is chosen per target.
overrides:
  hook-context-intelligence:
    config:
      destinations:
        local-dev:                       # static (default)
          url: "http://localhost:8080"
          api_key: "${MY_CI_KEY:}"
          include: ["**"]
        azure-team:                      # entra
          url: "https://ci.example.com"
          auth_mode: entra
          auth_resource: "api://<server-app-client-id>"
          include: ["**"]
  tool-context-intelligence-query:
    config:
      sources:
        azure-team:
          url: "https://ci.example.com"
          auth_mode: entra
          auth_resource: "api://<server-app-client-id>"
```

**Fail-loud.** A misconfigured target is a **mount error** (fail-fast, naming the offending target): `entra` with an empty `auth_resource`, or `static` with an empty `api_key` — evaluated after `${VAR}` expansion. The hook never silently sends an empty or blank bearer.

> **Entra mode works both interactively and non-interactively.** It acquires the bearer token via azure-identity's `DefaultAzureCredential`, which walks a credential chain: environment-variable service principal → managed identity → workload identity (federated OIDC) → shared token cache → `az login`. So the **same** `auth_mode: entra` serves a developer (falls through to `az login`, yielding a *delegated user* token) **and** a hosted app such as Resolve (managed identity / workload identity, yielding an *app-only service* token with a `roles` claim) — with no config change.
>
> **Non-interactive (app-to-app / M2M) prerequisite.** A hosted identity must be granted an **application** app-role (e.g. `Contributor` for write, `Reader` for read) on the server's Entra App Registration — the server authorizes app-only tokens on the `roles` claim. Ensure the runtime environment exposes a non-interactive credential `DefaultAzureCredential` can find (managed identity, workload identity, or `AZURE_*` env vars). CI/CD or hosts that have *no* Azure identity at all can still use a static `api_key`.
>
> **Server-side prerequisite.** Entra mode requires the **server** to be configured to validate Entra tokens (`auth_mode=entra`). Against a server that only accepts static keys, use `auth_mode: static`.

#### Token caching & refresh (Entra)

Entra mode (`auth_mode: entra`) caches the bearer token **in memory, per process** — shared across a session and its in-process subsessions. (The `static` `api_key` path holds a constant key: nothing is cached or refreshed.)

The cached token is **reused until shortly before it expires** (Azure CLI tokens typically live ~60–90 min), then **refreshed automatically on the next request**. Net effect: the `az` credential is invoked **at most once per token lifetime per process**, not on every request — so auth adds no meaningful latency to the hot path.

The refresh safety window is tunable via the env var **`AMPLIFIER_CONTEXT_INTELLIGENCE_TOKEN_REFRESH_MARGIN_S`** (default **300** seconds): the token is refreshed once it is within this many seconds of expiry.

```bash
# ~/.amplifier/keys.env — refresh 600s (10 min) before expiry instead of the default 300s
AMPLIFIER_CONTEXT_INTELLIGENCE_TOKEN_REFRESH_MARGIN_S=600
```

**Fail-loud.** If a refresh fails (e.g. your `az` session has expired or you ran `az logout`), the error surfaces — a stale or empty token is never sent. Re-running `az login` resolves it.

**Switching `az account` / tenant mid-session.** Because the token is cached, a freshly switched identity is **not** picked up until the cached token refreshes (within `~margin` of expiry); until then, events would be attributed to the previously cached identity. To switch identities immediately, **start a new session** — a fresh process resets the cache.

#### Other config keys

| Key | Source | Default | Description |
|-----|--------|---------|-------------|
| `workspace` | `${...}` placeholder, e.g. `${AMPLIFIER_CONTEXT_INTELLIGENCE_WORKSPACE}` | *(auto)* | Written into every `events.jsonl` record and `metadata.json`. Resolution: `config["workspace"]` → `coordinator.config["workspace"]` → `project_slug`. |
| `log_level` | `${...}` placeholder | `INFO` | Hook logging level. |
| `base_path` | direct value | `~/.amplifier/projects` | Root directory for local JSONL output. |
| `forwarding_log_dir` | `${...}` placeholder, e.g. `${AMPLIFIER_HOME}/context-intelligence-logs` | `~/.amplifier/context-intelligence-logs` | Directory for the **durable server-forwarding diagnostics log** — a per-day `forwarding-YYYY-MM-DD.jsonl` recording auth failures, give-ups, permanent rejects, and auth-token-unavailable events (each with the destination **name AND url**, HTTP status, and session id). This is a **separate sink from `events.jsonl`** (which holds intercepted events and is itself forwarded — operational errors never go there). Consolidated across destinations and sessions; append-only; best-effort (a write failure never disrupts dispatch). Resolution mirrors `base_path`: `config` → `coordinator.config` → default; an unexpanded `${...}` placeholder falls back to the default silently. Use it to diagnose a stuck/misrouted destination after the session has ended. |
| `exclude_events` | direct value | `[]` | fnmatch patterns for events to suppress (event names, not paths). |
| `dispatch_timeout` | `${...}` placeholder | `30` | HTTP write timeout (seconds) for server dispatch uploads. |
| `dispatch_connect_timeout` | `${...}` placeholder | `3.0` | HTTP connect timeout (seconds) for the TCP/TLS connect phase of server dispatch. Raised from the legacy hardcoded 0.5 s: a too-tight connect budget manufactures spurious `httpx.ConnectTimeout` → TRANSIENT failures (the `"unreachable, retrying with backoff"` warning) against a **healthy** server, especially for cross-region, Entra-authenticated calls over VPN/proxy. Classified TRANSIENT and retried; clamped to a `0.1` s floor. Raise it further on slow networks. |
| `dispatch_read_timeout` | `${...}` placeholder | `10.0` | HTTP read timeout (seconds) for server dispatch. Raised from the legacy hardcoded 3.0 s to avoid spurious read-timeout failures on slow server responses; classified TRANSIENT and retried. `pool` (0.5 s) remains fixed; `connect` is configurable via `dispatch_connect_timeout` (above). |
| `dispatch_failure_threshold` | `${...}` placeholder | `3` | Consecutive **transient** failures before the worker enters DEGRADED state and emits a warning notice; also gates the persistent-401 auth-escalation. Does **not** disable dispatch — dispatch is never permanently disabled. |
| `dispatch_queue_capacity` | direct value | `256` | Maximum queued HTTP dispatches per destination. Clamped to `>= 1` (a value of `0` would be unbounded). When full, the newest event is dropped (durable in `events.jsonl`) and a rate-limited warning is logged naming the real storage path; dispatch is **never** disabled. |
| `dispatch_backoff_initial` | `${...}` placeholder | `1.0` | Initial backoff sleep (seconds) for the first DEGRADED retry. |
| `dispatch_backoff_max` | `${...}` placeholder | `30.0` | Maximum backoff sleep (seconds); the cap for capped full-jitter backoff. |
| `dispatch_backoff_jitter` | `${...}` placeholder | `true` | Enable full-jitter backoff. Set `false` to use a fixed `dispatch_backoff_initial` sleep per retry. String-aware: `"false"`, `"0"`, `"no"`, `"off"` (any case) are treated as `false`. |
| `close_drain_timeout` | direct value | `0.5` | Shutdown grace period (seconds) for draining queued HTTP dispatches. |

> The **Source** column shows how a value reaches the config: a `${VAR}` placeholder in the YAML (expanded by app-cli from `keys.env`/environment), or a direct literal value. There is **no** automatic `AMPLIFIER_*` env-var → config mapping; only `${VAR}` placeholders present in the active config are read.

#### Deprecated — legacy single-server scalars

Supported for back-compat; prefer `destinations`. When set **and** no `destinations` block is present, these synthesize a single `default` destination with `include: ["**"]`.

| Key | `${...}` placeholder used by shipped YAML | Behavior |
|-----|-------------------------------------------|----------|
| `context_intelligence_server_url` | `${AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL:}` | Base URL of the single legacy server. |
| `context_intelligence_api_key` | `${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY:}` | Bearer token for it. |

Legacy degradation (differs from `destinations`): if `context_intelligence_server_url` is set but `context_intelligence_api_key` is empty after expansion, the hook **degrades to local-only and logs a WARNING — it does not fail to mount** (an empty `url`/`api_key` inside a `destinations` entry *is* a hard mount error).

---

#### Query tools (`graph-query`, `blob-read`) — read-side endpoint

The hook config above governs **where events are written** (the upload / fan-out side). The **query tools** `graph_query` and `blob_read` (both mounted by the `tool-context-intelligence-query` module) are the **read side** — they call a Context Intelligence server to answer graph queries and fetch blobs. They share a single `ToolConfigResolver` built once in `mount()`, so **a single config namespace** (`overrides.tool-context-intelligence-query.config`) serves both tools. They resolve their `(server_url, api_key)` independently per field, **explicit-read-config first**, and the chain is designed so that **configuring `destinations` alone is enough** — you do **not** have to repeat the endpoint for queries:

| Order | Source | Notes |
|-------|--------|-------|
| **1** | The `source`-selected entry, resolved across the **whole connectable pool** — the tool's own `sources` **and** the hook's `destinations`, merged by name (a `sources` entry wins on a name collision) — see [`sources`](#query-tools-graph-query-blob-read--read-side-endpoint) below for the full selection rules. | The explicit read override. Wins when set — `source=<name>` can name a `sources` entry **or** a hook `destination`. Applies to both `graph_query` and `blob_read` — pass `source=<name>` per call whenever the pool has 2+ configured sources. |
| **2** | First entry of the hook's `destinations` block | **The common case** — queries follow the same server you upload to, with zero extra config. This is the bridge that makes a `destinations`-only setup "just work" for reads. |
| **3** | Env `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` / `AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY` | Single canonical last-resort fallback (reached via `${VAR}` placeholders in the shipped YAML, same convention as everywhere else). |
| — | else | `configuration_error: "context-intelligence server URL not configured"`. |

Each field walks the chain independently (a tier that supplies a `url` but no `api_key` lets `api_key` fall through). **Env is a true fallback — it never outranks the hook destination** (tier 2). There are **no** `*_PRIVATE_*` environment variables; the only env names consulted are the canonical `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` / `_API_KEY`.

**`sources`** is a mapping keyed by name, mirroring the hook's `destinations` shape. Configure one
entry for the common case, or 2+ entries when queries must be able to target different servers.
**The read path does not fan out** — each `graph_query` / `blob_read` call still queries exactly
one source per invocation — but which one is now explicit rather than an accident of insertion
order:

| Configured sources | `source` argument passed | Result |
|---|---|---|
| 0 | omitted | Falls through to the hook's first `destination` (no fail-loud, regardless of how many destinations are configured), then env if there are no destinations either. |
| 0 | a name | You can still target a **specific destination** — `source=<destination-name>` resolves it from the connectable pool (`sources` ∪ `destinations`) even with zero `sources` configured. It is **not** an n/a case. Unknown name → error enumerating the whole connectable set. |
| 1 | omitted | That one source is used — no selector needed. |
| 1 | a name | Resolved across the whole connectable pool (the one configured source, or any hook `destination`). Used if the name matches; **error enumerating the whole connectable set (sources + destinations) if it doesn't.** |
| 2+ | a name | Resolved across the whole connectable pool; the named entry (source or destination) is used if found; **error enumerating the whole connectable set if not** — never silently substitutes a different source. |
| 2+ | omitted | **Error: `ambiguous_source_selection`.** With 2+ sources configured, a selector is required — there is no implicit "default" source chosen by insertion order. |

A misconfigured source (missing `url`, missing `api_key`/`auth_resource`) only blocks queries that
target **that** source by name — it does not block queries against other, correctly configured
sources (a startup-time WARNING is still logged for every misconfigured entry so operators see
typos immediately).

In most setups you configure nothing here: **with no `sources` set, the read tools use the first configured `destination` in the hook config as their read source** (tier 2 below). Reach for `sources` only when the read endpoint must differ from the upload destination (e.g. a read replica or a debugging override) — it overrides the **read path only** and does not change where the hook uploads:

```yaml
# ~/.amplifier/settings.yaml — only needed when queries must hit a DIFFERENT server than uploads.
# One config namespace covers both graph_query and blob_read tools — configure once.
overrides:
  tool-context-intelligence-query:
    config:
      sources:
        default:
          url: "http://read-replica.example.com"
          api_key: "${CI_READ_KEY}"        # secret lives in keys.env, referenced here
        archive:                            # a second source — now a first-class capability
          url: "http://archive-ci.example.com"
          api_key: "${CI_ARCHIVE_READ_KEY}"
```

```
# With 2+ sources configured (as above), every graph_query / blob_read call MUST pass
# `source`:
#   graph_query(query="...", source="default")
#   graph_query(query="...", source="archive")
# Omitting `source` here raises an error enumerating "default" and "archive".
```

| Sub-key | Required | Default | Description |
|---------|----------|---------|-------------|
| `url` | yes | — | Base URL of the CI server the query tool reads from. |
| `api_key` | yes | — | Bearer token for read requests to that server. |

With exactly one `sources` entry configured, `source` is optional on every call. With 2+ entries,
`source` is required on every call — see the table in [§4.1 above](#deprecated--legacy-single-server-scalars).

**Legacy back-compat (read side):** with no `sources` key present, explicit top-level scalars on the tool config (`context_intelligence_server_url` + `context_intelligence_api_key`, **both** required) synthesize a single `default` read entry at tier 1 — symmetric to the hook's legacy synthesis. With neither set, resolution falls through to the hook destination (tier 2) and then env (tier 3).

> **Most users configure nothing here.** A single hook `destinations` entry already powers both upload and query. `sources` exists only for the read-replica / split-endpoint case.

#### Read-path contract — success and failure envelopes

Both `graph_query` and `blob_read` resolve their connection via `resolve_query_connection()`
(the single-endpoint selection described above) and return a JSON-serializable result —
they never raise. Every result names the resolved endpoint's provenance as a `source` block:
`{"name": ..., "url": ..., "origin": "source" | "destination" | "env"}`.

**Success:**

```python
# graph_query
{"source": {"name": "default", "url": "...", "origin": "destination"}, "rows": [...]}

# blob_read
{"path": "/tmp/ci-blobs/<session_id>/<key>.json", "source": {"name": "default", "url": "...", "origin": "destination"}}
```

**`list_sources: true`** (either tool) returns the whole connectable pool instead of
running a query — no `api_key` is included:

```python
{"connectable_set": [{"name": "default", "url": "...", "origin": "destination"}, ...]}
```

**Fail-loud read path.** A down, slow, or rejecting endpoint never returns a silent
empty result — it returns `success: false` with a typed `error`:

```python
{"success": false, "error": {"type": "connection_error", "message": "...", "source": {...}}}
```

`error.type` is one of `connection_error | timeout | http_status | decode_error` for
transport failures. A genuine empty `200` response still succeeds (empty rows / an
empty-but-valid blob is not treated as an error). The `error` payload carries the resolved
`source` block **only once an endpoint has actually been resolved** — post-resolution
failures (the transport errors above, plus `validation_error` and `uri_error`) carry it;
pre-resolution failures (an unknown `source=` name, `ambiguous_source_selection`,
`configuration_error`) do not, since no endpoint was chosen.

**Read timeout.** Configurable via `request_timeout` in the tool's config, or the env var
`AMPLIFIER_CONTEXT_INTELLIGENCE_QUERY_TIMEOUT` — default **30s**. A non-positive value
clamps up to a **0.1s floor** rather than disabling the timeout. This is distinct from the
hook's `dispatch_*` timeouts in the config table above, which govern the write/upload path.

#### Graph-query skill — vendored statically (no configuration)

The `graph-analyst` agent uses a `context-intelligence-graph-query` skill that documents the Cypher patterns it issues. That skill is **vendored statically** in this repo at `skills/context-intelligence-graph-query/SKILL.md` (sourced from the [Context Intelligence Server](https://github.com/microsoft/amplifier-context-intelligence) repo's `main` branch). The bundle's behaviors deliver it at compose time — there is **no runtime skill fetching, syncing, or configuration knob**.

The vendored file carries its own leading **no-server guidance block**: when no graph server is configured for the session, the skill instructs the agent to delegate to `session-navigator` rather than attempt Cypher against an unreachable server.

> **Telemetry hook does not load skills.** The `hook-context-intelligence` module is **pure telemetry** — event capture and `destinations` fan-out only — and performs no skill loading.

---

## Server dispatch

### Dispatch isolation

The hook isolates server traffic behind a single bounded background worker per session. The event callback only appends local JSONL and enqueues best-effort HTTP work — it never waits for a server round trip. The worker lazily creates a persistent `httpx.AsyncClient`, reuses one keep-alive connection, and serializes POSTs to avoid unbounded task growth when the server is slow or unavailable.

HTTP timeouts are phase-specific: a configurable `dispatch_connect_timeout` for the `connect` phase (default 3.0 s), a fixed short `pool` bound (0.5 s), a configurable `dispatch_read_timeout` for the `read` phase (default 10.0 s; see config table above), and `dispatch_timeout` applied to the `write` phase so larger payload uploads do not fail prematurely.

Each live POST carries a deterministic `idempotency_key` derived from the sanitized event envelope. The server may use it to suppress duplicate live submissions while still allowing explicit replay from local `events.jsonl`.

If the dispatch queue fills, the newest event is dropped (it remains durable in `events.jsonl`) and a rate-limited warning is logged naming the real storage path. Dispatch is **never** permanently disabled.

### Connection reuse

The worker uses lazy creation: it creates an `httpx.AsyncClient` on the first dispatch request and keeps it alive for the entire session lifetime. This avoids opening a connection before any events arrive. TCP connection pooling means a single keep-alive connection is reused for all POSTs rather than opening a new one per event. The client is closed via `aclose()` during session finalization.

### Auto-recovery dispatch

**Architecture.** Each destination gets a dedicated `_DestinationDispatcher` that owns a single background worker draining a bounded `asyncio.Queue` (default capacity 256, clamped `>= 1`). The event callback (`__call__`) only appends local JSONL and enqueues best-effort HTTP work — it never blocks, never waits for a network round trip. The local `events.jsonl` is the durable backstop; server delivery is always best-effort.

**Flow.** `_post()` classifies every attempt into one of three outcomes:

- **DELIVERED** (2xx): reset failure counter and backoff; emit a RECOVERY notice if returning from DEGRADED state ("Reconnected — resuming delivery"); worker resumes draining the queue.
- **TRANSIENT** (connect/read/write/pool timeouts, 5xx, 429, 401): worker performs **retry-in-place** — it holds the in-flight event in a local variable and sleeps a capped full-jitter backoff (`dispatch_backoff_initial` → `dispatch_backoff_max`) before retrying the same event. The event is never re-queued and ordering is preserved by construction. After `dispatch_failure_threshold` consecutive transient failures the worker enters **DEGRADED** state and emits a DEGRADED notice once per continuous failure episode. Persistent-401 escalation: if `>= dispatch_failure_threshold` consecutive 401s are observed a rate-limited WARNING ("check credentials") is emitted at most once per 60 s.
- **PERMANENT** (403, 3xx redirect, 400, 413, 422): loud rate-limited log; skip event, call `task_done()`, advance to the next. Dispatch is **never** permanently disabled.

**Overflow.** When the in-memory queue is full, the newest event is dropped using the drop-newest strategy (oldest events in the queue are preserved in delivery order). The dropped event remains durable in `events.jsonl`. A rate-limited WARNING names the real storage path and the recovery command: `context-intelligence-upload --path <storage path>`.

**Shutdown.** When `close()` is called the worker is given a bounded drain window (`close_drain_timeout`, default 0.5 s) to finish in-flight work. Cancellation-safe: a sleeping backoff is cancelled cleanly. If any events remain undelivered an honest WARNING is emitted with a precise count (`queued + in-flight + overflow-dropped`) and the real storage path.

**Timeouts (all phases).** Connect: `dispatch_connect_timeout` (configurable, default 3.0 s; previously hardcoded 0.5 s — raised to prevent spurious connect-timeout failures on cross-region/VPN/proxy paths). Pool: 0.5 s (fixed). Write: `dispatch_timeout` (configurable, default 30 s). Read: `dispatch_read_timeout` (configurable, default 10.0 s; previously hardcoded 3.0 s — raised to prevent spurious read-timeout failures on slow server responses).

**Idempotency contract:** each POST carries a deterministic `idempotency_key` (SHA-256 over `{event, workspace, data}`). Retries are safe — the server can suppress duplicate deliveries. (The "single server-side record" guarantee is an assumption verified by the real-server E2E tests, not the unit suite.)

**Known limitation:** during a prolonged outage, once the bounded in-memory queue fills, the newest events are dropped from the queue but remain durable in `events.jsonl`. Recover them after the outage with `context-intelligence-upload --path <real storage path>` (--server-url/--api-key come from flags or env/config).

> Recovering an older **legacy `hooks-logging`** archive instead of a native one? It can be imported non-destructively with `--format logging-hook` — see [Legacy hooks-logging import](../modules/tool-context-intelligence-upload/README.md#legacy-hooks-logging-import---format-logging-hook) in the upload tool README.

See [`docs/dispatch-circuit-breaker.dot`](dispatch-circuit-breaker.dot) for the updated dispatch flow and [`docs/dispatch-auto-recovery-lifecycle.dot`](dispatch-auto-recovery-lifecycle.dot) for the consolidated auto-recovery lifecycle (HEALTHY → DEGRADED → RECOVERY → OVERFLOW → SHUTDOWN).

