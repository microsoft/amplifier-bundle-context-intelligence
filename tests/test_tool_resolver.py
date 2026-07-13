"""Unit tests for context_intelligence.tool_resolver.

Tests ToolConfigResolver.sources (including legacy synthesis — spec §7 cases
#9 and #10 at the unit level), _select_source (the default-path source tiers),
and validate_source/validate_sources (per-entry vs whole-map fail-loud).

v5 (docs/multi-source-build-spec-v5.md §4-5) replaced `_first_entry`,
`_first_destination`, `resolve_query_endpoint`, and `resolve_query_auth_strategy`
with `_connectable_pool`, `_select_from_pool`, and `resolve_query_connection` --
that coverage now lives in modules/tool-context-intelligence-query/tests/
test_pool_and_selection.py (pure unit, p1-p12) and test_multi_source_e2e.py
(real-socket scenarios e-j), alongside the module that owns the tools which are
resolve_query_connection's only callers.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


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


# NOTE (v5): _first_entry, _first_destination, resolve_query_endpoint, and
# resolve_query_auth_strategy were removed -- replaced by _connectable_pool,
# _select_from_pool, and resolve_query_connection. Their unit coverage now
# lives in modules/tool-context-intelligence-query/tests/test_pool_and_selection.py
# (pure, p1-p12) and test_multi_source_e2e.py (real-socket, scenarios e-j).


# ---------------------------------------------------------------------------
# TestToolConfigResolverSources
# ---------------------------------------------------------------------------


class TestToolConfigResolverSources:
    """ToolConfigResolver.sources — parsing and legacy synthesis."""

    def test_explicit_mapping_parsed(self) -> None:
        """Explicit sources key → parsed into Source dict."""
        from context_intelligence.tool_resolver import Source, ToolConfigResolver

        config = {
            "sources": {
                "default": {"url": "http://read.example.com", "api_key": "read-key"},
            }
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        rd = resolver.sources

        assert "default" in rd
        assert isinstance(rd["default"], Source)
        assert rd["default"].url == "http://read.example.com"
        assert rd["default"].api_key == "read-key"
        assert rd["default"].name == "default"

    def test_explicit_empty_dict_returns_empty(self) -> None:
        """Explicit sources={} → {} (no legacy synthesis)."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {"sources": {}}
        resolver = ToolConfigResolver(config, _make_coordinator())
        assert resolver.sources == {}

    def test_non_dict_entry_skipped(self) -> None:
        """Non-dict entries under sources are silently skipped."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {
            "sources": {
                "bad": "not-a-dict",
                "good": {"url": "http://read.example.com", "api_key": "k"},
            }
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        rd = resolver.sources
        assert "bad" not in rd
        assert "good" in rd

    def test_url_api_key_stripped(self) -> None:
        """Whitespace in url/api_key is stripped."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {
            "sources": {
                "default": {"url": "  http://read.example.com  ", "api_key": "  key  "},
            }
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        rd = resolver.sources
        assert rd["default"].url == "http://read.example.com"
        assert rd["default"].api_key == "key"

    # --- Case #9: legacy synthesis when BOTH scalars present ---

    def test_absent_key_both_scalars_synthesizes_default(self) -> None:
        """Case #9 unit: absent sources key + both scalars → synthesized default."""
        from context_intelligence.tool_resolver import Source, ToolConfigResolver

        config = {
            "context_intelligence_server_url": "http://legacy.example.com",
            "context_intelligence_api_key": "legacy-key",
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        rd = resolver.sources

        assert "default" in rd
        assert isinstance(rd["default"], Source)
        assert rd["default"].url == "http://legacy.example.com"
        assert rd["default"].api_key == "legacy-key"

    # --- Case #10: url-only → no synthesis ---

    def test_absent_key_url_only_no_synthesis(self) -> None:
        """Case #10 unit: absent sources key + url only → {} (no synthesis)."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {
            "context_intelligence_server_url": "http://legacy.example.com",
            # no context_intelligence_api_key
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        assert resolver.sources == {}

    def test_absent_key_no_scalars_returns_empty(self) -> None:
        """Absent key + no scalars → {} (neither synthesis nor parse)."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        resolver = ToolConfigResolver({}, _make_coordinator())
        assert resolver.sources == {}

    def test_sources_cached_after_first_access(self) -> None:
        """sources is cached — same object returned on second access."""
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {
            "sources": {
                "default": {"url": "http://read.example.com", "api_key": "k"},
            }
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        rd1 = resolver.sources
        rd2 = resolver.sources
        assert rd1 is rd2

    # --- env is excluded from legacy synthesis (tier 3 only) ---

    def test_absent_key_env_does_not_synthesize_into_tier1(self) -> None:
        """Env vars are excluded from legacy synthesis — env can never enter tier 1.

        Setting canonical env vars alone does NOT cause legacy synthesis to fire.
        Env is only consulted at tier 3 in resolve_query_connection(), which is
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
            rd = resolver.sources

        # Env must NOT synthesize into sources — result is empty dict.
        assert rd == {}


# ---------------------------------------------------------------------------
# TestSelectSource — §2.2 truth table (workstream-1-multi-source-query-tools.md)
# ---------------------------------------------------------------------------


def _src(name: str, url: str = "http://x.example.com", api_key: str = "k") -> Any:
    from context_intelligence.tool_resolver import Source

    return Source(name=name, url=url, api_key=api_key)


class TestSelectSource:
    """_select_source() — every row of the §2.2 truth table.

    | len(sources) | requested_name | Result |
    """

    # --- 0 sources ---

    def test_zero_sources_none_requested_returns_none(self) -> None:
        """0 | None -> None (unchanged: falls through to hook destination / env)."""
        from context_intelligence.tool_resolver import _select_source

        assert _select_source({}, None) is None

    def test_zero_sources_named_requested_raises_unknown_source(self) -> None:
        """0 | "foo" -> raises unknown_source, valid_names=[]."""
        from context_intelligence.tool_resolver import SourceSelectionError, _select_source

        with pytest.raises(SourceSelectionError) as excinfo:
            _select_source({}, "foo")
        assert excinfo.value.error_type == "unknown_source"
        assert excinfo.value.valid_names == []

    # --- 1 source ---

    def test_one_source_none_requested_returns_that_source(self) -> None:
        """1 | None -> that single source (unchanged behavior, now principled)."""
        from context_intelligence.tool_resolver import _select_source

        src = _src("default")
        result = _select_source({"default": src}, None)
        assert result is src

    def test_one_source_matching_name_returns_that_source(self) -> None:
        """1 | "default" (matches) -> that source."""
        from context_intelligence.tool_resolver import _select_source

        src = _src("default")
        assert _select_source({"default": src}, "default") is src

    def test_one_source_non_matching_name_raises_unknown_source(self) -> None:
        """1 | "bogus" (no match) -> raises unknown_source, valid_names=["default"]."""
        from context_intelligence.tool_resolver import SourceSelectionError, _select_source

        src = _src("default")
        with pytest.raises(SourceSelectionError) as excinfo:
            _select_source({"default": src}, "bogus")
        assert excinfo.value.error_type == "unknown_source"
        assert excinfo.value.valid_names == ["default"]

    # --- 2+ sources ---

    def test_two_plus_sources_none_requested_raises_ambiguous(self) -> None:
        """2+ | None -> raises ambiguous_source_selection, valid_names=[...].

        Unconditional: there is no implicit "use the first entry" fallback for any
        caller (the skill_sync.py carve-out that used to exist here is gone along
        with skill_sync.py itself).
        """
        from context_intelligence.tool_resolver import SourceSelectionError, _select_source

        sources = {"a": _src("a"), "b": _src("b")}
        with pytest.raises(SourceSelectionError) as excinfo:
            _select_source(sources, None)
        assert excinfo.value.error_type == "ambiguous_source_selection"
        assert excinfo.value.valid_names == ["a", "b"]

    def test_two_plus_sources_matching_name_returns_that_source(self) -> None:
        """2+ | "a" (matches) -> source "a"."""
        from context_intelligence.tool_resolver import _select_source

        src_a = _src("a")
        src_b = _src("b")
        sources = {"a": src_a, "b": src_b}
        assert _select_source(sources, "a") is src_a

    def test_two_plus_sources_non_matching_name_raises_unknown_source(self) -> None:
        """2+ | "z" (no match) -> raises unknown_source, valid_names=["a", "b", ...]."""
        from context_intelligence.tool_resolver import SourceSelectionError, _select_source

        sources = {"a": _src("a"), "b": _src("b")}
        with pytest.raises(SourceSelectionError) as excinfo:
            _select_source(sources, "z")
        assert excinfo.value.error_type == "unknown_source"
        assert excinfo.value.valid_names == ["a", "b"]

    def test_ambiguous_error_message_names_all_sources(self) -> None:
        from context_intelligence.tool_resolver import SourceSelectionError, _select_source

        sources = {"b": _src("b"), "a": _src("a")}
        with pytest.raises(SourceSelectionError) as excinfo:
            _select_source(sources, None)
        message = str(excinfo.value)
        assert "a" in message
        assert "b" in message
        assert "source=" in message


# ---------------------------------------------------------------------------
# TestValidateSourcePerEntry — criterion 4 (per-entry, not whole-map)
# ---------------------------------------------------------------------------


class TestValidateSourcePerEntry:
    """validate_sources() WARNS (never raises); validate_source(name) is fail-fast per-entry."""

    def _resolver_with_good_and_bad(self) -> Any:
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {
            "sources": {
                "good": {"url": "http://good.example.com", "api_key": "good-key"},
                "bad": {"url": "", "api_key": ""},  # missing url AND api_key
            }
        }
        return ToolConfigResolver(config, _make_coordinator())

    def test_validate_sources_does_not_raise_with_one_bad_entry(self) -> None:
        resolver = self._resolver_with_good_and_bad()
        # Must not raise
        problems = resolver.validate_sources()
        assert isinstance(problems, list)
        assert any("bad" in p for p in problems)
        assert not any(p.startswith("good:") for p in problems)

    def test_validate_sources_logs_warning(self, caplog: Any) -> None:
        import logging

        resolver = self._resolver_with_good_and_bad()
        with caplog.at_level(logging.WARNING, logger="context_intelligence.tool_resolver"):
            resolver.validate_sources()
        assert any("misconfigured" in r.message for r in caplog.records)

    def test_validate_source_good_returns_cleanly(self) -> None:
        resolver = self._resolver_with_good_and_bad()
        src = resolver.validate_source("good")
        assert src.name == "good"
        assert src.url == "http://good.example.com"

    def test_validate_source_bad_raises_naming_only_bad(self) -> None:
        resolver = self._resolver_with_good_and_bad()
        with pytest.raises(ValueError) as excinfo:
            resolver.validate_source("bad")
        message = str(excinfo.value)
        assert "bad" in message
        assert "good" not in message

    def test_validate_sources_all_good_returns_empty_list(self) -> None:
        from context_intelligence.tool_resolver import ToolConfigResolver

        config = {
            "sources": {
                "default": {"url": "http://good.example.com", "api_key": "k"},
            }
        }
        resolver = ToolConfigResolver(config, _make_coordinator())
        assert resolver.validate_sources() == []


# NOTE (v5): source_name threading, SourceSelectionError propagation (ambiguous /
# unknown / misconfigured), and this class's coverage all now live against
# resolve_query_connection() in modules/tool-context-intelligence-query/tests/
# test_pool_and_selection.py (p4-p9) -- alongside the module that owns its only
# callers. The old per-tier "criterion-7" DEBUG/INFO logging (noting untouched
# sibling sources) was diagnostic-only and was dropped as part of consolidating
# resolve_query_endpoint()/resolve_query_auth_strategy() into the single
# resolve_query_connection() (pure subtraction -- see spec §4.5's MJ callout).
