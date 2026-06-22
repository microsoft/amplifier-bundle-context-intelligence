"""Tests for validate_destinations() fail-fast behavior (C3)."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver


def _resolver(config: dict) -> ConfigResolver:
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get_capability = MagicMock(return_value=None)
    return ConfigResolver(config, coordinator)


class TestValidateDestinations:
    def test_missing_url_raises_value_error(self) -> None:
        r = _resolver({"destinations": {"broken": {"url": "", "api_key": "k"}}})
        with pytest.raises(ValueError, match="broken.*missing url"):
            r.validate_destinations()

    def test_missing_api_key_raises_value_error(self) -> None:
        r = _resolver({"destinations": {"broken": {"url": "http://x:8000", "api_key": ""}}})
        with pytest.raises(ValueError, match="broken.*missing api_key"):
            r.validate_destinations()

    def test_both_missing_raises_with_both_listed(self) -> None:
        r = _resolver({"destinations": {"broken": {"url": "", "api_key": ""}}})
        with pytest.raises(ValueError) as exc_info:
            r.validate_destinations()
        msg = str(exc_info.value)
        assert "missing url" in msg
        assert "missing api_key" in msg

    def test_legacy_url_missing_api_key_degrades_to_local_only(self) -> None:
        """Legacy url WITHOUT api_key must NOT raise — it degrades to local-only.

        Raising here would regress existing single-server setups from "dispatch
        disabled, local JSONL continues" to "mount fails". No destination is
        synthesized, so validate_destinations() returns {} (local-only).
        """
        r = _resolver(
            {
                "context_intelligence_server_url": "http://x:8000",
                # no api_key → no synthesis → local-only (NOT an error)
            }
        )
        assert r.validate_destinations() == {}

    def test_valid_destinations_returns_dict(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "team": {"url": "http://t:8000", "api_key": "tk"},
                    "personal": {"url": "http://p:8000", "api_key": "pk"},
                }
            }
        )
        result = r.validate_destinations()
        assert set(result.keys()) == {"team", "personal"}

    def test_empty_destinations_returns_empty_no_raise(self) -> None:
        r = _resolver({})
        result = r.validate_destinations()
        assert result == {}

    def test_explicit_empty_destinations_returns_empty_no_raise(self) -> None:
        r = _resolver({"destinations": {}})
        result = r.validate_destinations()
        assert result == {}

    def test_multiple_invalid_destinations_all_named(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "alpha": {"url": "", "api_key": "k"},
                    "beta": {"url": "http://b:8000", "api_key": ""},
                }
            }
        )
        with pytest.raises(ValueError) as exc_info:
            r.validate_destinations()
        msg = str(exc_info.value)
        assert "alpha" in msg
        assert "beta" in msg
