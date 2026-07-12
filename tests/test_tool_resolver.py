"""Unit tests for context_intelligence.tool_resolver.

Tests the shared helpers (_first_entry, _first_destination, resolve_query_endpoint)
and ToolConfigResolver.sources (including legacy synthesis — spec §7 cases
#9 and #10 at the unit level).
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

    | len(sources) | requested_name | allow_implicit_default | Result |
    """

    # --- 0 sources ---

    def test_zero_sources_none_requested_returns_none(self) -> None:
        """0 | None | any -> None (unchanged: falls through to hook destination / env)."""
        from context_intelligence.tool_resolver import _select_source

        assert _select_source({}, None) is None
        assert _select_source({}, None, allow_implicit_default=True) is None

    def test_zero_sources_named_requested_raises_unknown_source(self) -> None:
        """0 | "foo" | any -> raises unknown_source, valid_names=[]."""
        from context_intelligence.tool_resolver import SourceSelectionError, _select_source

        with pytest.raises(SourceSelectionError) as excinfo:
            _select_source({}, "foo")
        assert excinfo.value.error_type == "unknown_source"
        assert excinfo.value.valid_names == []

    # --- 1 source ---

    def test_one_source_none_requested_returns_that_source(self) -> None:
        """1 | None | any -> that single source (unchanged behavior, now principled)."""
        from context_intelligence.tool_resolver import _select_source

        src = _src("default")
        result = _select_source({"default": src}, None)
        assert result is src
        # allow_implicit_default irrelevant with exactly one source
        assert _select_source({"default": src}, None, allow_implicit_default=True) is src

    def test_one_source_matching_name_returns_that_source(self) -> None:
        """1 | "default" (matches) | any -> that source."""
        from context_intelligence.tool_resolver import _select_source

        src = _src("default")
        assert _select_source({"default": src}, "default") is src

    def test_one_source_non_matching_name_raises_unknown_source(self) -> None:
        """1 | "bogus" (no match) | any -> raises unknown_source, valid_names=["default"]."""
        from context_intelligence.tool_resolver import SourceSelectionError, _select_source

        src = _src("default")
        with pytest.raises(SourceSelectionError) as excinfo:
            _select_source({"default": src}, "bogus")
        assert excinfo.value.error_type == "unknown_source"
        assert excinfo.value.valid_names == ["default"]

    # --- 2+ sources ---

    def test_two_plus_sources_none_requested_allow_implicit_false_raises_ambiguous(
        self,
    ) -> None:
        """2+ | None | False (tools) -> raises ambiguous_source_selection, valid_names=[...]."""
        from context_intelligence.tool_resolver import SourceSelectionError, _select_source

        sources = {"a": _src("a"), "b": _src("b")}
        with pytest.raises(SourceSelectionError) as excinfo:
            _select_source(sources, None, allow_implicit_default=False)
        assert excinfo.value.error_type == "ambiguous_source_selection"
        assert excinfo.value.valid_names == ["a", "b"]

    def test_two_plus_sources_none_requested_allow_implicit_true_returns_first(
        self,
    ) -> None:
        """2+ | None | True (skill_sync only) -> first by insertion order, logged DEBUG."""
        from context_intelligence.tool_resolver import _select_source

        src_a = _src("a")
        src_b = _src("b")
        sources = {"a": src_a, "b": src_b}
        result = _select_source(sources, None, allow_implicit_default=True)
        assert result is src_a

    def test_two_plus_sources_matching_name_returns_that_source(self) -> None:
        """2+ | "a" (matches) | any -> source "a"."""
        from context_intelligence.tool_resolver import _select_source

        src_a = _src("a")
        src_b = _src("b")
        sources = {"a": src_a, "b": src_b}
        assert _select_source(sources, "a") is src_a
        assert _select_source(sources, "a", allow_implicit_default=True) is src_a

    def test_two_plus_sources_non_matching_name_raises_unknown_source(self) -> None:
        """2+ | "z" (no match) | any -> raises unknown_source, valid_names=["a", "b", ...]."""
        from context_intelligence.tool_resolver import SourceSelectionError, _select_source

        sources = {"a": _src("a"), "b": _src("b")}
        with pytest.raises(SourceSelectionError) as excinfo:
            _select_source(sources, "z")
        assert excinfo.value.error_type == "unknown_source"
        assert excinfo.value.valid_names == ["a", "b"]

    def test_explicit_request_never_falls_back_even_when_ambiguous_default_would_apply(
        self,
    ) -> None:
        """An explicit unknown name must raise even if allow_implicit_default=True."""
        from context_intelligence.tool_resolver import SourceSelectionError, _select_source

        sources = {"a": _src("a"), "b": _src("b")}
        with pytest.raises(SourceSelectionError) as excinfo:
            _select_source(sources, "z", allow_implicit_default=True)
        assert excinfo.value.error_type == "unknown_source"

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


# ---------------------------------------------------------------------------
# TestResolveQueryEndpointSourceName — source_name threading + criterion-7 log
# ---------------------------------------------------------------------------


class TestResolveQueryEndpointSourceName:
    """resolve_query_endpoint() — source_name threading, SourceSelectionError propagation,
    and the criterion-7 log line firing only when len(sources) >= 2."""

    def _resolver(self, config: dict) -> Any:
        from context_intelligence.tool_resolver import ToolConfigResolver

        return ToolConfigResolver(config, _make_coordinator())

    def test_source_name_selects_named_entry(self) -> None:
        from context_intelligence.tool_resolver import resolve_query_endpoint

        config = {
            "sources": {
                "a": {"url": "http://a.example.com", "api_key": "a-key"},
                "b": {"url": "http://b.example.com", "api_key": "b-key"},
            }
        }
        resolver = self._resolver(config)
        url, api_key = resolve_query_endpoint(None, resolver, source_name="b")
        assert url == "http://b.example.com"
        assert api_key == "b-key"

    def test_ambiguous_selection_raises_source_selection_error(self) -> None:
        from context_intelligence.tool_resolver import (
            SourceSelectionError,
            resolve_query_endpoint,
        )

        config = {
            "sources": {
                "a": {"url": "http://a.example.com", "api_key": "a-key"},
                "b": {"url": "http://b.example.com", "api_key": "b-key"},
            }
        }
        resolver = self._resolver(config)
        with pytest.raises(SourceSelectionError) as excinfo:
            resolve_query_endpoint(None, resolver)
        assert excinfo.value.error_type == "ambiguous_source_selection"
        assert excinfo.value.valid_names == ["a", "b"]

    def test_allow_implicit_default_true_avoids_ambiguity_error(self) -> None:
        from context_intelligence.tool_resolver import resolve_query_endpoint

        config = {
            "sources": {
                "a": {"url": "http://a.example.com", "api_key": "a-key"},
                "b": {"url": "http://b.example.com", "api_key": "b-key"},
            }
        }
        resolver = self._resolver(config)
        url, api_key = resolve_query_endpoint(None, resolver, allow_implicit_default=True)
        assert url == "http://a.example.com"
        assert api_key == "a-key"

    def test_unknown_source_name_raises_source_selection_error(self) -> None:
        from context_intelligence.tool_resolver import (
            SourceSelectionError,
            resolve_query_endpoint,
        )

        config = {
            "sources": {
                "a": {"url": "http://a.example.com", "api_key": "a-key"},
            }
        }
        resolver = self._resolver(config)
        with pytest.raises(SourceSelectionError) as excinfo:
            resolve_query_endpoint(None, resolver, source_name="nope")
        assert excinfo.value.error_type == "unknown_source"
        assert excinfo.value.valid_names == ["a"]

    def test_selected_source_misconfigured_raises_plain_value_error(self) -> None:
        """The selected source itself fails per-field validation -> plain ValueError."""
        from context_intelligence.tool_resolver import resolve_query_endpoint

        config = {
            "sources": {
                "bad": {"url": "", "api_key": ""},
            }
        }
        resolver = self._resolver(config)
        with pytest.raises(ValueError) as excinfo:
            resolve_query_endpoint(None, resolver, source_name="bad")
        # Must NOT be a SourceSelectionError (that's for selection ambiguity, not
        # misconfiguration) -- assert it lacks the SourceSelectionError-only attrs.
        assert not hasattr(excinfo.value, "error_type")
        assert "bad" in str(excinfo.value)

    def test_criterion7_log_line_fires_with_two_plus_sources(self, caplog: Any) -> None:
        import logging

        from context_intelligence.tool_resolver import resolve_query_endpoint

        config = {
            "sources": {
                "a": {"url": "http://a.example.com", "api_key": "a-key"},
                "b": {"url": "http://b.example.com", "api_key": "b-key"},
            }
        }
        resolver = self._resolver(config)
        with caplog.at_level(logging.INFO, logger="context_intelligence.tool_resolver"):
            resolve_query_endpoint(None, resolver, source_name="a")

        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert any("other configured source" in r.message for r in info_records)
        assert any("b" in r.message for r in info_records)

    def test_criterion7_log_line_does_not_fire_with_single_source(self, caplog: Any) -> None:
        import logging

        from context_intelligence.tool_resolver import resolve_query_endpoint

        config = {
            "sources": {
                "default": {"url": "http://only.example.com", "api_key": "k"},
            }
        }
        resolver = self._resolver(config)
        with caplog.at_level(logging.INFO, logger="context_intelligence.tool_resolver"):
            resolve_query_endpoint(None, resolver)

        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert not any("other configured source" in r.message for r in info_records)

    def test_criterion7_log_line_does_not_fire_when_falling_through_to_tier2(
        self, caplog: Any
    ) -> None:
        """No sources configured at all -> read is None -> criterion-7 log must not fire."""
        import logging

        from context_intelligence.tool_resolver import resolve_query_endpoint

        resolver = self._resolver({})
        with caplog.at_level(logging.INFO, logger="context_intelligence.tool_resolver"):
            resolve_query_endpoint(None, resolver)

        info_records = [r for r in caplog.records if r.levelno == logging.INFO]
        assert not any("other configured source" in r.message for r in info_records)
