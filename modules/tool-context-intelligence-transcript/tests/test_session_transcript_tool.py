"""Tests for the agent-facing native transcript tool."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import context_intelligence.native_transcript as native

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


def _local_tool(base_path: Path, current_session_id: str = "current-session"):
    resolver = SimpleNamespace(
        base_path=base_path,
        session_dir=lambda sid: (
            base_path / "current-project" / "sessions" / sid / "context-intelligence"
        ),
    )
    coordinator = SimpleNamespace(
        session_id=current_session_id,
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


def _capture_without_metadata(sessions_root: Path, session_id: str) -> Path:
    """A capture whose metadata.json has not been written yet.

    The logging handler creates metadata.json lazily, on the first event, so
    this is the real state of a session that has just started.
    """
    capture_dir = sessions_root / session_id / "context-intelligence"
    capture_dir.mkdir(parents=True)
    (capture_dir / "events.jsonl").write_text("", encoding="utf-8")
    return capture_dir


async def test_initialising_capture_never_replaced_by_derived_sub_agent(tmp_path):
    """A session ID prefixes every `{id}_{agent}` sub-agent capture it spawned."""
    parent = "abcdef12-1234-5678-9abc-123456789abc"
    _capture_without_metadata(tmp_path / "current-project" / "sessions", parent)
    _capture(tmp_path / "current-project" / "sessions", f"{parent}_foundation-explorer")
    result = await _local_tool(tmp_path).execute({"session_ids": [parent]})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "capture_unavailable"
    assert "Hello" not in str(result.output)


async def test_current_session_never_replaced_by_its_own_sub_agent(tmp_path):
    """The no-argument `/transcript` path resolves the same way."""
    current = "current-session"
    _capture_without_metadata(tmp_path / "current-project" / "sessions", current)
    _capture(tmp_path / "current-project" / "sessions", f"{current}_foundation-explorer")
    result = await _local_tool(tmp_path, current_session_id=current).execute({})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "capture_unavailable"
    assert "Hello" not in str(result.output)


async def test_initialising_capture_is_not_ambiguous_between_its_sub_agents(tmp_path):
    """Two sub-agents must not turn an exact, valid ID into an ambiguity error."""
    parent = "abcdef12-1234-5678-9abc-123456789abc"
    sessions = tmp_path / "current-project" / "sessions"
    _capture_without_metadata(sessions, parent)
    _capture(sessions, f"{parent}_foundation-explorer")
    _capture(sessions, f"{parent}_foundation-git-ops")
    result = await _local_tool(tmp_path).execute({"session_ids": [parent]})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "capture_unavailable"


async def test_id_with_only_derived_captures_is_reported_not_substituted(tmp_path):
    """No capture of its own: name the derived sessions instead of replaying one."""
    parent = "abcdef12-1234-5678-9abc-123456789abc"
    child = f"{parent}_foundation-explorer"
    _capture(tmp_path / "current-project" / "sessions", child)
    result = await _local_tool(tmp_path).execute({"session_ids": [parent]})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "session_not_found"
    assert child in result.error["message"]
    assert "Hello" not in str(result.output)


async def test_prefix_stopping_short_of_the_derivation_mark_still_resolves(tmp_path):
    """A genuine prefix is not mistaken for a parent ID with derived captures."""
    child = "abcdef12-1234-5678-9abc-123456789abc_foundation-explorer"
    _capture(tmp_path / "current-project" / "sessions", child)
    result = await _local_tool(tmp_path).execute({"session_ids": ["abcdef12-1234"]})
    assert result.success, result.error
    assert isinstance(result.output, dict)
    assert result.output["sessions"][0]["session_id"] == child


requires_secure_open = pytest.mark.skipif(
    not native._SECURE_OPEN_SUPPORTED, reason="descriptor-relative no-follow operations unavailable"
)


def _write_messages(directory, contents):
    (directory / "events.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    "event": "prompt:complete",
                    "timestamp": "2026-09-15T10:00:00Z",
                    "data": {
                        "response": content,
                        "raw": "PRIVATE-PROVIDER",
                        "tools": "PRIVATE-TOOL",
                    },
                }
            )
            for content in contents
        )
        + "\n",
        encoding="utf-8",
    )


@requires_secure_open
@pytest.mark.parametrize("output_format", ["json", "text"])
@pytest.mark.parametrize("bounds", [{"max_messages": 1}, {"max_content_chars": 10_000}])
async def test_alias_keeps_exact_copy_across_pages_with_long_verbatim_messages(
    tmp_path, output_format, bounds
):
    normal = _capture(tmp_path / "normal")
    first_copy = _capture(tmp_path / "first")
    second_copy = _capture(tmp_path / "second")
    long_content = " \n" + "界" * 8192 + "\n\nEND-SENTINEL  "
    _write_messages(normal, ["NORMAL"])
    _write_messages(first_copy, [long_content, long_content + "SECOND"])
    _write_messages(second_copy, ["SECOND-COPY"])
    coordinator = _coordinator("session-123", normal)
    tool = SessionTranscriptTool(
        coordinator,
        {
            "capture_roots": {
                alias: {"directory": str(directory), "session_id": "session-123"}
                for alias, directory in [("first", first_copy), ("second", second_copy)]
            }
        },
    )
    first = await tool.execute({"capture_root": "first", "format": output_format, **bounds})
    assert first.success, first.error
    assert isinstance(first.output, dict)
    page = first.output["sessions"][0]
    assert page["messages"][0]["content"] == long_content
    assert page["source"] == {
        "kind": "configured_capture",
        "capture_root": "first",
        "capture_directory": str(first_copy),
    }
    assert page["session_id"] == "session-123"
    assert page["has_more"]
    second = await tool.execute(
        {
            "capture_root": "first",
            "after_event_line": page["next_after_event_line"],
            "format": output_format,
            **bounds,
        }
    )
    assert second.success, second.error
    assert isinstance(second.output, dict)
    assert second.output["sessions"][0]["source"] == page["source"]
    assert not second.output["sessions"][0]["has_more"]
    assert [m["content"] for m in page["messages"] + second.output["sessions"][0]["messages"]] == [
        long_content,
        long_content + "SECOND",
    ]
    if output_format == "text":
        assert long_content in first.output["text"]
        assert long_content + "SECOND" in second.output["text"]
    assert "PRIVATE-PROVIDER" not in str(first.output)
    assert "PRIVATE-TOOL" not in str(first.output)
    other = await tool.execute({"capture_root": "second"})
    assert other.success
    assert isinstance(other.output, dict)
    assert other.output["sessions"][0]["messages"][0]["content"] == "SECOND-COPY"
    coordinator.get_capability.assert_not_called()
    ordinary = await tool.execute({"session_ids": ["session-123"]})
    assert ordinary.success
    assert isinstance(ordinary.output, dict)
    assert ordinary.output["sessions"][0]["messages"][0]["content"] == "NORMAL"
    assert "source" not in ordinary.output["sessions"][0]


@pytest.mark.parametrize(
    "alias",
    [
        "../copy",
        "/copy",
        "copy/x",
        "C:\\copy",
        "C:copy",
        "\\\\server\\copy",
        "*",
        "a?",
        "a[0]",
        "a\0b",
        "",
        "a" * 65,
        1,
        None,
        [],
        {},
        "é",
    ],
)
async def test_alias_attacks_are_rejected_before_any_filesystem_or_resolver(monkeypatch, alias):
    coordinator = MagicMock()
    tool = SessionTranscriptTool(coordinator)
    monkeypatch.setattr(native.os, "open", lambda *a, **kw: pytest.fail("filesystem access"))
    result = await tool.execute({"capture_root": alias})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "invalid_request"
    coordinator.get_capability.assert_not_called()


@pytest.mark.parametrize(
    "selectors",
    [
        {"session_ids": []},
        {"session_ids": None},
        {"after_event_lines": {}},
        {"after_event_lines": None},
    ],
)
async def test_alias_rejects_selector_keys_even_empty_or_null(monkeypatch, selectors):
    coordinator = MagicMock()
    tool = SessionTranscriptTool(coordinator)
    monkeypatch.setattr(native.os, "open", lambda *a, **kw: pytest.fail("filesystem access"))
    result = await tool.execute({"capture_root": "copy", **selectors})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "invalid_request"
    coordinator.get_capability.assert_not_called()


async def test_unconfigured_alias_never_opens_or_falls_back(monkeypatch):
    coordinator = MagicMock()
    tool = SessionTranscriptTool(coordinator)
    monkeypatch.setattr(native.os, "open", lambda *a, **kw: pytest.fail("filesystem access"))
    result = await tool.execute({"capture_root": "copy"})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "configured_capture_unknown"
    coordinator.get_capability.assert_not_called()


@requires_secure_open
async def test_host_config_mutations_cannot_authorize_a_new_alias_or_path(tmp_path):
    copy = _capture(tmp_path)
    config = {"capture_roots": {"copy": {"directory": str(copy), "session_id": "session-123"}}}
    tool = SessionTranscriptTool(MagicMock(), config)
    config["capture_roots"]["copy"]["directory"] = str(tmp_path / "not-authorized")
    config["capture_roots"]["copy"]["session_id"] = "other"
    config["capture_roots"]["new"] = config["capture_roots"]["copy"]
    result = await tool.execute({"capture_root": "copy"})
    assert result.success
    assert isinstance(result.output, dict)
    assert result.output["sessions"][0]["session_id"] == "session-123"
    assert result.output["sessions"][0]["source"]["capture_directory"] == str(copy)
    rejected = await tool.execute({"capture_root": "new"})
    assert not rejected.success
    assert isinstance(rejected.error, dict)
    assert rejected.error["type"] == "configured_capture_unknown"


@requires_secure_open
@pytest.mark.parametrize(
    "bounds",
    [
        {"after_event_line": None},
        {"after_event_line": True},
        {"after_event_line": -1},
        {"max_messages": 201},
        {"max_content_chars": 100_001},
    ],
)
async def test_configured_bounds_fail_before_filesystem_read(tmp_path, monkeypatch, bounds):
    copy = _capture(tmp_path)
    tool = SessionTranscriptTool(
        MagicMock(),
        {"capture_roots": {"copy": {"directory": str(copy), "session_id": "session-123"}}},
    )
    monkeypatch.setattr(native.os, "open", lambda *a, **kw: pytest.fail("filesystem access"))
    result = await tool.execute({"capture_root": "copy", **bounds})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "invalid_request"


@requires_secure_open
async def test_configured_oversize_message_is_loud_not_truncated(tmp_path):
    copy = _capture(tmp_path)
    _write_messages(copy, ["x" * 100_001])
    coordinator = MagicMock()
    tool = SessionTranscriptTool(
        coordinator,
        {"capture_roots": {"copy": {"directory": str(copy), "session_id": "session-123"}}},
    )
    result = await tool.execute({"capture_root": "copy", "max_content_chars": 100_000})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "message_too_large"
    assert "x" * 100 not in str(result.output)
    assert result.error["message"] == (
        "a configured capture message exceeds the requested content budget"
    )
    coordinator.get_capability.assert_not_called()


def _persisted_copy(directory, records):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "metadata.json").write_text('{"session_id":"session-123"}', encoding="utf-8")
    (directory / "transcript.jsonl").write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
        encoding="utf-8",
    )
    return {
        "directory": str(directory),
        "session_id": "session-123",
        "capture_format": "amplifier-transcript",
    }


@requires_secure_open
@pytest.mark.parametrize("output_format", ["json", "text"])
@pytest.mark.parametrize("bounds", [{"max_messages": 1}, {"max_content_chars": 10_000}])
async def test_persisted_profile_keeps_format_copy_and_text_parts_across_pages(
    tmp_path, output_format, bounds
):
    normal = _capture(tmp_path / "normal")
    _write_messages(normal, ["NORMAL-CI"])
    first_part = "  \n" + "界" * 8192 + "\nEND-SENTINEL  "
    second_part = "\n\nseparate block "
    if "max_content_chars" in bounds:
        bounds = {"max_content_chars": len(first_part) + len(second_part)}
    config = {
        "capture_roots": {
            "persisted": _persisted_copy(
                tmp_path / "renamed-copy",
                [
                    {"role": "system", "content": "SYSTEM-SECRET"},
                    {
                        "role": "user",
                        "content": "<system-reminder>stored</system-reminder>",
                        "metadata": {
                            "ephemeral": True,
                            "persisted": True,
                            "private": "FLAGS-SECRET",
                        },
                    },
                    {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": first_part},
                            {"type": "thinking", "thinking": "THINKING-SECRET"},
                            {"type": "text", "text": second_part},
                            {"type": "tool_call", "input": {"text": "TOOL-SECRET"}},
                        ],
                        "provider_continuation": {"text": "PROVIDER-SECRET"},
                    },
                    {"role": "tool", "content": "RESULT-SECRET"},
                ],
            ),
            "other": _persisted_copy(
                tmp_path / "different-copy", [{"role": "assistant", "content": "OTHER-COPY"}]
            ),
            "native": {"directory": str(normal), "session_id": "session-123"},
        }
    }
    coordinator = _coordinator("session-123", normal)
    tool = SessionTranscriptTool(coordinator, config)
    first = await tool.execute({"capture_root": "persisted", "format": output_format, **bounds})
    assert first.success, first.error
    assert isinstance(first.output, dict)
    page = first.output["sessions"][0]
    assert page["status"] == "complete"
    assert page["session_id"] == "session-123"
    assert page["workspace"] is page["schema_version"] is None
    assert page["projection"] == {"omitted_messages": 1, "omitted_blocks": 0}
    assert page["source"] == {
        "kind": "configured_capture",
        "capture_root": "persisted",
        "capture_directory": str(tmp_path / "renamed-copy"),
        "source_format": "amplifier-transcript",
        "source_file": "transcript.jsonl",
        "cursor_unit": "physical_line",
    }
    assert page["messages"][0]["context_flags"] == {"ephemeral": True, "persisted": True}
    assert page["messages"][0]["content"] == "<system-reminder>stored</system-reminder>"
    assert page["next_after_event_line"] == 2
    second = await tool.execute(
        {
            "capture_root": "persisted",
            "after_event_line": page["next_after_event_line"],
            "format": output_format,
            **bounds,
        }
    )
    assert second.success, second.error
    assert isinstance(second.output, dict)
    next_page = second.output["sessions"][0]
    assert next_page["source"] == page["source"]
    assert next_page["messages"][0]["content"] == [
        {"index": 0, "text": first_part},
        {"index": 2, "text": second_part},
    ]
    assert next_page["messages"][0]["timestamp"] is None
    assert next_page["messages"][0]["source_event"] == "transcript:message"
    assert next_page["content_chars"] == len(first_part) + len(second_part)
    assert next_page["projection"] == {"omitted_messages": 1, "omitted_blocks": 2}
    assert next_page["status"] == "complete"
    assert not next_page["has_more"]
    for output in (first.output, second.output):
        assert "SECRET" not in json.dumps(output)
    if output_format == "text":
        assert "[STORED CONTEXT FLAGS | ephemeral=true, persisted=true]" in first.output["text"]
        assert first_part in second.output["text"]
        assert second_part in second.output["text"]
        assert "[TEXT BLOCK 0]" in second.output["text"]
        assert "[TEXT BLOCK 2]" in second.output["text"]
        assert "[ASSISTANT | physical-line 3]" in second.output["text"]
        assert "@ " not in second.output["text"]
        assert "schema 1.0.0" not in second.output["text"]
    coordinator.get_capability.assert_not_called()
    other = await tool.execute({"capture_root": "other"})
    assert other.success
    assert isinstance(other.output, dict)
    assert other.output["sessions"][0]["messages"][0]["content"] == "OTHER-COPY"
    native_page = await tool.execute({"capture_root": "native"})
    assert native_page.success
    assert isinstance(native_page.output, dict)
    assert "source_format" not in native_page.output["sessions"][0]["source"]
    assert "projection" not in native_page.output["sessions"][0]
    ordinary = await tool.execute({})
    assert ordinary.success
    assert isinstance(ordinary.output, dict)
    assert ordinary.output["sessions"][0]["messages"][0]["content"] == "NORMAL-CI"
    assert "source" not in ordinary.output["sessions"][0]


@requires_secure_open
async def test_persisted_projection_issues_stay_on_their_message_page(tmp_path):
    entry = _persisted_copy(
        tmp_path,
        [
            {"role": "user", "content": ""},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "valid"},
                    {"type": "UNKNOWN-SECRET", "text": "SECRET"},
                ],
            },
        ],
    )
    tool = SessionTranscriptTool(MagicMock(), {"capture_roots": {"copy": entry}})
    first = await tool.execute({"capture_root": "copy", "max_messages": 1})
    assert first.success
    assert isinstance(first.output, dict)
    page = first.output["sessions"][0]
    assert page["status"] == "complete"
    assert page["issues"] == []
    assert page["messages"][0]["content"] == ""
    second = await tool.execute(
        {"capture_root": "copy", "after_event_line": page["next_after_event_line"]}
    )
    assert second.success
    assert isinstance(second.output, dict)
    assert second.output["status"] == "partial"
    assert second.output["sessions"][0]["messages"][0]["content"] == [{"index": 0, "text": "valid"}]
    assert second.output["sessions"][0]["issues"][0]["code"] == "unsupported_content_part"
    assert "SECRET" not in json.dumps(second.output)


@requires_secure_open
async def test_persisted_tool_budget_counts_entire_message_not_individual_blocks(tmp_path):
    entry = _persisted_copy(
        tmp_path,
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "x" * 60_000},
                    {"type": "text", "text": "y" * 60_000},
                ],
            },
        ],
    )
    tool = SessionTranscriptTool(MagicMock(), {"capture_roots": {"copy": entry}})
    result = await tool.execute({"capture_root": "copy", "max_content_chars": 100_000})
    assert not result.success
    assert isinstance(result.error, dict)
    assert result.error["type"] == "message_too_large"
    assert "x" * 100 not in str(result.output)
    assert "y" * 100 not in str(result.output)


@requires_secure_open
async def test_format_is_frozen_with_grant_and_cannot_be_selected_by_tool_caller(tmp_path):
    entry = _persisted_copy(tmp_path, [{"role": "assistant", "content": "PERSISTED"}])
    tool = SessionTranscriptTool(MagicMock(), {"capture_roots": {"copy": entry}})
    entry["capture_format"] = "context-intelligence"
    result = await tool.execute({"capture_root": "copy"})
    assert result.success
    assert isinstance(result.output, dict)
    assert result.output["sessions"][0]["messages"][0]["content"] == "PERSISTED"
    rejected = await tool.execute(
        {"capture_root": "copy", "capture_format": "context-intelligence"}
    )
    assert not rejected.success
    assert isinstance(rejected.error, dict)
    assert rejected.error["type"] == "invalid_request"
    assert "capture_format" not in tool.input_schema["properties"]
