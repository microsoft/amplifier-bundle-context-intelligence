"""Tests for strict local Context Intelligence transcript retrieval."""

from __future__ import annotations

import json
import os

import pytest

import context_intelligence.native_transcript as native
from context_intelligence.native_transcript import (
    CaptureLocator,
    NativeTranscriptError,
    TranscriptRequest,
    configure_capture_roots,
    read_configured_capture,
    read_native_transcript,
    render_native_transcript,
)


def _capture(tmp_path, events: list[object], *, metadata: dict | None = None) -> CaptureLocator:
    capture_dir = tmp_path / "capture"
    capture_dir.mkdir()
    meta = metadata or {
        "format": "context-intelligence",
        "version": "1.0.0",
        "session_id": "session-123",
        "workspace": "workspace-a",
    }
    (capture_dir / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (capture_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(event) if not isinstance(event, str) else event for event in events)
        + "\n",
        encoding="utf-8",
    )
    return CaptureLocator.from_session_dir(capture_dir)


def _event(name: str, timestamp: str, **data: object) -> dict:
    return {"event": name, "workspace": "workspace-a", "timestamp": timestamp, "data": data}


def test_reads_verbatim_user_and_assistant_messages(tmp_path) -> None:
    locator = _capture(
        tmp_path,
        [
            _event("prompt:submit", "2026-09-15T10:00:00Z", prompt="  preserve\nthis exactly  "),
            _event("prompt:complete", "2026-09-15T10:00:01Z", response="answer\n\nwith spacing"),
        ],
    )

    page = read_native_transcript(locator)

    assert page.status == "complete"
    assert [(message.role, message.content) for message in page.messages] == [
        ("user", "  preserve\nthis exactly  "),
        ("assistant", "answer\n\nwith spacing"),
    ]
    assert [message.event_line for message in page.messages] == [1, 2]


def test_page_boundary_drops_no_message_and_sets_cursor(tmp_path) -> None:
    locator = _capture(
        tmp_path,
        [
            _event("prompt:submit", "2026-09-15T10:00:00Z", prompt="one"),
            _event("prompt:complete", "2026-09-15T10:00:01Z", response="two"),
            _event("prompt:submit", "2026-09-15T10:00:02Z", prompt="three"),
        ],
    )

    first = read_native_transcript(locator, TranscriptRequest(max_messages=2))
    second = read_native_transcript(
        locator, TranscriptRequest(after_event_line=first.next_after_event_line or 0)
    )

    assert first.status == "complete"
    assert first.has_more is True
    assert first.next_after_event_line == 2
    assert [message.content for message in first.messages] == ["one", "two"]
    assert [message.content for message in second.messages] == ["three"]


def test_reports_malformed_event_as_capture_issue(tmp_path) -> None:
    locator = _capture(
        tmp_path,
        [
            _event("prompt:submit", "2026-09-15T10:00:00Z", prompt="one"),
            "{not valid JSON",
            _event("prompt:complete", "2026-09-15T10:00:01Z", response="two"),
        ],
    )

    page = read_native_transcript(locator)

    assert page.status == "partial"
    assert page.issues[0].code == "malformed_json"
    assert [message.content for message in page.messages] == ["one", "two"]


def test_reports_an_oversized_unrelated_event_without_parsing_it(tmp_path) -> None:
    locator = _capture(
        tmp_path,
        [
            _event("prompt:submit", "2026-09-15T10:00:00Z", prompt="one"),
            _event("prompt:complete", "2026-09-15T10:00:01Z", response="two"),
        ],
    )
    with locator.events_path.open("rb") as event_file:
        valid_lines = event_file.read()
    locator.events_path.write_bytes(
        valid_lines.splitlines(keepends=True)[0]
        + b'{"event":"llm:response","data":{"raw":"'
        + (b"x" * 1_000_000)
        + b'"}}\n'
        + valid_lines.splitlines(keepends=True)[1]
    )

    page = read_native_transcript(locator)

    assert page.status == "partial"
    assert page.issues[0].code == "event_too_large_or_invalid_encoding"
    assert [message.content for message in page.messages] == ["one", "two"]


def test_reports_an_unterminated_oversized_final_line_once_and_stops(tmp_path) -> None:
    locator = _capture(
        tmp_path,
        [_event("prompt:submit", "2026-09-15T10:00:00Z", prompt="one")],
    )
    unterminated_event = b'{"event":"llm:response","data":{"raw":"' + b"x" * 1_000_000
    locator.events_path.write_bytes(locator.events_path.read_bytes() + unterminated_event)

    page = read_native_transcript(locator)

    assert page.status == "partial"
    assert [issue.code for issue in page.issues] == ["event_too_large_or_invalid_encoding"]
    assert [message.content for message in page.messages] == ["one"]


def test_rejects_unknown_capture_schema_before_reading_events(tmp_path) -> None:
    locator = _capture(
        tmp_path,
        [],
        metadata={
            "format": "context-intelligence",
            "version": "999.0.0",
            "session_id": "s",
            "workspace": "w",
        },
    )

    with pytest.raises(NativeTranscriptError, match="unsupported capture version"):
        read_native_transcript(locator)


def test_reports_a_missing_events_file_without_a_precheck(tmp_path) -> None:
    locator = _capture(tmp_path, [])
    locator.events_path.unlink()

    with pytest.raises(NativeTranscriptError) as exc_info:
        read_native_transcript(locator)

    assert exc_info.value.code == "capture_unavailable"


@pytest.mark.parametrize(
    "transcript_request",
    [
        TranscriptRequest(after_event_line=True),
        TranscriptRequest(max_messages="one"),  # type: ignore[arg-type]
        TranscriptRequest(max_content_chars=False),
        TranscriptRequest(timestamp_every_seconds="five"),  # type: ignore[arg-type]
    ],
)
def test_rejects_non_integer_request_bounds(
    tmp_path, transcript_request: TranscriptRequest
) -> None:
    with pytest.raises(NativeTranscriptError) as exc_info:
        read_native_transcript(_capture(tmp_path, []), transcript_request)

    assert exc_info.value.code == "invalid_request"


def test_renderer_marks_periodic_timestamps_and_more_messages(tmp_path) -> None:
    locator = _capture(
        tmp_path,
        [
            _event("prompt:submit", "2026-09-15T10:00:00Z", prompt="one"),
            _event("prompt:complete", "2026-09-15T10:00:30Z", response="two"),
            _event("prompt:submit", "2026-09-15T10:05:30Z", prompt="three"),
        ],
    )
    page = read_native_transcript(locator, TranscriptRequest(max_messages=2))
    all_messages = read_native_transcript(locator)

    rendered = render_native_transcript(all_messages, timestamp_every_seconds=300)

    assert rendered.count("@ 2026-09-15") == 2
    assert "[MORE MESSAGES AVAILABLE | resume with after-event-line=2]" in render_native_transcript(
        page
    )
    assert "[END OF TRANSCRIPT | 3 messages shown]" in rendered


requires_secure_open = pytest.mark.skipif(
    not native._SECURE_OPEN_SUPPORTED, reason="descriptor-relative no-follow operations unavailable"
)


def _grant(locator: CaptureLocator, capture_format="context-intelligence"):
    if capture_format == "amplifier-transcript":
        (locator.metadata_path.parent / "transcript.jsonl").write_bytes(b"")
    return configure_capture_roots(
        {
            "copy": {
                "directory": str(locator.metadata_path.parent),
                "session_id": "session-123",
                "capture_format": capture_format,
            }
        }
    )["copy"]


@requires_secure_open
def test_configured_capture_shares_native_parser_and_library_budget(tmp_path):
    content = "  \n" + "界" * 8192 + "\n\nEND-SENTINEL  "
    locator = _capture(
        tmp_path,
        [
            _event("prompt:submit", "2026-09-15T10:00:00Z", prompt="first"),
            _event("llm:response", "2026-09-15T10:00:01Z", raw="NOT-A-MESSAGE"),
            _event("prompt:complete", "2026-09-15T10:00:02Z", response=content),
        ],
    )
    grant = _grant(locator)
    first = read_configured_capture(grant, TranscriptRequest(max_messages=1))
    second = read_configured_capture(
        grant, TranscriptRequest(after_event_line=first.next_after_event_line or 0)
    )
    assert first.next_after_event_line == 2
    assert [m.content for m in first.messages + second.messages] == ["first", content]
    assert second == read_native_transcript(
        locator, TranscriptRequest(after_event_line=first.next_after_event_line or 0)
    )
    assert content in render_native_transcript(second)
    assert "NOT-A-MESSAGE" not in str(second.as_dict())
    locator.events_path.write_text(
        json.dumps(_event("prompt:complete", "t", response="x" * 150_000)), encoding="utf-8"
    )
    assert (
        read_configured_capture(grant, TranscriptRequest(max_content_chars=200_000)).content_chars
        == 150_000
    )


@requires_secure_open
@pytest.mark.parametrize(
    "metadata,code",
    [
        (b"\xff", "metadata_unavailable"),
        (b"{PRIVATE-METADATA", "metadata_unavailable"),
        (b"x" * 65_537, "metadata_unavailable"),
        (b"[]", "metadata_unavailable"),
        (b'{"format":{"PRIVATE":"value"}}', "unsupported_capture_format"),
        (
            b'{"format":"context-intelligence","version":["PRIVATE"]}',
            "unsupported_schema_version",
        ),
        (
            b'{"format":"context-intelligence","version":"1.0.0",'
            b'"session_id":"PRIVATE-ID","workspace":"w"}',
            "configured_capture_identity_mismatch",
        ),
        (
            b'{"format":"context-intelligence","version":"1.0.0","session_id":"session-123"}',
            "metadata_unavailable",
        ),
    ],
)
def test_configured_metadata_errors_are_safe_and_never_open_events(
    tmp_path, monkeypatch, metadata, code
):
    locator = _capture(tmp_path, [])
    grant = _grant(locator)
    locator.metadata_path.write_bytes(metadata)
    original_open = native.os.open

    def checked_open(path, *args, **kwargs):
        assert path != "events.jsonl", "metadata must bind identity before events open"
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(native.os, "open", checked_open)
    with pytest.raises(NativeTranscriptError) as error:
        read_configured_capture(grant)
    assert error.value.code == code
    assert "PRIVATE" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


@requires_secure_open
def test_configured_metadata_size_limit_is_bytes_and_allows_exact_boundary(tmp_path):
    locator = _capture(tmp_path, [])
    grant = _grant(locator)
    metadata = locator.metadata_path.read_bytes()
    locator.metadata_path.write_bytes(metadata + b" " * (65_536 - len(metadata)))
    assert read_configured_capture(grant).status == "complete"


@requires_secure_open
@pytest.mark.parametrize(
    "capture_format,filename",
    [
        ("context-intelligence", "metadata.json"),
        ("context-intelligence", "events.jsonl"),
        ("amplifier-transcript", "metadata.json"),
        ("amplifier-transcript", "transcript.jsonl"),
    ],
)
@pytest.mark.parametrize("replacement", ["symlink", "fifo", "directory", "missing"])
def test_configured_files_must_be_literal_regular_files(
    tmp_path, capture_format, filename, replacement
):
    locator = _capture(tmp_path, [])
    grant = _grant(locator, capture_format)
    target = locator.metadata_path.parent / filename
    target.unlink()
    if replacement == "symlink":
        outside = tmp_path / "outside"
        outside.write_text("PRIVATE", encoding="utf-8")
        target.symlink_to(outside)
    elif replacement == "fifo":
        os.mkfifo(target)
    elif replacement == "directory":
        target.mkdir()
    with pytest.raises(NativeTranscriptError) as error:
        read_configured_capture(grant)
    assert error.value.code in {"configured_capture_unavailable", "configured_capture_unsafe"}
    assert "PRIVATE" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


@requires_secure_open
@pytest.mark.parametrize("ancestor", [False, True])
def test_configured_directory_and_ancestors_cannot_be_symlinks(tmp_path, ancestor):
    locator = _capture(tmp_path, [])
    link = tmp_path / "link"
    link.symlink_to(
        tmp_path if ancestor else locator.metadata_path.parent, target_is_directory=True
    )
    directory = link / "capture" if ancestor else link
    with pytest.raises(NativeTranscriptError) as error:
        configure_capture_roots(
            {"copy": {"directory": str(directory), "session_id": "session-123"}}
        )
    assert error.value.code == "invalid_capture_config"


@requires_secure_open
def test_missing_configured_directory_fails_mount_with_safe_error(tmp_path):
    with pytest.raises(NativeTranscriptError) as error:
        configure_capture_roots(
            {"copy": {"directory": str(tmp_path / "missing"), "session_id": "session-123"}}
        )
    assert error.value.code == "invalid_capture_config"
    assert str(tmp_path) not in str(error.value)


@requires_secure_open
def test_ancestor_symlink_replacement_after_mount_is_refused(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    locator = _capture(parent, [])
    grant = _grant(locator)
    parent.rename(tmp_path / "original-parent")
    parent.symlink_to(tmp_path / "original-parent", target_is_directory=True)
    with pytest.raises(NativeTranscriptError) as error:
        read_configured_capture(grant)
    assert error.value.code == "configured_capture_unavailable"


@requires_secure_open
@pytest.mark.parametrize("capture_format", ["context-intelligence", "amplifier-transcript"])
@pytest.mark.parametrize("symlink", [False, True])
def test_directory_replacement_after_mount_is_refused(tmp_path, symlink, capture_format):
    locator = _capture(tmp_path, [])
    grant = _grant(locator, capture_format)
    directory = locator.metadata_path.parent
    directory.rename(tmp_path / "original")
    if symlink:
        directory.symlink_to(tmp_path / "original", target_is_directory=True)
    else:
        directory.mkdir()
    with pytest.raises(NativeTranscriptError) as error:
        read_configured_capture(grant)
    assert error.value.code == (
        "configured_capture_unavailable" if symlink else "configured_capture_changed"
    )


@requires_secure_open
@pytest.mark.parametrize(
    "capture_format,filename",
    [
        ("context-intelligence", "metadata.json"),
        ("context-intelligence", "events.jsonl"),
        ("context-intelligence", "capture"),
        ("amplifier-transcript", "metadata.json"),
        ("amplifier-transcript", "transcript.jsonl"),
        ("amplifier-transcript", "capture"),
    ],
)
def test_symlink_swap_at_open_cannot_retarget_a_read(
    tmp_path, monkeypatch, filename, capture_format
):
    locator = _capture(tmp_path, [])
    grant = _grant(locator, capture_format)
    original_open = native.os.open
    swapped = False

    def swapping_open(path, *args, **kwargs):
        nonlocal swapped
        if path == filename and not swapped:
            swapped = True
            target = (
                locator.metadata_path.parent
                if filename == "capture"
                else locator.metadata_path.parent / filename
            )
            moved = target.with_name(target.name + "-original")
            target.rename(moved)
            target.symlink_to(moved, target_is_directory=filename == "capture")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(native.os, "open", swapping_open)
    with pytest.raises(NativeTranscriptError) as error:
        read_configured_capture(grant)
    assert swapped
    assert error.value.code == "configured_capture_unavailable"


@requires_secure_open
def test_configured_permission_error_is_sanitized(tmp_path, monkeypatch):
    grant = _grant(_capture(tmp_path, []))
    original_open = native.os.open

    def denied(path, *args, **kwargs):
        if path == "metadata.json":
            raise PermissionError("PRIVATE-PATH")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(native.os, "open", denied)
    with pytest.raises(NativeTranscriptError) as error:
        read_configured_capture(grant)
    assert error.value.code == "configured_capture_unavailable"
    assert "PRIVATE" not in str(error.value)


@requires_secure_open
@pytest.mark.parametrize("capture_format", ["context-intelligence", "amplifier-transcript"])
def test_configured_actual_permission_denial_is_loud(tmp_path, capture_format):
    if os.geteuid() == 0:
        pytest.skip("root bypasses filesystem read permissions")
    locator = _capture(tmp_path, [])
    grant = _grant(locator, capture_format)
    target = (
        locator.metadata_path.parent / "transcript.jsonl"
        if capture_format == "amplifier-transcript"
        else locator.metadata_path
    )
    target.chmod(0)
    try:
        with pytest.raises(NativeTranscriptError) as error:
            read_configured_capture(grant)
        assert error.value.code == "configured_capture_unavailable"
    finally:
        target.chmod(0o600)


@requires_secure_open
def test_configured_partial_event_issues_share_parser_and_safe_details(tmp_path):
    locator = _capture(tmp_path, [])
    locator.events_path.write_bytes(
        b'{"PRIVATE":\n\xff\n'
        + json.dumps(_event("prompt:complete", "t", response="intact")).encode()
        + b"\n"
        + b"x" * 1_000_001
    )
    page = read_configured_capture(_grant(locator))
    assert page.status == "partial"
    assert [issue.code for issue in page.issues] == [
        "malformed_json",
        "event_too_large_or_invalid_encoding",
        "event_too_large_or_invalid_encoding",
    ]
    assert [m.content for m in page.messages] == ["intact"]
    assert "PRIVATE" not in str(page.as_dict())


@requires_secure_open
def test_grants_are_detached_from_external_mutation_and_immutable(tmp_path):
    locator = _capture(tmp_path, [])
    config = {"copy": {"directory": str(locator.metadata_path.parent), "session_id": "session-123"}}
    grants = configure_capture_roots(config)
    config["copy"]["directory"] = str(tmp_path / "unauthorized")
    config["copy"]["session_id"] = "other-id"
    config["other"] = config["copy"]
    assert list(grants) == ["copy"]
    assert read_configured_capture(grants["copy"]).session_id == "session-123"
    with pytest.raises(TypeError):
        grants["other"] = grants["copy"]  # type: ignore[index]
    with pytest.raises(AttributeError):
        grants["copy"].session_id = "other-id"  # type: ignore[misc]


@requires_secure_open
@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"../alias": {}},
        {"copy": None},
        {"copy": {"directory": "/srv/captures"}},
        {"copy": {"directory": "/srv/captures", "session_id": "../session"}},
        {"copy": {"directory": "/srv/captures", "session_id": "id", "extra": True}},
        {"copy": {"directory": "relative", "session_id": "id"}},
        {"copy": {"directory": "C:\\captures\\copy", "session_id": "id"}},
        {"copy": {"directory": "\\\\server\\captures", "session_id": "id"}},
        {"copy": {"directory": "/srv/../captures", "session_id": "id"}},
        {"copy": {"directory": "/srv/./captures", "session_id": "id"}},
        {"copy": {"directory": "/srv/\0captures", "session_id": "id"}},
        {"copy": {"directory": "file:///srv/captures", "session_id": "id"}},
        {"copy": {"directory": 123, "session_id": "id"}},
    ],
)
def test_invalid_config_is_rejected_before_filesystem_access(monkeypatch, value):
    monkeypatch.setattr(native.os, "open", lambda *a, **kw: pytest.fail("filesystem access"))
    with pytest.raises(NativeTranscriptError) as error:
        configure_capture_roots(value)
    assert error.value.code == "invalid_capture_config"
    assert str(value) not in str(error.value)


def test_unsupported_secure_operations_do_not_affect_ordinary_reads(tmp_path, monkeypatch):
    locator = _capture(tmp_path, [])
    monkeypatch.setattr(native, "_SECURE_OPEN_SUPPORTED", False)
    assert configure_capture_roots({}) == {}
    with pytest.raises(NativeTranscriptError) as error:
        configure_capture_roots(
            {"copy": {"directory": str(locator.metadata_path.parent), "session_id": "session-123"}}
        )
    assert error.value.code == "configured_capture_unsupported"
    assert read_native_transcript(locator).status == "complete"
    grant = native.ConfiguredCapture(locator.metadata_path.parent, "session-123", (0, 0))
    with pytest.raises(NativeTranscriptError) as error:
        read_configured_capture(grant)
    assert error.value.code == "configured_capture_unsupported"


@requires_secure_open
@pytest.mark.parametrize("capture_format", ["context-intelligence", "amplifier-transcript"])
def test_repeated_success_and_errors_close_every_descriptor(tmp_path, monkeypatch, capture_format):
    locator = _capture(tmp_path, [])
    grant = _grant(locator, capture_format)
    metadata = locator.metadata_path.read_bytes()
    opened: set[int] = set()
    original_open, original_close, original_fdopen = os.open, os.close, os.fdopen

    def tracked_open(*args, **kwargs):
        descriptor = original_open(*args, **kwargs)
        opened.add(descriptor)
        return descriptor

    def tracked_close(descriptor):
        opened.remove(descriptor)
        original_close(descriptor)

    def tracked_fdopen(descriptor, *args, **kwargs):
        # fdopen takes ownership; the actual file context manager must close it.
        stream = original_fdopen(descriptor, *args, **kwargs)
        opened.remove(descriptor)
        streams.append(stream)
        return stream

    streams = []
    monkeypatch.setattr(native.os, "open", tracked_open)
    monkeypatch.setattr(native.os, "close", tracked_close)
    monkeypatch.setattr(native.os, "fdopen", tracked_fdopen)
    for _ in range(20):
        assert read_configured_capture(grant).status == "complete"
    locator.metadata_path.write_bytes(b"\xff")
    for _ in range(20):
        with pytest.raises(NativeTranscriptError):
            read_configured_capture(grant)
    locator.metadata_path.unlink()
    for _ in range(20):
        with pytest.raises(NativeTranscriptError):
            read_configured_capture(grant)
    locator.metadata_path.write_bytes(metadata)
    content_path = (
        locator.metadata_path.parent / "transcript.jsonl"
        if capture_format == "amplifier-transcript"
        else locator.events_path
    )
    content_path.unlink()
    for _ in range(20):
        with pytest.raises(NativeTranscriptError):
            read_configured_capture(grant)
    assert not opened
    assert streams and all(stream.closed for stream in streams)


@requires_secure_open
@pytest.mark.parametrize("capture_format", ["unknown-SECRET", "", None, False, 1, [], {}])
def test_unknown_or_malformed_capture_format_fails_before_filesystem_access(
    tmp_path, monkeypatch, capture_format
):
    monkeypatch.setattr(native.os, "open", lambda *a, **kw: pytest.fail("filesystem access"))
    with pytest.raises(NativeTranscriptError) as error:
        configure_capture_roots(
            {
                "copy": {
                    "directory": str(tmp_path),
                    "session_id": "id",
                    "capture_format": capture_format,
                }
            }
        )
    assert error.value.code == "invalid_capture_config"
    assert "SECRET" not in str(error.value)


@requires_secure_open
def test_default_format_is_still_strict_ci_and_never_probes_transcript(tmp_path, monkeypatch):
    locator = _capture(tmp_path, [])
    locator.metadata_path.write_text('{"session_id":"session-123"}', encoding="utf-8")
    grant = configure_capture_roots(
        {"copy": {"directory": str(locator.metadata_path.parent), "session_id": "session-123"}}
    )["copy"]
    original_open = native.os.open

    def checked_open(path, *args, **kwargs):
        assert path not in {"events.jsonl", "transcript.jsonl"}
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(native.os, "open", checked_open)
    with pytest.raises(NativeTranscriptError) as error:
        read_configured_capture(grant)
    assert error.value.code == "unsupported_capture_format"
    assert grant.capture_format == "context-intelligence"


@requires_secure_open
@pytest.mark.parametrize(
    "metadata,code",
    [
        (b"\xff", "metadata_unavailable"),
        (b"{SECRET", "metadata_unavailable"),
        (b"x" * 65_537, "metadata_unavailable"),
        (b"[]", "metadata_unavailable"),
        (b"{}", "metadata_unavailable"),
        (b'{"session_id":"other-SECRET"}', "configured_capture_identity_mismatch"),
    ],
)
def test_persisted_metadata_must_bind_identity_before_any_content_opens(
    tmp_path, monkeypatch, metadata, code
):
    locator = _capture(tmp_path, [])
    grant = _grant(locator, "amplifier-transcript")
    locator.metadata_path.write_bytes(metadata)
    original_open = native.os.open

    def checked_open(path, *args, **kwargs):
        assert path not in {"events.jsonl", "transcript.jsonl"}
        assert not str(path).endswith(".backup")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(native.os, "open", checked_open)
    with pytest.raises(NativeTranscriptError) as error:
        read_configured_capture(grant)
    assert error.value.code == code
    assert "SECRET" not in str(error.value)


@requires_secure_open
@pytest.mark.parametrize("missing", [False, True])
def test_explicit_profile_opens_only_selected_literal_primary(tmp_path, monkeypatch, missing):
    locator = _capture(tmp_path, [])
    grant = _grant(locator, "amplifier-transcript")
    locator.metadata_path.write_text('{"session_id":"session-123"}', encoding="utf-8")
    transcript = locator.metadata_path.parent / "transcript.jsonl"
    transcript.write_text('{"role":"assistant","content":"selected"}\n', encoding="utf-8")
    for name in ("metadata.json.backup", "transcript.jsonl.backup"):
        (locator.metadata_path.parent / name).write_text("BACKUP-SECRET", encoding="utf-8")
    if missing:
        transcript.unlink()
    original_open = native.os.open
    selected = []

    def checked_open(path, *args, **kwargs):
        assert path != "events.jsonl"
        assert not str(path).endswith(".backup")
        if path in {"metadata.json", "transcript.jsonl"}:
            selected.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(native.os, "open", checked_open)
    if missing:
        with pytest.raises(NativeTranscriptError) as error:
            read_configured_capture(grant)
        assert error.value.code == "configured_capture_unavailable"
    else:
        page = read_configured_capture(grant)
        assert [message.content for message in page.messages] == ["selected"]
        assert page.workspace is page.schema_version is None
        assert page.messages[0].timestamp is None
        assert "schema 1.0.0" not in render_native_transcript(page)
        assert "@ " not in render_native_transcript(page)
        assert "transcript.jsonl" in render_native_transcript(page)
    assert selected == ["metadata.json", "transcript.jsonl"]


@requires_secure_open
def test_persisted_profile_reports_partial_record_problems_with_safe_issues(tmp_path):
    locator = _capture(tmp_path, [])
    grant = _grant(locator, "amplifier-transcript")
    locator.metadata_path.write_text(
        '{"session_id":"session-123","workspace":{"private":"SECRET"}}', encoding="utf-8"
    )
    transcript = locator.metadata_path.parent / "transcript.jsonl"
    transcript.write_bytes(
        b"\nnull\n{}\n\xff\n{SECRET\n"
        + b"x" * 1_000_001
        + b"\n"
        + b'{"role":"assistant","content":[{"type":"text","text":"intact"},'
        + b'{"type":"UNKNOWN-SECRET","text":"SECRET"}]}\n'
    )
    page = read_configured_capture(grant)
    assert page.status == "partial"
    assert page.workspace is page.schema_version is None
    assert [issue.code for issue in page.issues] == [
        "invalid_metadata_workspace",
        "invalid_event_envelope",
        "invalid_message_role",
        "event_too_large_or_invalid_encoding",
        "malformed_json",
        "event_too_large_or_invalid_encoding",
        "unsupported_content_part",
    ]
    assert page.as_dict()["messages"][0]["content"] == [{"index": 0, "text": "intact"}]
    assert "SECRET" not in json.dumps(page.as_dict())
    assert "SECRET" not in render_native_transcript(page)


@requires_secure_open
def test_persisted_whole_message_budget_counts_all_blocks_and_library_max_is_unchanged(tmp_path):
    locator = _capture(tmp_path, [])
    grant = _grant(locator, "amplifier-transcript")
    record = {
        "role": "assistant",
        "content": [{"type": "text", "text": "x" * 75_000}, {"type": "text", "text": "y" * 75_000}],
    }
    (locator.metadata_path.parent / "transcript.jsonl").write_text(
        json.dumps(record), encoding="utf-8"
    )
    with pytest.raises(NativeTranscriptError) as error:
        read_configured_capture(grant, TranscriptRequest(max_content_chars=100_000))
    assert error.value.code == "message_too_large"
    assert (
        read_configured_capture(grant, TranscriptRequest(max_content_chars=200_000)).content_chars
        == 150_000
    )


@requires_secure_open
def test_native_json_shape_and_text_are_unchanged(tmp_path):
    locator = _capture(
        tmp_path, [_event("prompt:submit", "2026-09-15T10:00:00Z", prompt="verbatim")]
    )
    page = read_native_transcript(locator)
    expected = {
        "status": "complete",
        "session_id": "session-123",
        "workspace": "workspace-a",
        "schema_version": "1.0.0",
        "messages": [
            {
                "sequence": 1,
                "role": "user",
                "content": "verbatim",
                "timestamp": "2026-09-15T10:00:00Z",
                "event_line": 1,
                "source_event": "prompt:submit",
            }
        ],
        "issues": [],
        "through_event_line": 1,
        "next_after_event_line": None,
        "has_more": False,
        "content_chars": 8,
    }
    assert page.as_dict() == expected
    assert read_configured_capture(_grant(locator)).as_dict() == expected
    assert render_native_transcript(page) == (
        "=== SESSION session-123 ===\nWorkspace: workspace-a\n"
        "Capture: native events.jsonl (schema 1.0.0)\n\n"
        "@ 2026-09-15T10:00:00Z\n[USER | event-line 1]\nverbatim\n\n"
        "[END OF TRANSCRIPT | 1 messages shown]"
    )


@requires_secure_open
def test_persisted_renderer_preserves_available_timestamps_without_inventing_missing_ones(tmp_path):
    locator = _capture(tmp_path, [])
    grant = _grant(locator, "amplifier-transcript")
    timestamps = ["2026-09-15T10:00:00", "2026-09-15T10:00:01Z", None, "source-time"]
    (locator.metadata_path.parent / "transcript.jsonl").write_text(
        "\n".join(
            json.dumps({"role": "assistant", "content": "text", "timestamp": timestamp})
            for timestamp in timestamps
        ),
        encoding="utf-8",
    )
    page = read_configured_capture(grant)
    assert [message.timestamp for message in page.messages] == timestamps
    rendered = render_native_transcript(page)
    assert rendered.count("@ ") == 3
    for timestamp in timestamps:
        if timestamp is not None:
            assert "@ " + timestamp in rendered
