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
| `context-intelligence-upload-format-validation.yaml` | **Legacy hooks-logging IMPORT** — `--format logging-hook` ingests a shipped neutral synthetic legacy fixture; proves discrimination, runtime slug parity/no fork (graph workspaces exactly equal the runtime-derived set), coexistence/dedupe/idempotency (node count captured at runtime does not grow); self-contained, no host data, no pinned counts | Incus + Gitea mirror + **CI backend** (`context-intelligence-backend.yaml` launched fresh) — no LLM | `... launch .../context-intelligence-upload-format-validation.yaml --var gitea_host=... --var server_url=http://<gateway-ip>:38000 --var server_token=...` |
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

### The canonical non-compose backend

This bundle ships its own self-contained backend recipe —
[`context-intelligence-backend.yaml`](context-intelligence-backend.yaml) — that stands
up, inside one Incus container: **Neo4j Community 5.26.22 (5.26 LTS)** run directly via
`docker run` (APOC Core bundled in the image, no network fetch; GDS intentionally
omitted), plus the **standalone `context-intelligence-server`** (`WEB_CONCURRENCY=1`)
pointed at it over `bolt://localhost:7687`. **The server repo's docker-compose stack is
retired as a DTU dependency** (its compose files are being removed from
`microsoft/amplifier-context-intelligence` itself) — every server-backed profile below
launches this backend profile first and points at its forwarded port.

1. **Launch the backend:**
   ```bash
   amplifier-digital-twin launch \
     .amplifier/digital-twin-universe/profiles/context-intelligence-backend.yaml \
     --name ci-backend \
     --var NEO4J_PASSWORD=<strong-pw> --var API_KEY=<token> \
     --var HOST_PORT=38000 \
     --var SERVER_REF=766a9691850e6d7c29e7d4e90b537e88e69736bf
   ```
2. **Confirm it's reachable** from the Incus host-gateway IP (find it via `incus list`
   for the container's address, or `ip route | grep default` inside a consuming DTU):
   ```bash
   curl -sf http://<incus-gateway-ip>:38000/status
   ```
3. **Point the consuming profile at it** via its own `--var server_url=...` /
   `--var server_a_url=... --var server_b_url=...` (see each profile's header comment
   for the exact flags). For fan-out, launch the backend profile **twice** on distinct
   `HOST_PORT`s (`38001`, `38002`) — two fully separate Incus containers, so events
   never cross.

> **Container DNS/networking:** if the client can't reach the backend from inside the
> container, see `docs/container-dns-troubleshooting.md` (the gateway-IP + port pattern).

`example-dtu-external-server.yaml` is the reference "point a client at an already-running
server" profile — it doesn't stand up a server itself; use the backend profile above to
get one.

> The exact non-compose bring-up (Neo4j image, APOC, server invocation) lives in
> `context-intelligence-backend.yaml`'s header comment, cited against the **server repo**
> (`microsoft/amplifier-context-intelligence`). Pin the server version you're testing
> against (via `--var SERVER_REF`) and record it in your run evidence.

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
- **SUPERSEDED — the three server-backed seams' prior evidence** (captured against the
  now-**retired** docker-compose backend path; kept here for historical record only, not
  as current proof): real CI server stacks were stood up via `docker compose` from
  `microsoft/amplifier-context-intelligence`, Neo4j-backed, bundle loaded from the Gitea
  mirror:
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
  Instances + server stacks then destroyed. These two write profiles configure destinations via
  the **documented user path only** — `~/.amplifier/settings.yaml`
  `overrides.hook-context-intelligence.config.destinations` as a **named map** (keyed by
  destination name), each `api_key` a `${VAR}` from `~/.amplifier/keys.env` (see the main
  `README.md` §"Server forwarding — `destinations`"). Re-verified live through that path (no
  direct-config-injection workaround): **single-server** → server A received the session
  (`events_processed:22`), server B correctly received nothing; **fan-out** → one session, two
  named destinations → **both** servers independently received it (each `events_processed:22`,
  same workspace). **Notes:** (1) the earlier "no-op" was a mis-test — it used a *project-scope*
  `.amplifier/settings.yaml` (which routes through the foundation configurator overlay, not yet
  applied for hook/tool `config`) **and** a bare-*list* shape; the **user-level**
  `~/.amplifier/settings.yaml` named-map is the implemented, working path (app-cli
  `get_config_overrides`). No framework limitation — the support issue was closed as invalid.
  (2) `graph_query`/`blob_read` are **not** top-level tools in a plain session — the shipped read
  path is the `context-intelligence:graph-analyst` agent, which the query profile drives.
- **PENDING — fresh non-compose evidence.** The three server-backed seams above must be
  re-run against `context-intelligence-backend.yaml` (the current, non-compose backend)
  before their proof is current again. No results are recorded here yet — a follow-up DTU
  run will fill this in; do not treat the SUPERSEDED bullet above as still-valid proof.
- **Proven live — portable, no pinned values.** `context-intelligence-upload-format-validation.yaml`
  (Legacy hooks-logging IMPORT) was launched against a **fresh** `context-intelligence-backend.yaml`
  instance (Neo4j + standalone `context-intelligence-server`, non-compose) and reached
  `check-readiness` → `ready: true`, with the backend's own `/status` confirming
  `neo4j_connected: true`. The upload CLI was installed from the **branch under test**, resolved
  through a **Gitea mirror** (git `insteadOf` + `UV_NO_GITHUB_FAST_PATH=true`) — confirmed by an
  `exec` smoke (`BRANCH-CLI-OK`: `--help` advertises `--format logging-hook`, which only exists on
  the branch). The consumer profile's `readiness` computed `EXPECTED_SESSIONS` and the expected
  workspace set **at runtime** from the shipped fixture (never a pinned literal) and asserted, all
  live against the graph: **discrimination** (native-format ingest reads only the generated
  `context-intelligence/` twin, never the legacy `events.jsonl`), **coexistence** (the paired legacy
  twin converges into the SAME workspaces with **no growth** in the runtime-captured node count),
  **idempotency** (re-ingesting the same legacy fixture produces **no additional growth**),
  **no-fork/slug parity** (the graph's distinct-workspace set exactly equals the runtime-derived
  expected set, and per-workspace counts sum to the grand total), **session count** (`count_label
  Session` equals the shipped session count), and **`data.timestamp` presence** on a read-back
  event. All invariants held — this is a self-referential proof; no number was measured then
  pinned anywhere in the profile, the support scripts, or this entry. Both DTU instances (backend +
  consumer) were destroyed immediately after; `amplifier-digital-twin list` confirmed neither
  remained. The host's production server on `:8000` was never targeted (the consumer profile only
  ever pointed at the fresh backend's forwarded port via the Incus host-gateway IP), and the host's
  `~/.amplifier/settings.yaml` was untouched. Machine-agnostic by construction: re-running this on
  any host reproduces the same PASS/FAIL verdict from the same shipped fixture, regardless of that
  host's own path, hostname, or prior graph state (given a genuinely fresh backend).
- **Runtime-green is per-launch**, per the AGENTS.md rule — capture the run evidence
  (real request/response, provenance, fail-loud on a down/500/timeout) when you exercise
  a seam. Start with `context-intelligence-signals-validation.yaml` (no external deps) to
  confirm the harness, then the Gitea-mirror mode profiles for mode/agent/context changes.
