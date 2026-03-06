"""Tests for DuckDBGraphStore – buffer-first reads with async DuckDB persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.conftest import (
    PROMPT_NODE_ID,
    RUN_NODE_ID,
    SESSION_ID,
    SESSION_NODE_ID,
    TOOL_NODE_ID,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def store():
    """Fresh in-memory DuckDBGraphStore for test isolation."""
    from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

    s = DuckDBGraphStore()
    yield s
    s._conn.close()


# ---------------------------------------------------------------------------
# TestRunUsesGetRunningLoop
# ---------------------------------------------------------------------------
class TestRunUsesGetRunningLoop:
    """_run must use asyncio.get_running_loop(), not the deprecated get_event_loop()."""

    def test_run_signature_uses_callable_not_any(self):
        """_run should accept Callable[[], _T] and return Future[_T], not Any -> Any."""
        import inspect

        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        sig = inspect.signature(DuckDBGraphStore._run)
        fn_annotation = str(sig.parameters["fn"].annotation)
        ret_annotation = str(sig.return_annotation)
        # The fn parameter must reference Callable, not plain Any
        assert "Callable" in fn_annotation, (
            f"_run 'fn' param should be Callable[[], _T], not {fn_annotation}"
        )
        # The return type must not be bare Any
        assert ret_annotation != "Any", (
            f"_run return type should be Future[_T], not {ret_annotation}"
        )

    async def test_run_calls_get_running_loop(self):
        import asyncio
        from unittest.mock import patch

        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        with patch.object(asyncio, "get_running_loop", wraps=asyncio.get_running_loop) as mock_grl:
            await store.get_node("nonexistent")
            mock_grl.assert_called()


# ---------------------------------------------------------------------------
# TestProtocolConformance
# ---------------------------------------------------------------------------
class TestProtocolConformance:
    """DuckDBGraphStore must satisfy the GraphStore runtime protocol."""

    def test_isinstance_graph_store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore
        from amplifier_module_hook_context_intelligence.graph_store import GraphStore

        store = DuckDBGraphStore()
        assert isinstance(store, GraphStore)


# ---------------------------------------------------------------------------
# TestConstructor
# ---------------------------------------------------------------------------
class TestConstructor:
    """Verify constructor wiring: connection handling and table creation."""

    def test_default_connection_is_memory(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        assert store._connection_str == ":memory:"

    def test_file_path_expands_tilde(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        db_path = tmp_path / "sub" / "test.db"
        store = DuckDBGraphStore(connection=str(db_path))
        assert store._connection_str == str(db_path)
        assert db_path.parent.exists()

    def test_file_path_creates_parent_dirs(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        db_path = tmp_path / "deep" / "nested" / "dir" / "test.db"
        DuckDBGraphStore(connection=str(db_path))
        assert db_path.parent.exists()

    def test_tables_created_on_init(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        # Query information_schema to verify tables exist
        result = store._conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name IN ('nodes', 'edges', 'search_index') "
            "ORDER BY table_name"
        ).fetchall()
        table_names = [row[0] for row in result]
        assert "edges" in table_names
        assert "nodes" in table_names
        assert "search_index" in table_names


# ---------------------------------------------------------------------------
# TestBufferWrites
# ---------------------------------------------------------------------------
class TestBufferWrites:
    """upsert_node / upsert_edge write to in-memory buffers only."""

    async def test_upsert_node_writes_to_buffer(self, store):
        await store.upsert_node("n1", {"Label"}, {"key": "val"})
        assert "n1" in store._node_buffer
        # DuckDB should have nothing
        row = store._conn.execute("SELECT * FROM nodes WHERE node_id = 'n1'").fetchone()
        assert row is None

    async def test_upsert_edge_writes_to_buffer(self, store):
        await store.upsert_edge("a", "b", "KNOWS", {"weight": 1})
        assert ("a", "b", "KNOWS") in store._edge_buffer
        row = store._conn.execute(
            "SELECT * FROM edges WHERE source = 'a' AND target = 'b' AND edge_type = 'KNOWS'"
        ).fetchone()
        assert row is None

    async def test_upsert_node_merges_labels(self, store):
        await store.upsert_node("n1", {"A"}, {})
        await store.upsert_node("n1", {"B"}, {})
        assert store._node_buffer["n1"]["labels"] == {"A", "B"}

    async def test_upsert_node_merges_properties(self, store):
        await store.upsert_node("n1", set(), {"a": 1})
        await store.upsert_node("n1", set(), {"b": 2})
        props = store._node_buffer["n1"]["properties"]
        assert props == {"a": 1, "b": 2}

    async def test_upsert_edge_merges_properties(self, store):
        await store.upsert_edge("a", "b", "KNOWS", {"x": 1})
        await store.upsert_edge("a", "b", "KNOWS", {"y": 2})
        props = store._edge_buffer[("a", "b", "KNOWS")]["properties"]
        assert props == {"x": 1, "y": 2}


# ---------------------------------------------------------------------------
# TestBufferFirstReads
# ---------------------------------------------------------------------------
class TestBufferFirstReads:
    """get_node / get_edge must reflect buffered state."""

    async def test_get_node_returns_buffered_data(self, store):
        await store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "session_id": SESSION_ID,
                "iteration": 0,
                "prompt_text": "Hello, world!",
                "prompt_preview": "Hello, world!",
                "occurred_at": "2025-01-27T10:00:01Z",
            },
        )
        node = await store.get_node(PROMPT_NODE_ID)
        assert node is not None
        assert node["id"] == PROMPT_NODE_ID
        assert node["labels"] == {"Step", "PromptStep"}
        assert node["properties"]["prompt_text"] == "Hello, world!"

    async def test_get_edge_returns_buffered_data(self, store):
        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        edge = await store.get_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")
        assert edge is not None
        assert edge["source"] == SESSION_NODE_ID
        assert edge["target"] == RUN_NODE_ID
        assert edge["type"] == "HAS_RUN"
        assert edge["properties"] == {"seq": 1}

    async def test_get_nonexistent_node_returns_none(self, store):
        result = await store.get_node("nope")
        assert result is None

    async def test_get_nonexistent_edge_returns_none(self, store):
        result = await store.get_edge("x", "y", "NOPE")
        assert result is None

    async def test_buffer_wins_over_stale_duckdb(self, store):
        """Upsert after flush: buffer value should override stale DuckDB data."""
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await store.flush()
        # Now upsert a newer version into buffer
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "completed"},
        )
        node = await store.get_node(SESSION_NODE_ID)
        assert node is not None
        assert node["properties"]["status"] == "completed"


# ---------------------------------------------------------------------------
# TestFlush
# ---------------------------------------------------------------------------
class TestFlush:
    """flush() persists buffers to DuckDB and clears them."""

    async def test_flush_writes_nodes_to_duckdb(self, store):
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await store.flush()
        row = store._conn.execute(
            "SELECT node_id FROM nodes WHERE node_id = ?", [SESSION_NODE_ID]
        ).fetchone()
        assert row is not None
        assert row[0] == SESSION_NODE_ID

    async def test_flush_writes_edges_to_duckdb(self, store):
        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        await store.flush()
        row = store._conn.execute(
            "SELECT source, target, edge_type FROM edges "
            "WHERE source = ? AND target = ? AND edge_type = 'HAS_RUN'",
            [SESSION_NODE_ID, RUN_NODE_ID],
        ).fetchone()
        assert row is not None
        assert row == (SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")

    async def test_flush_clears_node_buffer(self, store):
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID},
        )
        await store.flush()
        assert len(store._node_buffer) == 0

    async def test_flush_clears_edge_buffer(self, store):
        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        await store.flush()
        assert len(store._edge_buffer) == 0

    async def test_get_node_from_duckdb_after_flush(self, store):
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await store.flush()
        # Buffer is empty now; read must come from DuckDB
        assert len(store._node_buffer) == 0
        node = await store.get_node(SESSION_NODE_ID)
        assert node is not None
        assert node["id"] == SESSION_NODE_ID
        assert node["labels"] == {"Session", "Root"}
        assert node["properties"]["session_id"] == SESSION_ID

    async def test_get_edge_from_duckdb_after_flush(self, store):
        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        await store.flush()
        assert len(store._edge_buffer) == 0
        edge = await store.get_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")
        assert edge is not None
        assert edge["source"] == SESSION_NODE_ID
        assert edge["target"] == RUN_NODE_ID
        assert edge["type"] == "HAS_RUN"
        assert edge["properties"] == {"seq": 1}

    async def test_flush_empty_buffer_is_noop(self, store):
        # Should not raise
        await store.flush()
        await store.flush()


# ---------------------------------------------------------------------------
# TestExecuteQuery
# ---------------------------------------------------------------------------
class TestExecuteQuery:
    """execute_query returns list of dicts and supports dialect validation."""

    async def test_execute_query_returns_list_of_dicts(self, store, seed_reference_graph):
        rows = await store.execute_query("SELECT node_id, labels FROM nodes")
        assert isinstance(rows, list)
        assert len(rows) == 4
        assert "node_id" in rows[0]
        assert "labels" in rows[0]

    def test_supported_dialects_returns_frozenset(self, store):
        dialects = store.supported_dialects
        assert isinstance(dialects, frozenset)
        assert "sql" in dialects

    async def test_execute_query_with_explicit_sql_dialect(self, store, seed_reference_graph):
        rows = await store.execute_query("SELECT node_id FROM nodes", dialect="sql")
        assert isinstance(rows, list)
        assert len(rows) == 4

    async def test_execute_query_with_none_dialect_uses_default(self, store, seed_reference_graph):
        rows = await store.execute_query("SELECT node_id FROM nodes", dialect=None)
        assert isinstance(rows, list)
        assert len(rows) == 4

    async def test_execute_query_with_params(self, store, seed_reference_graph):
        rows = await store.execute_query(
            "SELECT node_id FROM nodes WHERE node_id = $node_id",
            params={"node_id": SESSION_NODE_ID},
        )
        assert len(rows) == 1
        assert rows[0]["node_id"] == SESSION_NODE_ID

    async def test_execute_query_with_invalid_dialect_raises(self, store):
        with pytest.raises(ValueError, match="Unsupported dialect"):
            await store.execute_query("SELECT 1", dialect="cypher")


# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------
class TestClose:
    """close() must flush before closing the connection."""

    async def test_close_flushes_before_closing(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        db_path = tmp_path / "close_test.db"
        store = DuckDBGraphStore(connection=str(db_path))
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await store.close()

        # Reopen and verify data was persisted
        import duckdb

        conn = duckdb.connect(str(db_path))
        row = conn.execute(
            "SELECT node_id FROM nodes WHERE node_id = ?", [SESSION_NODE_ID]
        ).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == SESSION_NODE_ID


# ---------------------------------------------------------------------------
# TestPersistence
# ---------------------------------------------------------------------------
class TestPersistence:
    """Data must survive close and reopen."""

    async def test_data_survives_close_and_reopen(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        db_path = tmp_path / "persist_test.db"

        # Write data and close
        store = DuckDBGraphStore(connection=str(db_path))
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        await store.close()

        # Reopen and read back
        store2 = DuckDBGraphStore(connection=str(db_path))
        node = await store2.get_node(SESSION_NODE_ID)
        assert node is not None
        assert node["id"] == SESSION_NODE_ID
        assert node["labels"] == {"Session", "Root"}

        edge = await store2.get_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN")
        assert edge is not None
        assert edge["source"] == SESSION_NODE_ID
        assert edge["target"] == RUN_NODE_ID
        assert edge["type"] == "HAS_RUN"
        assert edge["properties"] == {"seq": 1}
        await store2.close()


# ---------------------------------------------------------------------------
# TestSearchIndexTable
# ---------------------------------------------------------------------------
class TestSearchIndexTable:
    """Verify search_index table creation and schema."""

    def test_search_index_table_created_on_init(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        result = store._conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = 'search_index'"
        ).fetchall()
        assert len(result) == 1
        assert result[0][0] == "search_index"

    def test_search_index_has_expected_columns(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        result = store._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'search_index' ORDER BY ordinal_position"
        ).fetchall()
        column_names = [row[0] for row in result]
        assert column_names == ["node_id", "session_id", "field_name", "content", "occurred_at"]

    def test_search_buffer_exists_and_empty_on_init(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        assert hasattr(store, "_search_buffer")
        assert store._search_buffer == []
        assert isinstance(store._search_buffer, list)


# ---------------------------------------------------------------------------
# TestSearchIndexFlush
# ---------------------------------------------------------------------------
def _make_search_entry(
    node_id: str = "n1",
    session_id: str = "sess1",
    field_name: str = "summary",
    content: str = "hello world",
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Build a search_index entry dict with sensible defaults."""
    return {
        "node_id": node_id,
        "session_id": session_id,
        "field_name": field_name,
        "content": content,
        "occurred_at": occurred_at,
    }


class TestSearchIndexFlush:
    """flush() persists search buffer entries to DuckDB search_index table."""

    async def test_flush_writes_search_entries_to_duckdb(self, store):
        store._search_buffer.append(_make_search_entry())
        await store.flush()
        row = store._conn.execute(
            "SELECT node_id, session_id, field_name, content FROM search_index WHERE node_id = 'n1'"
        ).fetchone()
        assert row is not None
        assert row[0] == "n1"
        assert row[1] == "sess1"
        assert row[2] == "summary"
        assert row[3] == "hello world"

    async def test_flush_clears_search_buffer(self, store):
        store._search_buffer.append(_make_search_entry())
        await store.flush()
        assert store._search_buffer == []

    async def test_flush_empty_search_buffer_is_noop(self, store):
        # All buffers empty - should not raise
        await store.flush()
        await store.flush()
        rows = store._conn.execute("SELECT * FROM search_index").fetchall()
        assert rows == []

    async def test_flush_writes_multiple_search_entries(self, store):
        store._search_buffer.append(_make_search_entry(node_id="n1", content="first"))
        store._search_buffer.append(
            _make_search_entry(
                node_id="n2", session_id="sess2", field_name="description", content="second"
            )
        )
        await store.flush()
        rows = store._conn.execute(
            "SELECT node_id, field_name, content FROM search_index ORDER BY node_id"
        ).fetchall()
        assert len(rows) == 2
        assert rows[0] == ("n1", "summary", "first")
        assert rows[1] == ("n2", "description", "second")

    async def test_flush_restores_search_buffer_on_failure(self, store):
        entry = _make_search_entry(content="hello")
        store._search_buffer.append(entry)

        # DuckDB C extension's execute is read-only, so we wrap the connection
        original_conn = store._conn

        class FailingConn:
            """Proxy that raises on search_index INSERT."""

            def __getattr__(self, name):
                return getattr(original_conn, name)

            def execute(self, sql, *args, **kwargs):
                if sql.startswith("INSERT") and "search_index" in sql:
                    raise RuntimeError("simulated failure")
                return original_conn.execute(sql, *args, **kwargs)

        store._conn = FailingConn()
        await store.flush()
        store._conn = original_conn  # restore for cleanup

        assert len(store._search_buffer) == 1
        assert store._search_buffer[0] == entry


# ---------------------------------------------------------------------------
# TestSearchIndexAutoPopulate
# ---------------------------------------------------------------------------
class TestSearchIndexAutoPopulate:
    """upsert_node auto-populates _search_buffer for indexable PromptStep nodes."""

    async def test_promptstep_with_prompt_text_populates_search_buffer(self, store):
        """PromptStep with prompt_text populates search buffer with correct fields."""
        await store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "prompt_text": "Hello, world!",
                "session_id": SESSION_ID,
                "occurred_at": "2025-01-27T10:00:01Z",
            },
        )
        assert len(store._search_buffer) == 1
        entry = store._search_buffer[0]
        assert entry["node_id"] == PROMPT_NODE_ID
        assert entry["session_id"] == SESSION_ID
        assert entry["field_name"] == "prompt_text"
        assert entry["content"] == "Hello, world!"
        assert entry["occurred_at"] == "2025-01-27T10:00:01Z"

    async def test_session_node_does_not_populate_search_buffer(self, store):
        """Session node does NOT populate search buffer."""
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "prompt_text": "should not matter"},
        )
        assert len(store._search_buffer) == 0

    async def test_promptstep_without_prompt_text_does_not_populate(self, store):
        """PromptStep WITHOUT prompt_text does NOT populate search buffer."""
        await store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {"session_id": SESSION_ID, "occurred_at": "2025-01-27T10:00:01Z"},
        )
        assert len(store._search_buffer) == 0

    async def test_promptstep_with_empty_prompt_text_does_not_populate(self, store):
        """PromptStep with EMPTY prompt_text does NOT populate search buffer."""
        await store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "prompt_text": "",
                "session_id": SESSION_ID,
                "occurred_at": "2025-01-27T10:00:01Z",
            },
        )
        assert len(store._search_buffer) == 0

    async def test_auto_populate_flows_through_to_duckdb_on_flush(self, store):
        """Auto-populated entries flow through flush() to DuckDB."""
        await store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "prompt_text": "Hello, world!",
                "session_id": SESSION_ID,
                "occurred_at": "2025-01-27T10:00:01Z",
            },
        )
        await store.flush()
        row = store._conn.execute(
            "SELECT node_id, session_id, field_name, content FROM search_index WHERE node_id = ?",
            [PROMPT_NODE_ID],
        ).fetchone()
        assert row is not None
        assert row[0] == PROMPT_NODE_ID
        assert row[1] == SESSION_ID
        assert row[2] == "prompt_text"
        assert row[3] == "Hello, world!"

    async def test_upsert_existing_node_does_not_duplicate_search_entry(self, store):
        """Second upsert to same node_id does NOT add duplicate search entry."""
        await store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "prompt_text": "Hello, world!",
                "session_id": SESSION_ID,
                "occurred_at": "2025-01-27T10:00:01Z",
            },
        )
        assert len(store._search_buffer) == 1
        # Second upsert to same node_id - should NOT add another entry
        await store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {"prompt_text": "Updated prompt"},
        )
        assert len(store._search_buffer) == 1


# ---------------------------------------------------------------------------
# TestStandingRuleDocstring
# ---------------------------------------------------------------------------
class TestStandingRuleDocstring:
    """Module docstring must contain the standing rule for skill synchronization."""

    def test_docstring_contains_standing_rule_section(self):
        import amplifier_module_hook_context_intelligence.duckdb_store as mod

        doc = mod.__doc__
        assert doc is not None, "Module docstring must not be None"
        assert "STANDING RULE" in doc

    def test_docstring_references_skill_path(self):
        import amplifier_module_hook_context_intelligence.duckdb_store as mod

        doc = mod.__doc__
        assert doc is not None
        assert "skills/context-intelligence-graph-search/SKILL.md" in doc

    def test_docstring_lists_all_schema_triggers(self):
        import amplifier_module_hook_context_intelligence.duckdb_store as mod

        doc = mod.__doc__
        assert doc is not None
        required_triggers = [
            "tables",
            "columns",
            "property graph definition",
            "search_index",
            "FTS indexes",
            "new label types",
            "new edge types",
            "new field_name values",
            "_INDEXABLE_FIELDS",
        ]
        for trigger in required_triggers:
            assert trigger in doc, f"Docstring missing trigger: {trigger!r}"

    def test_docstring_preserves_original_description(self):
        import amplifier_module_hook_context_intelligence.duckdb_store as mod

        doc = mod.__doc__
        assert doc is not None
        assert "DuckDBGraphStore" in doc
        assert "buffer-first reads with async DuckDB persistence" in doc


# ---------------------------------------------------------------------------
# TestPGQ — DuckPGQ property graph overlay (lazy init + structural queries)
# ---------------------------------------------------------------------------
class TestPGQ:
    """PGQ dialect support via DuckPGQ: lazy property graph creation and GRAPH_TABLE queries."""

    def test_pgq_in_supported_dialects(self, store):
        """'pgq' must be listed in supported_dialects."""
        assert "pgq" in store.supported_dialects

    async def test_ensure_pgq_creates_property_graph(self, store, seed_reference_graph):
        """_ensure_pgq() must create the 'context_graph' property graph queryable via GRAPH_TABLE."""
        store._ensure_pgq()
        result = store._conn.execute(
            f"""
            SELECT session_id, step_id
            FROM GRAPH_TABLE(context_graph
                MATCH (s:Session)-[hr:HAS_RUN]->(r:Session)-[hs:HAS_STEP]->(step:Session)
                WHERE s.node_id = '{SESSION_NODE_ID}'
                COLUMNS (s.node_id AS session_id, step.node_id AS step_id)
            )
            """
        ).fetchall()
        assert len(result) >= 1

    async def test_pgq_dialect_triggers_ensure_pgq(self, store, seed_reference_graph):
        """execute_query with dialect='pgq' auto-calls _ensure_pgq and runs GRAPH_TABLE query."""
        rows = await store.execute_query(
            f"""
            SELECT session_id, step_id
            FROM GRAPH_TABLE(context_graph
                MATCH (s:Session)-[hr:HAS_RUN]->(r:Session)-[hs:HAS_STEP]->(step:Session)
                WHERE s.node_id = '{SESSION_NODE_ID}'
                COLUMNS (s.node_id AS session_id, step.node_id AS step_id)
            )
            """,
            dialect="pgq",
        )
        assert rows[0]["step_id"] == PROMPT_NODE_ID

    async def test_pgq_structural_query_steps_in_session(self, store, seed_reference_graph):
        """Find all steps in a session's runs via GRAPH_TABLE."""
        rows = await store.execute_query(
            f"""
            SELECT step_id
            FROM GRAPH_TABLE(context_graph
                MATCH (s:Session)-[hr:HAS_RUN]->(r:Session)-[hs:HAS_STEP]->(step:Session)
                WHERE s.node_id = '{SESSION_NODE_ID}'
                COLUMNS (step.node_id AS step_id)
            )
            """,
            dialect="pgq",
        )
        step_ids = [r["step_id"] for r in rows]
        assert PROMPT_NODE_ID in step_ids

    async def test_pgq_triggered_tools_query(self, store, seed_reference_graph):
        """Query tools triggered by a step via TRIGGERED edge in GRAPH_TABLE."""
        rows = await store.execute_query(
            f"""
            SELECT tool_id
            FROM GRAPH_TABLE(context_graph
                MATCH (step:Session)-[t:TRIGGERED]->(tool:Session)
                WHERE step.node_id = '{PROMPT_NODE_ID}'
                COLUMNS (tool.node_id AS tool_id)
            )
            """,
            dialect="pgq",
        )
        tool_ids = [r["tool_id"] for r in rows]
        assert TOOL_NODE_ID in tool_ids

    async def test_ensure_pgq_idempotent(self, store, seed_reference_graph):
        """Calling _ensure_pgq() twice must not raise and GRAPH_TABLE must still work."""
        store._ensure_pgq()
        store._ensure_pgq()
        result = store._conn.execute(
            """
            SELECT session_id
            FROM GRAPH_TABLE(context_graph
                MATCH (s:Session)-[hr:HAS_RUN]->(r:Session)
                COLUMNS (s.node_id AS session_id)
            )
            """
        ).fetchall()
        assert len(result) >= 1
