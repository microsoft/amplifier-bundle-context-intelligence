"""Strict, local reading of native Context Intelligence transcript events.

This module deliberately accepts an explicit capture location. It does not know
which host created the capture or where a host stores session directories.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Literal

from .session_ids import is_safe_session_id

_SUPPORTED_SCHEMA_VERSION = "1.0.0"
_MAX_EVENT_BYTES = 1_000_000
_MAX_METADATA_BYTES = 65_536
_CAPTURE_ROOT_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,63}", re.ASCII)
_SECURE_OPEN_SUPPORTED = (
    os.name == "posix"
    and os.open in os.supports_dir_fd
    and all(hasattr(os, flag) for flag in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK"))
)

TranscriptRole = Literal["user", "assistant"]
TranscriptStatus = Literal["complete", "partial"]
CaptureFormat = Literal["context-intelligence", "amplifier-transcript"]


class NativeTranscriptError(RuntimeError):
    """A capture cannot safely produce a transcript page."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CaptureLocator:
    """Explicit paths for one native Context Intelligence capture."""

    events_path: Path
    metadata_path: Path

    @classmethod
    def from_session_dir(cls, session_dir: Path | str) -> "CaptureLocator":
        directory = Path(session_dir)
        return cls(
            events_path=directory / "events.jsonl", metadata_path=directory / "metadata.json"
        )


@dataclass(frozen=True)
class ConfiguredCapture:
    """A trusted, exact-directory read grant, pinned without retaining descriptors."""

    directory: Path
    session_id: str
    directory_identity: tuple[int, int]
    capture_format: CaptureFormat = "context-intelligence"


def is_capture_root_alias(value: object) -> bool:
    """Accept a portable opaque alias, never a filesystem expression."""
    return isinstance(value, str) and _CAPTURE_ROOT_PATTERN.fullmatch(value) is not None


def _require_secure_open() -> None:
    if not _SECURE_OPEN_SUPPORTED:
        raise NativeTranscriptError(
            "configured_capture_unsupported",
            "configured captures require descriptor-relative no-follow filesystem operations",
        )


@contextmanager
def _open_capture_directory(directory: Path) -> Iterator[int]:
    """Walk from the filesystem root, refusing symlinks at every component."""
    _require_secure_open()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK
    descriptor = os.open("/", flags)
    try:
        for component in directory.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def configure_capture_roots(value: object) -> Mapping[str, ConfiguredCapture]:
    """Validate and freeze trusted mount config; do not inspect capture bodies."""
    invalid = NativeTranscriptError(
        "invalid_capture_config", "capture_roots must map safe aliases to directory and session_id"
    )
    if not isinstance(value, dict):
        raise invalid
    if value:
        _require_secure_open()
    # Validate every entry before any filesystem operation. Copy only immutable
    # values, so later mutations of the host's config cannot extend this grant.
    entries: list[tuple[str, Path, str, CaptureFormat]] = []
    for alias, entry in value.items():
        if (
            not is_capture_root_alias(alias)
            or not isinstance(entry, dict)
            or not {"directory", "session_id"} <= set(entry)
            or set(entry) - {"directory", "session_id", "capture_format"}
        ):
            raise invalid
        directory, session_id = entry["directory"], entry["session_id"]
        capture_format = entry.get("capture_format", "context-intelligence")
        if capture_format not in ("context-intelligence", "amplifier-transcript"):
            raise invalid
        if (
            not isinstance(directory, str)
            or not directory.startswith("/")
            or directory.startswith("//")
            or "\0" in directory
            or any(part in {".", ".."} for part in directory.split("/"))
            or not is_safe_session_id(session_id)
        ):
            raise invalid
        entries.append((alias, Path(directory), session_id, capture_format))
    configured: dict[str, ConfiguredCapture] = {}
    for alias, directory, session_id, capture_format in entries:
        try:
            with _open_capture_directory(directory) as descriptor:
                identity = os.fstat(descriptor)
                configured[alias] = ConfiguredCapture(
                    directory, session_id, (identity.st_dev, identity.st_ino), capture_format
                )
        except OSError:
            raise NativeTranscriptError(
                "invalid_capture_config", "configured capture directory is unavailable or unsafe"
            ) from None
    return MappingProxyType(configured)


@contextmanager
def _open_capture_file(directory_fd: int, name: str) -> Iterator[BinaryIO]:
    """Open only a fixed literal child and reject FIFOs/devices before reading."""
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise NativeTranscriptError(
                "configured_capture_unsafe", "configured capture files must be regular files"
            )
        stream = os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
    with stream:
        yield stream


@dataclass(frozen=True)
class TranscriptRequest:
    """Bounds and presentation choices for one transcript page."""

    after_event_line: int = 0
    max_messages: int = 50
    max_content_chars: int = 50_000
    timestamp_every_seconds: int = 300


@dataclass(frozen=True)
class TranscriptTextPart:
    """One verbatim text block with its original zero-based content index."""

    index: int
    text: str


@dataclass(frozen=True)
class TranscriptMessage:
    sequence: int
    role: TranscriptRole
    content: str | tuple[TranscriptTextPart, ...]
    timestamp: str | None
    event_line: int
    source_event: Literal["prompt:submit", "prompt:complete", "transcript:message"]
    context_flags: tuple[tuple[str, bool], ...] = ()

    @property
    def content_chars(self) -> int:
        return (
            len(self.content)
            if isinstance(self.content, str)
            else sum(len(part.text) for part in self.content)
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result.pop("context_flags")
        if not isinstance(self.content, str):
            result["content"] = [asdict(part) for part in self.content]
        if self.context_flags:
            result["context_flags"] = dict(self.context_flags)
        return result


@dataclass(frozen=True)
class TranscriptIssue:
    code: str
    event_line: int | None
    detail: str


@dataclass(frozen=True)
class TranscriptPage:
    status: TranscriptStatus
    session_id: str
    workspace: str | None
    schema_version: str | None
    messages: tuple[TranscriptMessage, ...]
    issues: tuple[TranscriptIssue, ...]
    through_event_line: int | None
    next_after_event_line: int | None
    has_more: bool
    content_chars: int
    source_format: CaptureFormat = "context-intelligence"
    omitted_messages: int = 0
    omitted_blocks: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-shaped transcript page."""
        result = {
            "status": self.status,
            "session_id": self.session_id,
            "workspace": self.workspace,
            "schema_version": self.schema_version,
            "messages": [message.as_dict() for message in self.messages],
            "issues": [asdict(issue) for issue in self.issues],
            "through_event_line": self.through_event_line,
            "next_after_event_line": self.next_after_event_line,
            "has_more": self.has_more,
            "content_chars": self.content_chars,
        }
        if self.source_format == "amplifier-transcript":
            result["projection"] = {
                "omitted_messages": self.omitted_messages,
                "omitted_blocks": self.omitted_blocks,
            }
        return result


def _load_metadata(locator: CaptureLocator) -> dict[str, Any]:
    try:
        metadata = json.loads(locator.metadata_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NativeTranscriptError(
            "metadata_unavailable", f"metadata not found: {locator.metadata_path}"
        ) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise NativeTranscriptError(
            "metadata_unavailable", f"invalid metadata JSON: {locator.metadata_path}"
        ) from exc
    except OSError as exc:
        raise NativeTranscriptError(
            "read_error", f"could not read metadata: {locator.metadata_path}"
        ) from exc

    return _validate_metadata(metadata)


def _validate_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise NativeTranscriptError("metadata_unavailable", "metadata must be a JSON object")
    if metadata.get("format") != "context-intelligence":
        raise NativeTranscriptError(
            "unsupported_capture_format",
            "expected context-intelligence capture",
        )
    version = metadata.get("version")
    if version != _SUPPORTED_SCHEMA_VERSION:
        raise NativeTranscriptError(
            "unsupported_schema_version",
            f"unsupported capture version; expected {_SUPPORTED_SCHEMA_VERSION}",
        )
    session_id = metadata.get("session_id")
    workspace = metadata.get("workspace")
    if not isinstance(session_id, str) or not session_id:
        raise NativeTranscriptError("metadata_unavailable", "metadata has no session_id")
    if not isinstance(workspace, str):
        raise NativeTranscriptError("metadata_unavailable", "metadata has no workspace")
    return metadata


def _validate_request(request: TranscriptRequest) -> None:
    if (
        not isinstance(request.after_event_line, int)
        or isinstance(request.after_event_line, bool)
        or request.after_event_line < 0
    ):
        raise NativeTranscriptError("invalid_request", "after_event_line must be at least zero")
    if (
        not isinstance(request.max_messages, int)
        or isinstance(request.max_messages, bool)
        or not 1 <= request.max_messages <= 200
    ):
        raise NativeTranscriptError("invalid_request", "max_messages must be between 1 and 200")
    if (
        not isinstance(request.max_content_chars, int)
        or isinstance(request.max_content_chars, bool)
        or not 1 <= request.max_content_chars <= 200_000
    ):
        raise NativeTranscriptError(
            "invalid_request", "max_content_chars must be between 1 and 200000"
        )
    if (
        not isinstance(request.timestamp_every_seconds, int)
        or isinstance(request.timestamp_every_seconds, bool)
        or not 1 <= request.timestamp_every_seconds <= 86_400
    ):
        raise NativeTranscriptError(
            "invalid_request", "timestamp_every_seconds must be between 1 and 86400"
        )


def _iter_bounded_event_lines(events_path: Path):
    """Yield physical event lines without materialising an unbounded payload."""
    with events_path.open("rb") as event_file:
        yield from _iter_bounded_stream_lines(event_file)


def _iter_bounded_stream_lines(event_file: BinaryIO) -> Iterator[tuple[int, str | None]]:
    line_number = 0
    while raw_line := event_file.readline(_MAX_EVENT_BYTES + 1):
        line_number += 1
        if len(raw_line) > _MAX_EVENT_BYTES:
            while not raw_line.endswith(b"\n"):
                raw_line = event_file.readline(_MAX_EVENT_BYTES + 1)
                if not raw_line:
                    yield line_number, None
                    return
            yield line_number, None
            continue
        try:
            yield line_number, raw_line.decode("utf-8")
        except UnicodeDecodeError:
            yield line_number, None


def _message_from_event(
    event: dict[str, Any], line_number: int
) -> TranscriptMessage | TranscriptIssue | None:
    event_name = event.get("event")
    if not isinstance(event_name, str) or event_name not in {"prompt:submit", "prompt:complete"}:
        return None
    data = event.get("data")
    timestamp = event.get("timestamp")
    if not isinstance(data, dict) or not isinstance(timestamp, str):
        return TranscriptIssue(
            "invalid_event_envelope",
            line_number,
            "prompt event lacks object data or string timestamp",
        )

    role: TranscriptRole = "user" if event_name == "prompt:submit" else "assistant"
    field = "prompt" if role == "user" else "response"
    content = data.get(field)
    if not isinstance(content, str):
        return TranscriptIssue(
            "missing_message_field",
            line_number,
            f"{event_name} lacks string data.{field}",
        )
    return TranscriptMessage(
        sequence=0,
        role=role,
        content=content,
        timestamp=timestamp,
        event_line=line_number,
        source_event="prompt:submit" if role == "user" else "prompt:complete",
    )


def read_native_transcript(
    locator: CaptureLocator, request: TranscriptRequest = TranscriptRequest()
) -> TranscriptPage:
    """Read one bounded, lossless page of native user and assistant messages.

    A normal page boundary is successful pagination: the next complete message
    is left for the next call and ``has_more`` is true. A partial status means
    an actual capture problem, never merely that the requested page filled.
    """
    _validate_request(request)
    metadata = _load_metadata(locator)
    return _read_transcript_lines(
        locator, metadata, request, _iter_bounded_event_lines(locator.events_path)
    )


def read_configured_capture(
    capture: ConfiguredCapture, request: TranscriptRequest = TranscriptRequest()
) -> TranscriptPage:
    """Read a pinned grant using no-follow handles and the native parser."""
    _validate_request(request)
    locator = CaptureLocator.from_session_dir(capture.directory)
    try:
        with _open_capture_directory(capture.directory) as directory_fd:
            identity = os.fstat(directory_fd)
            if (identity.st_dev, identity.st_ino) != capture.directory_identity:
                raise NativeTranscriptError(
                    "configured_capture_changed", "configured capture directory identity changed"
                )
            with _open_capture_file(directory_fd, "metadata.json") as metadata_file:
                raw_metadata = metadata_file.read(_MAX_METADATA_BYTES + 1)
            if len(raw_metadata) > _MAX_METADATA_BYTES:
                raise NativeTranscriptError(
                    "metadata_unavailable", "configured capture metadata exceeds 64 KiB"
                )
            try:
                decoded_metadata = json.loads(raw_metadata.decode("utf-8"))
                if capture.capture_format == "context-intelligence":
                    metadata = _validate_metadata(decoded_metadata)
                else:
                    if not isinstance(decoded_metadata, dict):
                        raise NativeTranscriptError(
                            "metadata_unavailable", "metadata must be a JSON object"
                        )
                    if not isinstance(decoded_metadata.get("session_id"), str):
                        raise NativeTranscriptError(
                            "metadata_unavailable", "metadata has no session_id"
                        )
                    metadata = decoded_metadata
            except (ValueError, RecursionError):
                raise NativeTranscriptError(
                    "metadata_unavailable", "configured capture metadata is not valid UTF-8 JSON"
                ) from None
            if metadata["session_id"] != capture.session_id:
                raise NativeTranscriptError(
                    "configured_capture_identity_mismatch",
                    "configured capture metadata does not match the expected session identity",
                )
            # Identity and the selected profile must pass before content opens.
            filename = (
                "transcript.jsonl"
                if capture.capture_format == "amplifier-transcript"
                else "events.jsonl"
            )
            with _open_capture_file(directory_fd, filename) as event_file:
                return _read_transcript_lines(
                    locator,
                    metadata,
                    request,
                    _iter_bounded_stream_lines(event_file),
                    capture.capture_format,
                )
    except OSError:
        raise NativeTranscriptError(
            "configured_capture_unavailable", "configured capture is unavailable or unsafe"
        ) from None
    except NativeTranscriptError as exc:
        if exc.code == "message_too_large":
            raise NativeTranscriptError(
                "message_too_large",
                "a configured capture message exceeds the requested content budget",
            ) from None
        if exc.code in {"read_error", "capture_unavailable"}:
            raise NativeTranscriptError(
                "configured_capture_unavailable", "configured capture could not be read"
            ) from None
        raise


def _read_transcript_lines(
    locator: CaptureLocator,
    metadata: dict[str, Any],
    request: TranscriptRequest,
    event_lines: Iterable[tuple[int, str | None]],
    source_format: CaptureFormat = "context-intelligence",
) -> TranscriptPage:
    messages: list[TranscriptMessage] = []
    issues: list[TranscriptIssue] = []
    persisted = source_format == "amplifier-transcript"
    if persisted and "workspace" in metadata and not isinstance(metadata["workspace"], str):
        issues.append(
            TranscriptIssue("invalid_metadata_workspace", None, "workspace must be a string")
        )
    omitted_messages = omitted_blocks = 0
    content_chars = 0
    through_event_line: int | None = request.after_event_line or None
    has_more = False

    try:
        for line_number, raw_line in event_lines:
            if line_number <= request.after_event_line:
                continue
            if raw_line is None:
                issues.append(
                    TranscriptIssue(
                        "event_too_large_or_invalid_encoding",
                        line_number,
                        (
                            f"physical transcript line exceeds {_MAX_EVENT_BYTES} bytes or is not UTF-8"
                            if persisted
                            else f"event line exceeds {_MAX_EVENT_BYTES} bytes or is not UTF-8"
                        ),
                    )
                )
                through_event_line = line_number
                continue
            if line_number <= request.after_event_line or not raw_line.strip():
                continue
            try:
                event = json.loads(raw_line)
            except (ValueError, RecursionError):
                issues.append(
                    TranscriptIssue(
                        "malformed_json",
                        line_number,
                        "invalid JSON transcript line" if persisted else "invalid JSON event line",
                    )
                )
                through_event_line = line_number
                continue
            if not isinstance(event, dict):
                issues.append(
                    TranscriptIssue(
                        "invalid_event_envelope",
                        line_number,
                        "transcript line is not an object"
                        if persisted
                        else "event line is not an object",
                    )
                )
                through_event_line = line_number
                continue
            line_issues: tuple[TranscriptIssue, ...] = ()
            line_omitted_messages = line_omitted_blocks = 0
            if persisted:
                # The adapter is pure; this deferred import avoids a types cycle.
                from .persisted_transcript import decode_persisted_message

                projection = decode_persisted_message(event, line_number)
                message_or_issue = projection.message
                line_issues = projection.issues
                line_omitted_messages = projection.omitted_messages
                line_omitted_blocks = projection.omitted_blocks
            else:
                message_or_issue = _message_from_event(event, line_number)
            if isinstance(message_or_issue, TranscriptIssue):
                issues.append(message_or_issue)
                through_event_line = line_number
                continue
            if message_or_issue is None:
                issues.extend(line_issues)
                omitted_messages += line_omitted_messages
                omitted_blocks += line_omitted_blocks
                through_event_line = line_number
                continue

            message_size = message_or_issue.content_chars
            next_size = content_chars + message_size
            if not messages and message_size > request.max_content_chars:
                raise NativeTranscriptError(
                    "message_too_large",
                    f"event line {line_number} contains {message_size} characters; "
                    f"increase max_content_chars above {request.max_content_chars}",
                )
            if len(messages) >= request.max_messages or next_size > request.max_content_chars:
                has_more = True
                break
            messages.append(replace(message_or_issue, sequence=len(messages) + 1))
            issues.extend(line_issues)
            omitted_messages += line_omitted_messages
            omitted_blocks += line_omitted_blocks
            content_chars = next_size
            through_event_line = line_number
    except FileNotFoundError as exc:
        raise NativeTranscriptError(
            "capture_unavailable", f"events file not found: {locator.events_path}"
        ) from exc
    except OSError as exc:
        raise NativeTranscriptError(
            "read_error", f"could not read events: {locator.events_path}"
        ) from exc

    status: TranscriptStatus = "partial" if issues else "complete"
    next_after = through_event_line if has_more else None
    return TranscriptPage(
        status=status,
        session_id=metadata["session_id"],
        workspace=metadata.get("workspace") if isinstance(metadata.get("workspace"), str) else None,
        schema_version=None if persisted else metadata["version"],
        messages=tuple(messages),
        issues=tuple(issues),
        through_event_line=through_event_line,
        next_after_event_line=next_after,
        has_more=has_more,
        content_chars=content_chars,
        source_format=source_format,
        omitted_messages=omitted_messages,
        omitted_blocks=omitted_blocks,
    )


def _timestamp_is_due(previous: str | None, current: str, interval_seconds: int) -> bool:
    """Return whether an exact source timestamp needs a new display marker."""
    if previous is None:
        return True
    try:
        before = datetime.fromisoformat(previous.replace("Z", "+00:00"))
        after = datetime.fromisoformat(current.replace("Z", "+00:00"))
        return before.date() != after.date() or (after - before).total_seconds() >= interval_seconds
    except (ValueError, TypeError):
        # An invalid timestamp cannot be safely compared. Showing it preserves
        # provenance and makes the capture defect visible without invention.
        return True


def render_native_transcript(page: TranscriptPage, *, timestamp_every_seconds: int = 300) -> str:
    """Render a compact role-marked transcript without altering message content."""
    _validate_request(TranscriptRequest(timestamp_every_seconds=timestamp_every_seconds))
    lines = [
        f"=== SESSION {page.session_id} ===",
        f"Workspace: {page.workspace}",
        (
            "Capture: amplifier-transcript transcript.jsonl (stored text projection; physical lines)"
            if page.source_format == "amplifier-transcript"
            else f"Capture: native events.jsonl (schema {page.schema_version})"
        ),
        "",
    ]
    last_marker_timestamp: str | None = None
    for message in page.messages:
        if message.timestamp is not None and _timestamp_is_due(
            last_marker_timestamp, message.timestamp, timestamp_every_seconds
        ):
            lines.append(f"@ {message.timestamp}")
            last_marker_timestamp = message.timestamp
        unit = "physical-line" if page.source_format == "amplifier-transcript" else "event-line"
        lines.append(f"[{message.role.upper()} | {unit} {message.event_line}]")
        if message.context_flags:
            lines.append(
                "[STORED CONTEXT FLAGS | "
                + ", ".join(f"{key}={str(value).lower()}" for key, value in message.context_flags)
                + "]"
            )
        if isinstance(message.content, str):
            lines.append(message.content)
        else:
            for part in message.content:
                lines.append(f"[TEXT BLOCK {part.index}]")
                lines.append(part.text)
                lines.append(f"[END TEXT BLOCK {part.index}]")
        lines.append("")

    if page.has_more:
        lines.append(
            f"[MORE MESSAGES AVAILABLE | resume with after-event-line={page.next_after_event_line}]"
        )
    elif page.issues:
        lines.append("[END OF TRANSCRIPT WITH CAPTURE ISSUES]")
    else:
        lines.append(f"[END OF TRANSCRIPT | {len(page.messages)} messages shown]")
    return "\n".join(lines)
