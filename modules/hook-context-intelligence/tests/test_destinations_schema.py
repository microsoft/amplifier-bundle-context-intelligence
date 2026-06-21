"""Tests for Destination schema parsing and back-compat synthesis (D3/D10)."""

from __future__ import annotations

from unittest.mock import MagicMock

from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver, Destination


def _resolver(config: dict) -> ConfigResolver:
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get_capability = MagicMock(return_value=None)
    return ConfigResolver(config, coordinator)


class TestDestinationsParsing:
    """Dict-keyed destinations are parsed correctly."""

    def test_full_entry_parsed(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "team": {
                        "url": "http://team:8000",
                        "api_key": "tk",
                        "include": ["**/client-x/**"],
                        "exclude": ["**/private/**"],
                    }
                }
            }
        )
        dests = r.destinations
        assert "team" in dests
        d = dests["team"]
        assert d.url == "http://team:8000"
        assert d.api_key == "tk"
        assert d.include == ("**/client-x/**",)
        assert d.exclude == ("**/private/**",)

    def test_missing_include_defaults_to_catchall(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "personal": {
                        "url": "http://personal:8000",
                        "api_key": "pk",
                    }
                }
            }
        )
        d = r.destinations["personal"]
        assert d.include == ("**",), "missing include must default to ('**',)"

    def test_empty_include_list_defaults_to_catchall(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "personal": {
                        "url": "http://personal:8000",
                        "api_key": "pk",
                        "include": [],
                    }
                }
            }
        )
        d = r.destinations["personal"]
        assert d.include == ("**",), "empty include list must default to ('**',)"

    def test_missing_exclude_defaults_to_empty(self) -> None:
        r = _resolver(
            {"destinations": {"personal": {"url": "http://personal:8000", "api_key": "pk"}}}
        )
        d = r.destinations["personal"]
        assert d.exclude == (), "missing exclude must default to ()"

    def test_multiple_destinations_all_parsed(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "personal": {"url": "http://p:8000", "api_key": "pk"},
                    "team": {
                        "url": "http://t:8000",
                        "api_key": "tk",
                        "include": ["**/client/**"],
                    },
                }
            }
        )
        dests = r.destinations
        assert set(dests.keys()) == {"personal", "team"}

    def test_destination_name_preserved(self) -> None:
        r = _resolver(
            {"destinations": {"my-special-dest": {"url": "http://x:8000", "api_key": "xk"}}}
        )
        d = r.destinations["my-special-dest"]
        assert d.name == "my-special-dest"

    def test_returns_frozen_destinations(self) -> None:
        """Destination is a frozen dataclass — immutable and hashable."""
        r = _resolver({"destinations": {"default": {"url": "http://x:8000", "api_key": "xk"}}})
        d = r.destinations["default"]
        assert isinstance(d, Destination)

    def test_cached_after_first_access(self) -> None:
        r = _resolver({"destinations": {"default": {"url": "http://x:8000", "api_key": "xk"}}})
        first = r.destinations
        second = r.destinations
        assert first is second


class TestLegacySynthesis:
    """Legacy scalar keys synthesize a 'default' destination (D10)."""

    def test_legacy_url_synthesizes_default(self) -> None:
        r = _resolver(
            {
                "context_intelligence_server_url": "http://legacy:8000",
                "context_intelligence_api_key": "lk",
            }
        )
        dests = r.destinations
        assert "default" in dests
        d = dests["default"]
        assert d.url == "http://legacy:8000"
        assert d.api_key == "lk"
        assert d.include == ("**",)
        assert d.exclude == ()

    def test_legacy_synthesis_only_when_no_destinations_key(self) -> None:
        """When destinations dict is present (even empty), no legacy synthesis."""
        r = _resolver(
            {
                "destinations": {},
                "context_intelligence_server_url": "http://legacy:8000",
                "context_intelligence_api_key": "lk",
            }
        )
        # Explicit empty destinations: {} → {} (no synthesis)
        assert r.destinations == {}

    def test_explicit_empty_destinations_no_synthesis(self) -> None:
        r = _resolver({"destinations": {}})
        assert r.destinations == {}

    def test_no_url_no_synthesis(self) -> None:
        r = _resolver({})
        assert r.destinations == {}

    def test_legacy_missing_key_synthesizes_with_empty_key(self) -> None:
        """Legacy url without api_key synthesizes with empty api_key (C3 will catch it)."""
        r = _resolver({"context_intelligence_server_url": "http://x:8000"})
        dests = r.destinations
        assert "default" in dests
        # api_key may be empty/None; C3 (validate_destinations) will fail-fast
        assert dests["default"].url == "http://x:8000"
