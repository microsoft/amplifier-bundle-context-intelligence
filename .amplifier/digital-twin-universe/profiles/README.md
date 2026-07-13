# Context-Intelligence DTU Profiles

Digital Twin Universe (DTU) profiles for testing this bundle in isolated, realistic
environments. Launched with [`amplifier-digital-twin`](https://github.com/microsoft/amplifier-bundle-digital-twin-universe).

Per this repo's `AGENTS.md` ("Testing & what 'done' looks like"): green unit tests are
the floor, not proof. Any change that crosses a **seam** — the client↔server boundary,
networking, auth, or any agent / skill / mode / tool / hook / config edit — must be
proven with a **real DTU run**, not a mock. These profiles are that harness.

## How these DTUs verify (the principle)

**Structural and behavioural checks load the bundle through the Amplifier CLI — they do
NOT run code artefacts.** A real DTU test does what a user does: `amplifier bundle add`
the bundle, activate `/context-intelligence`, and drive the tools / agents / skills / mode
through an actual `amplifier` session, then assert the *loaded* behaviour. Running
`pytest` (or importing modules) inside a container is **not** an end-to-end test — it's a
unit test in a different directory. The unit/integration suites already run via
`uv run pytest` and `scripts/validate-full.sh`; DTUs exist to prove the *installed,
CLI-loaded* bundle actually works.

Consequence: any profile that verifies loading or behaviour **installs via
`amplifier bundle add`** (see the mode profiles' `install.command`) and, for local-branch
work, resolves that install to your branch through **Gitea** (`url_rewrites` +
`--var gitea_host=...`).

## Dev dependencies & setup

To run these DTUs during development you need the following on your host. Add them once:

| Dependency | Why | Check | Add it |
|------------|-----|-------|--------|
| **`amplifier-digital-twin` CLI** | launches/manages DTU environments | `amplifier-digital-twin --version` | `uv tool install git+https://github.com/microsoft/amplifier-bundle-digital-twin-universe@main` |
| **Incus** | the container runtime every profile uses | `incus version` | in an Amplifier session load the `digital-twin-universe` skill → `read_file("@digital-twin-universe:docs/installing-incus.md")` |
| **Docker** | Gitea + any server sidecar | `docker version` | load the `digital-twin-universe` skill → `read_file("@digital-twin-universe:docs/installing-docker.md")` |
| **Gitea** | serves *your local branch* so `amplifier bundle add` installs your code, not `main` — **required for the mode / seam profiles that test uncommitted changes** | `docker ps \| grep gitea` | in an Amplifier session load the `gitea` skill; mirror this repo, then pass the endpoint via `--var gitea_host=http://<gitea-host>:3000` (profiles carry the `url_rewrites` that redirect `@main` → the mirror) |
| **`GH_TOKEN`** | clone the bundle inside the container | `echo $GH_TOKEN` | `export GH_TOKEN=$(gh auth token)` |
| **A real LLM key** (`ANTHROPIC_API_KEY`) | any profile that drives an actual `amplifier` session (all behavioural scenarios) | `echo $ANTHROPIC_API_KEY` | export a real key — placeholder/short values will not run a session |

**Which profiles need what:** the `signals` profile needs only Incus (deterministic
library). Everything that loads the bundle to check mode/agents/skills/tools/config or runs
a session needs **Incus + Gitea mirror + a real LLM key** (and the CI server for
server-backed read/write seams — see below). There is no way to prove the CLI-loaded
seams without those; that is the point of an end-to-end test.

## Profile inventory (what each really tests)

| Profile | Tests | Needs | Launch |
|---------|-------|-------|--------|
| `context-intelligence-bundle-smoke-test.yaml` | Hook late-contributor path: a module mounting *after* the hook still lands its event in `events.jsonl` | Incus + `ANTHROPIC_API_KEY` + `GH_TOKEN` | `amplifier-digital-twin launch .amplifier/digital-twin-universe/profiles/context-intelligence-bundle-smoke-test.yaml` |
| `context-intelligence-signals-validation.yaml` | The deterministic `signals` scoring library + CLI (score fixtures, thresholds, render-findings). **No LLM, no server, no Gitea** | Incus only | `amplifier-digital-twin launch .../context-intelligence-signals-validation.yaml` |
| `context-intelligence-redesigned-mode-validation.yaml` | The 5-phase `/context-intelligence` mode end-to-end (tool policies, context injection, specialists, Phase-0/2 artifacts) | Incus + Gitea mirror + LLM | `... launch .../context-intelligence-redesigned-mode-validation.yaml --var gitea_host=http://<gitea>:3000` |
| `context-intelligence-contributes-migration-validation.yaml` | The `contributes.agents` gating migration (atomic mount w/ JSONL proof, clean unmount, sub-session delegation, skill search) | Incus + Gitea mirror + LLM | `... launch .../context-intelligence-contributes-migration-validation.yaml --var gitea_host=...` |
| `context-intelligence-mode-activation-validation.yaml` | Explicit `/context-intelligence` activation mounts the FULL gated surface — both specialists, all 3 context files, all 3 skills, tool policies — with an off→on→off round-trip | Incus + Gitea mirror + LLM | `... launch .../context-intelligence-mode-activation-validation.yaml --var gitea_host=...` |
| `context-intelligence-write-server-validation.yaml` | **WRITE to a single server** — hook dispatches a real session's events to ONE `destinations` server; proves the server received them (tagged by workspace) | Incus + Gitea mirror + LLM + **CI server** | `... launch .../context-intelligence-write-server-validation.yaml --var gitea_host=... --var ci_server_url=...` |
| `context-intelligence-write-fanout-validation.yaml` | **WRITE fan-out** — one session, TWO `destinations`; proves BOTH servers received the events (observes existing hook fan-out; never modifies it) | Incus + Gitea mirror + LLM + **2 CI servers** | `... launch .../context-intelligence-write-fanout-validation.yaml --var gitea_host=... --var ci_server_a=... --var ci_server_b=...` |
| `context-intelligence-query-validation.yaml` | **EXECUTE queries** (read side) — after logging, drives `graph_query` (Cypher) + `blob_read` (`ci-blob://`) via the `graph-analyst` agent; proves real rows/content come back with the `source` provenance naming the server | Incus + Gitea mirror + LLM + **CI server** | `... launch .../context-intelligence-query-validation.yaml --var gitea_host=... --var ci_server_url=...` |
| `example-dtu-external-server.yaml` | *Not a test* — reference profile: point the client hook at an **external CI server** with a tagged workspace | Incus + running CI server (below) | see below |

**Self-contained smoke to prove the harness works on your host:**
`context-intelligence-signals-validation.yaml` needs only Incus (no LLM/server/Gitea) — launch it first to confirm the DTU pipeline is healthy before the heavier profiles.

### How the mode profiles gate (runnable schema)

`amplifier-digital-twin` auto-runs a profile's **`provision`** and **`readiness`** on launch
(it ignores unknown keys like `manual_validation_steps`). So the three mode profiles put their
**deterministic structural proofs in `readiness`** — they gate the launch and fail it if the
CLI-loaded bundle is wrong. Each mode profile's `readiness` proves, for real:

- `amplifier` is usable and the bundle was **loaded via `amplifier bundle add` from the Gitea
  mirror** (the branch snapshot, not GitHub `main`);
- `amplifier bundle show context-intelligence-behavior` lists the **2 baseline agents**
  (graph-analyst, session-navigator) and the **2 mode-gated specialists are absent** while the
  mode is off (contributes.agents gating holds);
- the installed mirror mode file declares the full gated surface — `advertised: false`,
  `default_action: block`, 2 contributes.agents, 3 contributes.context (incl.
  `context-intelligence-strategy.md`), 3 contributes.skills (incl.
  `context-intelligence-evaluation-methodology`), and the `safe`/`warn` tool policies.

**Behavioural** activation — the real off→on→off round-trip (`/mode context-intelligence` →
`[context-intelligence]>` → `/mode off`) — is proven with a real Anthropic session and is
documented in each profile's `manual_validation_steps` as a **reproducible manual step**
(`amplifier-digital-twin exec <id> -- …`), because it needs a live PTY session.

**Honest limitation:** the *runtime-mounted set while the mode is active* (exactly which
agents/context/skills the mode manager mounts on activation) is **not dumpable via any CLI
command** in this Amplifier version, and the logging hook's `additional_events` covers
`delegate:*` only — it does **not** emit `mode:transition_completed`, so `events.jsonl` cannot
enumerate the mount. The profiles therefore prove the gated surface via *declared contributes +
inactive-baseline gating + the activation round-trip* — they do **not** claim a runtime
mount-list enumeration.

---

## Spinning up & using a Context-Intelligence server for end-to-end tests

The **read side** (`graph_query`, `blob_read`) and the **write side** (the telemetry
hook) both talk to a **Context-Intelligence server** — a separate component
([`microsoft/amplifier-context-intelligence`](https://github.com/microsoft/amplifier-context-intelligence),
backed by Neo4j, serving `/status`, `/events` (write), `/blobs/{session}` and Cypher
(read) on port `8000`). A true read-side e2e test needs a real server: **log events
via the hook, then query them back** via `graph_query` / `blob_read` and assert the
result + provenance.

There are two ways to give a DTU a server.

### Option A — External server on the host (simplest; the shipped path)

Run the server **outside** the DTU (on the host or another machine), and point the
DTU's client at it. This is exactly what `example-dtu-external-server.yaml` does.

1. **Start the server on the host** (per the server repo — Neo4j + server, listening on `:8000`):
   ```bash
   # in a checkout of microsoft/amplifier-context-intelligence — see that repo's README
   # (typically a docker compose bringing up Neo4j + the API on :8000)
   curl -sf http://localhost:8000/status   # confirm it's up
   ```
2. **Export the API key on the host** so the DTU passthrough can forward it:
   ```bash
   export AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY=<key>
   ```
3. **Launch the DTU pointed at it.** The server URL is auto-detected from the Incus
   bridge gateway (host), or set it explicitly:
   ```bash
   amplifier-digital-twin launch \
     .amplifier/digital-twin-universe/profiles/example-dtu-external-server.yaml \
     --var CONTEXT_INTELLIGENCE_WORKSPACE=e2e-readside \
     --var CONTEXT_INTELLIGENCE_SERVER_URL=http://<host-ip>:8000   # optional; auto-detected if omitted
   ```
   The profile wires `AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL / _API_KEY / _WORKSPACE`
   into the container and gates readiness on `curl $SERVER_URL/status`.
4. **Drive the e2e flow** inside the DTU (`amplifier-digital-twin exec <id> -- ...`):
   - run an Amplifier session → the hook POSTs events to `/events` (tagged with the workspace);
   - then read them back with the query tools — `graph_query` (Cypher, filtered by
     `workspace = "e2e-readside"`) and `blob_read` a `ci-blob://` URI — and assert the
     rows + the `source` provenance block name the server that answered.

> **Container DNS/networking:** if the client can't reach the server from inside the
> container, see `docs/container-dns-troubleshooting.md` (the gateway-IP + port pattern).

### Option B — Server as a Docker sidecar *inside* the DTU (self-contained)

Run the server **and Neo4j** as Docker containers **inside** the DTU via the
docker-in-incus pattern (DTU launches enable `security.nesting=true` by default). Use
this when you want one hermetic environment with no host dependency.

1. Read the nested-container guide first: load the `digital-twin-universe` skill →
   `read_file("@digital-twin-universe:docs/docker-in-incus.md")` (platform-specific
   networking, pre-flight with the `docker-in-incus` profile).
2. In the profile's `provision.setup_cmds`, bring up the server's compose (Neo4j + API)
   from the server repo, then point the client at `http://localhost:8000` via the same
   three `AMPLIFIER_CONTEXT_INTELLIGENCE_*` env vars as Option A.
3. Gate readiness on `curl -sf http://localhost:8000/status`, then run the same
   log→query→assert e2e flow as Option A.

> The exact compose/run invocation lives in the **server repo**
> (`microsoft/amplifier-context-intelligence`) — this bundle is the client side. Pin the
> server version you're testing against and record it in your run evidence.

### Read-side config the tools resolve (both options)

`graph_query` / `blob_read` resolve `(server_url, api_key)` per field via
`ToolConfigResolver`: **explicit read-config → hook `destinations` → env
`AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL` / `_API_KEY`**. Configuring the hook
`destinations` alone is enough — you don't have to repeat the endpoint for the read
tools (see the main `README.md` §"read side"). For multi-source, the connectable set is
`sources ∪ destinations`; `list_sources: true` shows it.

---

## Working-order status

- All profiles parse as **valid YAML** and reference only agents/skills/context that
  exist in this bundle.
- Host prerequisites confirmed present: `amplifier-digital-twin` 0.3.0, Incus 7.2, Docker.
- **Harness proven live:** `context-intelligence-signals-validation.yaml` was launched
  (Incus, no external deps) and passed end-to-end — readiness `all checks passed`, the
  `signals` public symbols import, and **all 12 fixtures scored OK / 0 failures**;
  instance then destroyed. This confirms the DTU pipeline (provision → install → run →
  assert) is healthy on a standard host.
- **The three mode profiles proven live** (manual run, captured — not yet CI-enforced):
  `redesigned-mode`, `contributes-migration`, and `mode-activation` were each launched
  against a **live Gitea mirror** of this branch (mirror HEAD `50a3bd5`, a working-tree
  snapshot of `docs/contributing-and-pr-template`) and each reached **`readiness: ready:True`** —
  i.e. their structural gates passed for real: `amplifier` usable; **bundle loaded via
  `amplifier bundle add` from the mirror** (not GitHub `main`); `amplifier bundle show
  context-intelligence-behavior` listed the **2 baseline agents with the 2 mode-gated
  specialists absent** while the mode is off; the installed mirror mode file declared the
  full gated surface (advertised:false, 3 context incl. `strategy.md`, 3 skills incl.
  `evaluation-methodology`, tool policies). The **behavioural off→on→off round-trip**
  (`/mode context-intelligence` → `[context-intelligence]>` → `/mode off`) was confirmed in a
  real Anthropic PTY session. All three instances were then destroyed.
  *Not independently re-logged in CI yet* — the `readiness` gates re-prove the structural
  claims on every launch; the behavioural round-trip is a documented manual `exec` step.
- **The three server-backed seams proven live** (manual run, captured — real CI server
  stacks stood up via `docker compose` from `microsoft/amplifier-context-intelligence`,
  Neo4j-backed, bundle loaded from the Gitea mirror):
  - **write to single server** — a real `amplifier` session's events reached the server:
    `/status` → `{workspace:"ci-write-single", events_processed:22}`, Cypher count **29** nodes
    tagged with that workspace.
  - **write fan-out** — one session, two `destinations` → **both** servers received identical
    events (A and B each: `events_processed:22`, Cypher count **29**); server B only ever held
    the fan-out session, proving independent delivery to both endpoints. (Observes the existing
    hook fan-out; the hook code is never modified.)
  - **execute queries** — after logging, the `graph-analyst` agent's `graph_query` returned
    **5 real rows** and `blob_read` resolved a real `ci-blob://…__raw` URI (44 KB, 8 top-level
    keys), each carrying the `source` provenance block naming the answering server
    (`{name:default, origin:destination, url:http://…:18001}`).
  Instances + server stacks then destroyed. **Honest findings from the run:** (1) the documented
  project-`settings.yaml` `overrides.hook-context-intelligence.config.destinations` path is a
  **no-op on the current amplifier-foundation build** (tools/config overrides "reserved for
  v1.1"), so the fan-out profile injects destinations directly into the loaded hook config; (2)
  `graph_query`/`blob_read` are **not** top-level tools in a plain session — the shipped read
  path is the `context-intelligence:graph-analyst` agent, which is what the query profile drives.
- **Runtime-green is per-launch**, per the AGENTS.md rule — capture the run evidence
  (real request/response, provenance, fail-loud on a down/500/timeout) when you exercise
  a seam. Start with `context-intelligence-signals-validation.yaml` (no external deps) to
  confirm the harness, then the Gitea-mirror mode profiles for mode/agent/context changes.
