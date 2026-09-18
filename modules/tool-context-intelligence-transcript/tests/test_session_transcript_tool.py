"""Tests for the agent-facing native transcript tool."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from amplifier_module_tool_context_intelligence_transcript.session_transcript_tool import (
    SessionTranscriptTool,
)


def _capture(tmp_path: Path, session_id: str = "session-123") -> Path:
    capture_dir = tmp_path / session_id / "context-intelligence"
    capture_dir.mkdir(parents=True)
    (capture_dir / "metadata.json").write_text(
        json.dumps(
            {
                "format": "context-intelligence",
                "version": "1.0.0",
                "session_id": session_id,
                "workspace": "workspace-a",
            }
        ),
        encoding="utf-8",
    )
    (capture_dir / "events.jsonl").write_text(
        json.dumps(
            {
                "event": "prompt:submit",
                "workspace": "workspace-a",
                "timestamp": "2026-09-15T10:00:00Z",
                "data": {"prompt": "Hello"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return capture_dir


def _coordinator(session_id: str, capture_dir: Path) -> MagicMock:
    coordinator = MagicMock()
    coordinator.session_id = session_id
    hook_resolver = SimpleNamespace(session_dir=lambda requested_id: capture_dir)
    coordinator.get_capability.side_effect = lambda name: (
        hook_resolver if name == "context_intelligence.hook_config_resolver" else None
    )
    return coordinator


async def test_omitted_session_ids_uses_runtime_current_session(tmp_path) -> None:
    capture_dir = _capture(tmp_path)
    tool = SessionTranscriptTool(_coordinator("session-123", capture_dir))

    result = await tool.execute({})

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["sessions"][0]["session_id"] == "session-123"
    assert "[USER | event-line 1]" in result.output["text"]


async def test_explicit_session_ids_use_capture_resolver_capability(tmp_path) -> None:
    capture_dir = _capture(tmp_path, "other-session")
    resolver = SimpleNamespace(
        resolve_capture=lambda requested_id: {
            "events_path": str(capture_dir / "events.jsonl"),
            "metadata_path": str(capture_dir / "metadata.json"),
        }
    )
    coordinator = MagicMock()
    coordinator.session_id = "current-session"
    coordinator.get_capability.side_effect = lambda name: (
        resolver if name == "context_intelligence.capture_resolver" else None
    )

    result = await SessionTranscriptTool(coordinator).execute(
        {"session_ids": ["other-session"], "format": "json"}
    )

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["sessions"][0]["session_id"] == "other-session"
    assert "text" not in result.output


async def test_missing_current_session_identity_fails_loudly() -> None:
    coordinator = MagicMock(spec=[])
    coordinator.get_capability = MagicMock(return_value=None)

    result = await SessionTranscriptTool(coordinator).execute({})

    assert result.success is False
    assert isinstance(result.error, dict)
    assert result.error["type"] == "current_session_unavailable"


async def test_invalid_tool_input_returns_a_structured_error() -> None:
    result = await SessionTranscriptTool(MagicMock()).execute({"format": []})

    assert result.success is False
    assert isinstance(result.error, dict)
    assert result.error["type"] == "invalid_request"


async def test_rejects_path_like_session_ids_before_resolving_a_capture() -> None:
    coordinator = MagicMock()

    result = await SessionTranscriptTool(coordinator).execute({"session_ids": ["../other-session"]})

    assert result.success is False
    assert isinstance(result.error, dict)
    assert result.error["type"] == "invalid_request"
    coordinator.get_capability.assert_not_called()


@pytest.mark.parametrize("metacharacter", ["*", "?", "[", "]"])
async def test_rejects_glob_metacharacters_in_session_ids_before_resolving_a_capture(
    metacharacter: str,
) -> None:
    coordinator = MagicMock()

    result = await SessionTranscriptTool(coordinator).execute(
        {"session_ids": [f"session{metacharacter}id"]}
    )

    assert result.success is False
    assert isinstance(result.error, dict)
    assert result.error["type"] == "invalid_request"
    coordinator.get_capability.assert_not_called()


@pytest.mark.parametrize("metacharacter", ["*", "?", "[", "]"])
async def test_rejects_glob_metacharacters_in_cursor_map_before_resolving_a_capture(
    metacharacter: str,
) -> None:
    coordinator = MagicMock()

    result = await SessionTranscriptTool(coordinator).execute(
        {
            "session_ids": ["requested-session"],
            "after_event_lines": {f"requested{metacharacter}session": 0},
        }
    )

    assert result.success is False
    assert isinstance(result.error, dict)
    assert result.error["type"] == "invalid_request"
    coordinator.get_capability.assert_not_called()


async def test_caches_the_capture_resolver_for_multiple_requested_sessions(tmp_path) -> None:
    first = _capture(tmp_path, "first-session")
    second = _capture(tmp_path / "other", "second-session")
    paths = {"first-session": first, "second-session": second}
    resolver = SimpleNamespace(
        resolve_capture=lambda session_id: {
            "events_path": str(paths[session_id] / "events.jsonl"),
            "metadata_path": str(paths[session_id] / "metadata.json"),
        }
    )
    coordinator = MagicMock()
    coordinator.get_capability.return_value = resolver

    result = await SessionTranscriptTool(coordinator).execute(
        {"session_ids": ["first-session", "second-session"]}
    )

    assert result.success is True
    coordinator.get_capability.assert_called_once_with("context_intelligence.capture_resolver")


async def test_multiple_sessions_accept_independent_pagination_cursors(tmp_path) -> None:
    first = _capture(tmp_path, "first-session")
    second = _capture(tmp_path / "other", "second-session")
    paths = {"first-session": first, "second-session": second}
    resolver = SimpleNamespace(
        resolve_capture=lambda session_id: {
            "events_path": str(paths[session_id] / "events.jsonl"),
            "metadata_path": str(paths[session_id] / "metadata.json"),
        }
    )
    coordinator = MagicMock()
    coordinator.get_capability.return_value = resolver

    result = await SessionTranscriptTool(coordinator).execute(
        {
            "session_ids": ["first-session", "second-session"],
            "after_event_lines": {"first-session": 1, "second-session": 0},
        }
    )

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["sessions"][0]["messages"] == []
    assert result.output["sessions"][1]["messages"][0]["content"] == "Hello"


async def test_hook_fallback_finds_only_the_literal_matching_session_directory(tmp_path) -> None:
    expected = _capture(tmp_path / "other-project" / "sessions", "other-session")
    _capture(tmp_path / "another-project" / "sessions", "other-session-copy")
    hook_resolver = SimpleNamespace(
        base_path=tmp_path,
        session_dir=lambda session_id: tmp_path / "current-project" / "sessions" / session_id,
    )
    coordinator = MagicMock()
    coordinator.get_capability.side_effect = lambda name: (
        hook_resolver if name == "context_intelligence.hook_config_resolver" else None
    )

    result = await SessionTranscriptTool(coordinator).execute(
        {"session_ids": ["other-session"], "format": "json"}
    )

    assert result.success is True
    assert isinstance(result.output, dict)
    assert result.output["sessions"][0]["session_id"] == "other-session"
    assert SessionTranscriptTool._find_capture_metadata(tmp_path, "other-session") == [
        expected / "metadata.json"
    ]


async def test_tool_rejects_content_limit_above_total_limit(tmp_path) -> None:
    tool = SessionTranscriptTool(_coordinator("session-123", _capture(tmp_path)))

    result = await tool.execute({"max_content_chars": 100_001})

    assert tool.input_schema["properties"]["max_content_chars"]["maximum"] == 100_000
    assert result.success is False
    assert isinstance(result.error, dict)
    assert result.error["type"] == "invalid_request"


def _local_tool(base_path: Path) -> SessionTranscriptTool:
    resolver = SimpleNamespace(
        base_path=base_path,
        session_dir=lambda sid: (
            base_path / "current-project" / "sessions" / sid / "context-intelligence"
        ),
    )
    coordinator = SimpleNamespace(
        session_id="current-session",
        get_capability=lambda name: (
            resolver if name == "context_intelligence.hook_config_resolver" else None
        ),
    )
    return SessionTranscriptTool(coordinator)


async def test_unique_prefix_in_another_workspace_returns_full_id_and_can_page(tmp_path):
    sid = "abcdef12-1234-5678-9abc-123456789abc"
    capture = _capture(tmp_path / "other-project" / "sessions", sid)
    with (capture / "events.jsonl").open("a") as stream:
        stream.write(
            json.dumps(
                {
                    "event": "prompt:complete",
                    "timestamp": "2026-09-15T10:00:01Z",
                    "data": {"response": "Second message"},
                }
            )
            + "\n"
        )
    tool = _local_tool(tmp_path)
    first = await tool.execute({"session_ids": ["abcdef12"], "max_messages": 1})
    assert first.success
    assert isinstance(first.output, dict)
    page = first.output["sessions"][0]
    assert page["session_id"] == sid
    assert page["has_more"]
    second = await tool.execute(
        {
            "session_ids": [page["session_id"]],
            "after_event_lines": {page["session_id"]: page["next_after_event_line"]},
        }
    )
    assert second.success
    assert isinstance(second.output, dict)
    assert second.output["sessions"][0]["messages"][0]["content"] == "Second message"


async def test_ambiguous_prefix_across_workspaces_never_prefers_current_workspace(tmp_path):
    first_id, second_id = "abcdef12-first", "abcdef12-second"
    _capture(tmp_path / "current-project" / "sessions", first_id)
    _capture(tmp_path / "other-project" / "sessions", second_id)
    result = await _local_tool(tmp_path).execute({"session_ids": ["abcdef12"]})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "ambiguous_session"
    assert first_id in result.error["message"]
    assert second_id in result.error["message"]
    assert "Hello" not in str(result.output)


async def test_missing_prefix_is_not_current_session_fallback(tmp_path):
    _capture(tmp_path / "current-project" / "sessions", "current-session")
    result = await _local_tool(tmp_path).execute({"session_ids": ["absent12"]})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "session_not_found"


@pytest.mark.parametrize("reference", ["abcdef12", "abcdef12-full"])
async def test_duplicate_capture_locations_are_ambiguous_even_with_same_full_id(
    tmp_path, reference
):
    for project in ("current-project", "other-project"):
        _capture(tmp_path / project / "sessions", "abcdef12-full")
    result = await _local_tool(tmp_path).execute({"session_ids": [reference]})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "ambiguous_session"
    assert "multiple capture roots" in result.error["message"]


async def test_two_prefixes_page_using_returned_full_ids(tmp_path):
    ids = ["abcdef12-full", "abcdef34-full"]
    for sid in ids:
        _capture(tmp_path / "one" / "sessions", sid)
    tool = _local_tool(tmp_path)
    first = await tool.execute({"session_ids": ["abcdef12", "abcdef34"]})
    assert first.success
    assert isinstance(first.output, dict)
    full_ids = [page["session_id"] for page in first.output["sessions"]]
    second = await tool.execute(
        {
            "session_ids": full_ids,
            "after_event_lines": dict.fromkeys(full_ids, 1),
        }
    )
    assert second.success
    assert isinstance(second.output, dict)
    assert all(page["messages"] == [] for page in second.output["sessions"])


async def test_custom_host_resolver_receives_prefix_unchanged(tmp_path):
    capture = _capture(tmp_path, "abcdef12-full")
    resolver = MagicMock(
        return_value={
            "events_path": str(capture / "events.jsonl"),
            "metadata_path": str(capture / "metadata.json"),
        }
    )
    coordinator = SimpleNamespace(
        get_capability=lambda name: (
            SimpleNamespace(resolve_capture=resolver)
            if name == "context_intelligence.capture_resolver"
            else None
        )
    )
    result = await SessionTranscriptTool(coordinator).execute({"session_ids": ["abcdef12"]})
    assert result.success
    resolver.assert_called_once_with("abcdef12")


def test_prefix_lookup_reads_directory_names_not_capture_contents(tmp_path, monkeypatch):
    capture = _capture(tmp_path / "one" / "sessions", "abcdef12-full")
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("content read"))
    assert SessionTranscriptTool._find_capture_metadata(tmp_path, "abcdef12") == [
        capture / "metadata.json"
    ]


async def test_unreadable_capture_store_returns_error_not_an_empty_success(tmp_path, monkeypatch):
    def denied(*args):
        raise PermissionError("cannot enumerate")

    monkeypatch.setattr(Path, "iterdir", denied)
    result = await _local_tool(tmp_path).execute({"session_ids": ["abcdef12"]})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "capture_unavailable"
