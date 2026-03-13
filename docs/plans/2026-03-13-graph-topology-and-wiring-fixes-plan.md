# Graph Topology and Wiring Fixes — Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Fix 6 graph topology bugs grouped into 3 root causes: blob store not wired, ToolExecution node ID collisions on parallel calls, and events landing on Session instead of OrchestratorRun.

**Architecture:** Three surgical fixes to existing modules — no new components. (1) Wire the existing `DiskBlobStore` into `GraphDataHook` so the blob processor fires. (2) Include `tool_call_id` in ToolExecution node IDs to prevent collisions. (3) Make DefaultHandler run-aware so unclaimed events attach to OrchestratorRun when one is active. Plus an investigation task for the orphan Session node, and documentation updates.

**Tech Stack:** Python 3.11+, pytest with pytest-asyncio (mode=auto), in-memory GraphState for unit tests, Neo4j for integration tests.

**Design doc:** `docs/plans/2026-03-13-graph-topology-and-wiring-fixes-design.md`

**Base paths (all relative to repo root):**
- Source: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/`
- Tests: `modules/hook-context-intelligence/tests/`
- Run tests: `cd modules/hook-context-intelligence && uv run pytest tests/ -v`
- Run one test: `cd modules/hook-context-intelligence && uv run pytest tests/test_foo.py::TestBar::test_baz -v`

---

## Task 1: ToolExecution Node ID Disambiguator (Root B — Problems 2, 3)

**Why:** Two parallel `tool:pre` events with the same millisecond timestamp produce identical node IDs via `make_node_id()`. The second upsert overwrites the first. The `PARALLEL_WITH` edge becomes a self-loop. Fix: append the `tool_call_id` as a disambiguator.

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/utils.py`
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/tool_execution.py`
- Modify: `modules/hook-context-intelligence/tests/test_utils.py`
- Modify: `modules/hook-context-intelligence/tests/test_tool_execution_handler.py`

---

### Task 1.1: Add `disambiguator` parameter to `make_node_id`

**Step 1: Write the failing tests**

Open `modules/hook-context-intelligence/tests/test_utils.py`. Add these tests to the existing `TestMakeNodeId` class (after the last test method, around line 54):

```python
    def test_disambiguator_appended_to_id(self):
        """When disambiguator is provided, it is appended as a fourth segment."""
        result = make_node_id("s1", "tool:pre", "2026-01-01T00:00:00Z", disambiguator="call_abc")
        assert result == "s1__tool_pre__1767225600000__call_abc"

    def test_disambiguator_none_preserves_old_format(self):
        """When disambiguator is None (default), ID format is unchanged."""
        result = make_node_id("s1", "tool:pre", "2026-01-01T00:00:00Z")
        assert result == "s1__tool_pre__1767225600000"

    def test_same_timestamp_different_disambiguator_produces_different_ids(self):
        """Two calls with same session/event/timestamp but different disambiguators produce different IDs."""
        a = make_node_id("s1", "tool:pre", "2026-01-01T00:00:00Z", disambiguator="call_001")
        b = make_node_id("s1", "tool:pre", "2026-01-01T00:00:00Z", disambiguator="call_002")
        assert a != b

    def test_disambiguator_deterministic(self):
        """Same inputs with same disambiguator always produce the same ID."""
        a = make_node_id("s1", "tool:pre", "2026-01-01T00:00:00Z", disambiguator="call_001")
        b = make_node_id("s1", "tool:pre", "2026-01-01T00:00:00Z", disambiguator="call_001")
        assert a == b
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_utils.py::TestMakeNodeId::test_disambiguator_appended_to_id tests/test_utils.py::TestMakeNodeId::test_disambiguator_none_preserves_old_format tests/test_utils.py::TestMakeNodeId::test_same_timestamp_different_disambiguator_produces_different_ids tests/test_utils.py::TestMakeNodeId::test_disambiguator_deterministic -v
```

Expected: FAIL — `make_node_id()` does not accept a `disambiguator` parameter.

**Step 3: Implement the change**

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/utils.py`. Change the `make_node_id` function (lines 10–23) to:

```python
def make_node_id(
    session_id: str,
    event_name: str,
    timestamp: str,
    disambiguator: str | None = None,
) -> str:
    """Generate a deterministic, filesystem-safe node ID from event data.

    Pattern: {session_id}__{safe_event}__{timestamp_ms}
    With disambiguator: {session_id}__{safe_event}__{timestamp_ms}__{disambiguator}

    Colons in *event_name* are replaced with underscores so the ID is safe
    for use as a filename component.  Parses ISO-8601 timestamps (with
    fractional seconds and timezone offsets) and converts to epoch
    milliseconds.

    The optional *disambiguator* (e.g. tool_call_id) is appended as a fourth
    segment when provided.  When omitted, the format is unchanged — full
    backward compatibility.
    """
    safe_event = event_name.replace(":", "_")
    dt = datetime.fromisoformat(timestamp)
    epoch_ms = int(dt.astimezone(timezone.utc).timestamp() * 1000)
    node_id = f"{session_id}__{safe_event}__{epoch_ms}"
    if disambiguator is not None:
        node_id = f"{node_id}__{disambiguator}"
    return node_id
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_utils.py::TestMakeNodeId -v
```

Expected: All `TestMakeNodeId` tests PASS (including the 7 original ones — backward compatibility).

---

### Task 1.2: Pass `tool_call_id` as disambiguator in ToolExecutionHandler

**Step 1: Write the failing test**

Open `modules/hook-context-intelligence/tests/test_tool_execution_handler.py`. Add a new test class at the end of the file:

```python
# ── TestParallelToolsSameTimestamp (collision fix) ─────────────────────


class TestParallelToolsSameTimestamp:
    """Two parallel tool:pre events with the SAME timestamp but different tool_call_ids
    must produce TWO distinct ToolExecution nodes — not one merged node.
    This is the critical test that validates the collision fix (Root B).
    """

    SAME_TIMESTAMP = "2026-03-06T03:10:00Z"

    async def test_two_tools_same_timestamp_produce_distinct_nodes(
        self, services: HookStateService
    ) -> None:
        """Two tool:pre with identical timestamp but different tool_call_id → 2 nodes."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)

        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "toolu_AAAA",
                "tool_name": "bash",
                "parallel_group_id": "pg_same",
            },
        )
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "toolu_BBBB",
                "tool_name": "read_file",
                "parallel_group_id": "pg_same",
            },
        )

        # Both nodes must exist and be distinct
        te_a = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP, disambiguator="toolu_AAAA")
        te_b = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP, disambiguator="toolu_BBBB")

        node_a = await services.graph.get_node(te_a)
        node_b = await services.graph.get_node(te_b)
        assert node_a is not None, f"Node A not found: {te_a}"
        assert node_b is not None, f"Node B not found: {te_b}"
        assert te_a != te_b
        assert node_a["properties"]["tool_name"] == "bash"
        assert node_b["properties"]["tool_name"] == "read_file"

    async def test_parallel_with_edge_not_self_loop(
        self, services: HookStateService
    ) -> None:
        """PARALLEL_WITH edge connects two DIFFERENT nodes (not a self-loop)."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)

        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "toolu_CCCC",
                "tool_name": "bash",
                "parallel_group_id": "pg_same",
            },
        )
        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "toolu_DDDD",
                "tool_name": "read_file",
                "parallel_group_id": "pg_same",
            },
        )

        te_c = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP, disambiguator="toolu_CCCC")
        te_d = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP, disambiguator="toolu_DDDD")

        # PARALLEL_WITH edge should exist between them
        edge = await services.graph.get_edge(te_d, te_c, "PARALLEL_WITH")
        assert edge is not None, "PARALLEL_WITH edge missing"

        # Self-loop must NOT exist
        self_loop_c = await services.graph.get_edge(te_c, te_c, "PARALLEL_WITH")
        self_loop_d = await services.graph.get_edge(te_d, te_d, "PARALLEL_WITH")
        assert self_loop_c is None, "Self-loop on node C"
        assert self_loop_d is None, "Self-loop on node D"

    async def test_tool_call_map_uses_disambiguated_ids(
        self, services: HookStateService
    ) -> None:
        """tool_call_map entries use the disambiguated node IDs."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)

        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "toolu_EEEE",
                "tool_name": "bash",
                "parallel_group_id": "pg_same",
            },
        )

        cursors = services.get_cursors("s1")
        expected_id = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP, disambiguator="toolu_EEEE")
        assert cursors.tool_call_map["toolu_EEEE"] == expected_id

    async def test_missing_tool_call_id_falls_back_to_old_format(
        self, services: HookStateService
    ) -> None:
        """When tool_call_id is empty, make_node_id uses the old format (no disambiguator)."""
        await _seed_through_step(services)
        handler = ToolExecutionHandler(services)

        await handler(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": self.SAME_TIMESTAMP,
                "tool_call_id": "",
                "tool_name": "bash",
                "parallel_group_id": "",
            },
        )

        old_format_id = make_node_id("s1", "tool:pre", self.SAME_TIMESTAMP)
        node = await services.graph.get_node(old_format_id)
        assert node is not None, "Fallback to old format should work when tool_call_id is empty"
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_tool_execution_handler.py::TestParallelToolsSameTimestamp -v
```

Expected: FAIL — `make_node_id` is called without `disambiguator`, so two tools with the same timestamp produce the same node ID. The "two distinct nodes" test will fail because `node_b` merges into `node_a`.

**Step 3: Implement the change**

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/tool_execution.py`. In `_handle_tool_pre` (line 53), change the `te_id` generation from:

```python
        # Generate deterministic TE ID
        te_id = make_node_id(session_id, "tool:pre", timestamp)
```

to:

```python
        # Generate deterministic TE ID (tool_call_id disambiguates parallel calls)
        tool_call_id = data.get("tool_call_id", "")
        te_id = make_node_id(
            session_id, "tool:pre", timestamp,
            disambiguator=tool_call_id if tool_call_id else None,
        )
```

Also remove the duplicate `tool_call_id` extraction on the old line 107 — the variable is now set earlier. Change lines 106–109 from:

```python
        # Populate tool_call_map
        tool_call_id = data.get("tool_call_id", "")
        if tool_call_id:
            cursors.tool_call_map[tool_call_id] = te_id
```

to:

```python
        # Populate tool_call_map
        if tool_call_id:
            cursors.tool_call_map[tool_call_id] = te_id
```

**Step 4: Update existing test constants**

The existing tests in `test_tool_execution_handler.py` use `EXPECTED_TE1_ID`, `EXPECTED_TE2_ID`, `EXPECTED_TE3_ID` which were computed without disambiguators. Update lines 33–35 from:

```python
EXPECTED_TE1_ID = make_node_id("s1", "tool:pre", TOOL1_TIMESTAMP)
EXPECTED_TE2_ID = make_node_id("s1", "tool:pre", TOOL2_TIMESTAMP)
EXPECTED_TE3_ID = make_node_id("s1", "tool:pre", TOOL3_TIMESTAMP)
```

to:

```python
EXPECTED_TE1_ID = make_node_id("s1", "tool:pre", TOOL1_TIMESTAMP, disambiguator="call_001")
EXPECTED_TE2_ID = make_node_id("s1", "tool:pre", TOOL2_TIMESTAMP, disambiguator="call_002")
EXPECTED_TE3_ID = make_node_id("s1", "tool:pre", TOOL3_TIMESTAMP, disambiguator="call_003")
```

Also update the `_seed_one_tool` helper (line 94). Change:

```python
    return make_node_id(session_id, "tool:pre", timestamp)
```

to:

```python
    return make_node_id(session_id, "tool:pre", timestamp, disambiguator=tool_call_id)
```

Also update `conftest.py` line 28. Change:

```python
TOOL_NODE_ID = "55c8841a-test__tool_pre__1737972002000"
```

to:

```python
TOOL_NODE_ID = "55c8841a-test__tool_pre__1737972002000__call_001"
```

And update `conftest.py` lines 32–33. Change:

```python
TOOL_NODE_2_ID = "55c8841a-test__tool_pre__1737972004000"
DELEGATION_TE_NODE_ID = "55c8841a-test__tool_pre__1737972005000"
```

to:

```python
TOOL_NODE_2_ID = "55c8841a-test__tool_pre__1737972004000__call_002"
DELEGATION_TE_NODE_ID = "55c8841a-test__tool_pre__1737972005000__call_003"
```

**Step 5: Run ALL tests to verify pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_tool_execution_handler.py tests/test_utils.py -v
```

Expected: ALL tests PASS.

Then run the full suite to check for any other breakage from the conftest changes:

```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: ALL tests PASS. Any test that imports `TOOL_NODE_ID` from conftest must still pass with the new format.

**Step 6: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/utils.py \
       modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/tool_execution.py \
       modules/hook-context-intelligence/tests/test_utils.py \
       modules/hook-context-intelligence/tests/test_tool_execution_handler.py \
       modules/hook-context-intelligence/tests/conftest.py && \
git commit -m "fix: include tool_call_id in ToolExecution node IDs to prevent collision

Parallel tool:pre events with the same millisecond timestamp now produce
distinct node IDs by appending tool_call_id as a disambiguator.

Fixes: node ID collision (Problem 2), PARALLEL_WITH self-loop (Problem 3)"
```

---

## Task 2: DefaultHandler Becomes Run-Aware (Root C — Problems 5, 6)

**Why:** Events like `artifact:read` and `prompt:complete` fire during an active OrchestratorRun but the DefaultHandler always attaches them to Session via `HAS_EVENT`. Fix: check `cursors.current_run_id` and attach to the run when one is active.

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/default.py`
- Modify: `modules/hook-context-intelligence/tests/test_default_handler.py`

---

### Task 2.1: Write the failing tests for run-aware DefaultHandler

**Step 1: Write the failing tests**

Open `modules/hook-context-intelligence/tests/test_default_handler.py`. Add new imports at the top (after line 12):

```python
from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import OrchestratorRunHandler
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
```

Add a new test class at the end of the file:

```python
class TestDefaultHandlerRunAwareness:
    """DefaultHandler attaches HAS_EVENT to OrchestratorRun when one is active,
    falls back to Session when no active run.
    """

    async def _seed_active_run(self, services: HookStateService, session_id: str = "s1") -> str:
        """Create Session + prompt:submit + execution:start so current_run_id is set.

        Returns the run node ID.
        """
        session_handler = SessionHandler(services)
        await session_handler(
            "session:start",
            {"session_id": session_id, "timestamp": "2026-03-06T00:00:00Z"},
        )
        run_handler = OrchestratorRunHandler(services)
        await run_handler(
            "prompt:submit",
            {"session_id": session_id, "timestamp": "2026-03-06T01:00:00Z", "prompt": "Hello"},
        )
        await run_handler(
            "execution:start",
            {"session_id": session_id, "timestamp": "2026-03-06T02:00:00Z"},
        )
        cursors = services.get_cursors(session_id)
        assert cursors.current_run_id is not None
        return cursors.current_run_id

    async def test_event_during_active_run_attaches_to_run(
        self, services: HookStateService
    ) -> None:
        """When current_run_id exists, HAS_EVENT goes from OrchestratorRun to Event."""
        run_id = await self._seed_active_run(services)
        handler = DefaultHandler(services)
        await handler(
            "artifact:read",
            {"session_id": "s1", "timestamp": "2026-03-06T02:30:00Z"},
        )
        event_id = make_node_id("s1", "artifact:read", "2026-03-06T02:30:00Z")

        # HAS_EVENT should come from the run, not the session
        edge_from_run = await services.graph.get_edge(run_id, event_id, "HAS_EVENT")
        assert edge_from_run is not None, "HAS_EVENT edge from run is missing"

        # HAS_EVENT from session should NOT exist
        edge_from_session = await services.graph.get_edge("s1", event_id, "HAS_EVENT")
        assert edge_from_session is None, "HAS_EVENT from session should not exist when run is active"

    async def test_event_without_active_run_attaches_to_session(
        self, services: HookStateService
    ) -> None:
        """When no current_run_id, HAS_EVENT goes from Session (existing behavior)."""
        handler = DefaultHandler(services)
        await handler(
            "session:resume",
            {"session_id": "s1", "timestamp": "2026-01-01T02:00:00Z"},
        )
        event_id = make_node_id("s1", "session:resume", "2026-01-01T02:00:00Z")

        edge = await services.graph.get_edge("s1", event_id, "HAS_EVENT")
        assert edge is not None, "HAS_EVENT edge from session is missing"

    async def test_event_after_run_completes_attaches_to_session(
        self, services: HookStateService
    ) -> None:
        """After orchestrator:complete clears current_run_id, events go back to Session."""
        run_id = await self._seed_active_run(services)
        run_handler = OrchestratorRunHandler(services)
        await run_handler(
            "orchestrator:complete",
            {
                "session_id": "s1",
                "timestamp": "2026-03-06T03:00:00Z",
                "status": "success",
                "turn_count": 1,
            },
        )

        # current_run_id should be cleared
        cursors = services.get_cursors("s1")
        assert cursors.current_run_id is None

        handler = DefaultHandler(services)
        await handler(
            "prompt:complete",
            {"session_id": "s1", "timestamp": "2026-03-06T03:01:00Z"},
        )
        event_id = make_node_id("s1", "prompt:complete", "2026-03-06T03:01:00Z")

        # Should attach to session, not the (now-closed) run
        edge_from_session = await services.graph.get_edge("s1", event_id, "HAS_EVENT")
        assert edge_from_session is not None

    async def test_run_aware_event_node_still_has_correct_labels(
        self, services: HookStateService
    ) -> None:
        """Event node labels and properties are unchanged by run-awareness."""
        await self._seed_active_run(services)
        handler = DefaultHandler(services)
        await handler(
            "artifact:read",
            {"session_id": "s1", "timestamp": "2026-03-06T02:30:00Z"},
        )
        event_id = make_node_id("s1", "artifact:read", "2026-03-06T02:30:00Z")
        node = await services.graph.get_node(event_id)
        assert node is not None
        assert node["labels"] == {"Event", "ArtifactRead"}
        assert node["properties"]["event_name"] == "artifact:read"
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_default_handler.py::TestDefaultHandlerRunAwareness -v
```

Expected: FAIL — `test_event_during_active_run_attaches_to_run` fails because DefaultHandler always attaches to `session_id`.

**Step 3: Implement the change**

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/default.py`. Replace the `__call__` method (lines 37–59) with:

```python
    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            logger.debug("DefaultHandler: no session_id in %s, skipping", event)
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")
        derived = self.derive_label(event)

        # Create Event node
        event_node_id = make_node_id(session_id, event, timestamp)
        await self.services.graph.upsert_node(
            event_node_id,
            {"Event", derived},
            {"event_name": event, "occurred_at": timestamp, "data": json.dumps(data)},
        )

        # Attach to active run if one exists, otherwise to session
        cursors = self.services.get_cursors(session_id)
        parent_id = cursors.current_run_id if cursors.current_run_id else session_id

        await self.services.graph.upsert_edge(
            parent_id, event_node_id, "HAS_EVENT", {"occurred_at": timestamp}
        )

        return HookResult(action="continue")
```

**Step 4: Run ALL DefaultHandler tests to verify pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_default_handler.py -v
```

Expected: ALL tests PASS — both old tests (which have no active run, so behavior is unchanged) and new tests.

**Step 5: Run full suite**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: ALL tests PASS.

**Step 6: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/default.py \
       modules/hook-context-intelligence/tests/test_default_handler.py && \
git commit -m "fix: DefaultHandler attaches HAS_EVENT to OrchestratorRun when active

Unclaimed events (artifact:read, prompt:complete, etc.) that fire during
an active run now attach to the OrchestratorRun instead of floating on
the Session node. Falls back to Session when no run is active.

Fixes: events on wrong parent (Problems 5, 6)"
```

---

## Task 3: Blob Store Wiring Fix (Root A — Problem 1)

**Why:** The blob processor code is correct and wired in `mount.py`, but `graph_data_hook.py` never creates a `DiskBlobStore` and never passes it to `MountFlow`. The `blob_store` slot is `None` all the way through, so the guard in the dispatch wrapper always short-circuits and handlers get raw data with 437K+ inline properties.

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py`
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/graph_data_hook.py`
- Modify: `modules/hook-context-intelligence/tests/test_config_resolver.py`
- Modify: `modules/hook-context-intelligence/tests/test_graph_data_hook.py`

---

### Task 3.1: Add `blob_store_root` property to ConfigResolver

**Step 1: Write the failing test**

Open `modules/hook-context-intelligence/tests/test_config_resolver.py`. Add a new test class at the end:

```python
class TestBlobStoreRoot:
    """blob_store_root property resolves to the project-level context-intelligence directory."""

    def test_blob_store_root_returns_path(self):
        """blob_store_root is base_path / project_slug / 'sessions'."""
        from unittest.mock import MagicMock

        coordinator = MagicMock()
        coordinator.config = {}
        resolver = ConfigResolver(
            {"base_path": "/tmp/test-projects", "project_slug": "my-project"},
            coordinator,
        )
        result = resolver.blob_store_root
        assert result == Path("/tmp/test-projects") / "my-project" / "sessions"

    def test_blob_store_root_uses_default_base_path(self):
        """blob_store_root works with default base_path."""
        from unittest.mock import MagicMock

        coordinator = MagicMock()
        coordinator.config = {}
        resolver = ConfigResolver({"project_slug": "default"}, coordinator)
        result = resolver.blob_store_root
        expected = Path("~/.amplifier/projects").expanduser() / "default" / "sessions"
        assert result == expected
```

You will also need to ensure `Path` is importable in the test file. Check the existing imports — `ConfigResolver` is already imported, and `Path` from `pathlib` should be added if not already present.

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_config_resolver.py::TestBlobStoreRoot -v
```

Expected: FAIL — `ConfigResolver` has no `blob_store_root` property.

**Step 3: Implement the change**

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py`. Add a new property after `session_dir` (after line 242):

```python
    @property
    def blob_store_root(self) -> Path:
        """Root directory for blob storage.

        Returns: base_path / project_slug / 'sessions'

        DiskBlobStore uses this as its root, storing blobs in:
            <blob_store_root> / <session_id> / blobs / <key>.json
        which places them alongside the session's context-intelligence directory.
        """
        return self.base_path / self.project_slug / "sessions"
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_config_resolver.py::TestBlobStoreRoot -v
```

Expected: PASS.

---

### Task 3.2: Wire DiskBlobStore in GraphDataHook

**Step 1: Write the failing test**

Open `modules/hook-context-intelligence/tests/test_graph_data_hook.py`. Add a new test class at the end:

```python
class TestBlobStoreWiring:
    """GraphDataHook wires DiskBlobStore from resolver into MountFlow."""

    def test_graph_data_hook_passes_blob_store_to_mount_flow(self, mock_neo4j_store):
        """GraphDataHook creates a DiskBlobStore and passes it to MountFlow."""
        mock_cls, mock_store = mock_neo4j_store
        config = dict(_NEO4J_STORE_CONFIG)
        config["base_path"] = "/tmp/test-blob-wiring"
        config["project_slug"] = "test-project"
        resolver = _make_resolver(config)
        hook = GraphDataHook(resolver)

        # MountFlow should have a blob_store set
        assert hook._flow._blob_store is not None

    def test_blob_store_is_disk_blob_store(self, mock_neo4j_store):
        """The blob_store wired into MountFlow is a DiskBlobStore instance."""
        from amplifier_module_hook_context_intelligence.blob_store import DiskBlobStore

        mock_cls, mock_store = mock_neo4j_store
        config = dict(_NEO4J_STORE_CONFIG)
        config["base_path"] = "/tmp/test-blob-wiring"
        config["project_slug"] = "test-project"
        resolver = _make_resolver(config)
        hook = GraphDataHook(resolver)

        assert isinstance(hook._flow._blob_store, DiskBlobStore)
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_graph_data_hook.py::TestBlobStoreWiring -v
```

Expected: FAIL — `hook._flow._blob_store` is `None`.

**Step 3: Implement the change**

Open `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/graph_data_hook.py`. Add the import at line 11 (after the `neo4j_store` import):

```python
from .blob_store import DiskBlobStore
```

Then change the `__init__` method (lines 44–47) from:

```python
    def __init__(self, resolver: Any) -> None:
        self._resolver = resolver
        self._store = _create_neo4j_store(resolver)
        self._flow = MountFlow(config=resolver._config, graph_store=self._store, resolver=resolver)
```

to:

```python
    def __init__(self, resolver: Any) -> None:
        self._resolver = resolver
        self._store = _create_neo4j_store(resolver)
        self._blob_store = DiskBlobStore(root=resolver.blob_store_root)
        self._flow = MountFlow(
            config=resolver._config,
            graph_store=self._store,
            resolver=resolver,
            blob_store=self._blob_store,
        )
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_graph_data_hook.py -v
```

Expected: ALL tests PASS.

**Step 5: Run full suite**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: ALL tests PASS.

**Step 6: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/config_resolver.py \
       modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/graph_data_hook.py \
       modules/hook-context-intelligence/tests/test_config_resolver.py \
       modules/hook-context-intelligence/tests/test_graph_data_hook.py && \
git commit -m "fix: wire DiskBlobStore into GraphDataHook so blob processor fires

GraphDataHook now creates a DiskBlobStore from resolver.blob_store_root
and passes it to MountFlow. The existing blob processor in mount.py now
fires, replacing 437K+ inline properties with small \$blob_ref URIs.

Fixes: blob processor not running (Problem 1)"
```

---

## Task 4: Investigate and Fix Orphan Session Node (Problem 4)

**Why:** The smoke test showed a `Session` node with `node_id = <session_id>__execution_start__<ts>` — this is the OrchestratorRun's synthetic node ID being used as a session ID. The node has `data_orchestrator_complete` and `status=complete` while the actual OrchestratorRun is stuck at `status=in_progress`. Something is creating a Session node from the run's node ID.

**Files:**
- Read: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/mount.py` (lines 113–141)
- Read: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py` (lines 174–201)
- Read: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py` (lines 185–230)
- Potentially modify: one or more of the above files
- Test: `modules/hook-context-intelligence/tests/test_orchestrator_run_handler.py` (add investigation tests)

---

### Task 4.1: Investigate the orphan root cause

This is an investigation task. Do NOT write any production code until the investigation is complete and the root cause is identified.

**Step 1: Read and understand the session-guarantee wrapper**

Read `mount.py` lines 113–141 (the `_wrap_with_session_guarantee` method). Answer these questions:

1. Where does `session_id` come from? → `data.get("session_id")` on line 126.
2. What does `ensure_session_node` do with it? → Creates a `Session` node with `node_id = session_id` (see `services.py` line 200: `await self.graph.upsert_node(session_id, labels, properties)`).
3. Is `session_id` always the real session UUID? → It should be — `session_id` is infrastructure-injected into every event by the HookRegistry.

**Step 2: Read the orchestrator:complete handler**

Read `orchestrator_run.py` lines 185–230 (`_handle_orchestrator_complete`). Answer these questions:

1. Does it use `cursors.current_run_id` to find the run? → Yes, line 194.
2. What happens if `current_run_id` is `None`? → Logs warning and returns early (lines 195–197).
3. Does `orchestrator:complete` carry the real `session_id`? → Yes, it should carry the real UUID.

**Step 3: Write a diagnostic test**

Add this test to `modules/hook-context-intelligence/tests/test_orchestrator_run_handler.py` to reproduce the exact scenario:

```python
class TestOrchestratorCompleteDoesNotCreateOrphan:
    """orchestrator:complete must enrich the existing OrchestratorRun node,
    not create a spurious Session node with the run's node_id.
    """

    async def test_no_orphan_session_node_after_orchestrator_complete(
        self, services: HookStateService
    ) -> None:
        """The full lifecycle should not create a Session node with the run's node_id."""
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)

        # Create the OrchestratorRun
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": "2026-03-06T01:00:00Z", "prompt": "Hello"},
        )
        await handler(
            "execution:start",
            {"session_id": "s1", "timestamp": "2026-03-06T02:00:00Z"},
        )

        run_id = services.get_cursors("s1").current_run_id
        assert run_id is not None

        # Close the run
        await handler(
            "orchestrator:complete",
            {
                "session_id": "s1",
                "timestamp": "2026-03-06T03:00:00Z",
                "status": "success",
                "turn_count": 1,
            },
        )

        # The OrchestratorRun should be updated with status=complete
        run_node = await services.graph.get_node(run_id)
        assert run_node is not None
        assert run_node["properties"]["status"] == "complete"

        # There should NOT be a Session node with node_id = run_id
        orphan = await services.graph.get_node(run_id)
        if orphan is not None:
            # The node exists (it's the OrchestratorRun), but it must NOT have Session labels
            assert "Session" not in orphan["labels"], (
                f"Orphan Session node detected: a node with run_id {run_id} has Session label"
            )
```

Run it:

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_orchestrator_run_handler.py::TestOrchestratorCompleteDoesNotCreateOrphan -v
```

**Step 4: Analyze results and determine fix**

The investigation will tell you one of:

**(a) The orphan is caused by the session-guarantee wrapper**: `ensure_session_node(session_id, data)` is called with `session_id` extracted from `data.get("session_id")`. If for some reason the `session_id` field in `orchestrator:complete` contains the run's node_id instead of the real session UUID, the wrapper creates a Session node with the run ID.

Fix: Verify the test passes with in-memory GraphState. If it does, the orphan may be a Neo4j-specific artifact (race condition during async flush). In that case, this is a timing issue, not a code bug — document it and move on.

**(b) The orphan is caused by an event ordering race**: In real Amplifier sessions, multiple events might arrive and the session-guarantee wrapper might create a Session node before the `execution:start` event creates the OrchestratorRun. If the first event for a session uses a non-session `session_id`, the wrapper creates the orphan.

Fix: Add a guard in `ensure_session_node` that validates `session_id` looks like a UUID (not a synthetic node ID with `__` separators).

**(c) The test passes**: The orphan is not reproducible in unit tests. It may be a Neo4j async flush race. Document this finding and move on.

**Step 5: Implement the fix (if needed)**

Based on investigation findings, implement the minimal fix. If the test passes as-is (scenario c), just commit the diagnostic test and document the finding.

**Step 6: Run full suite**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: ALL tests PASS.

**Step 7: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/ && \
git commit -m "investigate: orphan Session node from orchestrator:complete (Problem 4)

Added diagnostic test to reproduce the orphan Session node scenario.
[Include investigation findings in extended commit message]"
```

---

## Task 5: Documentation and Dot File Updates

**Why:** The graph schema changes from Tasks 1–3 affect downstream documentation. The dot files and SKILL.md need to stay in sync.

**Files:**
- Modify: `context/orchestrator-run-assembly.dot`
- Modify: `skills/context-intelligence-neo4j-search/SKILL.md`

---

### Task 5.1: Update orchestrator-run-assembly.dot

**Step 1: Update the HAS_EVENT edge target in the "Resulting Graph Shape" section**

Open `context/orchestrator-run-assembly.dot`. Find line 331:

```dot
        r_session -> r_event [label="HAS_EVENT" color="#00695C" style=dotted];
```

Change it to show that HAS_EVENT can come from either Session or OrchestratorRun:

```dot
        r_run -> r_event [label="HAS_EVENT\n(run-scoped)" color="#00695C" style=dotted];
        r_session -> r_event [label="HAS_EVENT\n(session-scoped)" color="#00695C" style=dotted];
```

Also add an `r_event` description update. Find line 322:

```dot
        r_event [label=":Event" fillcolor="#B2DFDB"];
```

Change to:

```dot
        r_event [label=":Event\n(run-scoped if\nactive run exists)" fillcolor="#B2DFDB"];
```

**Step 2: Add a note about ToolExecution node ID format**

Find the `tp_create` node around line 167:

```dot
        tp_create [label="CREATE node\n:ToolExecution\n{\n  tool_call_id,\n  tool_name,\n  parallel_group_id,\n  started_at: timestamp,\n  status: 'executing'\n}"
```

Change to:

```dot
        tp_create [label="CREATE node\n:ToolExecution\n{\n  tool_call_id,\n  tool_name,\n  parallel_group_id,\n  started_at: timestamp,\n  status: 'executing'\n}\nnode_id includes tool_call_id\nas disambiguator"
```

---

### Task 5.2: Update SKILL.md

**Step 1: Update the Node ID Format section**

Open `skills/context-intelligence-neo4j-search/SKILL.md`. Find the "Node ID Format" section (around line 22). After the existing pattern description, add a note about the ToolExecution disambiguator. Change lines 26–32 from:

```markdown
**Pattern:** `{session_id}__{event_name}__{timestamp_ms}`

- `__` (double underscore) is the segment separator
- Colons in event names become underscores: `prompt:submit` → `prompt_submit`
- Session nodes use the raw `session_id` (a UUID) as their `node_id` — no
  transformation
- Example: `6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000`
```

to:

```markdown
**Pattern:** `{session_id}__{event_name}__{timestamp_ms}`

**ToolExecution pattern:** `{session_id}__{event_name}__{timestamp_ms}__{tool_call_id}`

- `__` (double underscore) is the segment separator
- Colons in event names become underscores: `prompt:submit` → `prompt_submit`
- Session nodes use the raw `session_id` (a UUID) as their `node_id` — no
  transformation
- ToolExecution nodes include `tool_call_id` as a fourth segment to prevent
  collisions when parallel tool calls share the same millisecond timestamp
- Example: `6afb3613-7041-4735-9c0f-c2171452ed18__prompt_submit__1741270343000`
- ToolExecution example: `6afb3613-...ed18__tool_pre__1741270343000__toolu_01G9FD9g`
```

**Step 2: Update the HAS_EVENT relationship description**

Find the relationship types table (around line 77). Change the `HAS_EVENT` row from:

```markdown
| `HAS_EVENT` | `Session` / `OrchestratorRun` / `Step` | `Event` | Attaches lifecycle/custom events to their scope |
```

to:

```markdown
| `HAS_EVENT` | `OrchestratorRun` (when active) / `Session` (fallback) | `Event` | Attaches lifecycle/custom events to their scope. DefaultHandler checks `cursors.current_run_id` — if an active run exists, the event attaches to the run; otherwise it falls back to the Session. |
```

**Step 3: Update the ID Format Reference section**

Find the "All other nodes" section near line 634. Add a ToolExecution example:

```markdown
### ToolExecution nodes

ToolExecution nodes include the `tool_call_id` as a disambiguator to prevent
collisions when parallel tool calls share the same millisecond timestamp:

```
55c8841a-1234-4abc-8def-000000000001__tool_pre__1737972005000__toolu_01G9FD9g
```

Parsing the ID:

```python
# Split on double underscore
parts = node_id.split("__")
# parts[0] = session_id UUID
# parts[1] = event_name (colons replaced with underscores)
# parts[2] = epoch_ms as string
# parts[3] = tool_call_id (only present on ToolExecution nodes)
```
```

**Step 4: Update Pattern 11 comment**

Find Pattern 11 (around line 477). The existing query shows `(s:Session)-[:HAS_EVENT]->(e:Event)`. Add a note:

```markdown
> **Note:** Since the DefaultHandler run-awareness fix, `HAS_EVENT` edges for
> events that fire during an active run come from the `OrchestratorRun` node,
> not the Session. Use the "Events across all scopes" query below to find
> events attached to either.
```

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add context/orchestrator-run-assembly.dot \
       skills/context-intelligence-neo4j-search/SKILL.md && \
git commit -m "docs: update dot files and SKILL.md for graph schema changes

- orchestrator-run-assembly.dot: HAS_EVENT targets OrchestratorRun when
  active, ToolExecution node ID includes tool_call_id disambiguator
- SKILL.md: updated node ID format, HAS_EVENT semantics, ID parsing examples"
```

---

## Summary

| Task | Root Cause | Problems Fixed | Files Modified |
|------|-----------|---------------|----------------|
| 1 | B: Node ID collision | 2, 3 | `utils.py`, `handlers/tool_execution.py`, `tests/test_utils.py`, `tests/test_tool_execution_handler.py`, `tests/conftest.py` |
| 2 | C: Events on wrong parent | 5, 6 | `handlers/default.py`, `tests/test_default_handler.py` |
| 3 | A: Blob store not wired | 1 | `config_resolver.py`, `graph_data_hook.py`, `tests/test_config_resolver.py`, `tests/test_graph_data_hook.py` |
| 4 | C: Orphan Session node | 4 | Investigation → fix TBD |
| 5 | N/A: Documentation | N/A | `context/orchestrator-run-assembly.dot`, `skills/.../SKILL.md` |

**Tasks 1, 2, 3 are independent** — they can be executed in parallel or any order.
**Task 4** depends on understanding Tasks 1–3 fixes but can be investigated independently.
**Task 5** should be done last after all code changes are final.

**Invariant:** Event data must NEVER be mutated in-place. The blob processor works on a deep clone. This invariant must be preserved in all changes.
