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

## Architecture note

This bundle ships **layered, composable behaviours** — `context-intelligence-navigation` ⊂
`-analysis` ⊂ `-design`, plus an orthogonal `-logging` (the telemetry hook only) and the umbrella
`context-intelligence`. The telemetry hook is **pure telemetry** (it does not load skills). The
`context-intelligence-graph-query` skill is **vendored statically** at
`skills/context-intelligence-graph-query/SKILL.md` (sourced from the server repo's `main`) and
carries its own leading no-server guidance block — there is **no runtime skill fetching, syncing,
or configuration knob**. See the README.

## Seam Awareness

This bundle has **seams** — integration boundaries where one module's wiring touches the rest of
the bundle (kernel lifecycle, config resolution, the skill↔server bridge, the served-skill↔loader
delivery path). These seams have **regressed before** (e.g. issue #283), which is why they are
documented rather than left implicit.

**Before changing any tool / skill / config wiring, read [`docs/SEAM-INVENTORY.md`](docs/SEAM-INVENTORY.md).**
It names each seam individually, what crosses it, and how to verify it.

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
