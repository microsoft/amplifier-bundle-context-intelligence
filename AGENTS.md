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
`context-intelligence`. The telemetry hook is **pure telemetry** (it does not fetch skills); skill
acquisition lives on `tool-context-intelligence-query` behind the `skill_sync_enabled` knob
(**default `false`** — opt-in). See `docs/context-intelligence-skill-sync-flow.dot` and the README.
