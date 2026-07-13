<!--
Populate every item from REAL evidence. For each: paste the evidence, or mark
`N/A — <reason>`. Never pre-check a box you can't back — an unchecked box reads
as "I forgot"; a checked box you can't back reads as "passed" when it didn't.
Read AGENTS.md before you start — it carries this repo's load-bearing rules.
-->

## Summary

<!-- What changed and WHY. One paragraph. Link the issue/PR this builds on, if any. -->

## Scope / guardrails

<!-- What you deliberately did NOT touch, and why. If your change is read-side, say
the write-side hook fan-out (fanout.py / _DestinationDispatcher / logging_handler)
is untouched. If you crossed a documented seam (see AGENTS.md "Seam Awareness"),
say which and how you proved the real crossing. -->

## Verification

- [ ] **Module tests** pass — `modules/tool-context-intelligence-query` (paste count)
- [ ] **Top-level tests** pass — `tests/` (paste count)
- [ ] **`ruff check` + `ruff format --check`** clean
- [ ] **`pyright`** clean (0 errors)
- [ ] **Full bundle validation** PASS — `scripts/validate-full.sh` → `validation_mode: full`
      (the lone mode-advertising ERROR is a documented FALSE POSITIVE — see AGENTS.md — do NOT "fix" it)

## Real evidence on seams (not mock-only)

<!-- Per AGENTS.md "Seam Awareness": an inbound fake sitting on the boundary it
claims to verify is NOT a gate until reconciled to real behavior. If this change
touches a seam (tool/skill/config wiring, the client↔server boundary, blob
handling), paste evidence of the REAL crossing — a live run / real HTTP server /
DTU run — not just a passing mock. -->

- [ ] Seam(s) crossed by this change are proven against real behavior (or `N/A — no seam crossed`)

## Docs & diagrams

- [ ] `bundle.dot` / `bundle.png` regenerated if bundle structure changed (the validator flags `BUNDLE_DOT_STALE`)
- [ ] README / SKILLs / agent files updated if a tool/skill contract changed
- [ ] Convention files updated if this surfaced a lasting lesson (`AGENTS.md`, this template, etc.)

## Notes / follow-ups

<!-- Non-blocking follow-ups, deferred items, coordination needed. -->
