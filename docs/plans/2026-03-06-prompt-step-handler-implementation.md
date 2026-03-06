# PromptStep Handler Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Implement `prompt:submit` event handling to create `:PromptStep` nodes linked to Sessions via `HAS_STEP`, plus shared utilities (`make_node_id`, `HandlerLogger`) and a SessionHandler retrofit.

**Architecture:** A shared `utils.py` module provides two cross-cutting utilities used by all handlers: `make_node_id` generates deterministic node IDs from event data (`{session_id}:{event_name}:{timestamp_ms}`), and `HandlerLogger`/`EventLogContext` provide structured logging with handler name, session ID, and event name pre-bound. The `OrchestratorRunHandler` gains `prompt:submit` handling that validates the Session exists, creates a `:PromptStep` node with labels `{Step, PromptStep}`, and links it to the Session via a `HAS_STEP` edge. The existing `SessionHandler` is retrofitted to use both utilities.

**Tech Stack:** Python 3.11+, pytest with `asyncio_mode = "auto"`, DuckDB in-memory graph store, `amplifier_core.models.HookResult`

**Design doc:** `docs/plans/2026-03-06-prompt-step-handler-design.md`
**State machine:** `context/prompt-submit-handler.dot`

---

## Codebase Orientation

**You must read these files before starting any task.** They are short and contain the patterns you need to follow.

| File | Why |
|------|-----|
| `amplifier_module_hook_context_intelligence/handlers/session.py` | Handler pattern to follow: imports, constructor, `handled_events`, `__call__` |
| `amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py` | The stub you'll modify in Task 3 |
| `amplifier_module_hook_context_intelligence/services.py` | `HookStateService` — the `services` object all handlers receive |
| `amplifier_module_hook_context_intelligence/protocol.py` | `EventHandler` protocol — your handler must conform |
| `tests/conftest.py` | The `services` fixture you'll use in all tests |
| `tests/test_session_handler.py` | Test patterns: class-based, top-level imports, async without decorators |

All paths below are relative to:
```
amplifier-bundle-context-intelligence/modules/hook-context-intelligence/
```

Run all commands from that directory:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence
```

---

## Task 1: Create `utils.py` with `make_node_id` and `HandlerLogger`

**Files:**
- Create: `amplifier_module_hook_context_intelligence/utils.py`
- Create: `tests/test_utils.py`

### Step 1: Write failing tests for `make_node_id`

Create the test file `tests/test_utils.py` with these tests:

```python
"""Tests for shared utilities — make_node_id and HandlerLogger."""

from __future__ import annotations

import logging

from amplifier_module_hook_context_intelligence.utils import (
    EventLogContext,
    HandlerLogger,
    make_node_id,
)


class TestMakeNodeId:
    def test_basic_iso_timestamp(self) -> None:
        result = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        assert result == "s1:prompt:submit:1767225600000"

    def test_timestamp_with_fractional_seconds(self) -> None:
        result = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00.500Z")
        assert result == "s1:prompt:submit:1767225600500"

    def test_timestamp_with_offset(self) -> None:
        # 2026-01-01T02:00:00Z is the same instant as the +00:00 form
        result = make_node_id("s1", "session:resume", "2026-01-01T02:00:00+00:00")
        assert result == "s1:session:resume:1767232800000"

    def test_deterministic_same_input_same_output(self) -> None:
        a = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        b = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        assert a == b

    def test_different_events_different_ids(self) -> None:
        a = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        b = make_node_id("s1", "execution:start", "2026-01-01T00:00:00Z")
        assert a != b

    def test_different_sessions_different_ids(self) -> None:
        a = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        b = make_node_id("s2", "prompt:submit", "2026-01-01T00:00:00Z")
        assert a != b

    def test_session_resume_matches_expected_pattern(self) -> None:
        """Verify the pattern used by SessionHandler retrofit (Task 2)."""
        result = make_node_id("s1", "session:resume", "2026-01-01T02:00:00Z")
        assert result == "s1:session:resume:1767232800000"
```

### Step 2: Run tests to verify they fail

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_utils.py -v
```
Expected: FAIL — `ModuleNotFoundError: No module named 'amplifier_module_hook_context_intelligence.utils'`

### Step 3: Implement `make_node_id`

Create `amplifier_module_hook_context_intelligence/utils.py`:

```python
"""Shared utilities for context-intelligence handlers."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any


def make_node_id(session_id: str, event_name: str, timestamp: str) -> str:
    """Generate a deterministic node ID from event data.

    Pattern: ``{session_id}:{event_name}:{timestamp_ms}``

    Session nodes are the EXCEPTION — they use ``session_id`` directly
    because ``session_id`` is the foreign key the entire event system references.
    """
    dt = datetime.fromisoformat(timestamp)
    epoch_ms = int(dt.timestamp() * 1000)
    return f"{session_id}:{event_name}:{epoch_ms}"
```

### Step 4: Run `make_node_id` tests to verify they pass

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_utils.py::TestMakeNodeId -v
```
Expected: 7 PASSED

### Step 5: Write failing tests for `HandlerLogger` and `EventLogContext`

Append to `tests/test_utils.py`:

```python


class TestHandlerLogger:
    def test_with_event_returns_event_log_context(self) -> None:
        raw_logger = logging.getLogger("test.handler_logger")
        handler_log = HandlerLogger("TestHandler", raw_logger)
        ctx = handler_log.with_event("prompt:submit", {"session_id": "s1", "timestamp": "t"})
        assert isinstance(ctx, EventLogContext)

    def test_with_event_missing_session_id_uses_empty_string(self) -> None:
        raw_logger = logging.getLogger("test.handler_logger")
        handler_log = HandlerLogger("TestHandler", raw_logger)
        ctx = handler_log.with_event("prompt:submit", {"timestamp": "t"})
        assert isinstance(ctx, EventLogContext)


class TestEventLogContext:
    def test_info_includes_prefix(self, caplog: logging.LogCaptureFixture) -> None:
        raw_logger = logging.getLogger("test.event_log_ctx")
        ctx = EventLogContext("MyHandler", raw_logger, "s1", "prompt:submit", "")
        with caplog.at_level(logging.INFO, logger="test.event_log_ctx"):
            ctx.info("hello %s", "world")
        assert len(caplog.records) == 1
        assert "[MyHandler]" in caplog.records[0].message
        assert "[s1]" in caplog.records[0].message
        assert "[prompt:submit]" in caplog.records[0].message
        assert "hello world" in caplog.records[0].message

    def test_warning_includes_prefix(self, caplog: logging.LogCaptureFixture) -> None:
        raw_logger = logging.getLogger("test.event_log_ctx")
        ctx = EventLogContext("MyHandler", raw_logger, "s1", "session:fork", "")
        with caplog.at_level(logging.WARNING, logger="test.event_log_ctx"):
            ctx.warning("no parent")
        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == "WARNING"
        assert "[MyHandler] [s1] [session:fork] no parent" == caplog.records[0].message

    def test_error_includes_prefix(self, caplog: logging.LogCaptureFixture) -> None:
        raw_logger = logging.getLogger("test.event_log_ctx")
        ctx = EventLogContext("MyHandler", raw_logger, "s1", "prompt:submit", "")
        with caplog.at_level(logging.ERROR, logger="test.event_log_ctx"):
            ctx.error("Session node not found")
        assert len(caplog.records) == 1
        assert caplog.records[0].levelname == "ERROR"
        assert "[MyHandler] [s1] [prompt:submit] Session node not found" == caplog.records[0].message
```

### Step 6: Run tests to verify `HandlerLogger`/`EventLogContext` tests fail

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_utils.py::TestHandlerLogger tests/test_utils.py::TestEventLogContext -v
```
Expected: FAIL — `ImportError: cannot import name 'HandlerLogger'`

### Step 7: Implement `HandlerLogger` and `EventLogContext`

Append to `amplifier_module_hook_context_intelligence/utils.py` (after the `make_node_id` function):

```python


class HandlerLogger:
    """Structured logging wrapper that binds handler name to every log call."""

    def __init__(self, handler_name: str, logger: logging.Logger) -> None:
        self._handler = handler_name
        self._logger = logger

    def with_event(self, event: str, data: dict[str, Any]) -> EventLogContext:
        """Create a log context bound to a specific event."""
        return EventLogContext(
            self._handler,
            self._logger,
            session_id=data.get("session_id", ""),
            event=event,
            timestamp=data.get("timestamp", ""),
        )


class EventLogContext:
    """Log context with handler name, session_id, and event name pre-bound."""

    def __init__(
        self,
        handler: str,
        logger: logging.Logger,
        session_id: str,
        event: str,
        timestamp: str,
    ) -> None:
        self._prefix = f"[{handler}] [{session_id}] [{event}]"
        self._logger = logger

    def info(self, msg: str, *args: Any) -> None:
        self._logger.info(f"{self._prefix} {msg}", *args)

    def warning(self, msg: str, *args: Any) -> None:
        self._logger.warning(f"{self._prefix} {msg}", *args)

    def error(self, msg: str, *args: Any) -> None:
        self._logger.error(f"{self._prefix} {msg}", *args)
```

### Step 8: Run all `test_utils.py` tests to verify they pass

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_utils.py -v
```
Expected: 12 PASSED (7 `make_node_id` + 2 `HandlerLogger` + 3 `EventLogContext`)

### Step 9: Commit

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/utils.py tests/test_utils.py && git commit -m "feat: add make_node_id and HandlerLogger utilities"
```

---

## Task 2: Retrofit SessionHandler to use `HandlerLogger` and `make_node_id`

**Files:**
- Modify: `amplifier_module_hook_context_intelligence/handlers/session.py`
- Modify: `tests/test_session_handler.py`

**Context:** The SessionHandler currently uses raw `logger.error(...)` calls and a manual f-string for the resume Event node_id. We're retrofitting it to use `HandlerLogger` and `make_node_id` from the new `utils.py`.

### Step 1: Update test expectations for the new resume node_id pattern

In `tests/test_session_handler.py`, make these changes:

**Change 1** — In `TestSessionResume.test_resume_creates_event_node` (around line 264), change the `event_id`:

```python
# OLD:
event_id = "s1:event:session_resume:2026-01-01T02:00:00Z"

# NEW:
event_id = "s1:session:resume:1767232800000"
```

**Change 2** — In `TestSessionResume.test_resume_creates_has_event_edge` (around line 279), change the `event_id`:

```python
# OLD:
event_id = "s1:event:session_resume:2026-01-01T02:00:00Z"

# NEW:
event_id = "s1:session:resume:1767232800000"
```

These are the only two tests that reference the old resume node_id pattern. All other resume tests check the Session node (which keeps `session_id` as its node_id — unchanged).

### Step 2: Run tests to verify they fail (old node_id won't match)

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_session_handler.py::TestSessionResume -v
```
Expected: 2 FAIL (`test_resume_creates_event_node`, `test_resume_creates_has_event_edge`) — the handler still generates the old node_id format, so `get_node` returns `None`

### Step 3: Update SessionHandler to use `HandlerLogger` and `make_node_id`

Edit `amplifier_module_hook_context_intelligence/handlers/session.py`. Here is the complete updated file:

```python
"""SessionHandler — owns :Session node lifecycle events."""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService
from ..utils import EventLogContext, HandlerLogger, make_node_id

logger = logging.getLogger(__name__)


class SessionHandler:
    handled_events: frozenset[str] = frozenset(
        {
            "session:start",
            "session:fork",
            "session:end",
            "session:resume",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self._log = HandlerLogger("SessionHandler", logger)

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        log = self._log.with_event(event, data)

        session_id = data.get("session_id")
        if not session_id:
            log.error("No session_id in event data")
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")

        if event == "session:start":
            await self._handle_start(session_id, timestamp, data)
        elif event == "session:fork":
            await self._handle_fork(session_id, timestamp, data, log)
        elif event == "session:end":
            await self._handle_end(session_id, timestamp, data)
        elif event == "session:resume":
            await self._handle_resume(session_id, timestamp, data)

        return HookResult(action="continue")

    async def _handle_start(self, session_id: str, timestamp: str, data: dict[str, Any]) -> None:
        parent_id = (data.get("parent_id") or "").strip()

        if parent_id:
            labels: set[str] = {"Session", "Subsession"}
        else:
            labels = {"Session", "Root"}

        properties: dict[str, Any] = {
            "started_at": timestamp,
            "status": "running",
            "metadata": data.get("metadata", {}),
        }

        await self.services.graph.upsert_node(session_id, labels, properties)

        if parent_id:
            await self.services.graph.upsert_edge(
                session_id, parent_id, "SUBSESSION_OF", {"occurred_at": timestamp}
            )

    async def _handle_fork(
        self, session_id: str, timestamp: str, data: dict[str, Any], log: EventLogContext
    ) -> None:
        parent = data.get("parent")

        if parent:
            labels: set[str] = {"Session", "Subsession", "ForkedSession"}
        else:
            labels = {"Session", "Root", "ForkedSession"}
            log.warning("session:fork for %r has no parent — degrading to Root", session_id)

        properties: dict[str, Any] = {
            "started_at": timestamp,
            "status": "running",
            "metadata": data.get("metadata", {}),
        }

        await self.services.graph.upsert_node(session_id, labels, properties)

        if parent:
            await self.services.graph.upsert_edge(
                session_id, parent, "SUBSESSION_OF", {"occurred_at": timestamp}
            )

    async def _handle_end(self, session_id: str, timestamp: str, data: dict[str, Any]) -> None:
        labels: set[str] = {"Session"}
        properties: dict[str, Any] = {
            "ended_at": timestamp,
            "status": data.get("status", "completed"),
        }

        await self.services.graph.upsert_node(session_id, labels, properties)

    async def _handle_resume(self, session_id: str, timestamp: str, data: dict[str, Any]) -> None:
        # Add Resumed label to the session node
        await self.services.graph.upsert_node(session_id, {"Session", "Resumed"}, {})

        # Create Event node with deterministic node_id
        event_node_id = make_node_id(session_id, "session:resume", timestamp)
        await self.services.graph.upsert_node(
            event_node_id,
            {"Event", "SessionResume"},
            {"occurred_at": timestamp},
        )

        # Create HAS_EVENT edge from session to event
        await self.services.graph.upsert_edge(
            session_id, event_node_id, "HAS_EVENT", {"occurred_at": timestamp}
        )
```

### Step 4: Run all session handler tests to verify they pass

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_session_handler.py -v
```
Expected: ALL PASSED (all existing tests including the 2 updated resume tests)

### Step 5: Run the full existing test suite to catch regressions

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/ -v
```
Expected: ALL PASSED. The handler conformance tests in `test_handlers.py` should still pass because:
- SessionHandler still conforms to `EventHandler` protocol
- `handled_events` is unchanged
- `__call__` still returns `HookResult(action="continue")`

### Step 6: Commit

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/handlers/session.py tests/test_session_handler.py && git commit -m "refactor: retrofit SessionHandler with HandlerLogger and make_node_id"
```

---

## Task 3: Implement `prompt:submit` handling on OrchestratorRunHandler

**Files:**
- Modify: `amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py`
- Create: `tests/test_prompt_step_handler.py`

**Context:** The `OrchestratorRunHandler` is currently a stub — its `__call__` returns `HookResult(action="continue")` for all events. You'll add real logic for `prompt:submit` while keeping the other 3 events (`execution:start`, `execution:end`, `orchestrator:complete`) as stubs.

**The state machine** (from `context/prompt-submit-handler.dot`):
```
prompt:submit arrives
  → Extract session_id, timestamp, prompt
  → Validate: get_node(session_id)
    → None? ERROR: log, return, no mutations
    → exists? make_node_id → upsert PromptStep node → upsert HAS_STEP edge → return
```

### Step 1: Write failing tests for `prompt:submit` happy path

Create `tests/test_prompt_step_handler.py`:

```python
"""Tests for prompt:submit handling on OrchestratorRunHandler."""

from __future__ import annotations

from amplifier_module_hook_context_intelligence.handlers.orchestrator_run import (
    OrchestratorRunHandler,
)
from amplifier_module_hook_context_intelligence.handlers.session import SessionHandler
from amplifier_module_hook_context_intelligence.services import HookStateService
from amplifier_module_hook_context_intelligence.utils import make_node_id


# --- Helpers ---

TIMESTAMP = "2026-03-06T01:00:00Z"
EXPECTED_NODE_ID = "s1:prompt:submit:1772758800000"


async def _seed_session(services: HookStateService, session_id: str = "s1") -> None:
    """Create a Session node via SessionHandler so the graph has it."""
    session_handler = SessionHandler(services)
    await session_handler(
        "session:start",
        {"session_id": session_id, "timestamp": "2026-01-01T00:00:00Z"},
    )


# --- Happy path ---


class TestPromptSubmitCreatesPromptStep:
    async def test_creates_prompt_step_node(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None

    async def test_prompt_step_has_correct_labels(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        assert node["labels"] == {"Step", "PromptStep"}

    async def test_prompt_step_stores_prompt_text(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        assert node["properties"]["prompt_text"] == "Hello AI"
        assert node["properties"]["prompt_preview"] == "Hello AI"

    async def test_prompt_preview_truncated_to_200_chars(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        long_prompt = "x" * 300
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": long_prompt},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        assert node["properties"]["prompt_text"] == long_prompt
        assert node["properties"]["prompt_preview"] == "x" * 200
        assert len(node["properties"]["prompt_preview"]) == 200

    async def test_prompt_step_properties(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is not None
        assert node["properties"]["iteration"] == 0
        assert node["properties"]["occurred_at"] == TIMESTAMP
        assert node["properties"]["session_id"] == "s1"

    async def test_creates_has_step_edge(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        edge = await services.graph.get_edge("s1", EXPECTED_NODE_ID, "HAS_STEP")
        assert edge is not None
        assert edge["properties"]["occurred_at"] == TIMESTAMP

    async def test_node_id_matches_make_node_id(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        expected_id = make_node_id("s1", "prompt:submit", TIMESTAMP)
        node = await services.graph.get_node(expected_id)
        assert node is not None
        assert expected_id == EXPECTED_NODE_ID

    async def test_returns_hook_result_continue(self, services: HookStateService) -> None:
        await _seed_session(services)
        handler = OrchestratorRunHandler(services)
        result = await handler(
            "prompt:submit",
            {"session_id": "s1", "timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        assert result.action == "continue"
```

### Step 2: Run tests to verify they fail

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_prompt_step_handler.py::TestPromptSubmitCreatesPromptStep -v
```
Expected: FAIL — the stub handler creates no nodes, so `get_node` returns `None`

### Step 3: Write failing tests for error paths and stub events

Append to `tests/test_prompt_step_handler.py`:

```python


# --- Error paths ---


class TestPromptSubmitErrorPaths:
    async def test_missing_session_id_returns_continue(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        result = await handler(
            "prompt:submit",
            {"timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        assert result.action == "continue"

    async def test_missing_session_id_creates_no_nodes(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        # No nodes should exist — graph should be empty
        node = await services.graph.get_node(EXPECTED_NODE_ID)
        assert node is None

    async def test_session_not_found_returns_continue(self, services: HookStateService) -> None:
        """Session node doesn't exist in graph — ERROR state."""
        handler = OrchestratorRunHandler(services)
        # Do NOT seed the session — it should not exist
        result = await handler(
            "prompt:submit",
            {"session_id": "nonexistent", "timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        assert result.action == "continue"

    async def test_session_not_found_creates_no_nodes(self, services: HookStateService) -> None:
        """Session not found — no PromptStep node should be created."""
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "nonexistent", "timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        expected_id = make_node_id("nonexistent", "prompt:submit", TIMESTAMP)
        node = await services.graph.get_node(expected_id)
        assert node is None

    async def test_session_not_found_creates_no_edges(self, services: HookStateService) -> None:
        """Session not found — no HAS_STEP edge should be created."""
        handler = OrchestratorRunHandler(services)
        await handler(
            "prompt:submit",
            {"session_id": "nonexistent", "timestamp": TIMESTAMP, "prompt": "Hello AI"},
        )
        expected_id = make_node_id("nonexistent", "prompt:submit", TIMESTAMP)
        edge = await services.graph.get_edge("nonexistent", expected_id, "HAS_STEP")
        assert edge is None


# --- Stub events (not yet implemented) ---


class TestStubEventsReturnContinue:
    async def test_execution_start_returns_continue(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        result = await handler("execution:start", {"timestamp": "2026-01-01T00:00:00Z"})
        assert result.action == "continue"

    async def test_execution_end_returns_continue(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        result = await handler("execution:end", {"timestamp": "2026-01-01T00:00:00Z"})
        assert result.action == "continue"

    async def test_orchestrator_complete_returns_continue(self, services: HookStateService) -> None:
        handler = OrchestratorRunHandler(services)
        result = await handler("orchestrator:complete", {"timestamp": "2026-01-01T00:00:00Z"})
        assert result.action == "continue"
```

### Step 4: Run all new tests to verify error path tests fail

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_prompt_step_handler.py -v
```
Expected: Happy path tests FAIL (no nodes created). Error path tests and stub tests may PASS (the stub already returns `HookResult(action="continue")`), but that's fine — the happy path failures confirm we need the implementation.

### Step 5: Implement `prompt:submit` handling

Replace the contents of `amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py` with:

```python
"""OrchestratorRunHandler — owns :OrchestratorRun and :Step:PromptStep lifecycle events."""

from __future__ import annotations

import logging
from typing import Any

from amplifier_core.models import HookResult

from ..services import HookStateService
from ..utils import EventLogContext, HandlerLogger, make_node_id

logger = logging.getLogger(__name__)


class OrchestratorRunHandler:
    handled_events: frozenset[str] = frozenset(
        {
            "prompt:submit",
            "execution:start",
            "execution:end",
            "orchestrator:complete",
        }
    )

    def __init__(self, services: HookStateService) -> None:
        self.services = services
        self._log = HandlerLogger("OrchestratorRunHandler", logger)

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        log = self._log.with_event(event, data)

        if event == "prompt:submit":
            return await self._handle_prompt_submit(data, log)

        # Stubs for future implementation
        return HookResult(action="continue")

    async def _handle_prompt_submit(
        self, data: dict[str, Any], log: EventLogContext
    ) -> HookResult:
        session_id = data.get("session_id")
        if not session_id:
            log.error("No session_id in event data")
            return HookResult(action="continue")

        timestamp = data.get("timestamp", "")

        # Validate: Session must already exist
        session_node = await self.services.graph.get_node(session_id)
        if session_node is None:
            log.error("Session node not found")
            return HookResult(action="continue")

        # Generate deterministic node ID
        node_id = make_node_id(session_id, "prompt:submit", timestamp)

        # Create PromptStep node
        prompt_text = data.get("prompt", "")
        await self.services.graph.upsert_node(
            node_id,
            {"Step", "PromptStep"},
            {
                "iteration": 0,
                "prompt_text": prompt_text,
                "prompt_preview": prompt_text[:200],
                "occurred_at": timestamp,
                "session_id": session_id,
            },
        )

        # Create HAS_STEP edge from Session to PromptStep
        await self.services.graph.upsert_edge(
            session_id, node_id, "HAS_STEP", {"occurred_at": timestamp}
        )

        log.info("Created PromptStep node %s", node_id)
        return HookResult(action="continue")
```

### Step 6: Run all prompt step tests to verify they pass

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/test_prompt_step_handler.py -v
```
Expected: ALL PASSED (8 happy path + 5 error path + 3 stub = 16 tests)

### Step 7: Commit

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git add amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py tests/test_prompt_step_handler.py && git commit -m "feat: implement prompt:submit handler — creates PromptStep with HAS_STEP edge"
```

---

## Task 4: Verify DOT file and full test suite

**Files:**
- Verify: `../../context/prompt-submit-handler.dot` (relative to module root; absolute: `amplifier-bundle-context-intelligence/context/prompt-submit-handler.dot`)

### Step 1: Verify the DOT file exists

Run:
```bash
cat amplifier-bundle-context-intelligence/context/prompt-submit-handler.dot | head -5
```
Expected output (first 5 lines):
```
digraph prompt_submit_handler {
    rankdir=TB;
    label="prompt:submit Handler — PromptStep Creation\nCreates Step:PromptStep node linked to Session via HAS_STEP";
    labelloc=t;
    fontsize=14;
```

If the file doesn't exist, something went wrong in the design phase. Do NOT create it here — check git history for commit `8f715ec`.

### Step 2: Run the full test suite

Run:
```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && uv run pytest tests/ -v
```
Expected: ALL PASSED. This includes:
- `test_utils.py` — 12 tests (make_node_id + HandlerLogger + EventLogContext)
- `test_session_handler.py` — all existing tests with updated resume node_id expectations
- `test_prompt_step_handler.py` — 16 tests (happy path + error path + stubs)
- `test_handlers.py` — protocol conformance, event claims, coverage (all should still pass)
- All other existing tests (`test_duckdb_store.py`, `test_services.py`, etc.)

If any test fails, fix it before proceeding. The most likely issues:
- Import errors if `utils.py` has a typo
- The `test_handler_returns_hook_result` parametrized test in `test_handlers.py` calls `OrchestratorRunHandler` with `{"timestamp": "2026-01-01T00:00:00Z"}` (no `session_id`). With our implementation, if `prompt:submit` is the event picked by `next(iter(events))`, it hits the "No session_id" error path and returns `HookResult(action="continue")`. This is correct — the test should pass.

### Step 3: Final commit (if any fixes were needed)

```bash
cd amplifier-bundle-context-intelligence/modules/hook-context-intelligence && git status
```

If there are changes (fixes from Step 2), commit them:
```bash
git add -A && git commit -m "fix: address test suite issues from PromptStep handler integration"
```

If `git status` is clean — you're done. No commit needed.

---

## Summary of Changes

| File | Action | Description |
|------|--------|-------------|
| `amplifier_module_hook_context_intelligence/utils.py` | Create | `make_node_id`, `HandlerLogger`, `EventLogContext` |
| `amplifier_module_hook_context_intelligence/handlers/session.py` | Modify | Retrofit to use `HandlerLogger` and `make_node_id` for resume |
| `amplifier_module_hook_context_intelligence/handlers/orchestrator_run.py` | Modify | Add `prompt:submit` handling (PromptStep + HAS_STEP) |
| `tests/test_utils.py` | Create | 12 tests for `make_node_id`, `HandlerLogger`, `EventLogContext` |
| `tests/test_session_handler.py` | Modify | Update 2 resume tests for new node_id pattern |
| `tests/test_prompt_step_handler.py` | Create | 16 tests for prompt:submit happy path, error paths, stubs |
| `context/prompt-submit-handler.dot` | Verify | Already committed at `8f715ec` |

## What Is NOT in This Plan

These items are explicitly deferred:
- `execution:start` handler (OrchestratorRun creation)
- `execution:end` / `orchestrator:complete` handling
- Other handlers (Step, Tool, Recipe, System, Default)
- Re-wiring `HAS_STEP` from Session to OrchestratorRun when `execution:start` arrives
- Research doc changes (already done in a previous session)