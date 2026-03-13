"""Smoke tests: context-intelligence storage layout on disk.

Verifies that the hook writes events.jsonl and metadata.json into the
correct directory structure:

    {base_path}/{project_slug}/sessions/{session_id}/context-intelligence/
                                                     ^^^^^^^^^^^^^^^^^^^
                                                     this subfolder is ours

The 'context-intelligence' subfolder MUST live under the normal Amplifier
sessions/{session_id}/ directory — never at the session root, never
alongside it, never anywhere else.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_hook_context_intelligence import mount

LOGGING_PRIORITY = 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DEFAULT_EVENTS = [
    "session:start",
    "session:end",
    "session:fork",
    "tool:pre",
    "tool:post",
    "orchestrator:start",
    "orchestrator:complete",
]


def _make_coordinator(
    contributed_events: list[list[str]] | None = None,
    working_dir: str = "/home/user/project",
) -> MagicMock:
    coordinator = MagicMock()
    coordinator.config = {}

    unregister_fns: list[MagicMock] = []

    def _register(*args: Any, **kwargs: Any) -> MagicMock:
        unreg = MagicMock()
        unregister_fns.append(unreg)
        return unreg

    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(side_effect=_register)
    coordinator._unregister_fns = unregister_fns
    if contributed_events is None:
        contributed_events = [_DEFAULT_EVENTS]
    coordinator.collect_contributions = AsyncMock(
        return_value=contributed_events,
    )

    def _get_capability(name: str) -> Any:
        if name == "session.working_dir":
            return working_dir
        return None

    coordinator.get_capability = MagicMock(side_effect=_get_capability)
    return coordinator


def _extract_logging_handler(coordinator: MagicMock) -> Any:
    for call in coordinator.hooks.register.call_args_list:
        if (
            call.kwargs.get("priority") == LOGGING_PRIORITY
            or call.kwargs.get("name") == "LoggingHandler"
        ):
            return call.args[1]
    msg = "LoggingHandler not found in registrations"
    raise AssertionError(msg)


async def _fire_session(
    handler: Any,
    session_id: str,
    *,
    extra_events: list[tuple[str, dict[str, Any]]] | None = None,
) -> None:
    """Fire a minimal session:start -> [extra] -> session:end sequence."""
    await handler(
        "session:start",
        {
            "session_id": session_id,
            "timestamp": "2026-03-12T10:00:00Z",
            "working_dir": "/home/user/project",
        },
    )
    for event_name, event_data in extra_events or []:
        await handler(event_name, {"session_id": session_id, **event_data})
    await handler(
        "session:end",
        {
            "session_id": session_id,
            "timestamp": "2026-03-12T10:00:05Z",
            "status": "completed",
        },
    )


# ---------------------------------------------------------------------------
# Tests: directory structure
# ---------------------------------------------------------------------------


class TestStorageDirectoryStructure:
    """The context-intelligence subfolder lives under sessions/{id}/."""

    async def test_files_land_in_context_intelligence_subfolder(
        self,
        tmp_path: Path,
    ) -> None:
        coordinator = _make_coordinator()
        cleanup = await mount(
            coordinator,
            config={"base_path": str(tmp_path), "project_slug": "myproject"},
        )
        handler = _extract_logging_handler(coordinator)
        await _fire_session(handler, "sess-001")

        ci_dir = tmp_path / "myproject" / "sessions" / "sess-001" / "context-intelligence"
        assert ci_dir.is_dir(), f"Expected context-intelligence dir at {ci_dir}"
        assert (ci_dir / "events.jsonl").is_file()
        assert (ci_dir / "metadata.json").is_file()
        cleanup()

    async def test_no_files_at_session_root(self, tmp_path: Path) -> None:
        """events.jsonl and metadata.json must NOT be at session root level."""
        coordinator = _make_coordinator()
        cleanup = await mount(
            coordinator,
            config={"base_path": str(tmp_path), "project_slug": "myproject"},
        )
        handler = _extract_logging_handler(coordinator)
        await _fire_session(handler, "sess-002")

        session_root = tmp_path / "myproject" / "sessions" / "sess-002"
        assert not (session_root / "events.jsonl").exists(), (
            "events.jsonl must be inside context-intelligence/, not at session root"
        )
        assert not (session_root / "metadata.json").exists(), (
            "metadata.json must be inside context-intelligence/, not at session root"
        )
        cleanup()

    async def test_multiple_sessions_each_get_own_subfolder(
        self,
        tmp_path: Path,
    ) -> None:
        coordinator = _make_coordinator()
        cleanup = await mount(
            coordinator,
            config={"base_path": str(tmp_path), "project_slug": "multi"},
        )
        handler = _extract_logging_handler(coordinator)

        await _fire_session(handler, "sess-aaa")
        await _fire_session(handler, "sess-bbb")

        for sid in ("sess-aaa", "sess-bbb"):
            ci_dir = tmp_path / "multi" / "sessions" / sid / "context-intelligence"
            assert ci_dir.is_dir(), f"Missing dir for {sid}"
            assert (ci_dir / "events.jsonl").is_file()
            assert (ci_dir / "metadata.json").is_file()
        cleanup()


# ---------------------------------------------------------------------------
# Tests: file content integrity
# ---------------------------------------------------------------------------


class TestStorageFileContent:
    """Files written have correct structure and content."""

    async def test_events_jsonl_records_are_valid_json(
        self,
        tmp_path: Path,
    ) -> None:
        coordinator = _make_coordinator()
        cleanup = await mount(
            coordinator,
            config={"base_path": str(tmp_path), "project_slug": "proj"},
        )
        handler = _extract_logging_handler(coordinator)
        await _fire_session(
            handler,
            "sess-json",
            extra_events=[
                ("tool:pre", {"timestamp": "2026-03-12T10:00:01Z", "tool_name": "bash"}),
                ("tool:post", {"timestamp": "2026-03-12T10:00:02Z", "tool_name": "bash"}),
            ],
        )

        events_file = (
            tmp_path / "proj" / "sessions" / "sess-json" / "context-intelligence" / "events.jsonl"
        )
        lines = events_file.read_text().strip().split("\n")
        assert len(lines) == 4, f"Expected 4 events, got {len(lines)}"

        for i, line in enumerate(lines):
            record = json.loads(line)
            assert set(record.keys()) == {"event", "timestamp", "data"}, (
                f"Line {i}: record keys must be exactly {{event, timestamp, data}}, "
                f"got {set(record.keys())}"
            )
        cleanup()

    async def test_metadata_json_has_required_fields(
        self,
        tmp_path: Path,
    ) -> None:
        coordinator = _make_coordinator()
        cleanup = await mount(
            coordinator,
            config={"base_path": str(tmp_path), "project_slug": "proj"},
        )
        handler = _extract_logging_handler(coordinator)
        await _fire_session(handler, "sess-meta")

        meta_file = (
            tmp_path / "proj" / "sessions" / "sess-meta" / "context-intelligence" / "metadata.json"
        )
        meta = json.loads(meta_file.read_text())
        assert meta["session_id"] == "sess-meta"
        assert meta["status"] == "completed"
        assert "started_at" in meta
        assert "ended_at" in meta
        cleanup()

    async def test_events_preserve_chronological_order(
        self,
        tmp_path: Path,
    ) -> None:
        coordinator = _make_coordinator()
        cleanup = await mount(
            coordinator,
            config={"base_path": str(tmp_path), "project_slug": "proj"},
        )
        handler = _extract_logging_handler(coordinator)
        await _fire_session(
            handler,
            "sess-order",
            extra_events=[
                ("tool:pre", {"timestamp": "2026-03-12T10:00:01Z", "tool_name": "grep"}),
            ],
        )

        events_file = (
            tmp_path / "proj" / "sessions" / "sess-order" / "context-intelligence" / "events.jsonl"
        )
        records = [json.loads(line) for line in events_file.read_text().strip().split("\n")]
        event_names = [r["event"] for r in records]
        assert event_names == ["session:start", "tool:pre", "session:end"]
        cleanup()
