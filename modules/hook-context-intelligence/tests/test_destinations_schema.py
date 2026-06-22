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

    def test_missing_include_defaults_to_empty(self) -> None:
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
        assert d.include == (), "missing include must default to () — matches nothing"

    def test_empty_include_list_defaults_to_empty(self) -> None:
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
        assert d.include == (), "empty include list must default to () — matches nothing"

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

    def test_legacy_url_without_key_no_synthesis_local_only(self) -> None:
        """Legacy url WITHOUT api_key must NOT synthesize a destination.

        Pre-fan-out behavior for url-without-key was "dispatch disabled, local
        JSONL continues". Synthesizing Destination(api_key="") would make
        validate_destinations() raise -> mount() fail (a regression). So a url
        without a key degrades to local-only ({}), with a WARNING logged.
        """
        r = _resolver({"context_intelligence_server_url": "http://x:8000"})
        assert r.destinations == {}


# ---------------------------------------------------------------------------
# New-default fanout semantics: no-include means nothing, legacy means all
# ---------------------------------------------------------------------------


class TestNoIncludeMatchesNothing:
    """A destination without an explicit include must receive ZERO sessions (Change C)."""

    def test_no_include_destination_is_inactive(self) -> None:
        """Destination parsed without include → empty tuple → destination_is_active returns False."""
        from amplifier_module_hook_context_intelligence.fanout import destination_is_active

        r = _resolver(
            {
                "destinations": {
                    "server": {
                        "url": "http://server:8000",
                        "api_key": "sk",
                        # NO include key
                    }
                }
            }
        )
        dest = r.destinations["server"]
        assert dest.include == (), "no include must yield empty tuple"
        assert not destination_is_active(dest, "/home/user/any-project/"), (
            "destination without include must be inactive (matches nothing)"
        )
        assert not destination_is_active(dest, "/"), (
            "destination without include must be inactive even for root path"
        )

    def test_explicit_include_enables_destination(self) -> None:
        """Destination WITH an explicit include is active for matching paths."""
        from amplifier_module_hook_context_intelligence.fanout import destination_is_active

        r = _resolver(
            {
                "destinations": {
                    "server": {
                        "url": "http://server:8000",
                        "api_key": "sk",
                        "include": ["**"],
                    }
                }
            }
        )
        dest = r.destinations["server"]
        assert dest.include == ("**",)
        assert destination_is_active(dest, "/home/user/any-project/")


class TestLegacySingleServerReceivesAll:
    """Back-compat: legacy scalar url+key still synthesizes include=['**'] (Change C back-compat)."""

    def test_legacy_synthesized_destination_matches_all(self) -> None:
        """Legacy synthesis always sets include=('**',) so existing users receive everything."""
        from amplifier_module_hook_context_intelligence.fanout import destination_is_active

        r = _resolver(
            {
                "context_intelligence_server_url": "http://legacy:8000",
                "context_intelligence_api_key": "lk",
            }
        )
        dest = r.destinations["default"]
        assert dest.include == ("**",), (
            "legacy synthesis must explicitly set include=('**',) — back-compat invariant"
        )
        assert destination_is_active(dest, "/home/user/any-project/"), (
            "legacy synthesized destination must match all sessions"
        )
        assert destination_is_active(dest, "/tmp/scratch/"), (
            "legacy synthesized destination must match all paths"
        )
