"""Shared utilities for context-intelligence handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any


def make_node_id(
    session_id: str,
    event_name: str,
    timestamp: str,
    disambiguator: str | None = None,
) -> str:
    """Generate a deterministic, filesystem-safe node ID from event data.

    Pattern: {session_id}__{safe_event}__{timestamp_ms}
    With disambiguator: {session_id}__{safe_event}__{timestamp_ms}__{disambiguator}

    Colons in *event_name* are replaced with underscores so the ID is safe
    for use as a filename component.  Parses ISO-8601 timestamps (with
    fractional seconds and timezone offsets) and converts to epoch
    milliseconds.

    The optional *disambiguator* (e.g. tool_call_id) is appended as a fourth
    segment when provided.  When omitted, the format is unchanged — full
    backward compatibility.
    """
    safe_event = event_name.replace(":", "_")
    dt = datetime.fromisoformat(timestamp)
    epoch_ms = int(dt.astimezone(timezone.utc).timestamp() * 1000)
    node_id = f"{session_id}__{safe_event}__{epoch_ms}"
    if disambiguator is not None:
        node_id = f"{node_id}__{disambiguator}"
    return node_id


def make_edge_id(source_id: str, target_id: str, edge_type: str) -> str:
    """Generate a deterministic edge ID from source, target, and type.

    Pattern: {source_id}==[{edge_type}]=={target_id}

    The ``==[`` and ``]==`` separators never appear in node IDs, so edge
    IDs are always unambiguously parseable back into their three components.
    """
    return f"{source_id}==[{edge_type}]=={target_id}"


class EventLogContext:
    """Log context with handler name, session_id, and event name pre-bound as prefix."""

    def __init__(
        self,
        handler_name: str,
        session_id: str,
        event: str,
        logger: logging.Logger,
    ) -> None:
        self._logger = logger
        self._prefix = f"[{handler_name}] [{session_id}] [{event}]"

    def info(self, message: str, *args: object) -> None:
        """Log an info message with the pre-bound prefix."""
        self._logger.info("%s " + message, self._prefix, *args)

    def warning(self, message: str, *args: object) -> None:
        """Log a warning message with the pre-bound prefix."""
        self._logger.warning("%s " + message, self._prefix, *args)

    def error(self, message: str, *args: object) -> None:
        """Log an error message with the pre-bound prefix."""
        self._logger.error("%s " + message, self._prefix, *args)


class HandlerLogger:
    """Structured logging wrapper that binds handler name to every log call."""

    def __init__(self, handler_name: str, logger: logging.Logger) -> None:
        self._handler_name = handler_name
        self._logger = logger

    def with_event(self, event: str, data: dict[str, Any]) -> EventLogContext:
        """Return an EventLogContext with session_id extracted from data."""
        session_id = data.get("session_id", "")
        return EventLogContext(
            handler_name=self._handler_name,
            session_id=session_id,
            event=event,
            logger=self._logger,
        )
