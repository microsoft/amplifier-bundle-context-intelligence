"""Shared utilities for context-intelligence handlers."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any


def make_node_id(session_id: str, event_name: str, timestamp: str) -> str:
    """Generate a deterministic node ID from event data.

    Pattern: {session_id}:{event_name}:{timestamp_ms}

    Parses ISO-8601 timestamps (with fractional seconds and timezone offsets)
    and converts to epoch milliseconds.
    """
    dt = datetime.fromisoformat(timestamp)
    epoch_ms = int(dt.astimezone(timezone.utc).timestamp() * 1000)
    return f"{session_id}:{event_name}:{epoch_ms}"


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

    def info(self, message: str) -> None:
        """Log an info message with the pre-bound prefix."""
        self._logger.info("%s %s", self._prefix, message)

    def warning(self, message: str) -> None:
        """Log a warning message with the pre-bound prefix."""
        self._logger.warning("%s %s", self._prefix, message)

    def error(self, message: str) -> None:
        """Log an error message with the pre-bound prefix."""
        self._logger.error("%s %s", self._prefix, message)


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
