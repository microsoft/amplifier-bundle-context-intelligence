# Contributing

Thanks for contributing to **amplifier-bundle-context-intelligence**. This repo has
a few specific conventions — read them before you change the furniture.

**Read [`AGENTS.md`](AGENTS.md) first.** It is the authoritative, always-loaded
guidance: validator version requirements, the full-validation command, and the
**seam-awareness** rules that govern how changes to tool/skill/config wiring must be
tested. Everything below is the short version.

## Layout

- `bundle.md` — thin root pointer. `behaviors/` — layered, composable behaviors
  (`-navigation` ⊂ `-analysis` ⊂ `-design`, orthogonal `-logging`, umbrella
  `context-intelligence`). `agents/`, `skills/`, `modes/`, `context/` — bundle content.
- `context_intelligence/` — the shared Python library. `modules/` — the installable
  modules (`tool-context-intelligence-query`, `hook-context-intelligence`,
  `tool-context-intelligence-upload`). `tests/` — top-level suite.

## Branches & commits

- Branch names: `feat/…`, `fix/…`, `docs/…`, `chore/…`.
- Conventional-commit subjects (`feat(query): …`, `fix(client): …`, `docs(skill): …`).
- Keep unrelated changes in separate commits (code / docs / deps).

## Dev setup

Uses [`uv`](https://docs.astral.sh/uv/). From a module dir or the repo root:

```bash
uv sync
```

`ruff` and `pyright` are declared as dev dependencies, so they are available in the venv.

## Testing — the gates

Unit tests are the floor, not the ceiling. Run and paste evidence for:

```bash
# module + top-level suites
uv run pytest                      # in modules/tool-context-intelligence-query
uv run pytest                      # in the repo root (tests/)
uv run ruff check . && uv run ruff format --check .
uv run pyright
```

**Real evidence on seams (this repo's signature rule).** Per `AGENTS.md`
"Seam Awareness": a mock that *fabricates the boundary's response* (e.g. a fake
server returning canned rows) is **not a gate** until it has been reconciled to real
behavior. If your change touches a seam — the client↔server boundary, blob handling,
tool/skill/config wiring — prove the **real** crossing (a live run against a real
server, or a Digital Twin run), not just a passing mock. Live testing here has caught
bugs that green mocks did not.

## Full bundle validation

Before opening a PR that touches bundle structure, run the repo's **full** validation
(not the bare recipe, which self-downgrades to `structural_only`):

```bash
scripts/validate-full.sh
```

It runs a pinned public CLI from a fresh `uv` venv with a prebuilt Core 1.6.1
wheel, `hatchling`, `pyyaml`, and `pip`, so the validator runs at
`validation_mode: full` without compiling Core. The CLI's normal Foundation
dependency is overridden in that private venv to
`f13d08168e14b5bc4720fbb06c40936eb1a7a7d1`; it is not added as a conflicting
direct requirement. The venv is removed on exit, including a newly supplied
`CI_VALIDATE_VENV` path. If zero or multiple Foundation recipes are cached,
selection fails before installation; select a readable one explicitly with
`CI_VALIDATE_RECIPE`. The wrapper disables optional LLM diagram-label
enhancement, not diagram checks.

Use Foundation's validator v3.16.1 or later for correct mode/path detection
(see `AGENTS.md`). Inspect `env_check.validation_mode`,
`build_check.build_tested`, `build_check.build_success`, and
`quality_classification.quality_level`; `final_report` must agree
with those machine results. Require full mode, a successful tested build, and
no ERROR findings. Process exit `0` alone is not PASS. No ERROR is waived; keep
the internal mode unadvertised and do not delete valid path references.

If your change altered bundle structure, regenerate the diagram and commit it
(the validator flags `BUNDLE_DOT_STALE` otherwise):

```bash
# regenerate bundle.dot / bundle.png via the generate-bundle-docs recipe
```

## End-to-end (DTU) testing — dev dependencies

Seam changes (mode / agent / skill / tool / hook / config / networking / auth) must be
proven with a **real Digital Twin Universe (DTU) run**, and DTU structural/behavioural
checks **load the bundle through the Amplifier CLI** (`amplifier bundle add` → activate
`/context-intelligence` → drive a real session) — **not** by running `pytest` inside a
container (that is a unit test in a different directory, not end-to-end).

The extra host dependencies for this — the `amplifier-digital-twin` CLI, Incus, Docker,
**Gitea** (to serve your local branch so the bundle install resolves to *your* code), and a
real LLM key for behavioural scenarios — plus exact install pointers and which profile needs
what, are documented in
[`.amplifier/digital-twin-universe/profiles/README.md`](.amplifier/digital-twin-universe/profiles/README.md).
Add them once before running the mode/seam profiles.

## Pull requests

Open PRs against `main` and **populate every item in the PR template** from real
evidence — paste it, or mark `N/A — <reason>`. Never pre-check a box you cannot back.
State what you deliberately did **not** touch (e.g. the write-side hook fan-out for a
read-side change).

## Capturing lessons

If your work surfaces a lasting lesson — a footgun, an invariant, a new gate — write it
back into the file that owns it (`AGENTS.md` for pitfalls/commands, the PR template for
a new gate) as it lands. Offer the entry and get agreement; keep what's worth keeping.
