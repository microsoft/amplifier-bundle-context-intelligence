"""Tests for validate_destinations() per-destination degradation behavior (C3).

validate_destinations() never raises: a misconfigured destination is logged
(per-destination, at a severity reflecting how "typo-like" the problem is)
and dropped from the returned dict. mount() (__init__.py) calls this method
with no try/except -- a raise here would abort the ENTIRE hook, including
local JSONL capture, which has no dependency on any destination. See the
method's docstring in config_resolver.py for the full reasoning.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver


def _resolver(config: dict) -> ConfigResolver:
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get_capability = MagicMock(return_value=None)
    return ConfigResolver(config, coordinator)


class TestValidateDestinations:
    def test_missing_url_dropped_and_logged_as_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A destination with no url is dropped -- and logged at ERROR (typo-class:
        there is no environmental path to an empty url the way there is for an
        api_key that failed ${VAR} expansion)."""
        r = _resolver({"destinations": {"broken": {"url": "", "api_key": "k"}}})
        with caplog.at_level(logging.WARNING):
            result = r.validate_destinations()
        assert result == {}
        errors = [rec for rec in caplog.records if rec.levelno == logging.ERROR]
        assert any("broken" in rec.message and "missing url" in rec.message for rec in errors)

    def test_missing_api_key_dropped_and_logged_as_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A destination with an unusable api_key is dropped -- but only logged at
        WARNING, matching the legacy-path precedent: this commonly reflects an
        environment/secret-injection problem, not a hand-typed mistake."""
        r = _resolver({"destinations": {"broken": {"url": "http://x:8000", "api_key": ""}}})
        with caplog.at_level(logging.WARNING):
            result = r.validate_destinations()
        assert result == {}
        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert any(
            "broken" in rec.message and "api_key is unusable" in rec.message for rec in warnings
        )
        # The per-destination line for 'broken' itself must stay WARNING, not be
        # escalated to ERROR -- an api_key-only problem is not typo-class. (A
        # separate, distinct summary ERROR is expected here too, since this is
        # the ONLY configured destination and it was dropped -- see
        # test_all_destinations_dropped_emits_summary_error -- but that summary
        # line is not "broken"'s own per-destination line.)
        assert not any(
            rec.levelno == logging.ERROR and "misconfigured: api_key is unusable" in rec.message
            for rec in caplog.records
        )

    def test_redacted_sentinel_api_key_dropped_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A '[REDACTED]' api_key (e.g. mounted from a resumed session's persisted,
        redacted metadata.json) is treated the same as a missing key: dispatch
        disabled for that destination only, named in the log -- never a raise."""
        r = _resolver(
            {"destinations": {"broken": {"url": "http://x:8000", "api_key": "[REDACTED]"}}}
        )
        with caplog.at_level(logging.WARNING):
            result = r.validate_destinations()
        assert result == {}
        assert any(
            "broken" in rec.message and "api_key is unusable" in rec.message
            for rec in caplog.records
        )

    def test_unexpanded_placeholder_api_key_dropped_not_raised(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """An unexpanded ${VAR} api_key placeholder is treated as unusable -- dropped,
        not raised."""
        r = _resolver(
            {"destinations": {"broken": {"url": "http://x:8000", "api_key": "${SOME_VAR}"}}}
        )
        with caplog.at_level(logging.WARNING):
            result = r.validate_destinations()
        assert result == {}
        assert any(
            "broken" in rec.message and "api_key is unusable" in rec.message
            for rec in caplog.records
        )

    def test_both_missing_dropped_with_both_reasons_listed_at_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Both url and api_key bad on the same destination -> single dropped entry,
        both reasons named in its log line, severity escalated to ERROR because a
        typo-class problem (missing url) is present."""
        r = _resolver({"destinations": {"broken": {"url": "", "api_key": ""}}})
        with caplog.at_level(logging.WARNING):
            result = r.validate_destinations()
        assert result == {}
        matching = [rec for rec in caplog.records if "broken" in rec.message]
        assert matching, "expected a log record naming the destination"
        combined = matching[0].message
        assert "missing url" in combined
        assert "api_key is unusable" in combined
        assert matching[0].levelno == logging.ERROR

    def test_legacy_url_missing_api_key_degrades_to_local_only(self) -> None:
        """Legacy url WITHOUT api_key must NOT raise -- it degrades to local-only.

        Raising here would regress existing single-server setups from "dispatch
        disabled, local JSONL continues" to "mount fails". No destination is
        synthesized, so validate_destinations() returns {} (local-only).
        """
        r = _resolver(
            {
                "context_intelligence_server_url": "http://x:8000",
                # no api_key -> no synthesis -> local-only (NOT an error)
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

    def test_multiple_invalid_destinations_both_named_in_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        r = _resolver(
            {
                "destinations": {
                    "alpha": {"url": "", "api_key": "k"},
                    "beta": {"url": "http://b:8000", "api_key": ""},
                }
            }
        )
        with caplog.at_level(logging.WARNING):
            result = r.validate_destinations()
        assert result == {}
        messages = [rec.message for rec in caplog.records]
        assert any("alpha" in m for m in messages)
        assert any("beta" in m for m in messages)

    def test_all_destinations_dropped_emits_summary_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When every configured destination fails validation, an additional
        summary ERROR is emitted -- distinct from "no destinations configured
        at all", which is the ordinary silent local-only case."""
        r = _resolver(
            {
                "destinations": {
                    "alpha": {"url": "", "api_key": "k"},
                    "beta": {"url": "http://b:8000", "api_key": ""},
                }
            }
        )
        with caplog.at_level(logging.WARNING):
            r.validate_destinations()
        summary = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.ERROR and "all 2 configured destination" in rec.message
        ]
        assert summary, "expected a summary ERROR naming both destinations dropped"

    def test_one_bad_one_good_destination_good_one_survives(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """THE core fix under test: one misconfigured destination must not take
        down a sibling destination that IS correctly configured. Only the bad
        one is dropped; the good one is still returned (and no summary-ERROR
        fires, since not ALL destinations failed)."""
        r = _resolver(
            {
                "destinations": {
                    "broken": {"url": "http://b:8000", "api_key": ""},
                    "good": {"url": "http://g:8000", "api_key": "gk"},
                }
            }
        )
        with caplog.at_level(logging.WARNING):
            result = r.validate_destinations()
        assert set(result.keys()) == {"good"}
        assert not any("all 2 configured destination" in rec.message for rec in caplog.records), (
            "summary ERROR must not fire when at least one destination survives"
        )

    def test_unknown_auth_mode_dropped_and_logged_as_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        r = _resolver(
            {
                "destinations": {
                    "weird": {"url": "http://ci:8000", "auth_mode": "kerberos", "api_key": "k"},
                }
            }
        )
        with caplog.at_level(logging.WARNING):
            result = r.validate_destinations()
        assert result == {}
        errors = [rec for rec in caplog.records if rec.levelno == logging.ERROR]
        assert any("weird" in rec.message and "kerberos" in rec.message for rec in errors)
