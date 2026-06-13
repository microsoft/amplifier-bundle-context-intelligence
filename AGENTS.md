# AGENTS.md — amplifier-bundle-context-intelligence

## What this repo is

Composable behaviors for session observability + context intelligence, organized as a **layered
"onion"** plus an independent telemetry hook. Each layer adds exactly one capability and
`includes:` the layer beneath it:

- **`context-intelligence-navigation`** — innermost; `session-navigator` agent + local-JSONL nav skill.
- **`context-intelligence-analysis`** — includes navigation; adds `graph-analyst` + graph skills.
- **`context-intelligence-design`** — includes analysis; adds the `/context-intelligence` design **mode**
  (registered via `hooks-mode` `search_paths: ["@context-intelligence:modes"]`; mode is `advertised: false`, activated on demand).
- **`context-intelligence-logging`** — independent; the `hook-context-intelligence` telemetry hook only.
- **`context-intelligence`** (full umbrella) — composes `design` + `logging`.

Ships as a bundle with behaviors in `behaviors/`.

---

## Key directories

| Path | Contents |
|------|----------|
| `behaviors/` | Layered behavior YAMLs: `-navigation` → `-analysis` → `-design` (nested onion) + independent `-logging` + the full `context-intelligence` umbrella |
| `modules/tool-graph-query/` | Graph query tool + `SkillFetcher` + `skill_sync` (`on_session_ready`) — owns dynamic skill-content sync from the server |
| `modules/tool-blob-read/` | `blob_read` tool for resolving `ci-blob://` URIs |
| `modules/hook-context-intelligence/` | Telemetry hook (logging behavior) — **pure telemetry**, no skill sync code |
| `context_intelligence/` | Shared library (config + `HookConfigResolver` + `ToolConfigResolver`) used by all three modules |
| `agents/` | `graph-analyst` + `session-navigator` agent definitions |
| `docs/` | Product docs + diagrams (`bundle.dot` and `bundle.png` are at the **repo root**, not here) |
| `.amplifier/digital-twin-universe/profiles/` | DTU profiles for end-to-end behavioral testing |

---

## Setup

Each module is an independent `uv` package — set up separately:

```bash
cd modules/tool-graph-query           && uv sync
cd modules/tool-blob-read             && uv sync
cd modules/hook-context-intelligence  && uv sync
```

---

## Test commands

Run before claiming done — reviewer expects evidence:

```bash
cd modules/tool-graph-query           && PYTHONPATH="$(git rev-parse --show-toplevel)" uv run pytest -q   # 87 tests
cd modules/tool-blob-read             && PYTHONPATH="$(git rev-parse --show-toplevel)" uv run pytest -q   # 35 tests
cd modules/hook-context-intelligence  && PYTHONPATH="$(git rev-parse --show-toplevel)" uv run pytest -q   # 295 tests
```

Lint + types (run from each module directory):

```bash
uv run ruff check . && uv run ruff format --check . && uv run pyright
```

---

## DTU end-to-end tests (REQUIRED for server/sync changes)

Changes touching `skill_sync.py`, `SkillFetcher`, `on_session_ready`, or `ToolConfigResolver`
**must** be validated against a live context-intelligence server via all four DTU scenarios:

| Scenario | What it covers |
|----------|----------------|
| **S1** | Analysis-layer sync — skill fetched and discoverable after `on_session_ready` |
| **S2** | Offline-drift invalidation — ETag/hash sidecars removed when body drifts; content retained |
| **S3** | Logging-only — zero skill activity (hook has no sync code) |
| **S4** | Full behavior — telemetry hook + analysis-layer sync both active |

DTU profiles live in `.amplifier/digital-twin-universe/profiles/` (including
`context-intelligence-analyst-behavioral-test`, `context-intelligence-logging-behavioral-test`, `context-intelligence-hook-behavioral-test`,
`example-dtu-external-server`). Load the `digital-twin-universe` skill or use the
`amplifier-tester` bundle to run them.

> **Mandatory:** the DTU mirrors your **local branch** to Gitea (`url_rewrite` in the profile)
> so it runs your uncommitted code — not a published version. Confirm `url_rewrite` is set
> before trusting DTU results.

---

## Verification gradient

| Change area | Required verification |
|-------------|----------------------|
| `skill_sync` / `SkillFetcher` / `on_session_ready` | Unit tests + all 4 DTU scenarios |
| `ToolConfigResolver` / config resolution | Unit tests + placeholder-expansion regression tests |
| `tool-graph-query` / `tool-blob-read` tool logic | Unit tests |
| `hook-context-intelligence` | Unit tests |
| Bundle YAML / behaviors / agents | Regenerate `bundle.dot` via the `generate-bundle-docs` recipe |

---

## Common pitfalls

Each of these burned real debugging time:

- **Shared lib is a `@main` git self-reference, not a path source** — each module's `pyproject`
  declares `amplifier-bundle-context-intelligence @ git+...@main` (with `[tool.hatch.metadata]`
  `allow-direct-references = true`); there is no `[tool.uv.sources]` `path = "../.."` override.
  This makes modules install identically in the monorepo and standalone (PR #36's intent). For
  LOCAL unit runs, the installed copy is the git `@main` snapshot, so tests import the LOCAL shared
  library by shadowing it with the repo root on `PYTHONPATH`:
  `PYTHONPATH="$(git rev-parse --show-toplevel)" uv run pytest -q`. Do NOT reintroduce a
  `[tool.uv.sources]` `path = "../.."` override to fix imports.

- **`skills find()` returns `None` — SKILL.md must start with `---`** — tool-skills' catalog
  builder silently drops any `SKILL.md` lacking a leading `---` YAML frontmatter delimiter.
  When drifting skill content in a test, change the **body only** and keep the `---` header.
  Frontmatter-destroying drift makes the skill vanish from discovery before sync runs — that's a
  test-methodology bug, not a product bug.

- **`amplifier-core` is the PyPI wheel (>=1.6.0), NOT a git/Rust source build** — all three
  modules pin `amplifier-core>=1.6.0` from PyPI (prebuilt wheel). Do not switch to a git source
  or downgrade to v1.2.x — that forces a maturin/Rust build that hangs the test run.

- **Analysis-layer config placeholders** — `${AMPLIFIER_CONTEXT_INTELLIGENCE_*}` placeholders in
  behavior config are expanded by `ToolConfigResolver._expand`. In the analysis layer without the
  hook (e.g. `context-intelligence-analysis`/`-design` composed without `-logging`),
  the tool resolver — not the hook resolver — supplies `server_url`/`api_key`. If placeholder
  expansion produces raw `${...}` strings at runtime, check whether you edited the tool resolver
  path, not just the hook resolver.

---

## Done means

- Module unit tests green (87 + 35 + 295).
- For sync/server changes: all 4 DTU scenarios pass.
- `bundle.dot` regenerated (via `generate-bundle-docs` recipe) if bundle structure changed.
- `.github/PULL_REQUEST_TEMPLATE.md` (if present) is honored.
