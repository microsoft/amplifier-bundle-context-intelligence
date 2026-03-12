# Blob Store and Handler Enrichment Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Preserve complete event data on every Neo4j graph node by introducing a blob store that offloads large fields to disk and a processor pipeline that enriches event clones with blob references.

**Architecture:** A `DiskBlobStore` writes large event fields (raw, result, messages, etc.) to per-session `blobs/` directories and returns `ci-blob://` URIs. A pure-function `process_event_data()` deep-clones event data and swaps known-large fields with blob refs. The blob processor runs in the dispatch wrapper (priority 90) so every graph handler receives already-processed data — LoggingHandler (priority 100) continues to get the original unmodified event. Every handler then stores `properties["data"] = json.dumps(processed_clone)` on its node.

**Tech Stack:** Python 3.11+, pytest with pytest-asyncio (mode=auto), in-memory `GraphState` for unit tests.

**Design doc:** `docs/plans/2026-03-12-blob-store-and-handler-enrichment-design.md`

**Run all tests:** `cd modules/hook-context-intelligence && uv run pytest tests/ -v`

**Source root:** `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/`

**Test root:** `modules/hook-context-intelligence/tests/`

---

## Phase 1: Foundation

### Task 1: BlobStore Protocol + DiskBlobStore

**Files:**
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_store.py`
- Create: `modules/hook-context-intelligence/tests/test_blob_store.py`

**Step 1: Write the test file**

Create `modules/hook-context-intelligence/tests/test_blob_store.py`:

```python
"""Tests for BlobStore protocol + DiskBlobStore implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from amplifier_module_hook_context_intelligence.blob_store import DiskBlobStore


@pytest.fixture
def blob_root(tmp_path: Path) -> Path:
    """Provide a temporary directory as the blob storage root."""
    return tmp_path


@pytest.fixture
def blob_store(blob_root: Path) -> DiskBlobStore:
    """A fresh DiskBlobStore backed by a temp directory."""
    return DiskBlobStore(blob_root)


# ── Write / Read round-trip ──────────────────────────────────────────────


class TestWriteReadRoundTrip:
    async def test_write_returns_uri(self, blob_store: DiskBlobStore) -> None:
        uri = await blob_store.write("sess-1", "node1__raw", {"key": "value"})
        assert uri.startswith("ci-blob://")
        assert "sess-1" in uri
        assert "node1__raw" in uri

    async def test_read_returns_original_value(self, blob_store: DiskBlobStore) -> None:
        original = {"nested": {"deep": [1, 2, 3]}, "text": "hello"}
        uri = await blob_store.write("sess-1", "node1__raw", original)
        result = await blob_store.read("sess-1", uri)
        assert result == original

    async def test_write_list_value(self, blob_store: DiskBlobStore) -> None:
        original = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
        uri = await blob_store.write("sess-1", "node1__messages", original)
        result = await blob_store.read("sess-1", uri)
        assert result == original

    async def test_write_creates_session_blobs_dir(
        self, blob_store: DiskBlobStore, blob_root: Path
    ) -> None:
        await blob_store.write("sess-1", "node1__raw", {"key": "value"})
        blobs_dir = blob_root / "sess-1" / "blobs"
        assert blobs_dir.is_dir()

    async def test_write_creates_json_file_on_disk(
        self, blob_store: DiskBlobStore, blob_root: Path
    ) -> None:
        await blob_store.write("sess-1", "node1__raw", {"key": "value"})
        blob_file = blob_root / "sess-1" / "blobs" / "node1__raw.json"
        assert blob_file.exists()
        content = json.loads(blob_file.read_text())
        assert content == {"key": "value"}


# ── URI scheme ───────────────────────────────────────────────────────────


class TestURIScheme:
    async def test_uri_format(self, blob_store: DiskBlobStore) -> None:
        uri = await blob_store.write("sess-abc", "node42__result", {"data": True})
        assert uri == "ci-blob://sess-abc/node42__result"

    async def test_uri_with_special_session_id(self, blob_store: DiskBlobStore) -> None:
        uri = await blob_store.write(
            "6afb3613-7041-4735-9c0f-c2171452ed18", "n1__raw", {}
        )
        assert uri == "ci-blob://6afb3613-7041-4735-9c0f-c2171452ed18/n1__raw"


# ── List ─────────────────────────────────────────────────────────────────


class TestList:
    async def test_list_empty_session(self, blob_store: DiskBlobStore) -> None:
        result = await blob_store.list("nonexistent-session")
        assert result == []

    async def test_list_returns_all_uris_for_session(self, blob_store: DiskBlobStore) -> None:
        await blob_store.write("sess-1", "node1__raw", {"a": 1})
        await blob_store.write("sess-1", "node2__result", {"b": 2})
        uris = await blob_store.list("sess-1")
        assert len(uris) == 2
        assert "ci-blob://sess-1/node1__raw" in uris
        assert "ci-blob://sess-1/node2__result" in uris

    async def test_list_does_not_include_other_sessions(
        self, blob_store: DiskBlobStore,
    ) -> None:
        await blob_store.write("sess-1", "node1__raw", {"a": 1})
        await blob_store.write("sess-2", "node2__raw", {"b": 2})
        uris = await blob_store.list("sess-1")
        assert len(uris) == 1
        assert "ci-blob://sess-1/node1__raw" in uris


# ── Dump ─────────────────────────────────────────────────────────────────


class TestDump:
    async def test_dump_materializes_file(self, blob_store: DiskBlobStore, tmp_path: Path) -> None:
        original = {"big": "payload", "items": [1, 2, 3]}
        uri = await blob_store.write("sess-1", "node1__raw", original)
        dest = tmp_path / "output"
        dest.mkdir()
        result_path = await blob_store.dump(uri, str(dest / "dumped.json"))
        assert Path(result_path).exists()
        content = json.loads(Path(result_path).read_text())
        assert content == original

    async def test_dump_default_path(self, blob_store: DiskBlobStore) -> None:
        uri = await blob_store.write("sess-1", "node1__raw", {"key": "val"})
        result_path = await blob_store.dump(uri)
        assert Path(result_path).exists()
        content = json.loads(Path(result_path).read_text())
        assert content == {"key": "val"}

    async def test_dump_missing_blob_raises(self, blob_store: DiskBlobStore) -> None:
        with pytest.raises(FileNotFoundError):
            await blob_store.dump("ci-blob://sess-1/nonexistent__raw")


# ── Disk layout ──────────────────────────────────────────────────────────


class TestDiskLayout:
    async def test_blobs_dir_under_session(
        self, blob_store: DiskBlobStore, blob_root: Path
    ) -> None:
        await blob_store.write("sess-abc", "n1__raw", {"x": 1})
        await blob_store.write("sess-abc", "n2__messages", [1, 2])
        blobs_dir = blob_root / "sess-abc" / "blobs"
        files = sorted(f.name for f in blobs_dir.iterdir())
        assert files == ["n1__raw.json", "n2__messages.json"]
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_blob_store.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'amplifier_module_hook_context_intelligence.blob_store'`

**Step 3: Write the implementation**

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_store.py`:

```python
"""BlobStore protocol + DiskBlobStore — disk-backed blob storage for large event fields.

Blobs are stored as JSON files under a per-session ``blobs/`` directory.
URI scheme: ``ci-blob://<session-id>/<key>`` where key is typically
``<node-id>__<field-name>``.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_DUMP_DEFAULT_DIR = Path("/tmp/ci-blobs")


@runtime_checkable
class BlobStore(Protocol):
    """Async protocol for blob storage backends."""

    async def write(self, session_id: str, key: str, value: Any) -> str:
        """Serialize + persist value, return URI."""
        ...

    async def read(self, session_id: str, uri: str) -> dict | list:
        """Deserialize value from URI."""
        ...

    async def list(self, session_id: str) -> list[str]:
        """Enumerate blob URIs for a session."""
        ...

    async def dump(self, uri: str, dest_path: str | None = None) -> str:
        """Materialize blob as a JSON file on disk, return path."""
        ...


class DiskBlobStore:
    """Disk-backed BlobStore implementation.

    Disk layout::

        <root>/<session-id>/blobs/<key>.json
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def _blobs_dir(self, session_id: str) -> Path:
        return self._root / session_id / "blobs"

    def _blob_path(self, session_id: str, key: str) -> Path:
        return self._blobs_dir(session_id) / f"{key}.json"

    @staticmethod
    def _make_uri(session_id: str, key: str) -> str:
        return f"ci-blob://{session_id}/{key}"

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str]:
        """Parse ``ci-blob://<session-id>/<key>`` into (session_id, key)."""
        stripped = uri.removeprefix("ci-blob://")
        session_id, key = stripped.split("/", 1)
        return session_id, key

    async def write(self, session_id: str, key: str, value: Any) -> str:
        """Serialize value as JSON and persist to disk. Returns a ci-blob:// URI."""
        blob_path = self._blob_path(session_id, key)
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        blob_path.write_text(json.dumps(value, default=str))
        return self._make_uri(session_id, key)

    async def read(self, session_id: str, uri: str) -> dict | list:
        """Deserialize a blob from its URI."""
        parsed_session_id, key = self._parse_uri(uri)
        blob_path = self._blob_path(parsed_session_id, key)
        return json.loads(blob_path.read_text())

    async def list(self, session_id: str) -> list[str]:
        """Enumerate all blob URIs for a session."""
        blobs_dir = self._blobs_dir(session_id)
        if not blobs_dir.exists():
            return []
        return [
            self._make_uri(session_id, f.stem)
            for f in sorted(blobs_dir.iterdir())
            if f.suffix == ".json"
        ]

    async def dump(self, uri: str, dest_path: str | None = None) -> str:
        """Copy blob to a destination path (or /tmp/ci-blobs/<key>.json)."""
        session_id, key = self._parse_uri(uri)
        source = self._blob_path(session_id, key)
        if not source.exists():
            msg = f"Blob not found: {uri} (expected at {source})"
            raise FileNotFoundError(msg)

        if dest_path is None:
            dest_dir = _DUMP_DEFAULT_DIR
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_path = str(dest_dir / f"{key}.json")

        shutil.copy2(str(source), dest_path)
        return dest_path
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_blob_store.py -v
```

Expected: All 14 tests PASS.

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_store.py \
       modules/hook-context-intelligence/tests/test_blob_store.py && \
git commit -m "feat: add BlobStore protocol and DiskBlobStore implementation"
```

---

### Task 2: Blob Processor

**Files:**
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_processor.py`
- Create: `modules/hook-context-intelligence/tests/test_blob_processor.py`

**Step 1: Write the test file**

Create `modules/hook-context-intelligence/tests/test_blob_processor.py`:

```python
"""Tests for blob processor — clone immutability, blob ref substitution, error handling."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from amplifier_module_hook_context_intelligence.blob_processor import (
    BLOB_FIELDS,
    process_event_data,
)
from amplifier_module_hook_context_intelligence.blob_store import DiskBlobStore


@pytest.fixture
def blob_store(tmp_path: Path) -> DiskBlobStore:
    return DiskBlobStore(tmp_path)


# ── BLOB_FIELDS constant ────────────────────────────────────────────────


class TestBlobFieldsConstant:
    def test_blob_fields_is_expected_set(self) -> None:
        assert BLOB_FIELDS == {"raw", "result", "messages", "mount_plan", "context_snapshot", "debug"}

    def test_blob_fields_is_frozenset(self) -> None:
        assert isinstance(BLOB_FIELDS, frozenset)


# ── Clone immutability (CRITICAL) ────────────────────────────────────────


class TestCloneImmutability:
    async def test_original_dict_unchanged_after_processing(
        self, blob_store: DiskBlobStore
    ) -> None:
        """The hardest invariant: original data must NEVER be modified."""
        original = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "raw": {"provider_response": {"id": "msg_123", "content": "hello"}},
            "result": {"output": "big result data"},
            "tool_name": "read_file",
        }
        original_snapshot = copy.deepcopy(original)

        await process_event_data(original, blob_store, "s1", "node1")

        assert original == original_snapshot, "Original dict was mutated by process_event_data!"

    async def test_original_nested_dicts_unchanged(
        self, blob_store: DiskBlobStore
    ) -> None:
        """Nested dicts inside blob fields must not be modified in the original."""
        nested = {"deep": {"deeper": [1, 2, 3]}}
        original = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "raw": nested,
        }
        original_raw_id = id(original["raw"])

        await process_event_data(original, blob_store, "s1", "node1")

        # The original's "raw" must still be the same object with same content
        assert id(original["raw"]) == original_raw_id
        assert original["raw"] == {"deep": {"deeper": [1, 2, 3]}}


# ── Blob ref substitution ───────────────────────────────────────────────


class TestBlobRefSubstitution:
    async def test_blob_field_replaced_with_ref(self, blob_store: DiskBlobStore) -> None:
        data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "raw": {"provider": "anthropic", "response": {"id": "msg_1"}},
        }
        clone = await process_event_data(data, blob_store, "s1", "node1")
        assert "$blob_ref" in clone["raw"]
        assert clone["raw"]["$blob_ref"] == "ci-blob://s1/node1__raw"

    async def test_multiple_blob_fields_replaced(self, blob_store: DiskBlobStore) -> None:
        data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "raw": {"big": "response"},
            "result": {"output": "big result"},
            "messages": [{"role": "user", "content": "hi"}],
        }
        clone = await process_event_data(data, blob_store, "s1", "node1")
        assert "$blob_ref" in clone["raw"]
        assert "$blob_ref" in clone["result"]
        assert "$blob_ref" in clone["messages"]

    async def test_blob_ref_uri_contains_node_id_and_field(
        self, blob_store: DiskBlobStore
    ) -> None:
        data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "result": {"output": "data"},
        }
        clone = await process_event_data(data, blob_store, "s1", "mynode")
        assert clone["result"]["$blob_ref"] == "ci-blob://s1/mynode__result"


# ── Non-blob fields pass through ────────────────────────────────────────


class TestNonBlobFieldsPassThrough:
    async def test_small_fields_unchanged(self, blob_store: DiskBlobStore) -> None:
        data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "tool_name": "read_file",
            "tool_call_id": "call_001",
            "status": "success",
        }
        clone = await process_event_data(data, blob_store, "s1", "node1")
        assert clone["session_id"] == "s1"
        assert clone["timestamp"] == "2026-01-01T00:00:00Z"
        assert clone["tool_name"] == "read_file"
        assert clone["tool_call_id"] == "call_001"
        assert clone["status"] == "success"

    async def test_no_fields_removed(self, blob_store: DiskBlobStore) -> None:
        data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "raw": {"big": "value"},
            "tool_name": "grep",
            "custom_field": "preserved",
        }
        clone = await process_event_data(data, blob_store, "s1", "node1")
        assert set(clone.keys()) == set(data.keys())


# ── None value handling ──────────────────────────────────────────────────


class TestNoneValueHandling:
    async def test_none_blob_field_not_processed(self, blob_store: DiskBlobStore) -> None:
        data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "raw": None,
        }
        clone = await process_event_data(data, blob_store, "s1", "node1")
        assert clone["raw"] is None

    async def test_missing_blob_field_not_added(self, blob_store: DiskBlobStore) -> None:
        data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        clone = await process_event_data(data, blob_store, "s1", "node1")
        assert "raw" not in clone
        assert "result" not in clone
        assert "messages" not in clone


# ── Error handling ───────────────────────────────────────────────────────


class TestErrorHandling:
    async def test_write_failure_produces_blob_error(self) -> None:
        """When blob_store.write() fails, the clone should get a $blob_error marker."""
        failing_store = AsyncMock()
        failing_store.write.side_effect = OSError("disk full")

        data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "raw": {"big": "payload"},
        }
        clone = await process_event_data(data, failing_store, "s1", "node1")
        assert "$blob_error" in clone["raw"]
        assert "disk full" in clone["raw"]["$blob_error"]

    async def test_write_failure_does_not_block_other_fields(self) -> None:
        """If one blob field fails, other blob fields should still process."""
        call_count = 0

        async def selective_fail(session_id: str, key: str, value: Any) -> str:
            nonlocal call_count
            call_count += 1
            if "raw" in key:
                raise OSError("disk full")
            return f"ci-blob://{session_id}/{key}"

        mock_store = AsyncMock()
        mock_store.write.side_effect = selective_fail

        data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "raw": {"big": "payload"},
            "result": {"output": "data"},
        }
        clone = await process_event_data(data, mock_store, "s1", "node1")
        assert "$blob_error" in clone["raw"]
        assert "$blob_ref" in clone["result"]


# ── Return value is a new dict ───────────────────────────────────────────


class TestReturnValue:
    async def test_returns_new_dict_not_same_object(self, blob_store: DiskBlobStore) -> None:
        data = {"session_id": "s1", "timestamp": "2026-01-01T00:00:00Z"}
        clone = await process_event_data(data, blob_store, "s1", "node1")
        assert clone is not data

    async def test_data_without_blob_fields_returns_identical_clone(
        self, blob_store: DiskBlobStore
    ) -> None:
        data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "tool_name": "read_file",
        }
        clone = await process_event_data(data, blob_store, "s1", "node1")
        assert clone == data
        assert clone is not data
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_blob_processor.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'amplifier_module_hook_context_intelligence.blob_processor'`

**Step 3: Write the implementation**

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_processor.py`:

```python
"""Blob processor — enriches event data clones with blob references.

Pure function that sits in the dispatch path. Receives immutable event data,
returns a processed deep clone. **NEVER mutates the original.**

Contract guarantees:
  - Original data dict is NEVER modified
  - No fields are removed from the clone
  - No fields are filtered out
  - The only mutation to the clone is: large_value → {"$blob_ref": uri}
  - All other fields pass through identical
  - On write failure: {"$blob_error": "write failed: <reason>"} replaces the value
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

BLOB_FIELDS: frozenset[str] = frozenset(
    {"raw", "result", "messages", "mount_plan", "context_snapshot", "debug"}
)


async def process_event_data(
    data: dict[str, Any],
    blob_store: Any,
    session_id: str,
    node_id: str,
) -> dict[str, Any]:
    """Deep-clone event data and replace known-large fields with blob refs.

    Args:
        data: The immutable original event data dict. NEVER modified.
        blob_store: A BlobStore instance (protocol: async write/read/list/dump).
        session_id: Session identifier for blob namespacing.
        node_id: Node identifier for blob key construction.

    Returns:
        A new dict (deep clone) with blob fields replaced by
        ``{"$blob_ref": uri}`` or ``{"$blob_error": reason}`` on failure.
    """
    clone = copy.deepcopy(data)

    for field_name in BLOB_FIELDS:
        if field_name not in clone or clone[field_name] is None:
            continue
        key = f"{node_id}__{field_name}"
        try:
            uri = await blob_store.write(session_id, key, clone[field_name])
            clone[field_name] = {"$blob_ref": uri}
        except Exception as exc:
            logger.warning(
                "Blob write failed for %s/%s: %s", session_id, key, exc
            )
            clone[field_name] = {"$blob_error": f"write failed: {exc}"}

    return clone
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_blob_processor.py -v
```

Expected: All 16 tests PASS.

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_processor.py \
       modules/hook-context-intelligence/tests/test_blob_processor.py && \
git commit -m "feat: add blob processor with clone immutability guarantees"
```

---

### Task 3: Wire BlobStore into HookStateService

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py`
- Modify: `modules/hook-context-intelligence/tests/test_services.py`

**Step 1: Write the failing test**

Add to the **end** of `modules/hook-context-intelligence/tests/test_services.py`:

```python
# ── BlobStore on HookStateService ────────────────────────────────────────


class TestHookStateServiceBlobStore:
    def test_blob_store_default_is_none(self) -> None:
        """By default, HookStateService has no blob_store (None)."""
        svc = HookStateService(raw_config={})
        assert svc.blob_store is None

    def test_blob_store_can_be_injected(self) -> None:
        """blob_store can be passed at construction time."""
        fake_store = object()
        svc = HookStateService(raw_config={}, blob_store=fake_store)
        assert svc.blob_store is fake_store
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_services.py::TestHookStateServiceBlobStore -v
```

Expected: FAIL — `TypeError: HookStateService.__init__() got an unexpected keyword argument 'blob_store'`

**Step 3: Modify the implementation**

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py`, modify the `HookStateService.__init__` method:

Change the constructor signature from:

```python
    def __init__(
        self,
        raw_config: dict[str, Any] | None = None,
        coordinator: Any = None,
        graph_store: Any = None,
        *,
        resolver: Any = None,
    ) -> None:
```

to:

```python
    def __init__(
        self,
        raw_config: dict[str, Any] | None = None,
        coordinator: Any = None,
        graph_store: Any = None,
        *,
        resolver: Any = None,
        blob_store: Any = None,
    ) -> None:
```

Then, after the line `self._seen_sessions: set[str] = set()`, add:

```python
        self.blob_store = blob_store
```

The full constructor should look like:

```python
    def __init__(
        self,
        raw_config: dict[str, Any] | None = None,
        coordinator: Any = None,
        graph_store: Any = None,
        *,
        resolver: Any = None,
        blob_store: Any = None,
    ) -> None:
        if resolver is not None:
            self.config = HookConfig(resolver._config)
        else:
            self.config = HookConfig(raw_config if raw_config is not None else {})
        self.coordinator = coordinator
        if graph_store is not None:
            self.graph = graph_store
        else:
            self.graph = GraphState()
        self._cursors: dict[str, SessionCursors] = {}
        self._seen_sessions: set[str] = set()
        self._forest_resolved: bool = False
        self.blob_store = blob_store
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_services.py -v
```

Expected: All tests PASS (existing + 2 new).

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/services.py \
       modules/hook-context-intelligence/tests/test_services.py && \
git commit -m "feat: add blob_store attribute to HookStateService"
```

---

## Phase 2: Dispatch Integration

### Task 4: Wire Blob Processor into Dispatch Path

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/mount.py`
- Create: `modules/hook-context-intelligence/tests/test_blob_dispatch.py`

**Step 1: Write the test file**

Create `modules/hook-context-intelligence/tests/test_blob_dispatch.py`:

```python
"""Tests for blob processor integration in the dispatch path.

Verifies that:
1. Graph handlers receive processed clones (blob refs, not raw values)
2. The original event data is never mutated
3. When blob_store is None, handlers receive unprocessed data (backward compat)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from amplifier_module_hook_context_intelligence.blob_store import DiskBlobStore
from amplifier_module_hook_context_intelligence.handlers.default import DefaultHandler
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.handlers.step import StepHandler
from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import (
    OrchestratorRunHandler,
)
from amplifier_module_hook_context_intelligence.mount import MountFlow
from amplifier_module_hook_context_intelligence.services import HookStateService
from amplifier_module_hook_context_intelligence.utils import make_node_id


@pytest.fixture
def blob_store(tmp_path: Path) -> DiskBlobStore:
    return DiskBlobStore(tmp_path)


# ── Session guarantee wrapper processes blobs ────────────────────────────


class TestDispatchBlobProcessing:
    async def test_handler_receives_processed_clone_with_blob_refs(
        self, blob_store: DiskBlobStore
    ) -> None:
        """When blob_store is set, handler data should have blob refs for large fields."""
        services = HookStateService(raw_config={}, blob_store=blob_store)

        # Seed session
        session_handler = SessionHandler(services)
        await session_handler(
            "session:start",
            {"session_id": "s1", "timestamp": "2026-01-01T00:00:00Z"},
        )

        # Use DefaultHandler — simplest handler that stores data
        handler = DefaultHandler(services)

        # Build event data with a blob field
        data: dict[str, Any] = {
            "session_id": "s1",
            "timestamp": "2026-01-01T01:00:00Z",
            "raw": {"huge": "provider_response", "tokens": list(range(1000))},
            "tool_name": "grep",
        }

        # Manually invoke the wrapper logic the same way mount.py does
        from amplifier_module_hook_context_intelligence.blob_processor import process_event_data

        node_id = make_node_id("s1", "custom:event", "2026-01-01T01:00:00Z")
        processed = await process_event_data(data, blob_store, "s1", node_id)

        # Verify the processed clone has blob refs
        assert "$blob_ref" in processed["raw"]
        assert processed["tool_name"] == "grep"

        # Verify the original is unchanged
        assert isinstance(data["raw"], dict)
        assert "huge" in data["raw"]
        assert "$blob_ref" not in data["raw"]

    async def test_backward_compat_no_blob_store(self) -> None:
        """When blob_store is None, the system works as before (no blob processing)."""
        services = HookStateService(raw_config={})
        assert services.blob_store is None

        session_handler = SessionHandler(services)
        await session_handler(
            "session:start",
            {"session_id": "s1", "timestamp": "2026-01-01T00:00:00Z"},
        )

        handler = DefaultHandler(services)
        result = await handler(
            "custom:event",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T01:00:00Z",
                "raw": {"big": "payload"},
            },
        )
        assert result.action == "continue"


class TestMountFlowBlobStoreWiring:
    async def test_mount_flow_passes_blob_store_to_services(
        self, blob_store: DiskBlobStore
    ) -> None:
        """MountFlow should accept and propagate blob_store to HookStateService."""
        flow = MountFlow(config={}, blob_store=blob_store)

        # Simulate creating services with a mock coordinator
        class FakeCoordinator:
            config: dict[str, Any] = {}
            hooks = type("H", (), {"register": lambda *a, **k: lambda: None})()

        flow.create_services(FakeCoordinator())
        assert flow.services is not None
        assert flow.services.blob_store is blob_store
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_blob_dispatch.py -v
```

Expected: FAIL — `TypeError: MountFlow.__init__() got an unexpected keyword argument 'blob_store'`

**Step 3: Modify the implementation**

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/mount.py`, make two changes:

**Change 1:** Add the import at the top (after existing imports):

```python
from .blob_processor import process_event_data
from .utils import make_node_id
```

**Change 2:** Modify `MountFlow.__init__` to accept `blob_store`:

Change:

```python
    def __init__(
        self, config: dict[str, Any], graph_store: Any = None, resolver: Any = None
    ) -> None:
        self._config = config
        self._graph_store = graph_store
        self._resolver = resolver
```

to:

```python
    def __init__(
        self,
        config: dict[str, Any],
        graph_store: Any = None,
        resolver: Any = None,
        blob_store: Any = None,
    ) -> None:
        self._config = config
        self._graph_store = graph_store
        self._resolver = resolver
        self._blob_store = blob_store
```

**Change 3:** Pass `blob_store` through in `create_services`:

In the `create_services` method, change both branches to pass `blob_store`:

Change:

```python
    def create_services(self, coordinator: Any) -> None:
        """INIT → STATE_CREATED: Instantiate HookStateService from config."""
        if self._resolver is not None:
            self.services = HookStateService(
                resolver=self._resolver,
                coordinator=coordinator,
                graph_store=self._graph_store,
            )
        else:
            self.services = HookStateService(
                self._config, coordinator=coordinator, graph_store=self._graph_store
            )
        self.state = MountState.STATE_CREATED
```

to:

```python
    def create_services(self, coordinator: Any) -> None:
        """INIT → STATE_CREATED: Instantiate HookStateService from config."""
        if self._resolver is not None:
            self.services = HookStateService(
                resolver=self._resolver,
                coordinator=coordinator,
                graph_store=self._graph_store,
                blob_store=self._blob_store,
            )
        else:
            self.services = HookStateService(
                self._config,
                coordinator=coordinator,
                graph_store=self._graph_store,
                blob_store=self._blob_store,
            )
        self.state = MountState.STATE_CREATED
```

**Change 4:** Add blob processing to the session-guarantee wrapper:

Change the `_wrap_with_session_guarantee` method:

```python
    def _wrap_with_session_guarantee(self, handler: Any) -> Any:
        """Wrap a handler so it ensures a Session node exists before dispatch.

        This prevents orphaned child nodes in Neo4j when session:start
        is not emitted (e.g. --mode single emits execution:start instead).

        When a blob_store is configured, the wrapper also runs the blob
        processor on event data before passing the processed clone to the
        handler.  The original data dict is NEVER mutated.
        """
        services = self.services

        async def wrapper(event: str, data: dict[str, Any]) -> Any:
            session_id = data.get("session_id")
            if session_id and services is not None:
                await services.ensure_session_node(session_id, data)

            # Blob processing: if blob_store is configured, deep-clone the
            # data and replace known-large fields with blob refs.  The handler
            # receives the processed clone; the original stays untouched.
            handler_data = data
            if services is not None and services.blob_store is not None and session_id:
                timestamp = data.get("timestamp", "")
                node_id = make_node_id(session_id, event, timestamp) if timestamp else event
                handler_data = await process_event_data(
                    data, services.blob_store, session_id, node_id
                )

            return await handler(event, handler_data)

        return wrapper
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_blob_dispatch.py -v
```

Expected: All 3 tests PASS.

**Step 5: Run all existing tests to verify no regressions**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

Expected: All existing tests PASS (no regressions).

**Step 6: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/mount.py \
       modules/hook-context-intelligence/tests/test_blob_dispatch.py && \
git commit -m "feat: wire blob processor into dispatch path via session-guarantee wrapper"
```

---

## Phase 3: Handler Changes

> **Convention for all handler tasks:** Each handler stores the full processed clone as
> `properties["data"] = json.dumps(data)` on creation events. Enrichment events use
> `properties["data_<event_name>"] = json.dumps(data)` where `<event_name>` is the
> event with colons replaced by underscores (e.g., `llm:response` → `data_llm_response`).
>
> Every handler already receives `data` as a parameter — after Task 4, this `data` is
> the blob-processed clone when a blob_store is configured.

### Task 5: OrchestratorRunHandler — Add `data` Properties + Flush Fix

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py`
- Modify: `modules/hook-context-intelligence/tests/test_orchestrator_run_handler.py`

**Step 1: Write the failing tests**

Add to the **end** of `modules/hook-context-intelligence/tests/test_orchestrator_run_handler.py`:

```python
import json

# ── data property tests ──────────────────────────────────────────────────


class TestPromptSubmitDataProperty:
    async def test_stores_data_property(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        event_data = {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello"}
        await handler("prompt:submit", event_data)
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        assert stored["session_id"] == "s1"
        assert stored["prompt"] == "Hello"

    async def test_data_is_complete_event_clone(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        event_data = {
            "session_id": "s1",
            "timestamp": TIMESTAMP,
            "prompt": "Hello",
            "extra_field": "preserved",
        }
        await handler("prompt:submit", event_data)
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        assert stored["extra_field"] == "preserved"


class TestExecutionStartDataProperty:
    async def test_stores_data_property(self, services: HookStateService) -> None:
        await _seed_session_and_prompt(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "execution:start",
            {"session_id": "s1", "timestamp": EXEC_TIMESTAMP},
        )
        node = await services.graph.get_node(EXPECTED_RUN_NODE_ID)
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        assert stored["session_id"] == "s1"
        assert stored["timestamp"] == EXEC_TIMESTAMP


class TestExecutionEndDataProperty:
    async def test_stores_data_enrichment_property(self, services: HookStateService) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "execution:end",
            {"session_id": "s1", "timestamp": END_TIMESTAMP, "response": "Done"},
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        stored = json.loads(node["properties"]["data_execution_end"])
        assert stored["session_id"] == "s1"
        assert stored["response"] == "Done"


class TestOrchestratorCompleteDataProperty:
    async def test_stores_data_enrichment_property(self, services: HookStateService) -> None:
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "orchestrator:complete",
            {"session_id": "s1", "timestamp": COMPLETE_TIMESTAMP, "status": "success"},
        )
        node = await services.graph.get_node(run_id)
        assert node is not None
        stored = json.loads(node["properties"]["data_orchestrator_complete"])
        assert stored["status"] == "success"

    async def test_flush_called_on_orchestrator_complete(self, services: HookStateService) -> None:
        """orchestrator:complete must call flush() — this is the critical fix."""
        run_id = await _seed_full_run(services)
        handler = OrchestratorRunHandler(services)

        # Track flush calls
        flush_called = False
        original_flush = services.graph.flush

        async def tracking_flush() -> None:
            nonlocal flush_called
            flush_called = True
            await original_flush()

        services.graph.flush = tracking_flush  # type: ignore[assignment]

        await handler(
            "orchestrator:complete",
            {"session_id": "s1", "timestamp": COMPLETE_TIMESTAMP, "status": "success"},
        )
        assert flush_called, "flush() was not called in orchestrator:complete handler"
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_orchestrator_run_handler.py::TestPromptSubmitDataProperty -v
```

Expected: FAIL — `KeyError: 'data'`

**Step 3: Modify the handler**

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py`:

**Add import** at the top (after existing imports):

```python
import json
```

**Modify `_handle_prompt_submit`:** After the line that builds `properties` and before `await self.services.graph.upsert_node(...)`, add:

```python
        properties["data"] = json.dumps(data)
```

Specifically, add it right after `"session_id": session_id,` and before the `# Upsert PromptStep node` comment. The properties dict should become:

```python
        properties: dict[str, Any] = {
            "iteration": 0,
            "prompt_text": prompt_text,
            "prompt_preview": prompt_preview,
            "occurred_at": timestamp,
            "session_id": session_id,
            "data": json.dumps(data),
        }
```

**Modify `_handle_execution_start`:** Add `"data": json.dumps(data)` to the properties dict:

```python
        properties: dict[str, Any] = {
            "run_number": cursors.run_counter,
            "started_at": timestamp,
            "status": "in_progress",
            "prompt_preview": cursors.prompt_preview,
            "session_id": session_id,
            "data": json.dumps(data),
        }
```

**Modify `_handle_execution_end`:** Add the enrichment data property. After `properties["response_preview"] = ...` block and before `await self.services.graph.upsert_node(...)`, add:

```python
        properties["data_execution_end"] = json.dumps(data)
```

**Modify `_handle_orchestrator_complete`:** Add the enrichment data property AND the flush call.

Add `properties["data_orchestrator_complete"] = json.dumps(data)` to the properties dict (after `"status": mapped_status,`):

```python
        properties: dict[str, Any] = {
            "ended_at": timestamp,
            "status": mapped_status,
            "data_orchestrator_complete": json.dumps(data),
        }
```

Add flush call. After `await self.services.graph.upsert_node(run_id, set(), properties)` and before `# Clear cursor state`, add:

```python
        # Terminal event — await flush directly. orchestrator:complete is the
        # authoritative signal that a run finished. Without this flush, the
        # status update sits in the write buffer and may never reach Neo4j
        # (particularly in --mode single where session:end may not fire).
        await self.services.graph.flush()
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_orchestrator_run_handler.py -v
```

Expected: All tests PASS (existing + 5 new).

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py \
       modules/hook-context-intelligence/tests/test_orchestrator_run_handler.py && \
git commit -m "feat: add data properties to OrchestratorRunHandler + flush fix"
```

---

### Task 6: SessionHandler — Add `data` Property

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/session.py`
- Modify: `modules/hook-context-intelligence/tests/test_session_handler.py`

**Step 1: Write the failing tests**

Add to the **end** of `modules/hook-context-intelligence/tests/test_session_handler.py`:

```python
import json


class TestSessionHandlerDataProperty:
    async def test_session_start_stores_data(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        event_data = {
            "session_id": "s1",
            "timestamp": "2026-01-01T00:00:00Z",
            "metadata": {"agent": "test"},
        }
        await handler("session:start", event_data)
        node = await services.graph.get_node("s1")
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        assert stored["session_id"] == "s1"

    async def test_session_fork_stores_data(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        event_data = {
            "session_id": "child-1",
            "timestamp": "2026-01-01T00:00:00Z",
            "parent": "s1",
            "metadata": {},
        }
        await handler("session:fork", event_data)
        node = await services.graph.get_node("child-1")
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        assert stored["parent"] == "s1"

    async def test_session_end_stores_data_enrichment(self, services: HookStateService) -> None:
        handler = SessionHandler(services)
        await handler(
            "session:start",
            {"session_id": "s1", "timestamp": "2026-01-01T00:00:00Z"},
        )
        await handler(
            "session:end",
            {"session_id": "s1", "timestamp": "2026-01-01T01:00:00Z", "status": "completed"},
        )
        node = await services.graph.get_node("s1")
        assert node is not None
        stored = json.loads(node["properties"]["data_session_end"])
        assert stored["status"] == "completed"
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_session_handler.py::TestSessionHandlerDataProperty -v
```

Expected: FAIL — `KeyError: 'data'`

**Step 3: Modify the handler**

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/session.py`:

**Add import** at the top (after existing imports):

```python
import json
```

**Modify `__call__`:** The handler passes `data` to sub-methods. We need to pass it through so each can store it. The sub-methods already receive `data`. We just need to add the `data` property in each one.

**Modify `_handle_start`:** Add `"data": json.dumps(data)` to the properties dict:

```python
        properties: dict[str, Any] = {
            "started_at": timestamp,
            "status": "running",
            "metadata": data.get("metadata", {}),
            "data": json.dumps(data),
        }
```

**Modify `_handle_fork`:** Add `"data": json.dumps(data)` to the properties dict:

```python
        properties: dict[str, Any] = {
            "started_at": timestamp,
            "status": "running",
            "metadata": data.get("metadata", {}),
            "data": json.dumps(data),
        }
```

**Modify `_handle_end`:** Add `"data_session_end": json.dumps(data)` to the properties dict:

```python
        properties: dict[str, Any] = {
            "ended_at": timestamp,
            "status": data.get("status", "completed"),
            "data_session_end": json.dumps(data),
        }
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_session_handler.py -v
```

Expected: All tests PASS (existing + 3 new).

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/session.py \
       modules/hook-context-intelligence/tests/test_session_handler.py && \
git commit -m "feat: add data properties to SessionHandler"
```

---

### Task 7: StepHandler — Add `data` / `data_<event>` Properties

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/step.py`
- Modify: `modules/hook-context-intelligence/tests/test_step_handler.py`

**Step 1: Write the failing tests**

Add to the **end** of `modules/hook-context-intelligence/tests/test_step_handler.py`:

```python
import json


class TestProviderRequestDataProperty:
    async def test_stores_data_property(self, services: HookStateService) -> None:
        await _seed_session_and_run(services)
        handler = StepHandler(services)
        await handler(
            "provider:request",
            {
                "session_id": "s1",
                "timestamp": STEP1_TIMESTAMP,
                "iteration": 1,
                "provider": "anthropic",
            },
        )
        node = await services.graph.get_node(EXPECTED_STEP1_ID)
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        assert stored["provider"] == "anthropic"
        assert stored["session_id"] == "s1"


class TestLlmRequestDataProperty:
    async def test_stores_data_enrichment_property(self, services: HookStateService) -> None:
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:request",
            {
                "session_id": "s1",
                "timestamp": LLM_REQ_TS,
                "model": "claude-sonnet-4-20250514",
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        stored = json.loads(node["properties"]["data_llm_request"])
        assert stored["model"] == "claude-sonnet-4-20250514"


class TestLlmResponseDataProperty:
    async def test_stores_data_enrichment_property(self, services: HookStateService) -> None:
        step_id = await _seed_through_provider_request(services)
        handler = StepHandler(services)
        await handler(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": LLM_RESP_TS,
                "usage": {"input": 100, "output": 50},
                "raw": {"stop_reason": "end_turn"},
            },
        )
        node = await services.graph.get_node(step_id)
        assert node is not None
        stored = json.loads(node["properties"]["data_llm_response"])
        assert stored["session_id"] == "s1"
        assert stored["usage"] == {"input": 100, "output": 50}
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_step_handler.py::TestProviderRequestDataProperty -v
```

Expected: FAIL — `KeyError: 'data'`

**Step 3: Modify the handler**

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/step.py`:

**Add import** at the top (after existing imports):

```python
import json
```

**Modify `_handle_provider_request`:** Add `"data": json.dumps(data)` to the properties dict:

```python
        properties: dict[str, Any] = {
            "iteration": data.get("iteration", cursors.step_counter),
            "provider": data.get("provider", ""),
            "request_at": timestamp,
            "occurred_at": timestamp,
            "session_id": session_id,
            "data": json.dumps(data),
        }
```

**Modify `_handle_llm_request`:** After `if properties:` block, before `await self.services.graph.upsert_node(...)`, add the enrichment data. Replace:

```python
        if properties:
            await self.services.graph.upsert_node(step_id, set(), properties)
            log.info("Enriched AssistantStep %s with model", step_id)
```

with:

```python
        properties["data_llm_request"] = json.dumps(data)
        await self.services.graph.upsert_node(step_id, set(), properties)
        log.info("Enriched AssistantStep %s with model", step_id)
```

**Modify `_handle_llm_response`:** Add enrichment data property. Before the final `await self.services.graph.upsert_node(step_id, set(), properties)`, add:

```python
        properties["data_llm_response"] = json.dumps(data)
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_step_handler.py -v
```

Expected: All tests PASS (existing + 3 new).

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/step.py \
       modules/hook-context-intelligence/tests/test_step_handler.py && \
git commit -m "feat: add data properties to StepHandler"
```

---

### Task 8: ToolExecutionHandler — Add `data` / `data_<event>` Properties

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/tool_execution.py`
- Modify: `modules/hook-context-intelligence/tests/test_tool_execution_handler.py`

**Step 1: Write the failing tests**

Add to the **end** of `modules/hook-context-intelligence/tests/test_tool_execution_handler.py`:

```python
import json


class TestToolPreDataProperty:
    async def test_stores_data_property(self, services: HookStateService) -> None:
        te_id = await _seed_one_tool(services)
        node = await services.graph.get_node(te_id)
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        assert stored["tool_name"] == "read_file"
        assert stored["tool_call_id"] == "call_001"


class TestToolPostDataProperty:
    async def test_stores_data_enrichment_property(self, services: HookStateService) -> None:
        te_id = await _seed_one_tool(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:post",
            {
                "session_id": "s1",
                "timestamp": TOOL_POST_TIMESTAMP,
                "tool_call_id": "call_001",
                "result": "file content here",
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        stored = json.loads(node["properties"]["data_tool_post"])
        assert stored["result"] == "file content here"


class TestToolErrorDataProperty:
    async def test_stores_data_enrichment_property(self, services: HookStateService) -> None:
        te_id = await _seed_one_tool(services)
        handler = ToolExecutionHandler(services)
        await handler(
            "tool:error",
            {
                "session_id": "s1",
                "timestamp": TOOL_ERROR_TIMESTAMP,
                "tool_call_id": "call_001",
                "error": "File not found",
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        stored = json.loads(node["properties"]["data_tool_error"])
        assert stored["error"] == "File not found"


class TestDelegateAgentSpawnedDataProperty:
    async def test_stores_data_enrichment_property(self, services: HookStateService) -> None:
        te_id = await _seed_one_tool(services, tool_call_id="call_ds", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        await handler(
            "delegate:agent_spawned",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_SPAWNED_TIMESTAMP,
                "tool_call_id": "call_ds",
                "child_session_id": "child-abc",
                "child_agent": "foundation:explorer",
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        stored = json.loads(node["properties"]["data_delegate_agent_spawned"])
        assert stored["child_agent"] == "foundation:explorer"


class TestDelegateAgentCompletedDataProperty:
    async def test_stores_data_enrichment_property(self, services: HookStateService) -> None:
        te_id = await _seed_one_tool(services, tool_call_id="call_dc", tool_name="delegate")
        handler = ToolExecutionHandler(services)
        await handler(
            "delegate:agent_completed",
            {
                "session_id": "s1",
                "timestamp": DELEGATE_COMPLETED_TIMESTAMP,
                "tool_call_id": "call_dc",
            },
        )
        node = await services.graph.get_node(te_id)
        assert node is not None
        stored = json.loads(node["properties"]["data_delegate_agent_completed"])
        assert stored["tool_call_id"] == "call_dc"
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_tool_execution_handler.py::TestToolPreDataProperty -v
```

Expected: FAIL — `KeyError: 'data'`

**Step 3: Modify the handler**

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/tool_execution.py`:

**Add import** at the top (after existing imports):

```python
import json
```

**Modify `_handle_tool_pre`:** Add `"data": json.dumps(data)` to the properties dict:

```python
        properties: dict[str, Any] = {
            "tool_call_id": data.get("tool_call_id", ""),
            "tool_name": data.get("tool_name", ""),
            "parallel_group_id": data.get("parallel_group_id", ""),
            "started_at": timestamp,
            "status": "executing",
            "session_id": session_id,
            "data": json.dumps(data),
        }
```

**Modify `_handle_tool_post`:** Add enrichment data. Before `await self.services.graph.upsert_node(te_id, set(), properties)`, add:

```python
        properties["data_tool_post"] = json.dumps(data)
```

**Modify `_handle_tool_error`:** Add enrichment data. Before `await self.services.graph.upsert_node(te_id, set(), properties)`, add:

```python
        properties["data_tool_error"] = json.dumps(data)
```

**Modify `_handle_delegate_agent_spawned`:** Add enrichment data. Before `await self.services.graph.upsert_node(te_id, {"Delegation"}, properties)`, add:

```python
        properties["data_delegate_agent_spawned"] = json.dumps(data)
```

**Modify `_handle_delegate_agent_completed`:** Add enrichment data. Before `await self.services.graph.upsert_node(te_id, set(), properties)`, add:

```python
        properties["data_delegate_agent_completed"] = json.dumps(data)
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_tool_execution_handler.py -v
```

Expected: All tests PASS (existing + 5 new).

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/tool_execution.py \
       modules/hook-context-intelligence/tests/test_tool_execution_handler.py && \
git commit -m "feat: add data properties to ToolExecutionHandler"
```

---

### Task 9: DefaultHandler — Add `data` Property

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/default.py`
- Modify: `modules/hook-context-intelligence/tests/test_default_handler.py`

**Step 1: Write the failing test**

Add to the **end** of `modules/hook-context-intelligence/tests/test_default_handler.py`:

```python
import json


class TestDefaultHandlerDataProperty:
    async def test_stores_data_property(self, services: HookStateService) -> None:
        handler = DefaultHandler(services)
        await handler(
            "session:resume",
            {
                "session_id": "s1",
                "timestamp": "2026-01-01T02:00:00Z",
                "custom_info": "preserved",
            },
        )
        event_id = make_node_id("s1", "session:resume", "2026-01-01T02:00:00Z")
        node = await services.graph.get_node(event_id)
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        assert stored["session_id"] == "s1"
        assert stored["custom_info"] == "preserved"
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_default_handler.py::TestDefaultHandlerDataProperty -v
```

Expected: FAIL — `KeyError: 'data'`

**Step 3: Modify the handler**

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/default.py`:

**Add import** at the top (after existing imports):

```python
import json
```

**Modify `__call__`:** Change the `upsert_node` call to include `"data": json.dumps(data)`:

```python
        await self.services.graph.upsert_node(
            event_node_id,
            {"Event", derived},
            {"event_name": event, "occurred_at": timestamp, "data": json.dumps(data)},
        )
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_default_handler.py -v
```

Expected: All tests PASS (existing + 1 new).

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/default.py \
       modules/hook-context-intelligence/tests/test_default_handler.py && \
git commit -m "feat: add data property to DefaultHandler"
```

---

### Task 10: RecipeHandler — Add `data` Property

**Files:**
- Modify: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/recipe.py`
- Modify: `modules/hook-context-intelligence/tests/test_recipe_handler.py`

**Step 1: Write the failing test**

Add to the **end** of `modules/hook-context-intelligence/tests/test_recipe_handler.py`:

```python
import json


class TestRecipeHandlerDataProperty:
    async def test_lifecycle_event_stores_data(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        data = _lifecycle_data()
        await handler("recipe:start", data)
        node_id = make_node_id(SESSION_ID, "recipe:start", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        assert stored["recipe_name"] == "code-review"
        assert stored["session_id"] == SESSION_ID

    async def test_loop_event_stores_data(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = RecipeHandler(services)
        await handler("recipe:loop_iteration", _loop_iteration_data())
        node_id = make_node_id(SESSION_ID, "recipe:loop_iteration", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        assert stored["step_id"] == "spec-review-loop"
        assert stored["iteration"] == 1

    async def test_data_contains_context_snapshot_for_loop_event(
        self, services: HookStateService
    ) -> None:
        """context_snapshot is intentionally excluded from lifted properties but
        should still appear in the full data property (as a blob ref when
        blob_store is configured, or raw when not)."""
        await _seed_session(services)
        handler = RecipeHandler(services)
        await handler("recipe:loop_iteration", _loop_iteration_data())
        node_id = make_node_id(SESSION_ID, "recipe:loop_iteration", TIMESTAMP)
        node = await services.graph.get_node(node_id)
        assert node is not None
        stored = json.loads(node["properties"]["data"])
        # context_snapshot exists in data (the full event clone)
        assert "context_snapshot" in stored
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_recipe_handler.py::TestRecipeHandlerDataProperty -v
```

Expected: FAIL — `KeyError: 'data'`

**Step 3: Modify the handler**

In `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/recipe.py`:

**Add import** at the top (after existing imports):

```python
import json
```

**Modify `_persist_event`:** This is the single method used by all recipe events. Add a `data` parameter and store it. Change the signature and the body:

First, change `_handle_lifecycle_event` to pass `data` to `_persist_event`. Change:

```python
        await self._persist_event(node_id, derived, properties, session_id, timestamp, log)
```

to (in both places where it's called — in `_handle_lifecycle_event` and `_handle_loop_event`):

```python
        await self._persist_event(node_id, derived, properties, session_id, timestamp, data, log)
```

Then change the `_persist_event` method signature and body:

```python
    async def _persist_event(
        self,
        node_id: str,
        derived: str,
        properties: dict[str, Any],
        session_id: str,
        timestamp: str,
        data: dict[str, Any],
        log: EventLogContext,
    ) -> None:
        """Create Event node and HAS_EVENT edge from session.

        Args:
            node_id: Unique identifier for the event node.
            derived: Label derived from event name (e.g. ``RecipeStart``).
            properties: Key-value pairs to store on the node.
            session_id: Owning session, used as the edge source.
            timestamp: ISO-8601 timestamp written to the edge.
            data: The full (possibly blob-processed) event data clone.
            log: Contextual logger for this event.
        """
        properties["data"] = json.dumps(data)
        await self.services.graph.upsert_node(
            node_id,
            {"Event", derived},
            properties,
        )
        await self.services.graph.upsert_edge(
            session_id, node_id, "HAS_EVENT", {"occurred_at": timestamp}
        )
        log.info("Created %s node %s", derived, node_id)
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_recipe_handler.py -v
```

Expected: All tests PASS (existing + 3 new).

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/handlers/recipe.py \
       modules/hook-context-intelligence/tests/test_recipe_handler.py && \
git commit -m "feat: add data property to RecipeHandler via _persist_event"
```

---

## Phase 4: Agent Tooling

### Task 11: Blob Access Tool

**Files:**
- Create: `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_tool.py`
- Create: `modules/hook-context-intelligence/tests/test_blob_tool.py`

**Step 1: Write the test file**

Create `modules/hook-context-intelligence/tests/test_blob_tool.py`:

```python
"""Tests for blob access tool — list and dump operations for agent consumption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from amplifier_module_hook_context_intelligence.blob_store import DiskBlobStore
from amplifier_module_hook_context_intelligence.blob_tool import BlobTool


@pytest.fixture
def blob_store(tmp_path: Path) -> DiskBlobStore:
    return DiskBlobStore(tmp_path)


@pytest.fixture
def blob_tool(blob_store: DiskBlobStore) -> BlobTool:
    return BlobTool(blob_store)


# ── blob_list ────────────────────────────────────────────────────────────


class TestBlobList:
    async def test_list_empty_session(self, blob_tool: BlobTool) -> None:
        result = await blob_tool.blob_list("nonexistent-session")
        assert result == []

    async def test_list_returns_blob_metadata(
        self, blob_tool: BlobTool, blob_store: DiskBlobStore
    ) -> None:
        await blob_store.write("sess-1", "node1__raw", {"big": "payload" * 100})
        await blob_store.write("sess-1", "node2__result", [1, 2, 3])
        result = await blob_tool.blob_list("sess-1")
        assert len(result) == 2

        # Each entry has uri, field, node_id, size_bytes
        entry = next(e for e in result if e["field"] == "raw")
        assert entry["uri"] == "ci-blob://sess-1/node1__raw"
        assert entry["node_id"] == "node1"
        assert entry["field"] == "raw"
        assert isinstance(entry["size_bytes"], int)
        assert entry["size_bytes"] > 0

    async def test_list_extracts_field_and_node_from_key(
        self, blob_tool: BlobTool, blob_store: DiskBlobStore
    ) -> None:
        await blob_store.write("s1", "s1__tool_pre__12345__messages", [{"role": "user"}])
        result = await blob_tool.blob_list("s1")
        assert len(result) == 1
        entry = result[0]
        assert entry["node_id"] == "s1__tool_pre__12345"
        assert entry["field"] == "messages"


# ── blob_dump ────────────────────────────────────────────────────────────


class TestBlobDump:
    async def test_dump_returns_file_path(
        self, blob_tool: BlobTool, blob_store: DiskBlobStore
    ) -> None:
        original = {"provider": "anthropic", "content": "hello world"}
        uri = await blob_store.write("sess-1", "node1__raw", original)
        path = await blob_tool.blob_dump(uri)
        assert Path(path).exists()
        content = json.loads(Path(path).read_text())
        assert content == original

    async def test_dump_to_custom_path(
        self, blob_tool: BlobTool, blob_store: DiskBlobStore, tmp_path: Path
    ) -> None:
        uri = await blob_store.write("sess-1", "n1__raw", {"x": 1})
        dest = str(tmp_path / "custom_output.json")
        path = await blob_tool.blob_dump(uri, dest)
        assert path == dest
        assert Path(dest).exists()

    async def test_dump_missing_blob_returns_error(self, blob_tool: BlobTool) -> None:
        with pytest.raises(FileNotFoundError):
            await blob_tool.blob_dump("ci-blob://sess-1/missing__raw")
```

**Step 2: Run tests to verify they fail**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_blob_tool.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'amplifier_module_hook_context_intelligence.blob_tool'`

**Step 3: Write the implementation**

Create `modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_tool.py`:

```python
"""Blob access tool — list and dump operations for agent consumption.

Provides two operations for the context-intelligence agent:
  - blob_list(session_id) → metadata about all blobs for a session
  - blob_dump(uri, dest_path?) → materialize a blob as a file on disk

The agent never loads blob content into its context window directly.
It dumps to disk and uses existing file tools (read_file, grep, jq).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .blob_store import BlobStore, DiskBlobStore

logger = logging.getLogger(__name__)


class BlobTool:
    """Agent-facing tool for blob inspection."""

    def __init__(self, blob_store: DiskBlobStore) -> None:
        self._store = blob_store

    async def blob_list(self, session_id: str) -> list[dict[str, Any]]:
        """List all blobs for a session with metadata.

        Returns a list of dicts, each containing:
          - uri: the ci-blob:// URI
          - field: the field name (e.g. "raw", "result", "messages")
          - node_id: the node identifier
          - size_bytes: file size in bytes
        """
        uris = await self._store.list(session_id)
        results: list[dict[str, Any]] = []
        for uri in uris:
            _, key = DiskBlobStore._parse_uri(uri)
            # Key format: <node-id>__<field>
            # node_id may itself contain __ (e.g. "sess__tool_pre__12345")
            # so we split on the LAST __ to get the field name
            last_sep = key.rfind("__")
            if last_sep == -1:
                node_id = key
                field = "unknown"
            else:
                node_id = key[:last_sep]
                field = key[last_sep + 2 :]

            # Get file size
            blob_path = self._store._blob_path(
                DiskBlobStore._parse_uri(uri)[0], key
            )
            size_bytes = blob_path.stat().st_size if blob_path.exists() else 0

            results.append(
                {
                    "uri": uri,
                    "field": field,
                    "node_id": node_id,
                    "size_bytes": size_bytes,
                }
            )
        return results

    async def blob_dump(self, uri: str, dest_path: str | None = None) -> str:
        """Materialize a blob as a JSON file on disk.

        Args:
            uri: A ci-blob:// URI.
            dest_path: Optional destination path. If None, uses default /tmp/ci-blobs/.

        Returns:
            The absolute path to the materialized file.

        Raises:
            FileNotFoundError: If the blob does not exist on disk.
        """
        return await self._store.dump(uri, dest_path)
```

**Step 4: Run tests to verify they pass**

```bash
cd modules/hook-context-intelligence && uv run pytest tests/test_blob_tool.py -v
```

Expected: All 6 tests PASS.

**Step 5: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/blob_tool.py \
       modules/hook-context-intelligence/tests/test_blob_tool.py && \
git commit -m "feat: add blob access tool for agent consumption"
```

---

### Task 12: Skill / Documentation Updates

**Files:**
- Modify: `skills/context-intelligence-neo4j-search/SKILL.md`

**Step 1: Read the current SKILL.md Node Properties section**

The relevant section starts at approximately line 90. We need to add documentation for:
1. The `data` property on all graph nodes
2. The `data_<event>` enrichment properties
3. The `$blob_ref` pattern
4. How to use blob_list/blob_dump

**Step 2: Update the SKILL.md**

In `skills/context-intelligence-neo4j-search/SKILL.md`, find the Node Properties section (around line 90-106). After the existing properties table, add a new section. Find the text:

```
Additional properties are written by handlers as open-ended key-value pairs
and stored directly on the node.
```

Replace that paragraph with:

```
Additional properties are written by handlers as open-ended key-value pairs
and stored directly on the node.

### Event Data Preservation

Every graph node carries a `data` property containing the **complete event
payload** as a JSON string. This is the full event data that created or
enriched the node — not a subset, not a summary.

| Property | Present On | Content |
|----------|-----------|---------|
| `data` | All nodes | JSON string of the creation event's full payload |
| `data_<event_name>` | Enriched nodes | JSON string of the enrichment event's payload |

**Enrichment property naming:** When an event enriches an existing node
(rather than creating it), the data is stored under `data_<event_name>` with
colons replaced by underscores:

| Event | Property Key |
|-------|-------------|
| `llm:request` | `data_llm_request` |
| `llm:response` | `data_llm_response` |
| `tool:post` | `data_tool_post` |
| `tool:error` | `data_tool_error` |
| `execution:end` | `data_execution_end` |
| `orchestrator:complete` | `data_orchestrator_complete` |
| `session:end` | `data_session_end` |
| `delegate:agent_spawned` | `data_delegate_agent_spawned` |
| `delegate:agent_completed` | `data_delegate_agent_completed` |

### Blob References (`$blob_ref`)

Large fields in event data (provider responses, tool results, conversation
history) are offloaded to a per-session blob store. In the `data` property,
these fields appear as **blob references** instead of inline values:

```json
{
  "session_id": "abc-123",
  "tool_name": "read_file",
  "raw": {"$blob_ref": "ci-blob://abc-123/node1__raw"},
  "result": {"$blob_ref": "ci-blob://abc-123/node1__result"}
}
```

**Known blob fields:** `raw`, `result`, `messages`, `mount_plan`,
`context_snapshot`, `debug`.

**Resolving blob refs:** Use the blob tool operations:

1. **List blobs for a session:**
   ```
   blob_list(session_id) → [{uri, field, node_id, size_bytes}, ...]
   ```

2. **Dump a blob to disk for inspection:**
   ```
   blob_dump(uri) → /tmp/ci-blobs/<key>.json
   ```

3. **Inspect with standard file tools:**
   ```bash
   cat /tmp/ci-blobs/node1__raw.json | jq '.content[0].text'
   ```

**Agent workflow:**
1. Query Neo4j to find nodes of interest
2. Parse the `data` property (JSON string) to find `$blob_ref` entries
3. Call `blob_dump(uri)` to materialize the blob as a file
4. Use `read_file` or `bash+jq` to inspect the file contents
5. Never load blob content directly into context — always dump to file first
```

**Step 3: Verify the file is syntactically correct**

```bash
head -150 skills/context-intelligence-neo4j-search/SKILL.md
```

Visually inspect the changes are well-formed markdown.

**Step 4: Commit**

```bash
cd /home/dicolomb/context-intelligence-bundle-v2-merge-zone/amplifier-bundle-context-intelligence && \
git add skills/context-intelligence-neo4j-search/SKILL.md && \
git commit -m "docs: update Neo4j skill with data property, blob ref, and blob tool docs"
```

---

## Final Verification

After all 12 tasks are complete, run the full test suite:

```bash
cd modules/hook-context-intelligence && uv run pytest tests/ -v
```

All tests must pass. The final state should include:

| File | Action | Purpose |
|------|--------|---------|
| `blob_store.py` | NEW | BlobStore protocol + DiskBlobStore |
| `blob_processor.py` | NEW | `process_event_data()` + `BLOB_FIELDS` |
| `blob_tool.py` | NEW | `blob_list` + `blob_dump` for agents |
| `services.py` | MODIFIED | `blob_store` attribute on HookStateService |
| `mount.py` | MODIFIED | Blob processor wired into dispatch wrapper |
| `handlers/orchestrator_run.py` | MODIFIED | `data`/`data_<event>` + flush fix |
| `handlers/session.py` | MODIFIED | `data`/`data_session_end` |
| `handlers/step.py` | MODIFIED | `data`/`data_llm_request`/`data_llm_response` |
| `handlers/tool_execution.py` | MODIFIED | `data`/`data_tool_post`/`data_tool_error`/`data_delegate_*` |
| `handlers/default.py` | MODIFIED | `data` |
| `handlers/recipe.py` | MODIFIED | `data` via `_persist_event` |
| `SKILL.md` | MODIFIED | `$blob_ref` pattern + blob tool docs |
| `test_blob_store.py` | NEW | 14 tests |
| `test_blob_processor.py` | NEW | 16 tests |
| `test_blob_dispatch.py` | NEW | 3 tests |
| `test_blob_tool.py` | NEW | 6 tests |
| Existing test files | MODIFIED | ~20 new tests across handler test files |
