# Seam Inventory

Reference for the **seams** in this bundle — the integration boundaries where one
module's wiring touches the rest of the bundle. Prior changes of this shape have
**regressed at the seams** (issue #283 among them), so the boundaries are documented
here for the next agent who edits tool / skill / config wiring.

Read this **before** changing any tool, skill, or config wiring. Also see the
**Seam Awareness** section of [`../AGENTS.md`](../AGENTS.md).

## What a "seam" is here

A seam is a boundary the removed or current machinery crosses to reach the rest of
the bundle: the kernel lifecycle, config resolution, the skill↔server bridge, and the
served-skill↔loader delivery path. Each seam either carries real bundle function or is
a leftover of removed machinery — the classification below is load-bearing.

## PERSIST vs ELIMINATED

Every seam is one of two kinds:

- **PERSIST** — the seam is part of the bundle's real function. It stays, and you must
  **cross it correctly**. It earns a **real crossing test** — one that exercises the
  actual boundary (a live server, the real on-disk file, the real loader), not a
  stand-in for the thing being asserted.
- **ELIMINATED** — the seam existed *only because of* the removed `skill_sync`
  machinery. After deletion it must be **GONE**, not tested-and-kept. A still-there
  eliminated seam (a lingering hook, capability, config path, or import) is residual
  machinery = a defect.

## Cutover check vs standing test (the test-lifetime rule)

A test that proves an ELIMINATED seam is *gone* is a **cutover check**: it *defines
DONE* for the removal, then **retires**. Keeping a permanent test that asserts a
deleted feature stays deleted is **testing a ghost** — the exact grown-back overhead
the removal set out to shed. For ELIMINATED seams the **standing** guard against
reintroduction is the cheap **residue grep**, not a bespoke forever-test. Only
**PERSIST seams and live core dependencies** earn a standing test.

## Mock-reconciliation rule

Mocks are fine — but **a mock is not a gate until it has been reconciled to the real
thing**. A double that records *what our code calls* (an **outbound spy** — e.g. "did
`mount()` register these two tools and make no `register_capability` call?") is
legitimate: it observes our behaviour, not the boundary's. A double that *fabricates
the boundary's response* (an **inbound fake** — e.g. a mock server returning canned
rows so a query "passes" with no server) is **banned as a gate** until it has been
compared against real behaviour, updated to match, and kept in sync. An unreconciled
mock sitting on the very boundary it claims to verify is not evidence. The enforcement
trigger that keeps inbound mocks honest is the periodic drift backstop (a live-server
run), not a code comment.

---

## Seam 1 — Kernel ↔ module lifecycle (`on_session_ready` + orphaned capability)

- **Kind:** **ELIMINATED**
- **What crosses it:** after all modules mount, the kernel looks up an optional
  module-level `on_session_ready` callback by attribute name and calls it. The removed
  `skill_sync` was its only user; the `_GRAPH_QUERY_TOOL_CAPABILITY` registration in
  `mount()` existed only to feed that callback.
- **Absence test (cutover):** call `mount(coordinator, {})` against a coordinator
  double and assert (a) both tools are registered by name (`graph_query`, `blob_read`);
  (b) `getattr(module, "on_session_ready", None) is None`; (c) the coordinator recorded
  **no** `register_capability("context_intelligence._graph_query_tool", …)` call. All
  three flip with the change, so the test cannot pass against pre-change code. The
  **standing** part is only the live-core-dependency check that the module still
  registers `graph_query`/`blob_read` (`tests/test_module.py`) — the "no
  `on_session_ready` / no capability" halves are a cutover proof, not a forever-test.
- **Mock reconciliation:** the coordinator double here is an **outbound spy** (it
  records what `mount()` calls, pinned to the kernel contract) — allowed. It does not
  fabricate a boundary response.

## Seam 2 — Config ↔ resolver (`skill_sync_enabled`)

- **Kind:** **ELIMINATED** (silent removal accepted and recorded)
- **What crosses it:** user/behavior/env config flowed raw into `ToolConfigResolver`;
  the `skill_sync_enabled` key was read there and passed through to the graph-query
  tool. Config has no closed schema, so an unknown top-level key is now read by nobody
  and rejected by nobody — setting `skill_sync_enabled` today is silently inert.
- **Absence test (cutover):** `hasattr(ToolConfigResolver(...), "skill_sync_enabled")`
  is **False** and `hasattr(GraphQueryTool(...), "skill_sync_enabled")` is **False**
  (both were True before removal → the assertion fails against pre-change code, passes
  after). A secondary inertness check confirms `mount()` with a stray
  `skill_sync_enabled: true` still completes without raising. **No standing test** —
  the residue grep is the standing guard against reintroduction. (The retired flag was
  opt-in and defaulted False, so silent removal is fine; note it in the PR/CHANGELOG.)
- **Mock reconciliation:** n/a — this is a pure config-attribute absence check, no
  boundary double involved.

## Seam 3 — Graph-query tool ↔ live server (schema / pattern truth)

- **Kind:** **PERSIST**
- **What crosses it:** `graph_query` executes Cypher against the real server via
  `AsyncCIClient`. The **vendored skill's schema claims and example patterns** are only
  true if they match the live graph. This is the bridge the vendored skill is *about*.
- **Real crossing test:** the §2.5 evaluation harness (a committed, endpoint-configurable
  dev/eval tool) run against a **live** server — introspect the live schema and diff it
  against what the skill documents, and execute the skill's example queries to confirm
  they return the claimed shape. This is dev inner-loop hygiene, **not** a CI gate
  (it needs server credentials, is expensive, and would go falsely red on unrelated
  PRs or when the server is simply down).
- **Mock reconciliation:** the existing `AsyncCIClient` mock in the tool's unit tests is
  an **inbound fake** sitting directly on this seam — it proves nothing about real
  schema truth and is **not a gate** for accuracy. Reconciliation is the harness run
  above; without a periodic live run, "the mock is in sync" is a claim, not evidence.

## Seam 4 — Query-module tests ↔ hook `build_payload`

- **Kind:** **ELIMINATED**
- **What crosses it:** the deleted `TestBuildPayloadCouplingGuard` reached from the
  query module's tests into `amplifier_module_hook_context_intelligence.upload.build_payload`
  — a tripwire from a past refactor. It was conditionally skipped whenever the hook
  module wasn't importable, so in the query venv it usually **skipped** (unverified, not
  passing). It was deleted as a rider when its test file went.
- **Guard that stays (do not duplicate):** the real cross-module production consumer of
  `build_payload` is `uploader.py`, which calls it live in `run_upload()`. That path is
  tested **unmocked, no skip** in
  `modules/hook-context-intelligence/tests/test_uploader.py` — **that** is the standing
  guard. The seam's own removal needs no new test; cite `test_uploader.py` and confirm
  it stays.
- **Mock reconciliation:** n/a — the surviving guard exercises the real call path
  unmocked.

## Seam 5 — Served `SKILL.md` ↔ `load_skill` delivery

- **Kind:** **PERSIST** (highest risk — was silent)
- **What crosses it:** behaviors declare the skill by git subdirectory; `tool-skills`
  fetches the directory at compose time; `load_skill("context-intelligence-graph-query")`
  returns **whatever `SKILL.md` is on disk**. Historically that on-disk file was a
  "Server Unavailable" stub and `skill_sync` overwrote it at runtime — removing
  `skill_sync` without making the served file the real body would silently load the stub
  in every session (issue #283). The served file is now the real body **with a leading
  no-server block**.
- **Real crossing test — layer 1 (cheap, always-on `pytest`):** read
  `skills/context-intelligence-graph-query/SKILL.md` **from disk** and assert three
  things, each pinned so it can actually fail:
  1. a positive marker (exact literal) for **each** of the five must-teach categories —
     (i) levers (`created_by` / `working_dir` / `work_space`), (ii) blob handling
     (`.data` JSON / `ci-blob://`), (iii) pagination / progressive discovery, (iv) the
     silent-wrong-result gotchas incl. the **ZONED DATETIME** trap, (v) the no-server
     block;
  2. **stub absent** — `"Server Unavailable"` not in the body;
  3. **position** — the no-server delegation block appears **before** the real-body
     content, so a future edit can't bury the safe default deep in the file and still
     pass.
- **Real crossing test — layer 2 (E2E delivery proof):** a DTU run provisioned from the
  branch/SHA under test (not `@main`) that installs the query module, runs a real
  session which `load_skill`s the skill, and asserts the returned body contains the same
  pinned marker **and** the no-server block — a positive assertion, never absence-only.
  This proves *delivery*, which the disk read alone cannot. Dev/Step-2 hygiene, recorded,
  not a CI gate (server-availability / cost / false-red reasons).
- **Mock reconciliation:** layer 1 reads the **real** on-disk artifact (no mock); layer 2
  crosses the **real** compose-time fetch + loader path. Neither substitutes a fabricated
  response for the boundary.
