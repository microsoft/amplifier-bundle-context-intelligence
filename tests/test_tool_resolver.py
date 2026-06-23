"""Unit tests for context_intelligence.tool_resolver.

Tests the shared helpers (_first_entry, _first_destination, resolve_query_endpoint)
and ToolConfigResolver.read_destinations (including legacy synthesis — spec §7 cases
#9 and #10 at the unit level).
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(config: dict | None = None) -> MagicMock:
    """MagicMock coordinator with a real .config dict (not a Mock auto-attr)."""
    coordinator = MagicMock()
    coordinator.config = config if config is not None else {}
    return coordinator


def _make_dest(url: str, api_key: str, name: str = "default") -> SimpleNamespace:
    """Destination-like SimpleNamespace."""
    return SimpleNamespace(name=name, url=url, api_key=api_key)


# ---------------------------------------------------------------------------
# TestFirstEntry
# ---------------------------------------------------------------------------


class TestFirstEntry:
    """_first_entry() — edge-case coverage."""

    def test_none_returns_none(self) -> None:
        from context_intelligence.tool_resolver import _first_entry

        assert _first_entry(None) is None

    def test_empty_dict_returns_none(self) -> None:
        from context_intelligence.tool_resolver import _first_entry

        assert _first_entry({}) is None

    def test_non_dict_mock_returns_none(self) -> None:
        """A MagicMock (auto-created attribute) is not a dict → None."""
        from context_intelligence.tool_resolver import _first_entry

        assert _first_entry(MagicMock()) is None

    def test_single_entry_returns_value(self) -> None:
        from context_intelligence.tool_resolver import _first_entry

        result = _first_entry({"alpha": "hello"})
        assert result == "hello"

    def test_two_entry_dict_returns_first(self) -> None:
        from context_intelligence.tool_resolver import _first_entry

        result = _first_entry({"alpha": 1, "beta": 2})
        assert result == 1


# ---------------------------------------------------------------------------
# TestFirstDestination
# ---------------------------------------------------------------------------


class TestFirstDestination:
    """_first_destination() — delegating edge cases."""

    def test_none_resolver_returns_none(self) -> None:
        from context_intelligence.tool_resolver import _first_destination

        assert _first_destination(None) is None

    def test_resolver_with_empty_destinations_returns_none(self) -> None:
        from context_intelligence.tool_resolver import _first_destination

        resolver = MagicMock()
        resolver.destinations = {}
        assert _first_destination(resolver) is None

    def test_resolver_with_no_destinations_attr_returns_none(self) -> None:
        """getattr(resolver, 'destinations', None) returns None for a plain object."""
        from context_intelligence.tool_resolver import _first_destination

        class NoDestinations:
            pass

        assert _first_destination(NoDestinations()) is None


# ---------------------------------------------------------------------------
# TestResolveQueryEndpointHelpers
# ---------------------------------------------------------------------------


class TestResolveQueryEndpoint:
    """resolve_query_endpoint() — the three-tier chain."""

    def _make_empty_tool_resolver(self) -> Any:
        from context_intelligence.tool_resolver import ToolConfigResolver

        return ToolConfigResolver({}, _make_coordinator())

    async def test_none_hook_and_empty_tool_resolver_returns_none_tuple(self) -> None:
        from context_intelligence.tool_resolver import resolve_query_endpoint

        tool_resolver = self._make_empty_tool_resolver()
        # Clear all CI env vars so tier-3 fallback cannot supply a URL
        clean = {k: "" for k in os.environ if k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")}
        with patch.dict(os.environ, clean):
            url, api_key = resolve_query_endpoint(None, tool_resolver)
        assert url is None
        assert api_key is None

    async def test_debug_log_emitted(self, caplog: Any) -> None:
        import logging

        from context_intelligence.tool_resolver import resolve_query_endpoint

        tool_resolver = self._make_empty_tool_resolver()
        with caplog.at_level(logging.DEBUG, logger="context_intelligence.tool_resolver"):
            resolve_query_endpoint(None, tool_resolver)

        assert any("CI query endpoint resolved" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# TestToolConfigResolverReadDestinations
# ---------------------------------------------------------------------------


class TestToolConfigResolverReadDestinations:
    """ToolConfigResolver.read_destinations — parsing and legacy synthesis."""

    def test_explicit_mapping_parsed(self) -> None:
        """Explicit read_destinations key → parsed into ReadEndpoint dict."""
        from context_intelligence.tool_resolver import ReadEndpoint, ToolConfigResolver

        config = {
            "read_destinations": {
                "default": {"url": "http://read.example.com", "api_key": "read-key"},
            }
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        rd = resolver.read_destinations

        assert "default" in rd
        assert isinstance(rd["default"], ReadEndpoint)
        assert rd["default"].url == "http://read.example.com"
        assert rd["default"].api_key == "read-key"
        assert rd["default"].name == "default"

    def test_explicit_empty_dict_returns_empty(self) -> None:
        """Explicit read_destinations={} → {} (no legacy synthesis)."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {"read_destinations": {}}
        resolver = ToolConfigResolver(config, _make_coordinator())
        assert resolver.read_destinations == {}

    def test_non_dict_entry_skipped(self) -> None:
        """Non-dict entries under read_destinations are silently skipped."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {
            "read_destinations": {
                "bad": "not-a-dict",
                "good": {"url": "http://read.example.com", "api_key": "k"},
            }
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        rd = resolver.read_destinations
        assert "bad" not in rd
        assert "good" in rd

    def test_url_api_key_stripped(self) -> None:
        """Whitespace in url/api_key is stripped."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {
            "read_destinations": {
                "default": {"url": "  http://read.example.com  ", "api_key": "  key  "},
            }
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        rd = resolver.read_destinations
        assert rd["default"].url == "http://read.example.com"
        assert rd["default"].api_key == "key"

    # --- Case #9: legacy synthesis when BOTH scalars present ---

    def test_absent_key_both_scalars_synthesizes_default(self) -> None:
        """Case #9 unit: absent read_destinations + both scalars → synthesized default."""
        from context_intelligence.tool_resolver import ReadEndpoint, ToolConfigResolver

        config = {
            "context_intelligence_server_url": "http://legacy.example.com",
            "context_intelligence_api_key": "legacy-key",
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        rd = resolver.read_destinations

        assert "default" in rd
        assert isinstance(rd["default"], ReadEndpoint)
        assert rd["default"].url == "http://legacy.example.com"
        assert rd["default"].api_key == "legacy-key"

    # --- Case #10: url-only → no synthesis ---

    def test_absent_key_url_only_no_synthesis(self) -> None:
        """Case #10 unit: absent read_destinations + url only → {} (no synthesis)."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {
            "context_intelligence_server_url": "http://legacy.example.com",
            # no context_intelligence_api_key
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        assert resolver.read_destinations == {}

    def test_absent_key_no_scalars_returns_empty(self) -> None:
        """Absent key + no scalars → {} (neither synthesis nor parse)."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        resolver = ToolConfigResolver({}, _make_coordinator())
        assert resolver.read_destinations == {}

    def test_read_destinations_cached_after_first_access(self) -> None:
        """read_destinations is cached — same object returned on second access."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {
            "read_destinations": {
                "default": {"url": "http://read.example.com", "api_key": "k"},
            }
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        rd1 = resolver.read_destinations
        rd2 = resolver.read_destinations
        assert rd1 is rd2

    # --- env is excluded from legacy synthesis (tier 3 only) ---

    def test_absent_key_env_does_not_synthesize_into_tier1(self) -> None:
        """Env vars are excluded from legacy synthesis — env can never enter tier 1.

        Setting canonical env vars alone does NOT cause legacy synthesis to fire.
        Env is only consulted at tier 3 in resolve_query_endpoint(), which is
        BELOW the hook destination (tier 2). This prevents env from outranking
        an upload destination via the synthesis path.
        """
        from context_intelligence.tool_resolver import ToolConfigResolver

        env_patch = {
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env-scalar.example.com",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY": "env-scalar-key",
        }
        with patch.dict(os.environ, env_patch):
            resolver = ToolConfigResolver({}, _make_coordinator())
            rd = resolver.read_destinations

        # Env must NOT synthesize into read_destinations — result is empty dict.
        assert rd == {}
