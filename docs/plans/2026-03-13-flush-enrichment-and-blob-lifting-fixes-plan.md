# Flush Enrichment and Blob Lifting Fixes — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Fix 3 data-quality bugs in the Neo4j graph pipeline: orphan Session nodes from empty-label upserts, incorrect token counts from Python `or` truthiness, and missing `finish_reason` because blob offloading destroys `raw` before handlers run.

**Architecture:** Three targeted fixes in the event processing pipeline — (1) neo4j_store flush distinguishes creation (MERGE with labels) from enrichment (MATCH by node_id), (2) blob_processor lifts `raw.usage`, `raw.stop_reason`, `raw.finish_reason` into the clone before offloading, (3) StepHandler uses explicit `is None` checks, prefers provider token keys, stores `message_count` separately from `input_tokens`.

**Tech Stack:** Python 3.11+, Neo4j async driver, pytest with pytest-asyncio (auto mode)

**Design doc:** `docs/plans/2026-03-13-flush-enrichment-and-blob-lifting-fixes-design.md`

---

## Conventions

All paths are relative to the submodule root:
```
/home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence/
```

Source code lives in:
```
modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/
```

Tests live in:
```
modules/hook-context-intelligence/tests/
```

Run tests with:
```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

Run a specific test with:
```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestFlush::test_name -v
```

pytest is configured with `asyncio_mode = "auto"` — async test methods do NOT need `@pytest.mark.asyncio` in most classes. However, `test_neo4j_store.py` uses explicit `@pytest.mark.asyncio` decorators on its test methods — **follow this pattern when adding tests to that file**.

Neo4j tests require a live test container at `neo4j://localhost:7690` with `NEO4J_AUTH=none`. The `neo4j_store` fixture in `test_neo4j_store.py` handles connection and cleanup.

Handler tests use the `services` fixture from `tests/conftest.py` which provides `HookStateService` with an in-memory `GraphState`. No Neo4j container needed.

---

## Task 1: MATCH for Empty-Label Upserts in neo4j_store.py

Tasks 1 and 2 are independent — they can be done in any order. Task 3 depends on Task 2.

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/neo4j_store.py`
- Modify: `modules/hook-context-intelligence/tests/test_neo4j_store.py`

### Step 1: Write the failing tests

Add two new test methods to the existing `TestFlush` class in `tests/test_neo4j_store.py`. Insert them after the existing `test_flush_restores_buffers_on_failure` method (after line 556).

Add these two methods inside the `TestFlush` class:

```python
    @pytest.mark.asyncio
    async def test_flush_enrichment_uses_match_not_merge(self, neo4j_store):
        """Upsert with labels + flush, then upsert same node_id with empty labels + flush.

        Must produce ONE node (not two).  The empty-label upsert enriches the
        existing node via MATCH, not MERGE with a default 'Session' label.
        This reproduces the exact bug: orchestrator:complete fires after flush,
        creating a second upsert_node(run_id, set(), ...) that previously
        defaulted to MERGE (n:Session ...) and created an orphan.
        """
        # Phase 1: Create node with label, flush to Neo4j
        await neo4j_store.upsert_node(
            "enrich-n1", {"OrchestratorRun"}, {"status": "running"}
        )
        await neo4j_store.flush()

        # Phase 2: Enrich same node with empty labels (post-flush buffer is empty)
        await neo4j_store.upsert_node(
            "enrich-n1", set(), {"status": "complete", "duration_ms": 1234}
        )
        await neo4j_store.flush()

        # Verify: ONE node, not two
        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: $nid}) RETURN n, labels(n) AS labels",
                nid="enrich-n1",
            )
            records = [record async for record in result]

        assert len(records) == 1, (
            f"Expected 1 node but found {len(records)} — "
            f"empty-label upsert created an orphan instead of enriching"
        )
        node = records[0]["n"]
        labels = set(records[0]["labels"])
        assert "OrchestratorRun" in labels, "Original label must be preserved"
        assert "Session" not in labels, "Must NOT have Session label from default fallback"
        assert node["status"] == "complete", "Enriched property must overwrite"
        assert node["duration_ms"] == 1234, "New enriched property must be present"

    @pytest.mark.asyncio
    async def test_flush_enrichment_skips_nonexistent_node(self, neo4j_store):
        """Upsert with empty labels when node doesn't exist -> MATCH finds nothing, no crash."""
        await neo4j_store.upsert_node("ghost-n1", set(), {"status": "complete"})
        await neo4j_store.flush()  # must not raise

        # Verify: no node created
        async with neo4j_store._driver.session(database=neo4j_store._database) as session:
            result = await session.run(
                "MATCH (n {node_id: $nid}) RETURN n", nid="ghost-n1"
            )
            record = await result.single()

        assert record is None, (
            "Empty-label upsert on nonexistent node must not create anything"
        )
```

### Step 2: Run tests to verify they fail

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestFlush::test_flush_enrichment_uses_match_not_merge tests/test_neo4j_store.py::TestFlush::test_flush_enrichment_skips_nonexistent_node -v
```

Expected: BOTH FAIL.
- `test_flush_enrichment_uses_match_not_merge` fails with `AssertionError: Expected 1 node but found 2` (the bug: empty labels defaults to "Session", creating an orphan).
- `test_flush_enrichment_skips_nonexistent_node` fails with `AssertionError: Empty-label upsert on nonexistent node must not create anything` (the bug: MERGE creates a Session node).

### Step 3: Implement the fix in neo4j_store.py

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/neo4j_store.py`, replace the entire node-processing section inside the `flush()` method.

**Find this code block** (lines 263–309):

```python
                    if node_snapshot:
                        # Group by primary label for MERGE efficiency
                        primary_groups: dict[str, list[dict[str, Any]]] = {}
                        for node_id, entry in node_snapshot.items():
                            labels = entry["labels"]
                            primary = sorted(labels)[0] if labels else "Session"
                            row: dict[str, Any] = {
                                "node_id": node_id,
                                "props": {
                                    **self._convert_timestamps(
                                        self._sanitize_properties(entry["properties"])
                                    ),
                                    "node_id": node_id,
                                    "graph_forest_name": forest,
                                },
                                "labels": list(labels),
                            }
                            primary_groups.setdefault(primary, []).append(row)

                        for primary_label, rows in primary_groups.items():
                            # primary_label is safe: comes from handler code
                            # (e.g. "Session", "OrchestratorRun"), not user input.
                            await tx.run(
                                f"UNWIND $rows AS row "  # noqa: S608
                                f"MERGE (n:`{primary_label}` {{node_id: row.node_id}}) "
                                f"SET n += row.props",
                                rows=rows,
                            )

                        # -- Apply additional labels in second pass --
                        all_rows = [r for group in primary_groups.values() for r in group]
                        label_groups: dict[frozenset[str], list[str]] = {}
                        for row in all_rows:
                            key = frozenset(row["labels"])
                            if len(key) > 1:  # only needed when >1 label
                                label_groups.setdefault(key, []).append(row["node_id"])

                        for label_set, node_ids in label_groups.items():
                            label_clause = ":".join(f"`{lbl}`" for lbl in sorted(label_set))
                            # label_clause is safe: values come from internal frozenset keys
                            # populated by upsert_node() callers, never from raw user input.
                            await tx.run(
                                f"UNWIND $ids AS nid "  # noqa: S608
                                f"MATCH (n {{node_id: nid}}) "
                                f"SET n:{label_clause}",
                                ids=node_ids,
                            )
```

**Replace with:**

```python
                    if node_snapshot:
                        # Separate enrichment nodes (empty labels -> MATCH)
                        # from creation nodes (have labels -> MERGE by primary).
                        enrichment_rows: list[dict[str, Any]] = []
                        primary_groups: dict[str, list[dict[str, Any]]] = {}
                        for node_id, entry in node_snapshot.items():
                            labels = entry["labels"]
                            row: dict[str, Any] = {
                                "node_id": node_id,
                                "props": {
                                    **self._convert_timestamps(
                                        self._sanitize_properties(entry["properties"])
                                    ),
                                    "node_id": node_id,
                                    "graph_forest_name": forest,
                                },
                                "labels": list(labels),
                            }
                            if labels:
                                primary = sorted(labels)[0]
                                primary_groups.setdefault(primary, []).append(row)
                            else:
                                enrichment_rows.append(row)

                        # -- Enrichment: MATCH existing nodes by node_id --
                        # Empty labels = enriching an already-created node.
                        # MATCH finds the node regardless of its labels.
                        # If the node doesn't exist, MATCH is a silent no-op.
                        if enrichment_rows:
                            logger.debug(
                                "flush: enriching %d node(s) via MATCH (empty labels)",
                                len(enrichment_rows),
                            )
                            await tx.run(
                                "UNWIND $rows AS row "
                                "MATCH (n {node_id: row.node_id}) "
                                "SET n += row.props",
                                rows=enrichment_rows,
                            )

                        # -- Creation: MERGE nodes by primary label --
                        for primary_label, rows in primary_groups.items():
                            # primary_label is safe: comes from handler code
                            # (e.g. "Session", "OrchestratorRun"), not user input.
                            await tx.run(
                                f"UNWIND $rows AS row "  # noqa: S608
                                f"MERGE (n:`{primary_label}` {{node_id: row.node_id}}) "
                                f"SET n += row.props",
                                rows=rows,
                            )

                        # -- Apply additional labels in second pass --
                        all_rows = [
                            r for group in primary_groups.values() for r in group
                        ]
                        label_groups: dict[frozenset[str], list[str]] = {}
                        for row in all_rows:
                            key = frozenset(row["labels"])
                            if len(key) > 1:  # only needed when >1 label
                                label_groups.setdefault(key, []).append(
                                    row["node_id"]
                                )

                        for label_set, node_ids in label_groups.items():
                            label_clause = ":".join(
                                f"`{lbl}`" for lbl in sorted(label_set)
                            )
                            # label_clause is safe: values come from internal
                            # frozenset keys populated by upsert_node() callers,
                            # never from raw user input.
                            await tx.run(
                                f"UNWIND $ids AS nid "  # noqa: S608
                                f"MATCH (n {{node_id: nid}}) "
                                f"SET n:{label_clause}",
                                ids=node_ids,
                            )
```

**What changed:**
1. The `"Session"` default for empty labels is **removed entirely**.
2. Nodes with empty labels go into `enrichment_rows` and use `MATCH` instead of `MERGE`.
3. Nodes with labels continue to use `MERGE` by primary label (unchanged behavior).
4. The second pass (additional labels) only operates on labeled nodes (enrichment nodes have no labels to apply).
5. A `logger.debug()` call logs how many enrichment nodes are being flushed.

### Step 4: Run tests to verify they pass

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py::TestFlush -v
```

Expected: ALL tests in `TestFlush` pass, including the two new tests.

Then run the full neo4j_store test suite to verify no regressions:

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_neo4j_store.py -v
```

Expected: ALL tests pass.

### Step 5: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/neo4j_store.py modules/hook-context-intelligence/tests/test_neo4j_store.py
git commit -m "fix: empty-label upserts use MATCH not MERGE in flush

Nodes buffered with empty labels (enrichment calls after a prior flush)
now use MATCH (n {node_id: ...}) instead of MERGE (n:Session {node_id: ...}).
This prevents creation of orphan Session nodes when orchestrator:complete
fires after execution:end has already flushed the OrchestratorRun node.

The 'Session' default for empty labels is removed entirely.
Empty labels = enrichment = MATCH by node_id.
If the node doesn't exist, MATCH is a silent no-op."
```

---

## Task 2: Blob Processor Lifts Fields from `raw` Before Offloading

Tasks 1 and 2 are independent. Task 3 depends on Task 2.

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_processor.py`
- Modify: `modules/hook-context-intelligence/tests/test_blob_processor.py`

### Step 1: Write the failing tests

Add a new test class `TestRawFieldLifting` at the end of `tests/test_blob_processor.py` (after the existing `TestReturnValue` class, after line 300).

Append this entire class:

```python


# ---------------------------------------------------------------------------
# Raw field lifting
# ---------------------------------------------------------------------------


class TestRawFieldLifting:
    """Blob processor lifts stop_reason, finish_reason, usage from raw before offloading."""

    async def test_lifts_stop_reason_and_merges_usage(self) -> None:
        """Main scenario: raw.stop_reason promoted, raw.usage merged with existing usage."""
        store = MockBlobStore()
        data = {
            "raw": {
                "stop_reason": "tool_use",
                "usage": {
                    "input_tokens": 107421,
                    "output_tokens": 146,
                    "cache_read_input_tokens": 105205,
                },
            },
            "usage": {"input": 3, "output": 146, "cache_read": 105205},
        }

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        # stop_reason lifted to top level
        assert result["stop_reason"] == "tool_use"

        # usage merged: provider keys + orchestrator keys coexist
        usage = result["usage"]
        assert usage["input_tokens"] == 107421, "Provider key lifted from raw.usage"
        assert usage["output_tokens"] == 146, "Provider key lifted from raw.usage"
        assert usage["cache_read_input_tokens"] == 105205, "Provider key lifted from raw.usage"
        assert usage["input"] == 3, "Orchestrator key preserved"
        assert usage["output"] == 146, "Orchestrator key preserved (wins on collision)"
        assert usage["cache_read"] == 105205, "Orchestrator key preserved"

        # raw replaced with blob ref
        assert "$blob_ref" in result["raw"]

    async def test_does_not_overwrite_existing_stop_reason(self) -> None:
        """Top-level stop_reason is NOT overwritten by raw.stop_reason."""
        store = MockBlobStore()
        data = {
            "raw": {"stop_reason": "tool_use"},
            "stop_reason": "length",
        }

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert result["stop_reason"] == "length", "Existing top-level value must win"

    async def test_does_not_overwrite_existing_finish_reason(self) -> None:
        """Top-level finish_reason is NOT overwritten by raw.finish_reason."""
        store = MockBlobStore()
        data = {
            "raw": {"finish_reason": "tool_use"},
            "finish_reason": "stop",
        }

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert result["finish_reason"] == "stop", "Existing top-level value must win"

    async def test_existing_usage_keys_win_on_collision(self) -> None:
        """When raw.usage and clone.usage share a key, the existing clone value wins."""
        store = MockBlobStore()
        data = {
            "raw": {"usage": {"output": 999, "input_tokens": 100}},
            "usage": {"output": 146},
        }

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert result["usage"]["output"] == 146, "Existing key wins on collision"
        assert result["usage"]["input_tokens"] == 100, "New provider key added"

    async def test_raw_not_dict_skips_lifting(self) -> None:
        """When raw is not a dict (e.g. a string), lifting is skipped, no crash."""
        store = MockBlobStore()
        data = {"raw": "some string blob", "usage": {"input": 3}}

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert "$blob_ref" in result["raw"]
        assert result["usage"] == {"input": 3}, "Existing usage unchanged"
        assert "stop_reason" not in result

    async def test_raw_without_usage_or_stop_reason(self) -> None:
        """Raw dict without recognized subfields does not crash or mutate clone."""
        store = MockBlobStore()
        data = {
            "raw": {"some_field": "value"},
            "usage": {"input": 3},
        }

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert "$blob_ref" in result["raw"]
        assert result["usage"] == {"input": 3}, "Existing usage unchanged"
        assert "stop_reason" not in result
        assert "finish_reason" not in result

    async def test_lifts_raw_usage_when_no_existing_usage(self) -> None:
        """When clone has no usage dict, raw.usage is promoted as-is."""
        store = MockBlobStore()
        data = {
            "raw": {"usage": {"input_tokens": 100, "output_tokens": 50}},
        }

        result = await process_event_data(data, store, session_id="s1", node_id="n1")

        assert result["usage"] == {"input_tokens": 100, "output_tokens": 50}
        assert "$blob_ref" in result["raw"]

    async def test_original_data_unchanged_after_lifting(self) -> None:
        """Original data dict is NEVER mutated by the lifting step."""
        store = MockBlobStore()
        raw_usage = {"input_tokens": 107421, "output_tokens": 146}
        raw = {"stop_reason": "tool_use", "usage": raw_usage}
        original_usage = {"input": 3, "output": 146}
        data = {
            "raw": raw,
            "usage": original_usage,
            "session_id": "s1",
        }

        await process_event_data(data, store, session_id="s1", node_id="n1")

        # Original data must be completely untouched
        assert data["raw"] is raw
        assert data["raw"]["stop_reason"] == "tool_use"
        assert data["raw"]["usage"] is raw_usage
        assert "stop_reason" not in data, "stop_reason must NOT appear in original"
        assert data["usage"] is original_usage
        assert data["usage"] == {"input": 3, "output": 146}, "Original usage must NOT be merged"
```

### Step 2: Run tests to verify they fail

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_blob_processor.py::TestRawFieldLifting -v
```

Expected: ALL 8 tests FAIL. The lifting function doesn't exist yet, so:
- `test_lifts_stop_reason_and_merges_usage` fails with `KeyError: 'stop_reason'` or `assert result.get("stop_reason") != "tool_use"`.
- `test_lifts_raw_usage_when_no_existing_usage` fails because `usage` key won't be in result.
- The "does not overwrite" and "collision" tests may pass incidentally (they test preservation of existing values, which already works). That's OK.
- `test_original_data_unchanged_after_lifting` should pass (immutability already works). That's OK — it's a safety net.

The critical tests that MUST fail are `test_lifts_stop_reason_and_merges_usage` and `test_lifts_raw_usage_when_no_existing_usage`.

### Step 3: Implement the fix in blob_processor.py

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_processor.py`, make two changes:

**Change 1: Add the `_lift_raw_fields` helper function.**

Insert this function BEFORE the `process_event_data` function (after the `_Writable` class definition, after line 23):

```python


def _lift_raw_fields(clone: dict) -> None:
    """Lift queryable subfields from clone['raw'] into the clone's top level.

    Extracts ``stop_reason``, ``finish_reason``, and ``usage`` from the raw
    provider response and promotes them so downstream handlers can access
    them after ``raw`` is replaced with a blob ref.

    Mutates *clone* in place.  Only called on the deep-cloned copy, never
    on the original event data.
    """
    raw = clone.get("raw")
    if not isinstance(raw, dict):
        return

    # Lift stop_reason / finish_reason (only if not already set at top level)
    if raw.get("stop_reason") is not None and clone.get("stop_reason") is None:
        clone["stop_reason"] = raw["stop_reason"]

    if raw.get("finish_reason") is not None and clone.get("finish_reason") is None:
        clone["finish_reason"] = raw["finish_reason"]

    # Merge raw.usage into clone.usage (existing keys win on collision)
    raw_usage = raw.get("usage")
    if isinstance(raw_usage, dict):
        existing_usage = clone.get("usage")
        if isinstance(existing_usage, dict):
            # Provider keys supplement orchestrator keys; existing wins on collision
            clone["usage"] = {**raw_usage, **existing_usage}
        else:
            clone["usage"] = dict(raw_usage)
```

**Change 2: Call the lifting function in `process_event_data`.**

Find this line in `process_event_data` (line 60):

```python
    clone: dict = copy.deepcopy(data)
```

Replace it with:

```python
    clone: dict = copy.deepcopy(data)

    # Lift useful subfields from raw before offloading to blob store.
    # This makes provider token counts and finish reason available to
    # downstream handlers after raw is replaced with a blob ref.
    _lift_raw_fields(clone)
```

The `for field_name in BLOB_FIELDS:` loop that follows remains unchanged.

### Step 4: Run tests to verify they pass

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_blob_processor.py -v
```

Expected: ALL tests pass (both existing and new).

### Step 5: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_processor.py modules/hook-context-intelligence/tests/test_blob_processor.py
git commit -m "feat: blob processor lifts raw.usage and raw.stop_reason before offloading

Before replacing raw with a blob ref, the blob processor now extracts
useful subfields and promotes them into the clone's top level:
- raw.stop_reason -> clone.stop_reason (if not already set)
- raw.finish_reason -> clone.finish_reason (if not already set)
- raw.usage merged into clone.usage (existing keys win on collision)

This makes provider token counts (input_tokens, output_tokens) and
finish reason available to downstream handlers after raw is offloaded."
```

---

## Task 3: StepHandler Uses Correct Field Names for Tokens

**Depends on Task 2** — the handler now expects `stop_reason` at top level and provider token keys in the merged `usage` dict, both provided by the blob processor's lifting step.

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/step.py`
- Modify: `modules/hook-context-intelligence/tests/test_step_handler.py`

### Step 1: Update broken tests and add new tests

This step modifies `tests/test_step_handler.py` in several places.

#### 1a. Rewrite `test_canonical_short_usage_keys` (line 527)

This test currently passes `usage.input` as a token count. After the fix, `input` is stored as `message_count`, not `input_tokens`.

**Find** the entire method body of `test_canonical_short_usage_keys` inside `TestLlmResponseCanonicalKeys` (lines 527–553):

```python
    async def test_canonical_short_usage_keys(self, services: HookStateService) -> None:
        """The orchestrator emits usage={input, output, cache_read, cache_write}."""
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {
                    "input": 101132,
                    "output": 145,
                    "cache_read": 97000,
                    "cache_write": 3533,
                },
                "raw": {"stop_reason": "tool_use"},
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        props = node["properties"]
        assert props["input_tokens"] == 101132
        assert props["output_tokens"] == 145
        assert props["cached_tokens"] == 97000
        assert props["cache_write_tokens"] == 3533
        assert props["finish_reason"] == "tool_use"
        assert props["response_at"] == LLM_RESP_TS
```

**Replace with:**

```python
    async def test_canonical_short_usage_keys(self, services: HookStateService) -> None:
        """Orchestrator-only short keys: input stored as message_count, NOT input_tokens.

        When only short keys are present (no provider input_tokens key),
        the handler stores input as message_count and does NOT set input_tokens.
        output, cache_read, cache_write still fall back correctly.
        stop_reason at top level (lifted by blob processor) maps to finish_reason.
        """
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {
                    "input": 3,
                    "output": 145,
                    "cache_read": 97000,
                    "cache_write": 3533,
                },
                "stop_reason": "tool_use",
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        props = node["properties"]
        assert "input_tokens" not in props, "input is message count, NOT token count"
        assert props["message_count"] == 3
        assert props["output_tokens"] == 145
        assert props["cached_tokens"] == 97000
        assert props["cache_write_tokens"] == 3533
        assert props["finish_reason"] == "tool_use"
        assert props["response_at"] == LLM_RESP_TS
```

#### 1b. Rewrite `test_finish_reason_from_raw_stop_reason` (line 555)

This test currently relies on the handler digging into `raw` for `stop_reason`. After the fix, the handler reads `stop_reason` from the top level (where the blob processor places it).

**Find** the method (lines 555–570):

```python
    async def test_finish_reason_from_raw_stop_reason(self, services: HookStateService) -> None:
        """stop_reason lives inside data['raw'], not at the top level."""
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {"input": 10, "output": 5},
                "raw": {"stop_reason": "end_turn"},
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        assert node["properties"]["finish_reason"] == "end_turn"
```

**Replace with:**

```python
    async def test_finish_reason_from_raw_stop_reason(self, services: HookStateService) -> None:
        """stop_reason at top level (lifted from raw by blob processor) maps to finish_reason."""
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {"input": 10, "output": 5},
                "stop_reason": "end_turn",
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        assert node["properties"]["finish_reason"] == "end_turn"
```

#### 1c. Rewrite `test_finish_reason_from_raw_finish_reason` (line 572)

Same issue — handler no longer digs into `raw`.

**Find** the method (lines 572–587):

```python
    async def test_finish_reason_from_raw_finish_reason(self, services: HookStateService) -> None:
        """OpenAI-style raw response uses finish_reason instead of stop_reason."""
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {"input": 10, "output": 5},
                "raw": {"finish_reason": "stop"},
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        assert node["properties"]["finish_reason"] == "stop"
```

**Replace with:**

```python
    async def test_finish_reason_from_raw_finish_reason(self, services: HookStateService) -> None:
        """finish_reason at top level (lifted from raw by blob processor) is used directly."""
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {"input": 10, "output": 5},
                "finish_reason": "stop",
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        assert node["properties"]["finish_reason"] == "stop"
```

#### 1d. Enhance `test_enriches_step_with_tokens` (line 370)

Add a `message_count` assertion. Find the assertion block at the end of this method (around line 394):

```python
        assert props["finish_reason"] == "end_turn"
```

Add this line immediately after it:

```python
        assert "message_count" not in props, "No input key in usage -> no message_count"
```

#### 1e. Enhance `test_empty_usage_is_safe` (line 413)

Find the assertion block at the end of this method (around line 431):

```python
        assert "reasoning_tokens" not in node["properties"]
```

Add this line immediately after it:

```python
        assert "message_count" not in node["properties"]
```

#### 1f. Enhance `test_missing_usage_key_is_safe` (line 433)

Find the assertion block at the end of this method (around line 451):

```python
        assert "reasoning_tokens" not in node["properties"]
```

Add this line immediately after it:

```python
        assert "message_count" not in node["properties"]
```

#### 1g. Add new test class `TestLlmResponseTokenSeparation`

Add this new class at the end of `tests/test_step_handler.py` (after the last class):

```python


# -- TestLlmResponseTokenSeparation ----------------------------------------


class TestLlmResponseTokenSeparation:
    """Token counts (from provider) are stored separately from message counts (from orchestrator)."""

    async def test_merged_usage_provider_and_orchestrator_keys(
        self, services: HookStateService
    ) -> None:
        """Post-blob-processor usage has both short and long keys.

        input_tokens comes from provider (real token count).
        message_count comes from orchestrator's input key.
        """
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {
                    "input": 3,
                    "output": 146,
                    "cache_read": 105205,
                    "input_tokens": 107421,
                    "output_tokens": 146,
                    "cache_read_input_tokens": 105205,
                },
                "stop_reason": "tool_use",
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        props = node["properties"]
        assert props["input_tokens"] == 107421, "Provider's real token count"
        assert props["output_tokens"] == 146
        assert props["cached_tokens"] == 105205
        assert props["message_count"] == 3, "Orchestrator's message count"
        assert props["finish_reason"] == "tool_use"
        assert props["response_at"] == LLM_RESP_TS

    async def test_zero_message_count_not_treated_as_falsy(
        self, services: HookStateService
    ) -> None:
        """input=0 must be stored as message_count=0, not skipped.

        The old code used `or` which treated 0 as falsy and would skip it.
        Explicit `is None` checks handle 0 correctly.
        """
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {
                    "input": 0,
                    "input_tokens": 100,
                    "output_tokens": 50,
                },
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        props = node["properties"]
        assert props["input_tokens"] == 100
        assert props["output_tokens"] == 50
        assert props["message_count"] == 0, "Zero must not be treated as falsy"
```

### Step 2: Run tests to verify they fail

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_step_handler.py::TestLlmResponseCanonicalKeys::test_canonical_short_usage_keys tests/test_step_handler.py::TestLlmResponseTokenSeparation -v
```

Expected:
- `test_canonical_short_usage_keys` FAILS: `assert "input_tokens" not in props` fails because current code sets `input_tokens = 3`.
- `test_merged_usage_provider_and_orchestrator_keys` FAILS: `assert props["input_tokens"] == 107421` fails because current code sets `input_tokens = 3` (via `usage.get("input") or usage.get("input_tokens")` → `3 or 107421` → `3`).
- `test_zero_message_count_not_treated_as_falsy` FAILS: `assert props["message_count"] == 0` fails because current code never sets `message_count`.

### Step 3: Implement the fix in handlers/step.py

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/step.py`, replace the usage extraction and finish_reason extraction blocks in `_handle_llm_response`.

**Find this code block** (lines 143–179):

```python
        # Extract usage tokens.
        # The llm:response event uses normalised short keys ("input", "output",
        # "cache_read") but some providers may emit Anthropic-style long keys
        # ("input_tokens", "output_tokens", "cache_read_input_tokens").
        # Try the short (canonical) key first, fall back to the long form.
        usage = data.get("usage")
        if usage and isinstance(usage, dict):
            input_tokens = usage.get("input") or usage.get("input_tokens")
            if input_tokens is not None:
                properties["input_tokens"] = input_tokens
            output_tokens = usage.get("output") or usage.get("output_tokens")
            if output_tokens is not None:
                properties["output_tokens"] = output_tokens
            cached = (
                usage.get("cache_read")
                or usage.get("cache_read_input_tokens")
                or usage.get("cached_tokens")
            )
            if cached is not None:
                properties["cached_tokens"] = cached
            cache_write = usage.get("cache_write") or usage.get("cache_creation_input_tokens")
            if cache_write is not None:
                properties["cache_write_tokens"] = cache_write
            reasoning_tokens = usage.get("reasoning") or usage.get("reasoning_tokens")
            if reasoning_tokens is not None:
                properties["reasoning_tokens"] = reasoning_tokens

        # Extract finish_reason / stop_reason.
        # Try top-level data first, then fall back to raw response payload
        # (the orchestrator puts the provider response under data["raw"]).
        finish_reason = data.get("finish_reason") or data.get("stop_reason")
        if finish_reason is None:
            raw = data.get("raw")
            if isinstance(raw, dict):
                finish_reason = raw.get("stop_reason") or raw.get("finish_reason")
        if finish_reason is not None:
            properties["finish_reason"] = finish_reason
```

**Replace with:**

```python
        # Extract usage tokens.
        # After blob processing, the usage dict contains both the orchestrator's
        # normalized short keys (input, output — where input is message count)
        # and the provider's long keys (input_tokens, output_tokens — real token
        # counts lifted from raw.usage).  Prefer provider long keys for tokens.
        # Store orchestrator's "input" separately as message_count.
        usage = data.get("usage")
        if usage and isinstance(usage, dict):
            # Token counts from provider (lifted from raw.usage by blob processor)
            input_tokens = usage.get("input_tokens")
            if input_tokens is not None:
                properties["input_tokens"] = input_tokens

            output_tokens = usage.get("output_tokens")
            if output_tokens is None:
                output_tokens = usage.get("output")
            if output_tokens is not None:
                properties["output_tokens"] = output_tokens

            # Cached tokens: prefer provider key, fall back to orchestrator's
            cached = usage.get("cache_read_input_tokens")
            if cached is None:
                cached = usage.get("cache_read")
            if cached is None:
                cached = usage.get("cached_tokens")
            if cached is not None:
                properties["cached_tokens"] = cached

            cache_write = usage.get("cache_creation_input_tokens")
            if cache_write is None:
                cache_write = usage.get("cache_write")
            if cache_write is not None:
                properties["cache_write_tokens"] = cache_write

            reasoning = usage.get("reasoning_tokens")
            if reasoning is None:
                reasoning = usage.get("reasoning")
            if reasoning is not None:
                properties["reasoning_tokens"] = reasoning

            # Message count from orchestrator (stored separately from token counts)
            message_count = usage.get("input")
            if message_count is not None:
                properties["message_count"] = message_count

        # Finish reason: now at top level (lifted from raw by blob processor).
        # No need to dig into raw — blob processor promotes stop_reason/finish_reason.
        finish_reason = data.get("finish_reason")
        if finish_reason is None:
            finish_reason = data.get("stop_reason")
        if finish_reason is not None:
            properties["finish_reason"] = finish_reason
```

**What changed:**
1. `input_tokens` reads ONLY from `usage.get("input_tokens")` — the provider's real token count. It does NOT fall back to `usage.get("input")` (which is message count, not tokens).
2. `output_tokens` tries `usage.get("output_tokens")` first, falls back to `usage.get("output")` — this is safe because `output` IS genuinely the output token count (only `input` is ambiguous).
3. All `or` chains replaced with explicit `if x is None` chains.
4. New property `message_count` stores `usage.get("input")` — the orchestrator's message count, separate from token counts.
5. `finish_reason` extraction simplified: reads from top level only (`data.get("finish_reason")` then `data.get("stop_reason")`). The `raw` dict fallback is removed — the blob processor now handles lifting.

### Step 4: Run tests to verify they pass

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_step_handler.py -v
```

Expected: ALL tests pass.

### Step 5: Run full test suite

```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: ALL tests across all test files pass.

### Step 6: Commit

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/step.py modules/hook-context-intelligence/tests/test_step_handler.py
git commit -m "fix: StepHandler uses explicit None checks, separates message_count from input_tokens

Replace Python 'or' truthiness chains with explicit 'is None' checks
for all usage field extraction.  This fixes the bug where usage.input=3
(the orchestrator's message count) was stored as input_tokens because
3 is truthy and short-circuits the or-chain.

Key changes:
- input_tokens reads ONLY from usage.input_tokens (provider's real count)
- usage.input stored as message_count (separate property)
- output_tokens falls back to usage.output (output is genuinely tokens)
- finish_reason/stop_reason read from top level only (blob processor lifts)
- All or-chains replaced with if x is None chains"
```

---

## Summary of All Changes

| File | Change | Lines |
|---|---|---|
| `neo4j_store.py` | Empty labels → MATCH (enrichment), remove `"Session"` default | ~50 lines |
| `blob_processor.py` | Add `_lift_raw_fields()`, call before blob offload | ~35 lines |
| `handlers/step.py` | Explicit None checks, prefer provider keys, add message_count | ~40 lines |
| `tests/test_neo4j_store.py` | 2 new tests in `TestFlush` | ~55 lines |
| `tests/test_blob_processor.py` | 8 new tests in `TestRawFieldLifting` | ~120 lines |
| `tests/test_step_handler.py` | 3 rewritten tests, 3 enhanced tests, 2 new tests | ~120 lines |

**New graph property:** `message_count` on `AssistantStep` nodes stores the orchestrator's message count (`usage.input`), separate from `input_tokens` (the provider's real token count). This is a schema addition — the Cypher skill at `skills/context-intelligence-neo4j-search/SKILL.md` should be updated in a follow-up to document this property.

**Invariants preserved:**
- Original event data is NEVER mutated (deep clone only)
- Nodes are always created with labels first; enrichment via MATCH finds them by `node_id`
- All existing tests continue to pass with no behavioral regressions
