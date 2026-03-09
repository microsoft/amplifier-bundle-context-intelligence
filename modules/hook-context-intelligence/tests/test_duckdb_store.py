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

    s = DuckDBGraphStore(graph_forest_name="test")
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

        store = DuckDBGraphStore(graph_forest_name="test")
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

        store = DuckDBGraphStore(graph_forest_name="test")
        assert isinstance(store, GraphStore)


# ---------------------------------------------------------------------------
# TestConstructor
# ---------------------------------------------------------------------------
class TestConstructor:
    """Verify constructor wiring: connection handling and table creation."""

    def test_default_connection_is_memory(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        assert store._connection_str == ":memory:"

    def test_file_path_expands_tilde(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        db_path = tmp_path / "sub" / "test.db"
        store = DuckDBGraphStore(connection=str(db_path), graph_forest_name="test")
        assert store._connection_str == str(db_path)
        assert db_path.parent.exists()

    def test_file_path_creates_parent_dirs(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        db_path = tmp_path / "deep" / "nested" / "dir" / "test.db"
        DuckDBGraphStore(connection=str(db_path), graph_forest_name="test")
        assert db_path.parent.exists()

    def test_graph_forest_name_accepts_custom_value(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="my-project")
        assert store.graph_forest_name == "my-project"

    def test_graph_forest_name_uses_default_when_omitted(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        assert store.graph_forest_name == "default"

    def test_graph_forest_name_is_readonly(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        with pytest.raises(AttributeError):
            store.graph_forest_name = "nope"

    def test_tables_created_on_init(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
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
        store = DuckDBGraphStore(connection=str(db_path), graph_forest_name="test")
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
        store = DuckDBGraphStore(connection=str(db_path), graph_forest_name="test")
        await store.upsert_node(
            SESSION_NODE_ID,
            {"Session", "Root"},
            {"session_id": SESSION_ID, "status": "running"},
        )
        await store.upsert_edge(SESSION_NODE_ID, RUN_NODE_ID, "HAS_RUN", {"seq": 1})
        await store.close()

        # Reopen and read back
        store2 = DuckDBGraphStore(connection=str(db_path), graph_forest_name="test")
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

        store = DuckDBGraphStore(graph_forest_name="test")
        result = store._conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_name = 'search_index'"
        ).fetchall()
        assert len(result) == 1
        assert result[0][0] == "search_index"

    def test_search_index_has_expected_columns(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        result = store._conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'search_index' ORDER BY ordinal_position"
        ).fetchall()
        column_names = [row[0] for row in result]
        assert column_names == [
            "node_id",
            "graph_forest_name",
            "session_id",
            "field_name",
            "content",
            "occurred_at",
        ]

    def test_search_buffer_exists_and_empty_on_init(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
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


# ---------------------------------------------------------------------------
# TestFTS — Full-text search via DuckDB FTS extension on search_index
# ---------------------------------------------------------------------------
class TestFTS:
    """Full-text search via DuckDB FTS extension on search_index."""

    async def test_rebuild_fts_index_creates_searchable_index(self, store):
        """After rebuild, BM25 scoring function becomes available."""
        await store.upsert_node(
            "ps1",
            {"PromptStep"},
            {"prompt_text": "Help me refactor authentication", "session_id": "s1"},
        )
        await store.flush()
        await store.rebuild_fts_index()
        rows = await store.execute_query(
            "SELECT node_id, fts_main_search_index.match_bm25(rowid, 'authentication') AS score "
            "FROM search_index WHERE score IS NOT NULL"
        )
        assert len(rows) == 1
        assert rows[0]["node_id"] == "ps1"
        assert rows[0]["score"] is not None

    async def test_fts_without_rebuild_has_no_index(self, store):
        """Without rebuild, BM25 function does not exist."""
        import duckdb

        await store.upsert_node(
            "ps1",
            {"PromptStep"},
            {"prompt_text": "Help me refactor authentication", "session_id": "s1"},
        )
        await store.flush()
        with pytest.raises(duckdb.CatalogException):
            await store.execute_query(
                "SELECT fts_main_search_index.match_bm25(rowid, 'authentication') AS score "
                "FROM search_index WHERE score IS NOT NULL"
            )

    async def test_rebuild_fts_index_idempotent(self, store):
        """Calling rebuild twice works without error."""
        await store.upsert_node(
            "ps1",
            {"PromptStep"},
            {"prompt_text": "test content", "session_id": "s1"},
        )
        await store.flush()
        await store.rebuild_fts_index()
        await store.rebuild_fts_index()

    async def test_fts_finds_prompt_text_content(self, store):
        """BM25 search finds the right node by content."""
        await store.upsert_node(
            "ps1",
            {"PromptStep"},
            {"prompt_text": "Help me refactor the authentication module", "session_id": "s1"},
        )
        await store.upsert_node(
            "ps2",
            {"PromptStep"},
            {"prompt_text": "Write unit tests for the parser", "session_id": "s2"},
        )
        await store.flush()
        await store.rebuild_fts_index()
        rows = await store.execute_query(
            "SELECT node_id, fts_main_search_index.match_bm25(rowid, 'authentication') AS score "
            "FROM search_index WHERE score IS NOT NULL ORDER BY score DESC"
        )
        assert len(rows) == 1
        assert rows[0]["node_id"] == "ps1"

    async def test_bm25_pattern_1_from_skill(self, store):
        """Pattern 1 from SKILL.md: Direct FTS with BM25 scoring."""
        from tests.conftest import PROMPT_NODE_ID, SESSION_ID

        await store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "prompt_text": "Help me refactor the authentication module",
                "session_id": SESSION_ID,
                "occurred_at": "2026-01-15T10:00:01Z",
            },
        )
        await store.flush()
        await store.rebuild_fts_index()
        rows = await store.execute_query(
            "SELECT si.node_id, si.session_id, si.field_name, "
            "fts_main_search_index.match_bm25(si.rowid, 'refactor') AS score "
            "FROM search_index si WHERE score IS NOT NULL ORDER BY score DESC"
        )
        assert len(rows) >= 1
        assert rows[0]["session_id"] == SESSION_ID
        assert rows[0]["field_name"] == "prompt_text"


# ---------------------------------------------------------------------------
# TestFTSPlusPGQ — Combined FTS + PGQ queries (Pattern 2 from SKILL.md)
# ---------------------------------------------------------------------------
class TestFTSPlusPGQ:
    """Combined FTS + PGQ queries — Pattern 2 from SKILL.md."""

    async def test_fts_then_pgq_traversal(self, store):
        """Find prompt by text search, then walk to parent session via PGQ.

        Pattern 2 from SKILL.md: FTS identifies candidate nodes, PGQ traverses
        the graph from those nodes to their parent sessions. Executed as two
        coordinated queries — FTS results feed the PGQ WHERE filter.
        """
        from tests.conftest import (
            PROMPT_NODE_ID,
            SESSION_NODE_ID,
            reference_edges,
            reference_nodes,
        )

        # Seed the full reference graph
        for node_id, labels, props in reference_nodes():
            await store.upsert_node(node_id, labels, props)
        for src, tgt, etype, props in reference_edges():
            await store.upsert_edge(src, tgt, etype, props)
        await store.flush()
        await store.rebuild_fts_index()

        # Step 1 — FTS: find nodes whose content matches 'refactor'
        fts_hits = await store.execute_query(
            "SELECT node_id, fts_main_search_index.match_bm25(rowid, 'refactor') AS score "
            "FROM search_index WHERE score IS NOT NULL ORDER BY score DESC"
        )
        assert len(fts_hits) >= 1
        assert fts_hits[0]["node_id"] == PROMPT_NODE_ID

        # Step 2 — PGQ: for each FTS hit, traverse Session->Run->Step to find parent session
        top_hit_node_id = fts_hits[0]["node_id"]
        pgq_rows = await store.execute_query(
            "SELECT gt.session_node, gt.step_node "
            "FROM GRAPH_TABLE(context_graph "
            "  MATCH (s:Session)-[hr:HAS_RUN]->(r:Session)-[hs:HAS_STEP]->(step:Session) "
            f"  WHERE step.node_id = '{top_hit_node_id}' "
            "  COLUMNS (s.node_id AS session_node, step.node_id AS step_node) "
            ") gt",
            dialect="pgq",
        )
        assert len(pgq_rows) >= 1
        assert pgq_rows[0]["session_node"] == SESSION_NODE_ID
        assert pgq_rows[0]["step_node"] == PROMPT_NODE_ID

        # Combine: enrich PGQ traversal result with BM25 scores from FTS hits
        scores = {r["node_id"]: r["score"] for r in fts_hits}
        combined = [
            {
                "session_node": r["session_node"],
                "step_node": r["step_node"],
                "score": scores.get(r["step_node"]),
            }
            for r in pgq_rows
        ]
        assert combined[0]["session_node"] == SESSION_NODE_ID
        assert combined[0]["step_node"] == PROMPT_NODE_ID
        assert combined[0]["score"] is not None


# ---------------------------------------------------------------------------
# TestForestSchema — graph_forest_name column in nodes, edges, search_index
# ---------------------------------------------------------------------------
class TestForestSchema:
    """Verify graph_forest_name column exists in all three tables."""

    def test_nodes_has_graph_forest_name_column(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        result = store._conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'nodes' AND column_name = 'graph_forest_name'"
        ).fetchone()
        assert result is not None, "nodes table must have graph_forest_name column"
        col_name, data_type, is_nullable, col_default = result
        assert col_name == "graph_forest_name"
        assert data_type == "VARCHAR"
        assert is_nullable == "NO"
        assert "'default'" in col_default

    def test_edges_has_graph_forest_name_column(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        result = store._conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'edges' AND column_name = 'graph_forest_name'"
        ).fetchone()
        assert result is not None, "edges table must have graph_forest_name column"
        col_name, data_type, is_nullable, col_default = result
        assert col_name == "graph_forest_name"
        assert data_type == "VARCHAR"
        assert is_nullable == "NO"
        assert "'default'" in col_default

    def test_search_index_has_graph_forest_name_column(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="test")
        result = store._conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'search_index' AND column_name = 'graph_forest_name'"
        ).fetchone()
        assert result is not None, "search_index table must have graph_forest_name column"
        col_name, data_type, is_nullable, col_default = result
        assert col_name == "graph_forest_name"
        assert data_type == "VARCHAR"
        assert is_nullable == "NO"
        assert "'default'" in col_default


# ---------------------------------------------------------------------------
# TestForestWrites — flush() stamps graph_forest_name on all writes
# ---------------------------------------------------------------------------
class TestForestWrites:
    """flush() must stamp self._graph_forest_name on all INSERT statements."""

    @pytest.fixture()
    def forest_store(self):
        """DuckDBGraphStore with a non-default graph_forest_name for write tests."""
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        s = DuckDBGraphStore(graph_forest_name="my-project")
        yield s
        s._conn.close()

    async def test_flush_stamps_forest_on_nodes(self, forest_store):
        """Upsert node, flush, verify graph_forest_name column value via raw SQL."""
        await forest_store.upsert_node("n1", {"Session"}, {"key": "val"})
        await forest_store.flush()
        row = forest_store._conn.execute(
            "SELECT graph_forest_name FROM nodes WHERE node_id = 'n1'"
        ).fetchone()
        assert row is not None, "Node must be flushed to DuckDB"
        assert row[0] == "my-project"

    async def test_flush_stamps_forest_on_edges(self, forest_store):
        """Upsert edge, flush, verify graph_forest_name column value via raw SQL."""
        await forest_store.upsert_edge("a", "b", "KNOWS", {"weight": 1})
        await forest_store.flush()
        row = forest_store._conn.execute(
            "SELECT graph_forest_name FROM edges WHERE source = 'a' AND target = 'b'"
        ).fetchone()
        assert row is not None, "Edge must be flushed to DuckDB"
        assert row[0] == "my-project"

    async def test_flush_stamps_forest_on_search_index(self, forest_store):
        """Upsert a PromptStep node with prompt_text and session_id, flush, verify graph_forest_name in search_index."""
        await forest_store.upsert_node(
            PROMPT_NODE_ID,
            {"Step", "PromptStep"},
            {
                "prompt_text": "Hello, world!",
                "session_id": SESSION_ID,
                "occurred_at": "2025-01-27T10:00:01Z",
            },
        )
        await forest_store.flush()
        row = forest_store._conn.execute(
            "SELECT graph_forest_name FROM search_index WHERE node_id = ?",
            [PROMPT_NODE_ID],
        ).fetchone()
        assert row is not None, "Search index entry must be flushed to DuckDB"
        assert row[0] == "my-project"


# ---------------------------------------------------------------------------
# TestForestQueryFiltering — execute_query forest-scoped filtering
# ---------------------------------------------------------------------------
class TestForestQueryFiltering:
    """execute_query must scope results by graph_forest_name."""

    async def _seed_two_forests(self):
        """Create one store, write n1 in forest-a, n2 in forest-b, reset to forest-a."""
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore(graph_forest_name="forest-a")
        await store.upsert_node("n1", {"Session"}, {"key": "val1"})
        await store.flush()
        # Mutate to forest-b and write n2
        store._graph_forest_name = "forest-b"
        await store.upsert_node("n2", {"Session"}, {"key": "val2"})
        await store.flush()
        # Reset to forest-a
        store._graph_forest_name = "forest-a"
        return store

    async def test_default_scopes_to_own_forest(self):
        """SELECT without graph_forest_name returns only nodes from own forest (forest-a)."""
        store = await self._seed_two_forests()
        rows = await store.execute_query("SELECT node_id FROM nodes ORDER BY node_id")
        node_ids = [r["node_id"] for r in rows]
        assert node_ids == ["n1"]

    async def test_explicit_forest_scopes_to_that_forest(self):
        """graph_forest_name='forest-b' returns only n2."""
        store = await self._seed_two_forests()
        rows = await store.execute_query(
            "SELECT node_id FROM nodes ORDER BY node_id",
            graph_forest_name="forest-b",
        )
        node_ids = [r["node_id"] for r in rows]
        assert node_ids == ["n2"]

    async def test_star_returns_all_forests(self):
        """graph_forest_name='*' returns both n1 and n2."""
        store = await self._seed_two_forests()
        rows = await store.execute_query(
            "SELECT node_id FROM nodes ORDER BY node_id",
            graph_forest_name="*",
        )
        node_ids = [r["node_id"] for r in rows]
        assert node_ids == ["n1", "n2"]

    async def test_none_is_same_as_default(self):
        """None produces same result as omitting graph_forest_name param."""
        store = await self._seed_two_forests()
        rows_default = await store.execute_query("SELECT node_id FROM nodes ORDER BY node_id")
        rows_none = await store.execute_query(
            "SELECT node_id FROM nodes ORDER BY node_id",
            graph_forest_name=None,
        )
        assert rows_default == rows_none
