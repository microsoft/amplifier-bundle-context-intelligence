"""Tests for GraphDataHook — thin orchestrator wrapping MountFlow + Neo4jGraphStore."""

from __future__ import annotations

import asyncio
import importlib.util
from typing import Any, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver
from amplifier_module_hook_context_intelligence.graph_data_hook import (
    GraphDataHook,
    _create_neo4j_store,
)
from amplifier_module_hook_context_intelligence.mount import MountState

_NEO4J_STORE_CONFIG: dict[str, Any] = {
    "graph_store": {
        "type": "neo4j",
        "graph_forest_name": "default",
        "config": {
            "uri": "neo4j://localhost:7687",
            "username": "neo4j",
            "password": "test",
            "database": "neo4j",
        },
    }
}


def _make_coordinator(
    contributed_events: list[list[str]] | None = None,
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.config = {}
    unregister_fns: list[MagicMock] = []

    def _register_side_effect(*args, **kwargs):
        unreg = MagicMock()
        unregister_fns.append(unreg)
        return unreg

    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(side_effect=_register_side_effect)
    coordinator._unregister_fns = unregister_fns

    if contributed_events is None:
        contributed_events = []
    coordinator.collect_contributions = AsyncMock(return_value=contributed_events)
    coordinator.get_capability = MagicMock(return_value=None)

    return coordinator


def _make_resolver(config: dict[str, Any]) -> ConfigResolver:
    """Create a ConfigResolver with a MagicMock coordinator (config={})."""
    coordinator = MagicMock()
    coordinator.config = {}
    return ConfigResolver(config, coordinator)


@pytest.fixture
def mock_neo4j_store():
    """Mock Neo4jGraphStore to avoid real Neo4j connections in unit tests."""
    mock_store = MagicMock()
    mock_store.close = AsyncMock()
    with patch(
        "amplifier_module_hook_context_intelligence.graph_data_hook.Neo4jGraphStore",
        return_value=mock_store,
    ) as mock_cls:
        yield mock_cls, mock_store


class TestCreateNeo4jStore:
    """_create_neo4j_store reads from resolver.neo4j_config and resolver.forest_name."""

    def test_creates_neo4j_store_from_resolver(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        result = _create_neo4j_store(resolver)
        assert result is mock_store

    def test_passes_uri_from_resolver(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["uri"] == "neo4j://localhost:7687"

    def test_passes_auth_tuple_from_resolver(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["auth"] == ("neo4j", "test")

    def test_passes_database_from_resolver(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["database"] == "neo4j"

    def test_does_not_pass_forest_name_at_construction(self, mock_neo4j_store):
        """forest_name is NOT passed at construction — resolved lazily at event time."""
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert "graph_forest_name" not in call_kwargs, (
            "graph_forest_name should not be passed at construction; "
            "it is resolved lazily from coordinator runtime data at event time"
        )

    def test_raises_when_no_neo4j_config(self, mock_neo4j_store):
        """ValueError when neo4j_config is None (no graph_store in config)."""
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver({})
        with pytest.raises(ValueError):
            _create_neo4j_store(resolver)

    def test_auth_is_none_when_credentials_absent(self, mock_neo4j_store):
        """When username/password are absent, auth should be None."""
        config = {
            "graph_store": {
                "config": {
                    "uri": "bolt://localhost:7687",
                    # no username, no password
                }
            }
        }
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(config)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["auth"] is None

    def test_database_defaults_to_neo4j_when_absent(self, mock_neo4j_store):
        """When database key is absent, it should default to 'neo4j'."""
        config = {
            "graph_store": {
                "config": {
                    "uri": "bolt://localhost:7687",
                    "username": "neo4j",
                    "password": "test",
                    # no database key
                }
            }
        }
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(config)
        _create_neo4j_store(resolver)
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["database"] == "neo4j"


class TestGraphDataHookInit:
    """GraphDataHook.__init__ accepts a resolver and creates Neo4jGraphStore."""

    def test_creates_neo4j_store_not_composite(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        hook = GraphDataHook(resolver)
        assert hook._store is mock_store

    def test_no_composite_store_attribute(self, mock_neo4j_store):
        """_composite_store attribute must NOT exist in the new implementation."""
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        hook = GraphDataHook(resolver)
        assert not hasattr(hook, "_composite_store")

    def test_creates_mount_flow_with_store(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        hook = GraphDataHook(resolver)
        assert hook._flow is not None
        assert hook._flow._graph_store is mock_store

    def test_neo4j_store_class_is_called(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        GraphDataHook(resolver)
        mock_cls.assert_called_once()


class TestGraphDataHookMount:
    """GraphDataHook.mount() runs MountFlow to READY and returns cleanup callable."""

    async def test_mount_returns_cleanup_callable(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        hook = GraphDataHook(resolver)
        coordinator = _make_coordinator()
        cleanup = await hook.mount(coordinator, events={"session:start", "session:end", "tool:pre"})
        assert callable(cleanup)

    async def test_mount_runs_mount_flow_to_ready(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        hook = GraphDataHook(resolver)
        coordinator = _make_coordinator()
        await hook.mount(coordinator, events={"session:start", "session:end", "tool:pre"})
        assert hook._flow.state == MountState.READY

    async def test_mount_registers_handlers(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        hook = GraphDataHook(resolver)
        coordinator = _make_coordinator()
        await hook.mount(coordinator, events={"session:start", "session:end", "tool:pre"})
        assert coordinator.hooks.register.call_count >= 3

    async def test_cleanup_calls_unregister(self, mock_neo4j_store):
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        hook = GraphDataHook(resolver)
        coordinator = _make_coordinator()
        cleanup = await hook.mount(coordinator, events={"session:start"})
        cleanup()
        # cleanup() schedules an async task — yield control so it runs
        await asyncio.sleep(0)
        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()

    async def test_cleanup_schedules_store_close(self, mock_neo4j_store):
        """Cleanup must schedule store.close() (fire-and-forget)."""
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        hook = GraphDataHook(resolver)
        coordinator = _make_coordinator()
        cleanup = await hook.mount(coordinator, events={"session:start"})
        cleanup()
        # Yield to event loop so the create_task() coroutine can run
        await asyncio.sleep(0)
        mock_store.close.assert_called_once()


class TestGraphDataHookCleanupEventLoopHandling:
    """Tests for cleanup() when called from a different event loop than mount().

    Production scenario: mount() runs on the session event loop (Loop A).
    At teardown the CLI may call cleanup() from a different loop (Loop B).
    The Neo4j driver's internal asyncio.Futures are bound to Loop A, so calling
    ``await driver.close()`` from a task on Loop B raises::

        RuntimeError: Task ... got Future ... attached to a different loop

    The fix: capture the creation loop at mount() time and, in cleanup(), skip the
    async close path entirely when a *different* loop is running (data has already
    been flushed; the driver connection is released by GC).
    """

    # ------------------------------------------------------------------
    # Helper: run mount in an isolated loop, return cleanup callable
    # ------------------------------------------------------------------

    @staticmethod
    def _mount_in_new_loop(hook: "GraphDataHook", coordinator: Any) -> "Callable":
        loop_a = asyncio.new_event_loop()
        try:
            return loop_a.run_until_complete(hook.mount(coordinator, events={"session:start"}))
        finally:
            loop_a.close()

    # ------------------------------------------------------------------
    # Failing test #1 — store.close must NOT be called from a foreign loop
    # ------------------------------------------------------------------

    def test_cleanup_different_loop_does_not_call_store_close(self, mock_neo4j_store):
        """When cleanup() runs on a different loop from mount(), store.close()
        must be skipped to avoid 'attached to a different loop' RuntimeError.

        Fails with current code because create_task(_async_cleanup()) schedules
        store.close() on Loop B anyway.
        """
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        coordinator = _make_coordinator()
        hook = GraphDataHook(resolver)

        cleanup = self._mount_in_new_loop(hook, coordinator)  # Loop A → closed

        loop_b = asyncio.new_event_loop()
        try:

            async def run() -> None:
                cleanup()
                await asyncio.sleep(0.05)  # let any tasks run

            loop_b.run_until_complete(run())
        finally:
            loop_b.close()

        # After the fix: different-loop branch skips store.close entirely.
        mock_store.close.assert_not_called()

    # ------------------------------------------------------------------
    # Failing test #2 — no RuntimeError must surface in the calling loop
    # ------------------------------------------------------------------

    def test_cleanup_different_loop_no_runtime_error(self, mock_neo4j_store):
        """RuntimeError from a loop-bound Future must not surface when cleanup()
        is called from a different loop.

        Simulates the real Neo4j driver by making store.close() await a
        Future that is explicitly bound to Loop A.  When Loop B's task tries
        to step through that Future it raises
        ``RuntimeError: Task ... got Future ... attached to a different loop``.

        Fails with current code (create_task runs on Loop B and trips over the
        Loop-A Future).  Passes after the fix (different-loop branch never calls
        store.close at all).
        """
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        coordinator = _make_coordinator()
        hook = GraphDataHook(resolver)

        loop_a = asyncio.new_event_loop()
        try:
            # Create a Future explicitly bound to loop_a — simulates the
            # asyncio.Future objects inside the real AsyncGraphDatabase driver.
            loop_a_future: asyncio.Future[None] = loop_a.create_future()

            async def loop_sensitive_close() -> None:
                # Awaiting a loop_a-bound Future from inside a loop_b task
                # causes Task.__step to raise:
                # "Task ... got Future ... attached to a different loop"
                await loop_a_future

            mock_store.close = loop_sensitive_close
            cleanup = loop_a.run_until_complete(hook.mount(coordinator, events={"session:start"}))
        finally:
            loop_a.close()

        unhandled_errors: list[Exception] = []
        loop_b = asyncio.new_event_loop()
        loop_b.set_exception_handler(
            lambda _loop, ctx: unhandled_errors.append(
                ctx.get("exception", RuntimeError(ctx["message"]))
            )
        )
        try:

            async def run() -> None:
                cleanup()
                await asyncio.sleep(0.05)  # let tasks run (and potentially fail)

            loop_b.run_until_complete(run())
        finally:
            loop_b.close()

        runtime_errors = [e for e in unhandled_errors if isinstance(e, RuntimeError)]
        assert not runtime_errors, f"RuntimeError during cleanup: {runtime_errors}"

    # ------------------------------------------------------------------
    # Regression test — flow_cleanup (unregister) must always be called
    # ------------------------------------------------------------------

    def test_cleanup_different_loop_still_calls_flow_cleanup(self, mock_neo4j_store):
        """Even when cleanup() runs on a different loop, hook unregistration
        (flow_cleanup) must still happen — not be deferred or skipped.
        """
        mock_cls, mock_store = mock_neo4j_store
        resolver = _make_resolver(_NEO4J_STORE_CONFIG)
        coordinator = _make_coordinator()
        hook = GraphDataHook(resolver)

        cleanup = self._mount_in_new_loop(hook, coordinator)

        loop_b = asyncio.new_event_loop()
        try:

            async def run() -> None:
                cleanup()
                await asyncio.sleep(0.05)

            loop_b.run_until_complete(run())
        finally:
            loop_b.close()

        for unreg in coordinator._unregister_fns:
            unreg.assert_called_once()


def _read_graph_data_hook_source() -> str:
    """Read the source of graph_data_hook module."""
    spec = importlib.util.find_spec("amplifier_module_hook_context_intelligence.graph_data_hook")
    assert spec is not None and spec.origin is not None
    with open(spec.origin) as f:
        return f.read()


class TestNoForbiddenImports:
    """graph_data_hook must not reference store_factory or CompositeGraphStore."""

    def test_no_store_factory_import(self):
        content = _read_graph_data_hook_source()
        assert "store_factory" not in content, "graph_data_hook.py must not reference store_factory"

    def test_no_composite_graph_store_reference(self):
        content = _read_graph_data_hook_source()
        assert "CompositeGraphStore" not in content, (
            "graph_data_hook.py must not reference CompositeGraphStore"
        )

    def test_no_graph_stores_plural_key(self):
        """graph_data_hook.py must use 'graph_store' (singular), not 'graph_stores' (plural)."""
        content = _read_graph_data_hook_source()
        assert "graph_stores" not in content, (
            "graph_data_hook.py must not reference 'graph_stores' (plural)"
        )


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
