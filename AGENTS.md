# AGENTS.md — amplifier-bundle-context-intelligence

Guidance for AI agents and developers working in **this** bundle repository.

## Known validator false positive — do NOT "fix" it

`validate-bundle-repo` (v3.6.0) reports a mode-advertising **ERROR**:

> `unadvertised_but_referenced`: mode `context-intelligence` (`modes/context-intelligence.md`,
> `advertised: false`) is referenced by name in `context/safe-extraction-patterns.md`
> and `context/agents/session-storage-knowledge.md`.

**This is a FALSE POSITIVE. Do not act on it.** The flagged occurrences are **not** mode
invocations — they are:

- **disk paths** — `~/.amplifier/projects/{slug}/sessions/{id}/context-intelligence/`
  (the CI storage subdirectory; the `/` before the name is a path separator, not a slash-command),
- **`@mention` prefixes** — `@context-intelligence:context/...`, and
- **skill names** — `context-intelligence-graph-query`, `context-intelligence-session-navigation`.

The bundle, its on-disk storage subdirectory, its skills, **and** the internal design mode all
share the name `context-intelligence`. The validator's `/<mode>` + `name="<mode>"` regex cannot
disambiguate them. The **full-mode** validator (see below) re-reads the source files and itself
**confirms this as a false positive — overall verdict PASS**.

**Therefore:** leave `modes/context-intelligence.md` at `advertised: false` (the mode is correctly
internal), and do **not** remove the path/skill references. The only proper fix, if any, is an
upstream tightening of the validator regex — never a change to this repo.

## Running the bundle validator in FULL mode

The validator runs its Python checks through a bash `python3` heredoc. In a default Amplifier
environment that `python3` lacks `amplifier_foundation` / `hatchling`, so the recipe self-downgrades
to `validation_mode: structural_only` — skipping BundleRegistry resolution of the layered includes
and the package build checks. To run **full** validation:

```bash
scripts/validate-full.sh           # validates this repo
scripts/validate-full.sh <path>    # or another bundle repo
```

It builds a throwaway `uv` venv with `hatchling` + `amplifier-foundation` + `amplifier-core` +
`pyyaml`, puts it first on `PATH`, and runs `validate-bundle-repo` so its `python3` resolves to an
interpreter that has the deps → `validation_mode: full`.

**Last full run: ✅ PASS** — 10/10 bundles clean, all hygiene/structure/placement/freshness gates
green, the lone mode "error" confirmed a false positive (name collision). Only the build *dry-run*
is skipped (no `pip wheel` in the venv); the wheels build cleanly under `uv build`.

## Testing & what "done" looks like

Run these before calling anything done:

```bash
uv run pytest          # in modules/tool-context-intelligence-query   (module suite)
uv run pytest          # in the repo root                             (tests/, top-level suite)
uv run ruff check . && uv run ruff format --check . && uv run pyright
scripts/validate-full.sh   # → validation_mode: full, overall PASS
```

**Green unit tests are the FLOOR, not proof of done.** This bundle wires **skills, modes,
networking, tools, and auth** — capabilities whose real behaviour lives at **seams** (see
*Seam Awareness* below), where a passing mock can hide a real break. Real, recent proof: two
read-path bugs (a blob-envelope parse and a `ci-blob://` bare-key normalization) passed *every*
unit test and were caught only against a **live server**.

So **"done" for any change that touches a seam is not "units are green" — it is real,
captured end-to-end evidence that the bundle works in production order.** A seam is any edit at
the edges: skill / mode / tool / hook / config wiring, the client↔server boundary, networking,
or auth. For those changes, done **requires**:

- **A real end-to-end run — Digital Twin Universe (DTU) or an equivalent live run** — exercising
  the ACTUAL code path a user hits: real server, real network, real auth. Not an inbound mock of
  the boundary (which is *banned as a gate* until reconciled to real behaviour — see *Seam Awareness*).
- **Captured evidence:** the real request/response, the identity/provenance of what actually
  answered, and the **fail-loud** behaviour on a real failure (down / 5xx / timeout / bad auth).
- If you crossed a seam without real evidence, it is **NOT done** — reconcile the mock to the real
  thing first.

**Agent, skill, and mode edits ALWAYS require a DTU run + the evaluation harness — never ship them
on unit tests alone.** An agent's delegation, a skill's guidance, a mode's gating, and the
tool/networking/auth wiring behind them only prove out when *loaded and exercised in a real
environment*. This repo ships the harnesses for exactly that — use them, don't reinvent them:

- **DTU profiles** — `.amplifier/digital-twin-universe/profiles/` (e.g. `context-intelligence-bundle-smoke-test.yaml`,
  `context-intelligence-redesigned-mode-validation.yaml`, `context-intelligence-contributes-migration-validation.yaml`,
  `context-intelligence-mode-activation-validation.yaml`, `context-intelligence-signals-validation.yaml`,
  `context-intelligence-upload-format-validation.yaml`). Launch the
  change in a DTU and drive the real agent/skill/mode/tool path end-to-end.
- **DTU backends are provisioned non-compose**: Neo4j Community 5.26.22 LTS via
  `docker run` (APOC Core bundled, GDS omitted) + the standalone
  `context-intelligence-server` over `bolt://localhost:7687` (`WEB_CONCURRENCY=1`).
  The server repo's docker-compose path is retired as a DTU dependency — every
  server-backed profile launches `context-intelligence-backend.yaml` first and
  points at its forwarded port. See that profile's header comment for the recipe.
- **Evaluation methodology** — the `context-intelligence-eval-design` and
  `context-intelligence-evaluation-methodology` skills. Design/run the evaluation scenarios that
  score the behaviour, and capture the results as the evidence of working order.

Match the gate to the change: pure-internal logic → units + `validate-full.sh`; **a seam crossing —
including ANY agent / skill / mode / tool / networking / auth edit → units + `validate-full.sh` + a
real DTU run + the evaluation harness, with captured evidence.** Skipping the live run on a seam
change is how a green build ships a broken bundle.

## Authoring data-query & navigation skills — evidence-based, against a live graph

The data-query and navigation skills (`context-intelligence-graph-query`, `-derived-metrics`,
`-gds`, `-session-navigation`, `-session-reconstruction`, and any skill that ships Cypher or
graph/file navigation steps) encode queries whose **correctness only exists relative to REAL
data**. A query that *parses* is not a query that is *correct* — it can 500 on a type it didn't
expect, silently over-count by reading echoed events, or read a corrupted/renamed property and
return a confident wrong number. None of that is visible without running it against real data.

So authoring these skills is **evidence-based, live-first — never from memory or the schema doc**:

- **Validate every query/step against a LIVE graph (or real files) as you author it**, not only
  at the "done" gate. Run it against a real server's `/cypher` (a DTU-backed server for isolation)
  — or, for file-navigation skills, against real session `events.jsonl` — on real data.
- **Cross-check the result against ground truth**, not just "it ran": a second independent
  computation (e.g. aggregate vs an independent reduce), a hand-verified subset, and a **negative
  control** that proves the discipline is load-bearing (e.g. the bare `sum()` that returns HTTP
  500 without `toFloat()`). "Returned 200" is not evidence of correctness.
- **Work in an evaluation loop:** author → run against live data → check vs ground truth → correct
  → re-run, until the query is proven. For skills whose value is a *behaviour* (an agent following
  the guidance), score the behaviour with the evaluation harness (`context-intelligence-eval-design`
  / `-evaluation-methodology`) on a DTU — not the query in isolation.
- **Never author without validating the skill's impact on ACTUAL behaviour.** A guidance or query
  edit that has not been shown to change what runs — *correctly* — is **not done**. Capture the
  before/after: the wrong query over-counts / 500s; the shipped one returns the verified figure.

The bar, from this repo's own history: the cost/token aggregation patterns were proven three ways
(aggregate == independent reduce == hand-summed subset) on a live graph, **and** re-run on a DTU
with a negative control, **before** a single line was written into the skill. Ship queries you have
watched return the right answer on real data — nothing less.

## Architecture note

This bundle ships **layered, composable behaviours** — `context-intelligence-local` ⊂
`-analysis` ⊂ `-design`, plus an orthogonal `-logging` (the telemetry hook only) and the umbrella
`context-intelligence`. The telemetry hook is **pure telemetry** (it does not load skills). The
`context-intelligence-graph-query` skill is **vendored statically** at
`skills/context-intelligence-graph-query/SKILL.md` (sourced from the server repo's `main`) and
carries its own leading no-server guidance block — there is **no runtime skill fetching, syncing,
or configuration knob**. See the README.

## Seam Awareness

This bundle has **seams** — integration boundaries where one module's wiring touches the rest of
the bundle (kernel lifecycle, config resolution, the skill↔server bridge, the served-skill↔loader
delivery path). These seams have **regressed before** (e.g. issue #283), so treat any change to
tool / skill / config wiring as crossing one.

Two rules govern how you treat a seam:

- **Know PERSIST vs ELIMINATED.** A **PERSIST** seam is part of the bundle's real function — you
  must **cross it and test the real crossing** (not a stand-in). An **ELIMINATED** seam existed
  only because of removed machinery (the old `skill_sync`) — it must **stay gone**; its removal was
  proven **once** at cutover, and the standing guard against reintroduction is the residue grep,
  **not** a permanent test asserting a deleted feature stays deleted (that is testing a ghost).
- **Never trust a mock on a seam until it's reconciled to the real thing.** A double that records
  what our code *calls* (outbound spy) is fine. A double that *fabricates the boundary's response*
  (inbound fake — e.g. a mock server returning canned rows) is **not a gate** until it has been
  compared against real behaviour and kept in sync. An unreconciled mock sitting on the very
  boundary it claims to verify is banned.
