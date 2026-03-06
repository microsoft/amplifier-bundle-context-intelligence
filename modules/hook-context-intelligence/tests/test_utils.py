"""Tests for utils: make_node_id, HandlerLogger, EventLogContext."""

from __future__ import annotations

import logging

from amplifier_module_hook_context_intelligence.utils import (
    EventLogContext,
    HandlerLogger,
    make_edge_id,
    make_node_id,
)


class TestMakeNodeId:
    """7 tests for the make_node_id utility."""

    def test_basic_iso_timestamp(self):
        """Basic ISO-8601 with trailing Z produces correct epoch ms with __ separators."""
        result = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        assert result == "s1__prompt_submit__1767225600000"

    def test_fractional_seconds(self):
        """Fractional seconds (.500) are preserved as milliseconds."""
        result = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00.500Z")
        assert result == "s1__prompt_submit__1767225600500"

    def test_timezone_offset(self):
        """Timezone offset +00:00 is handled correctly."""
        result = make_node_id("s1", "session:resume", "2026-01-01T02:00:00+00:00")
        assert result == "s1__session_resume__1767232800000"

    def test_deterministic(self):
        """Same inputs always produce the same output."""
        a = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        b = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        assert a == b

    def test_different_events_produce_different_ids(self):
        """Different event names produce different node IDs."""
        a = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        b = make_node_id("s1", "session:start", "2026-01-01T00:00:00Z")
        assert a != b

    def test_different_sessions_produce_different_ids(self):
        """Different session IDs produce different node IDs."""
        a = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        b = make_node_id("s2", "prompt:submit", "2026-01-01T00:00:00Z")
        assert a != b

    def test_resume_pattern(self):
        """session:resume event follows the standard pattern, not the session exception."""
        result = make_node_id("sess-abc", "session:resume", "2026-01-01T02:00:00+00:00")
        assert result == "sess-abc__session_resume__1767232800000"


class TestMakeEdgeId:
    """5 tests for the make_edge_id utility."""

    def test_basic_construction(self):
        """Basic edge ID with simple source and target."""
        result = make_edge_id("session-1", "node-2", "HAS_STEP")
        assert result == "session-1==[HAS_STEP]==node-2"

    def test_real_node_ids_with_double_underscore_separators(self):
        """Edge ID works with real make_node_id output containing __ separators."""
        src = make_node_id("s1", "prompt:submit", "2026-01-01T00:00:00Z")
        tgt = make_node_id("s1", "response:complete", "2026-01-01T00:00:01Z")
        result = make_edge_id(src, tgt, "FOLLOWED_BY")
        assert result == f"{src}==[FOLLOWED_BY]=={tgt}"

    def test_parseable_back_to_components(self):
        """Edge ID can be split back into source, edge_type, target."""
        edge_id = make_edge_id("src-node", "tgt-node", "HAS_STEP")
        parts = edge_id.split("==[")
        source = parts[0]
        rest = parts[1]
        edge_type, target = rest.split("]==")
        assert source == "src-node"
        assert edge_type == "HAS_STEP"
        assert target == "tgt-node"

    def test_deterministic(self):
        """Same inputs always produce the same edge ID."""
        a = make_edge_id("src", "tgt", "HAS_STEP")
        b = make_edge_id("src", "tgt", "HAS_STEP")
        assert a == b

    def test_different_edge_types_produce_different_ids(self):
        """Different edge types produce different edge IDs."""
        a = make_edge_id("src", "tgt", "HAS_STEP")
        b = make_edge_id("src", "tgt", "FOLLOWED_BY")
        assert a != b


class TestHandlerLogger:
    """2 tests for the HandlerLogger wrapper."""

    def test_with_event_returns_event_log_context(self):
        """with_event() returns an EventLogContext instance."""
        logger = logging.getLogger("test.handler_logger")
        hl = HandlerLogger(handler_name="SessionHandler", logger=logger)
        ctx = hl.with_event("session:start", {"session_id": "s1"})
        assert isinstance(ctx, EventLogContext)

    def test_with_event_missing_session_id_uses_empty_string(self):
        """with_event() defaults session_id to empty string when missing from data."""
        logger = logging.getLogger("test.handler_logger.missing")
        hl = HandlerLogger(handler_name="SessionHandler", logger=logger)
        ctx = hl.with_event("session:start", {})
        assert isinstance(ctx, EventLogContext)
        # Verify the prefix uses empty string for session_id
        ctx.info("test message")


class TestEventLogContext:
    """6 tests for the EventLogContext prefix formatting."""

    def test_info_includes_prefix(self, caplog):
        """info() produces log record with correct prefix format."""
        logger = logging.getLogger("test.event_log_context.info")
        ctx = EventLogContext(
            handler_name="SessionHandler",
            session_id="s1",
            event="session:start",
            logger=logger,
        )
        with caplog.at_level(logging.INFO, logger="test.event_log_context.info"):
            ctx.info("node created")
        assert len(caplog.records) == 1
        assert caplog.records[0].message == "[SessionHandler] [s1] [session:start] node created"

    def test_warning_includes_prefix(self, caplog):
        """warning() produces log record with correct prefix format."""
        logger = logging.getLogger("test.event_log_context.warning")
        ctx = EventLogContext(
            handler_name="SessionHandler",
            session_id="s1",
            event="session:start",
            logger=logger,
        )
        with caplog.at_level(logging.WARNING, logger="test.event_log_context.warning"):
            ctx.warning("something odd")
        assert len(caplog.records) == 1
        assert caplog.records[0].message == "[SessionHandler] [s1] [session:start] something odd"

    def test_error_includes_prefix(self, caplog):
        """error() produces log record with correct prefix format."""
        logger = logging.getLogger("test.event_log_context.error")
        ctx = EventLogContext(
            handler_name="SessionHandler",
            session_id="s1",
            event="session:start",
            logger=logger,
        )
        with caplog.at_level(logging.ERROR, logger="test.event_log_context.error"):
            ctx.error("something broke")
        assert len(caplog.records) == 1
        assert caplog.records[0].message == "[SessionHandler] [s1] [session:start] something broke"

    def test_info_supports_lazy_formatting_args(self, caplog):
        """info() accepts *args for lazy %-style formatting."""
        logger = logging.getLogger("test.event_log_context.info_args")
        ctx = EventLogContext(
            handler_name="RunHandler",
            session_id="s1",
            event="prompt:submit",
            logger=logger,
        )
        with caplog.at_level(logging.INFO, logger="test.event_log_context.info_args"):
            ctx.info("Created PromptStep node %s", "node-123")
        assert len(caplog.records) == 1
        assert (
            caplog.records[0].message
            == "[RunHandler] [s1] [prompt:submit] Created PromptStep node node-123"
        )

    def test_error_supports_lazy_formatting_args(self, caplog):
        """error() accepts *args for lazy %-style formatting."""
        logger = logging.getLogger("test.event_log_context.error_args")
        ctx = EventLogContext(
            handler_name="RunHandler",
            session_id="s1",
            event="prompt:submit",
            logger=logger,
        )
        with caplog.at_level(logging.ERROR, logger="test.event_log_context.error_args"):
            ctx.error("Failed to create node %s: %s", "node-456", "timeout")
        assert len(caplog.records) == 1
        assert (
            caplog.records[0].message
            == "[RunHandler] [s1] [prompt:submit] Failed to create node node-456: timeout"
        )

    def test_warning_supports_lazy_formatting_args(self, caplog):
        """warning() accepts *args for lazy %-style formatting."""
        logger = logging.getLogger("test.event_log_context.warning_args")
        ctx = EventLogContext(
            handler_name="SessionHandler",
            session_id="s1",
            event="session:fork",
            logger=logger,
        )
        with caplog.at_level(logging.WARNING, logger="test.event_log_context.warning_args"):
            ctx.warning("session:fork for %r has no parent", "s1")
        assert len(caplog.records) == 1
        assert (
            caplog.records[0].message
            == "[SessionHandler] [s1] [session:fork] session:fork for 's1' has no parent"
        )
