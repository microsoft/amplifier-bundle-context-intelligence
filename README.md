# amplifier-bundle-context-intelligence

An [Amplifier](https://github.com/microsoft/amplifier) bundle that captures session events as structured data for analysis and querying.

The bundle writes every session event to a local JSONL log and — when one or more **destinations** are configured — fans those events out to one or more [Context Intelligence Server](https://github.com/microsoft/amplifier-context-intelligence) instances for graph storage and blob management. Each session is routed to every destination whose `.gitignore`-style include/exclude patterns match the session's working directory, so different projects can flow to different servers (or to none) while local capture always happens.

---

## What it does

| Always active | When one or more `destinations` are configured |
|---------------|------------------------------------------------|
| Writes `events.jsonl` + `metadata.json` per session, both tagged with `workspace` | Fans each session's events out to every destination whose include/exclude patterns match the session's working directory |
| | Enables graph-powered Cypher queries via `graph_query` tool |
| | Enables `blob_read` tool for resolving `ci-blob://` URIs |

Two agents are included for querying session data:

- **`graph-analyst`** — primary entry point. Queries the context-intelligence property graph using Cypher, resolves `ci-blob://` URIs, and automatically delegates to `session-navigator` when the graph server is unreachable or returns 0 sessions.
- **`session-navigator`** — local fallback agent. Navigates session data via flat JSONL files using safe `bash`/`jq`/`grep` extraction patterns when the server is unavailable. Invoked only by `graph-analyst` via the delegation chain — external callers should use `graph-analyst` as the entry point.

A **`/context-intelligence` mode** is also included for building new context intelligence-aware tooling. Activate it to enter a design workspace where you can investigate session data, explore the graph model, and produce reusable Amplifier components (skills, agents, context files, recipes, CLIs) for your project.

### Composition — pick the layer you need

The bundle ships as **composable layered behaviors** rather than one monolith. Richer layers `include` the lighter ones, so you compose only what you need:

| Behavior | Adds | Use when |
|----------|------|----------|
| `context-intelligence-logging` | the telemetry hook only (event capture + optional server fan-out) | you want **pure session telemetry/logging** — no agents, tools, skills, or mode |
| `context-intelligence-navigation` (Layer 1) | `session-navigator` (reads raw JSONL on disk; no graph server) | local/offline navigation fallback only |
| `context-intelligence-analysis` (Layer 2) | `graph-analyst` + graph/query skills + the query tools (⊃ navigation) | graph read/query/exploration, no design mode |
| `context-intelligence-design` (Layer 3) | the `/context-intelligence` design mode (⊃ analysis) | full read/query **plus** the tooling-design workflow |
| `context-intelligence` (umbrella) | `design` **+** `logging` together | the full drop-in: read/query/design **and** session instrumentation |

The umbrella `context-intelligence.yaml` is the drop-in install (Quick Start below). Reach for a single layer when an integrator needs something leaner — e.g. compose `context-intelligence-logging` into an app that only needs telemetry, with zero analysis/skill machinery.

---

## Understanding workspace

**Workspace** is the primary isolation boundary for event data. It is written into every local file and every server POST — sessions in different workspaces are completely independent whether queried locally or via the graph. Typical uses: separate projects (`my-api`, `frontend`), separate environments (`dev`, `staging`, `prod`), or separate teams on a shared server.

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

## Quick Start

### 1. Install

**Add to an existing app** (recommended) — layers the behavior on top of your active bundle without pulling in foundation as a dependency:

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main#subdirectory=behaviors/context-intelligence.yaml --app
```

**Standalone** — creates a dedicated session configuration using the full root bundle (includes foundation):

```bash
amplifier bundle add git+https://github.com/microsoft/amplifier-bundle-context-intelligence@main
amplifier bundle use context-intelligence
```

Every Amplifier session will now write events to local JSONL files automatically — no server required.

### 2. (Optional) Forward events to one or more servers

To push events to the [Context Intelligence Server](https://github.com/microsoft/amplifier-context-intelligence) for graph storage and querying, you need a running server instance and its API key. See the [server repository](https://github.com/microsoft/amplifier-context-intelligence) for setup instructions.

Forwarding is configured with **`destinations`** — a named map of servers, each routed by `.gitignore`-style patterns on the session's working directory. Put the secret in `~/.amplifier/keys.env` (loaded automatically, never committed) and reference it from `~/.amplifier/settings.yaml`:

```bash
# ~/.amplifier/keys.env  — secrets only, never commit
MAIN_CI_KEY=<your-api-key>
```

```yaml
# ~/.amplifier/settings.yaml
overrides:
  hook-context-intelligence:
    config:
      destinations:
        main:
          url: "http://localhost:8000"
          api_key: "${MAIN_CI_KEY}"      # resolved from keys.env before it reaches the hook
          include: ["**"]                # every session (the default)
```

That single-destination block is the common case. To route different projects to different servers — or to exclude some entirely — add more named destinations; for the full routing model and pattern rules, see [Server forwarding — `destinations`](#server-forwarding--destinations) in the Configuration reference.

> **Never write a literal API key into `settings.yaml`.** That file is version-controllable configuration; a secret written there is one accidental commit away from exposure. Always reference it via `${VAR}` from `keys.env`.

### 3. Verify

After running a session:

```bash
ls ~/.amplifier/projects/<project_slug>/sessions/*/context-intelligence/
# events.jsonl  metadata.json

head -1 ~/.amplifier/projects/<project_slug>/sessions/*/context-intelligence/events.jsonl | jq .workspace
cat ~/.amplifier/projects/<project_slug>/sessions/*/context-intelligence/metadata.json | jq .workspace
```

If the server is configured, open `http://localhost:8000/dashboard` — your session will appear once authenticated.

---

## Exploration guide

If you're wondering what's worth trying after getting set up, [`docs/context-intelligence-exploration-guide.md`](docs/context-intelligence-exploration-guide.md) is a curated list of things to explore — verifying the connection, testing session capture, querying the graph, and more. Not a formal test plan; more "here's what's interesting."

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

    # Tuning — all optional
    "log_level": "WARNING",
    "exclude_events": ["context:compaction"],
    "dispatch_timeout": 30,
    "dispatch_failure_threshold": 3,
}

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
| **`entra`** | `auth_mode: entra` | a Microsoft Entra bearer token from your developer `az login` session, requested for the audience named by `auth_resource` |

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

> **Scope of Entra mode in this version.** Entra mode provides **parity with your existing `az login` identity** — it uses your developer `az login` session to obtain the bearer token. It is the **interactive-login** path, not a full enterprise non-interactive auth system.
>
> **Non-interactive environments are not yet served by Entra mode.** CI/CD pipelines and cloud-hosted services that cannot run `az login` should use a static `api_key` for now — if you are a pipeline author, reach for `auth_mode: static` to avoid surprises. Non-interactive credential support (managed identity / OIDC / service principal) is a planned follow-up.
>
> **Server-side prerequisite.** Entra mode requires the **server** to be configured to validate Entra tokens. Against a server that only accepts static keys, use `auth_mode: static`.

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
| `exclude_events` | direct value | `[]` | fnmatch patterns for events to suppress (event names, not paths). |
| `dispatch_timeout` | `${...}` placeholder | `30` | HTTP write timeout (seconds) for server dispatch uploads. |
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
| **1** | First entry of `sources` on the tool's own config (`overrides.tool-context-intelligence-query.config`) | The explicit read override. Wins when set. Applies to both `graph_query` and `blob_read` — configure once. |
| **2** | First entry of the hook's `destinations` block | **The common case** — queries follow the same server you upload to, with zero extra config. This is the bridge that makes a `destinations`-only setup "just work" for reads. |
| **3** | Env `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` / `AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY` | Single canonical last-resort fallback (reached via `${VAR}` placeholders in the shipped YAML, same convention as everywhere else). |
| — | else | `configuration_error: "context-intelligence server URL not configured"`. |

Each field walks the chain independently (a tier that supplies a `url` but no `api_key` lets `api_key` fall through). **Env is a true fallback — it never outranks the hook destination** (tier 2). There are **no** `*_PRIVATE_*` environment variables; the only env names consulted are the canonical `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` / `_API_KEY`.

**`sources`** is a mapping keyed by name, mirroring the hook's `destinations` shape. **Only a single read source is supported in this version** — the read path does **not** fan out to multiple sources. Configure exactly one entry (conventionally named `default`); if more than one is present, only the **first** (declaration / insertion order) is used and the rest are ignored.

In most setups you configure nothing here: **with no `sources` set, the read tools use the first configured `destination` in the hook config as their read source** (tier 2 below). Reach for `sources` only when the read endpoint must differ from the upload destination (e.g. a read replica or a debugging override) — it overrides the **read path only** and does not change where the hook uploads:

```yaml
# ~/.amplifier/settings.yaml — only needed when queries must hit a DIFFERENT server than uploads.
# One config namespace covers both graph_query and blob_read tools — configure once.
# Only ONE read source is supported; name it `default`.
overrides:
  tool-context-intelligence-query:
    config:
      sources:
        default:                            # the single read source (only the first entry is honored)
          url: "http://read-replica.example.com"
          api_key: "${CI_READ_KEY}"        # secret lives in keys.env, referenced here
```

| Sub-key | Required | Default | Description |
|---------|----------|---------|-------------|
| `url` | yes | — | Base URL of the CI server the query tool reads from. |
| `api_key` | yes | — | Bearer token for read requests to that server. |

**Legacy back-compat (read side):** with no `sources` key present, explicit top-level scalars on the tool config (`context_intelligence_server_url` + `context_intelligence_api_key`, **both** required) synthesize a single `default` read entry at tier 1 — symmetric to the hook's legacy synthesis. With neither set, resolution falls through to the hook destination (tier 2) and then env (tier 3).

> **Most users configure nothing here.** A single hook `destinations` entry already powers both upload and query. `sources` exists only for the read-replica / split-endpoint case.

#### Skill body sync — `skill_sync_enabled` (opt-in)

The `graph-analyst` agent uses a `context-intelligence-graph-query` skill that documents the Cypher patterns it issues. The bundle ships a **SHA-pinned vendored copy** of that skill body (`bundled_skill/context-intelligence-graph-query.md`, inside the `tool-context-intelligence-query` module), so the skill works fully offline with **zero network traffic** for skill acquisition. The optional `skill_sync_enabled` knob controls whether — at session start — the skill body is *refreshed from a Context Intelligence server* instead of using the vendored copy. It lives in the same namespace as `sources` (`overrides.tool-context-intelligence-query.config`).

| Key | Source | Default | Description |
|-----|--------|---------|-------------|
| `skill_sync_enabled` | tool config → `coordinator.config` → env `AMPLIFIER_CONTEXT_INTELLIGENCE_SKILL_SYNC_ENABLED` → default | **`false`** | When **`false`** (default): no skill network traffic at all. If a server source is resolved, the vendored offline body is swapped in (still zero network); if none is resolved, the shipped stub is retained. When **`true`** and a server source resolves: the skill body is version-gated and conditionally fetched from the server at session start (a `skill:unloaded` reload handler is also registered). When `true` but the server is offline, the agent degrades gracefully (no crash). |

```yaml
# ~/.amplifier/settings.yaml — opt IN to refreshing the graph-query skill body from the server.
# Default is OFF: the bundled, SHA-pinned skill body is used and NO skill traffic occurs.
overrides:
  tool-context-intelligence-query:
    config:
      skill_sync_enabled: true        # default false — leave unset for the zero-network path
```

Skill sync resolves its server using the **same** `(server_url, api_key)` chain as the query tools above (`sources` → hook `destinations` → env). The vendored-body install **fails loud** if the on-disk body's SHA does not match the pin. See [`docs/context-intelligence-skill-sync-flow.png`](docs/context-intelligence-skill-sync-flow.png) for the full enabled/disabled decision flow.

> **Telemetry hook does not fetch skills.** Skill acquisition belongs entirely to the query tool (above) and is opt-in. The `hook-context-intelligence` module is **pure telemetry** — event capture and `destinations` fan-out only — and performs no skill loading.

---

## Server dispatch

### Dispatch isolation

The hook isolates server traffic behind a single bounded background worker per session. The event callback only appends local JSONL and enqueues best-effort HTTP work — it never waits for a server round trip. The worker lazily creates a persistent `httpx.AsyncClient`, reuses one keep-alive connection, and serializes POSTs to avoid unbounded task growth when the server is slow or unavailable.

HTTP timeouts are phase-specific: short `connect`/`pool` fail-fast bounds, a moderate `read` timeout, and `dispatch_timeout` applied to the `write` phase so larger payload uploads do not fail prematurely.

Each live POST carries a deterministic `idempotency_key` derived from the sanitized event envelope. The server may use it to suppress duplicate live submissions while still allowing explicit replay from local `events.jsonl`.

If the dispatch queue fills, the newest event is dropped (it remains durable in `events.jsonl`) and a rate-limited warning is logged naming the real storage path. Dispatch is **never** permanently disabled.

### Connection reuse

The worker uses lazy creation: it creates an `httpx.AsyncClient` on the first dispatch request and keeps it alive for the entire session lifetime. This avoids opening a connection before any events arrive. TCP connection pooling means a single keep-alive connection is reused for all POSTs rather than opening a new one per event. The client is closed via `aclose()` during session finalization.

### Auto-recovery

The dispatcher never permanently disables a destination. On any transient failure (connection errors, timeouts, 5xx, 429, 401) the worker enters **DEGRADED** state: it holds the failed event in a local variable and retries the *same* event with capped full-jitter backoff (`dispatch_backoff_initial`, `dispatch_backoff_max`, `dispatch_backoff_jitter`) until the POST succeeds or the session ends. Ordering is preserved by construction — a single background worker, one in-flight event, new events queue behind it.

**Failure classification** — `_post()` returns one of three outcomes:
- **DELIVERED** (2xx): reset failure counter and backoff; worker resumes draining the queue.
- **TRANSIENT** (connection errors, timeouts, 5xx, 429, 401): worker enters/stays DEGRADED, sleeps backoff, retries the same event.
- **PERMANENT** (403, 400, 413, 422): loud rate-limited log; skip event, advance to the next.

**Notifications — self-healing (emitted once; do not require user action):**
- **DEGRADED notice** — emitted once per continuous failure episode (not per retry) when `_consecutive_failures` reaches `dispatch_failure_threshold`.
- **RECOVERY notice** — "Reconnected — resuming delivery" on the first successful POST after DEGRADED. No event count (the count is not knowable without a replay scan).
- **Persistent-401 escalation** — if `>= dispatch_failure_threshold` consecutive 401s are observed, a rate-limited WARNING is emitted periodically (at most once per 60 s): "this looks like an auth problem, not a network blip. Check credentials."

**Notifications — action-needed (require user action to recover):**
- **OVERFLOW** — when the in-memory queue is full, the newest event is dropped (remains durable in `events.jsonl`) and a rate-limited WARNING is logged naming `context-intelligence-upload --path <real storage path>` (--server-url/--api-key come from flags or env/config).
- **Shutdown-undelivered** — if any events remain undelivered when `close()` is called, a WARNING is emitted with an honest count (`queued + in-flight + overflow-dropped`) and the real storage path.

**Idempotency contract:** each POST carries a deterministic `idempotency_key` (SHA-256 over `{event, workspace, data}`). Retries are safe — the server can suppress duplicate deliveries. (The "single server-side record" guarantee is an assumption verified by the real-server E2E tests, not the unit suite.)

**Known limitation:** during a prolonged outage, once the bounded in-memory queue fills, the newest events are dropped from the queue but remain durable in `events.jsonl`. Recover them after the outage with `context-intelligence-upload --path <real storage path>` (--server-url/--api-key come from flags or env/config).

See [`docs/dispatch-circuit-breaker.dot`](docs/dispatch-circuit-breaker.dot) for the updated dispatch flow and [`docs/dispatch-auto-recovery-lifecycle.dot`](docs/dispatch-auto-recovery-lifecycle.dot) for the consolidated auto-recovery lifecycle (HEALTHY → DEGRADED → RECOVERY → OVERFLOW → SHUTDOWN).

---

## What gets stored

### Local files (always)

```
<base_path>/<project_slug>/sessions/<session_id>/context-intelligence/
├── events.jsonl    ← one JSON line per event, each tagged with workspace
└── metadata.json   ← session lifecycle record, also tagged with workspace
```

`events.jsonl` record schema — fields in order:

```
event      string   — event name, e.g. "session:start", "tool:call"
workspace  string   — isolation scope (empty string if not configured)
timestamp  string   — ISO 8601 timestamp from event data
data       object   — full sanitized event payload
```

`metadata.json` schema:

```
format      string   — always "context-intelligence"
version     string   — always "1.0.0"
session_id  string
workspace   string   — same value as in events.jsonl
parent_id   string   — empty if root session
started_at  string
status      string   — "running" → "completed" / "failed"
ended_at    string   — set on finalisation
working_dir string
```

Optional fields (present only when set): `agent_name`, `parallel_group_id`, `recipe_name`, `recipe_step`.

### Server-side graph (when server configured)

All graph building, Neo4j writes, and blob management happen in the CI server.
See [`context/graph-model-reference.md`](context/graph-model-reference.md) for the Neo4j graph model.

---

## Agents

| Agent | Available | Tools | Role |
|-------|-----------|-------|------|
| `graph-analyst` | Always | `graph_query`, `blob_read`, `tool-filesystem`, `tool-bash`, `tool-skills` | Primary entry point — graph-powered analysis via Cypher across all three data layers, blob resolution, automatic fallback |
| `session-navigator` | Always (via delegation) | `tool-filesystem`, `tool-search`, `tool-bash`, `tool-skills` | Local fallback — safe JSONL navigation via bash/jq/grep; invoked by graph-analyst when the server is unreachable |
| `context-intelligence-design-facilitator` | `/context-intelligence` mode only | `tool-skills` | Design guide — domain elicitation and component design facilitation for building new context intelligence-aware tooling |

**Delegation chain:** External callers always invoke `graph-analyst`. If the server is unreachable or the workspace contains 0 sessions, it delegates to `session-navigator`, which navigates local JSONL files using safe extraction patterns. `session-navigator` is never invoked directly.

The `context-intelligence-design-facilitator` is a conversational design guide available only when the `/context-intelligence` mode is active. It asks questions to understand the user's domain, maps that domain to context intelligence data layers, and helps design the right Amplifier component shape (skill, agent, recipe, CLI, etc.) for the investigation findings. It delegates investigation to `graph-analyst` and component authoring mechanics to ecosystem experts (`foundation:foundation-expert`, `recipes:recipe-author`).

See [`context/safe-extraction-patterns.md`](context/safe-extraction-patterns.md) for JSONL navigation patterns.

---

## Context Intelligence Design Mode

Activate with `/context-intelligence` (or `/mode context-intelligence`).

The design mode is an opt-in workspace for building new context intelligence-aware Amplifier components and standalone tools. It adds the `context-intelligence-design-facilitator` agent on top of the always-active bundle capabilities — nothing existing is removed or hidden.

### What it does

The mode supports an investigate → design → produce lifecycle:

1. **Investigate** — use `graph-analyst` (graph-powered, all three data layers) and `session-navigator` (local JSONL fallback) to understand what context intelligence can already observe about the target runtime
2. **Design** — the facilitator asks domain questions (what events does the runtime emit? what behaviors are invisible today? what would be valuable to observe?), maps findings to data layers, and recommends the right output shape
3. **Produce** — create reusable components: skills, context files, agents, recipes, docs, agent tool modules, or standalone CLI tools

### Tool policies in the mode

| Tool | Policy |
|------|--------|
| `graph_query`, `blob_read`, `read_file`, `glob`, `grep` | Safe — always allowed |
| `write_file`, `edit_file` | Warn — first call blocked with a reminder; retry proceeds |
| `bash` | Blocked — the mode processes potentially untrusted session data |

### What the mode produces

The output shape depends on the user's needs. Anything produced is **vendored into the consuming project** — not shipped in this bundle. The consuming project owns updates when the context intelligence schema changes.

| Shape | When to use |
|-------|-------------|
| Skill | Reusable Cypher query pattern or JSONL extraction pattern |
| Context file | Domain-specific awareness injected into project agents |
| Agent | Specialist that investigates a specific runtime |
| Recipe | Repeatable multi-step investigation or analysis workflow |
| CLI tool | Standalone investigation utility outside Amplifier sessions |
| Agent tool module | Production Amplifier tool wrapping a verified pattern |
| Docs | Captured forensic findings, query guides, schema notes |

### Accumulating project context

Save investigation findings to `.amplifier/context-intelligence/` in your project. The mode auto-scans `.md` files there (up to 50KB) on entry, making accumulated project-specific knowledge available across sessions.

See [`context/dual-path-library-template.md`](context/dual-path-library-template.md) for the library template that every generated tool should follow, and [`context/jsonl-event-schema.md`](context/jsonl-event-schema.md) for the events.jsonl schema contract.

---

## Repository structure

```
amplifier-bundle-context-intelligence/
├── bundle.md                           ← root bundle definition
├── bundle.dot / bundle.png             ← generated bundle structure diagram
├── behaviors/                          ← composable layered behaviors (compose what you need)
│   ├── context-intelligence-navigation.yaml  ← Layer 1: session-navigator only
│   ├── context-intelligence-analysis.yaml    ← Layer 2: + graph-analyst + query tools (⊃ navigation)
│   ├── context-intelligence-design.yaml      ← Layer 3: + design mode (⊃ analysis)
│   ├── context-intelligence-logging.yaml     ← orthogonal: telemetry hook only
│   └── context-intelligence.yaml             ← umbrella: design + logging (full drop-in)
├── modes/
│   └── context-intelligence.md  ← design-time mode
├── agents/
│   ├── graph-analyst.md  ← primary entry point agent
│   ├── session-navigator.md      ← local fallback agent
│   └── context-intelligence-design-facilitator.md  ← design guide agent (mode only)
├── context/
│   ├── event-schema.md                 ← all 51+ Amplifier events
│   ├── graph-model-reference.md        ← Neo4j graph model for Cypher queries
│   ├── safe-extraction-patterns.md     ← JSONL navigation patterns
│   ├── config-resolution.dot           ← HookConfigResolver fallback chain diagram
│   ├── session-disk-layout.dot         ← on-disk session directory structure
│   ├── delegation-strategy.dot         ← graph-analyst → session-navigator delegation logic
│   ├── agents/
│   │   └── session-storage-knowledge.md
│   ├── dual-path-library-template.md      ← copy-paste library template for dual-path tools
│   └── jsonl-event-schema.md               ← events.jsonl schema contract
├── modules/
│   ├── hook-context-intelligence/      ← the Python hook module — PURE TELEMETRY
│   │                                     (no skill_fetcher.py / legacy_content/ — skills moved out)
│   └── tool-context-intelligence-query/ ← graph_query + blob_read tools + opt-in skill sync
│       └── amplifier_module_tool_context_intelligence_query/
│           ├── graph_query_tool.py     ← skill_sync_enabled knob (default false)
│           ├── skill_sync.py           ← on_session_ready orchestration (opt-in)
│           ├── skill_fetcher.py        ← server version-gate + conditional fetch (only when enabled)
│           └── bundled_skill/
│               └── context-intelligence-graph-query.md  ← SHA-pinned vendored offline body
├── docs/
│   ├── context-intelligence-exploration-guide.md   ← what to explore and how to test
│   ├── context-intelligence-skill-sync-flow.dot    ← skill-sync enabled/disabled decision flow
│   ├── dispatch-circuit-breaker.dot    ← dispatch flow and circuit breaker state machine
│   └── logging-handler-flow.dot        ← thin forwarder architecture
├── skills/
│   ├── context-intelligence-graph-query/
│   ├── context-intelligence-session-navigation/
│   └── …                               ← additional graph/design skills
└── tests/
```

---

## Development

```bash
# Module tests
cd modules/hook-context-intelligence
uv sync
uv run pytest tests/ -q

# Bundle-level tests
uv run pytest ../../tests/ -q

# YAML validation — requires pyyaml (not installed by default in the bundle virtualenv)
# Install pyyaml first if the command fails with "No module named 'yaml'":
#   pip install pyyaml   OR   uv pip install pyyaml
uv run python -c "
import yaml; from pathlib import Path
data = yaml.safe_load(Path('behaviors/context-intelligence.yaml').read_text())
[print(f'  - {t[\"module\"]}') for t in data.get('tools', [])]
[print(f'  - {h[\"module\"]}') for h in data.get('hooks', [])]
print('YAML validates OK')
"
```

---

## Related

- [amplifier-context-intelligence](https://github.com/microsoft/amplifier-context-intelligence) — the CI server (Neo4j + blob storage + dashboard)
- [amplifier-app-cli](https://github.com/microsoft/amplifier-app-cli) — CLI that sends `project_slug` used for workspace resolution
- [amplifier](https://github.com/microsoft/amplifier) — the Amplifier framework


## Contributing

> [!NOTE]
> This project is not currently accepting external contributions, but we're actively working toward opening this up. We value community input and look forward to collaborating in the future. For now, feel free to fork and experiment!

Most contributions require you to agree to a
Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us
the rights to use your contribution. For details, visit [Contributor License Agreements](https://cla.opensource.microsoft.com).

When you submit a pull request, a CLA bot will automatically determine whether you need to provide
a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions
provided by the bot. You will only need to do this once across all repos using our CLA.

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/).
For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or
contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft
trademarks or logos is subject to and must follow
[Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/legal/intellectualproperty/trademarks/usage/general).
Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship.
Any use of third-party trademarks or logos are subject to those third-party's policies.
