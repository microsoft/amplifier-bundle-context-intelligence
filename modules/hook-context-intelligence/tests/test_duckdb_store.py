"""Tests for DuckDBGraphStore – buffer-first reads with async DuckDB persistence."""

from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# TestRunUsesGetRunningLoop
# ---------------------------------------------------------------------------
class TestRunUsesGetRunningLoop:
    """_run must use asyncio.get_running_loop(), not the deprecated get_event_loop()."""

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
            "WHERE table_schema = 'main' AND table_name IN ('nodes', 'edges') "
            "ORDER BY table_name"
        ).fetchall()
        table_names = [row[0] for row in result]
        assert "edges" in table_names
        assert "nodes" in table_names


# ---------------------------------------------------------------------------
# TestBufferWrites
# ---------------------------------------------------------------------------
class TestBufferWrites:
    """upsert_node / upsert_edge write to in-memory buffers only."""

    @pytest.fixture
    def store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        return DuckDBGraphStore()

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

    @pytest.fixture
    def store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        return DuckDBGraphStore()

    async def test_get_node_returns_buffered_data(self, store):
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        node = await store.get_node("n1")
        assert node is not None
        assert node["id"] == "n1"
        assert node["labels"] == {"Person"}
        assert node["properties"] == {"name": "Alice"}

    async def test_get_edge_returns_buffered_data(self, store):
        await store.upsert_edge("a", "b", "KNOWS", {"since": 2020})
        edge = await store.get_edge("a", "b", "KNOWS")
        assert edge is not None
        assert edge["source"] == "a"
        assert edge["target"] == "b"
        assert edge["type"] == "KNOWS"
        assert edge["properties"] == {"since": 2020}

    async def test_get_nonexistent_node_returns_none(self, store):
        result = await store.get_node("nope")
        assert result is None

    async def test_get_nonexistent_edge_returns_none(self, store):
        result = await store.get_edge("x", "y", "NOPE")
        assert result is None

    async def test_buffer_wins_over_stale_duckdb(self, store):
        """Upsert after flush: buffer value should override stale DuckDB data."""
        await store.upsert_node("n1", {"V1"}, {"version": 1})
        await store.flush()
        # Now upsert a newer version into buffer
        await store.upsert_node("n1", {"V2"}, {"version": 2})
        node = await store.get_node("n1")
        assert node is not None
        assert node["labels"] == {"V2"}
        assert node["properties"] == {"version": 2}


# ---------------------------------------------------------------------------
# TestFlush
# ---------------------------------------------------------------------------
class TestFlush:
    """flush() persists buffers to DuckDB and clears them."""

    @pytest.fixture
    def store(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        return DuckDBGraphStore()

    async def test_flush_writes_nodes_to_duckdb(self, store):
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await store.flush()
        row = store._conn.execute("SELECT node_id FROM nodes WHERE node_id = 'n1'").fetchone()
        assert row is not None
        assert row[0] == "n1"

    async def test_flush_writes_edges_to_duckdb(self, store):
        await store.upsert_edge("a", "b", "KNOWS", {"w": 1})
        await store.flush()
        row = store._conn.execute(
            "SELECT source, target, edge_type FROM edges "
            "WHERE source = 'a' AND target = 'b' AND edge_type = 'KNOWS'"
        ).fetchone()
        assert row is not None
        assert row == ("a", "b", "KNOWS")

    async def test_flush_clears_both_buffers(self, store):
        await store.upsert_node("n1", {"X"}, {})
        await store.upsert_edge("a", "b", "R", {})
        await store.flush()
        assert len(store._node_buffer) == 0
        assert len(store._edge_buffer) == 0

    async def test_get_node_from_duckdb_after_flush(self, store):
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await store.flush()
        # Buffer is empty now; read must come from DuckDB
        assert len(store._node_buffer) == 0
        node = await store.get_node("n1")
        assert node is not None
        assert node["id"] == "n1"
        assert node["labels"] == {"Person"}
        assert node["properties"] == {"name": "Alice"}

    async def test_get_edge_from_duckdb_after_flush(self, store):
        await store.upsert_edge("a", "b", "KNOWS", {"w": 1})
        await store.flush()
        assert len(store._edge_buffer) == 0
        edge = await store.get_edge("a", "b", "KNOWS")
        assert edge is not None
        assert edge["source"] == "a"
        assert edge["target"] == "b"
        assert edge["type"] == "KNOWS"
        assert edge["properties"] == {"w": 1}

    async def test_flush_empty_buffer_is_noop(self, store):
        # Should not raise
        await store.flush()
        await store.flush()


# ---------------------------------------------------------------------------
# TestExecuteQuery
# ---------------------------------------------------------------------------
class TestExecuteQuery:
    """execute_query returns list of dicts using column names."""

    async def test_execute_query_returns_list_of_dicts(self):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        store = DuckDBGraphStore()
        await store.upsert_node("n1", {"Person"}, {"name": "Alice"})
        await store.flush()
        rows = await store.execute_query("SELECT node_id, labels FROM nodes")
        assert isinstance(rows, list)
        assert len(rows) == 1
        assert "node_id" in rows[0]
        assert "labels" in rows[0]
        assert rows[0]["node_id"] == "n1"


# ---------------------------------------------------------------------------
# TestClose
# ---------------------------------------------------------------------------
class TestClose:
    """close() must flush before closing the connection."""

    async def test_close_flushes_before_closing(self, tmp_path: Path):
        from amplifier_module_hook_context_intelligence.duckdb_store import DuckDBGraphStore

        db_path = tmp_path / "close_test.db"
        store = DuckDBGraphStore(connection=str(db_path))
        await store.upsert_node("n1", {"X"}, {"val": 42})
        await store.close()

        # Reopen and verify data was persisted
        import duckdb

        conn = duckdb.connect(str(db_path))
        row = conn.execute("SELECT node_id FROM nodes WHERE node_id = 'n1'").fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "n1"


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
        await store.upsert_node("n1", {"Person"}, {"name": "Bob"})
        await store.upsert_edge("n1", "n2", "KNOWS", {"since": 2021})
        await store.close()

        # Reopen and read back
        store2 = DuckDBGraphStore(connection=str(db_path))
        node = await store2.get_node("n1")
        assert node is not None
        assert node["id"] == "n1"
        assert node["labels"] == {"Person"}
        assert node["properties"] == {"name": "Bob"}

        edge = await store2.get_edge("n1", "n2", "KNOWS")
        assert edge is not None
        assert edge["source"] == "n1"
        assert edge["target"] == "n2"
        assert edge["type"] == "KNOWS"
        assert edge["properties"] == {"since": 2021}
        await store2.close()
