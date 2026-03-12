"""Tests for blob processing in the dispatch path (task-4).

Three tests verifying:
1. test_handler_receives_processed_clone_with_blob_refs — handler gets a processed
   clone with blob refs substituted; original data stays untouched (manually invokes
   process_event_data for verification).
2. test_backward_compat_no_blob_store — when blob_store is None, the wrapper passes
   data through unchanged (backward compatibility).
3. test_mount_flow_passes_blob_store_to_services — MountFlow propagates blob_store
   to HookStateService via create_services().
"""

from __future__ import annotations

from unittest.mock import MagicMock

from amplifier_module_hook_context_intelligence.blob_processor import process_event_data
from amplifier_module_hook_context_intelligence.mount import MountFlow
from amplifier_module_hook_context_intelligence.services import HookStateService
from amplifier_module_hook_context_intelligence.utils import make_node_id


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------


class MockBlobStore:
    """In-memory blob store that records writes and returns ci-blob:// URIs."""

    def __init__(self) -> None:
        self.written: dict[str, object] = {}

    async def write(self, session_id: str, key: str, value: object) -> str:
        self.written[key] = value
        return f"ci-blob://{session_id}/{key}"


# ---------------------------------------------------------------------------
# Test 1: handler receives processed clone with blob refs
# ---------------------------------------------------------------------------


class TestHandlerReceivesProcessedCloneWithBlobRefs:
    """Wrapped handler receives processed clone; original data is unchanged."""

    async def test_handler_receives_processed_clone_with_blob_refs(self) -> None:
        """When blob_store is set, handler receives a clone with blob refs substituted.

        This test manually invokes process_event_data to verify:
        - The processed clone has blob refs for large fields.
        - The original data dict is never mutated.

        It also exercises the MountFlow._wrap_with_session_guarantee path end-to-end,
        confirming that the wrapped handler receives the processed clone (not raw data).
        """
        blob_store = MockBlobStore()

        data = {
            "session_id": "sess-abc",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "raw": {"big": "payload"},
        }
        original_raw = data["raw"]  # save reference to verify immutability

        # --- Manually invoke process_event_data to understand expected output ---
        node_id = make_node_id("sess-abc", "tool:pre", "2026-01-01T00:00:00+00:00")
        manual_clone = await process_event_data(data, blob_store, "sess-abc", node_id)

        # The processed clone has a blob ref in the "raw" field
        assert "$blob_ref" in manual_clone["raw"]
        assert manual_clone["raw"]["$blob_ref"].startswith("ci-blob://")

        # The original data is unchanged after process_event_data
        assert data["raw"] is original_raw
        assert data["raw"] == {"big": "payload"}

        # --- Now exercise the full MountFlow dispatch path ---
        handler_received: list[dict] = []

        async def capturing_handler(event: str, evt_data: dict) -> None:
            handler_received.append(dict(evt_data))

        # MountFlow must accept blob_store and wire it through
        flow = MountFlow(config={}, blob_store=blob_store)
        flow.create_services(None)

        wrapped = flow._wrap_with_session_guarantee(capturing_handler)
        await wrapped("tool:pre", data)

        assert len(handler_received) == 1
        handler_data = handler_received[0]

        # Handler should have received the processed clone (with blob refs)
        assert "$blob_ref" in handler_data["raw"]
        assert handler_data["raw"]["$blob_ref"].startswith("ci-blob://")

        # Original data must remain untouched after the full dispatch path
        assert data["raw"] is original_raw
        assert data["raw"] == {"big": "payload"}


# ---------------------------------------------------------------------------
# Test 2: backward compatibility — no blob_store
# ---------------------------------------------------------------------------


class TestBackwardCompatNoBlobStore:
    """When blob_store is None, system works exactly as before (no blob processing)."""

    async def test_backward_compat_no_blob_store(self) -> None:
        """When blob_store is None (default), the wrapper passes data through unchanged."""
        handler_received: list[dict] = []

        async def capturing_handler(event: str, evt_data: dict) -> None:
            handler_received.append(dict(evt_data))

        # Create MountFlow WITHOUT a blob_store — the default
        flow = MountFlow(config={})
        flow.create_services(None)

        assert flow.services is not None
        assert flow.services.blob_store is None  # no blob_store wired

        wrapped = flow._wrap_with_session_guarantee(capturing_handler)

        data = {
            "session_id": "sess-xyz",
            "timestamp": "2026-01-01T00:00:00+00:00",
            "raw": {"big": "payload"},
        }

        await wrapped("tool:pre", data)

        assert len(handler_received) == 1
        handler_data = handler_received[0]

        # Handler received the original data contents — no blob processing applied
        assert handler_data["raw"] == {"big": "payload"}
        assert "$blob_ref" not in handler_data["raw"]


# ---------------------------------------------------------------------------
# Test 3: MountFlow propagates blob_store to HookStateService
# ---------------------------------------------------------------------------


class TestMountFlowPassesBlobStoreToServices:
    """MountFlow propagates blob_store to HookStateService via create_services()."""

    def test_mount_flow_passes_blob_store_to_services(self) -> None:
        """MountFlow(blob_store=...) stores blob_store and passes it to HookStateService."""
        blob_store = MockBlobStore()

        flow = MountFlow(config={}, blob_store=blob_store)

        # MountFlow must store the blob_store
        assert flow._blob_store is blob_store

        # create_services() must wire blob_store into HookStateService
        flow.create_services(None)

        assert flow.services is not None
        assert isinstance(flow.services, HookStateService)
        assert flow.services.blob_store is blob_store

    def test_mount_flow_passes_blob_store_to_services_resolver_path(self) -> None:
        """Resolver path also propagates blob_store to HookStateService."""
        blob_store = MockBlobStore()
        resolver = MagicMock()
        resolver._config = {}

        flow = MountFlow(config={}, resolver=resolver, blob_store=blob_store)
        flow.create_services(None)

        assert flow.services is not None
        assert flow.services.blob_store is blob_store
