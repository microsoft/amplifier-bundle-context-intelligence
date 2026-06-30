# pyright: reportMissingImports=false
# (pytest / amplifier_core are runtime/CI deps not visible to the static checker here.)
"""End-to-end proof of the §C.3 base_path consistency warning (Restless-Old-Brian gate).

These tests drive the REAL ``on_session_ready`` against the REAL ``amplifier_core``
runtime (not the resolver in isolation) and assert the loud divergence warning
actually fires — closing the gap that the writer/consistency branches were only
ever verified by hand/parity.

NOTE: like the rest of this module's tests, these require ``amplifier_core`` to be
importable (it is in the Amplifier tool venv / CI, but NOT in the bundle's own
isolated ``.venv``). They are skipped automatically if the runtime is unavailable.
"""

from __future__ import annotations

import logging

import pytest

pytest.importorskip("amplifier_core", reason="amplifier_core runtime not installed in this venv")

from tests.helpers import make_lifecycle_coordinator, mount_and_ready  # noqa: E402

_ENV_VAR = "AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH"


def _messages(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


class TestConsistencyWarningEndToEnd:
    """Real on_session_ready, real runtime — watch the warning fire (or stay silent)."""

    async def test_warns_when_writer_relocated_but_env_unset(self, caplog, monkeypatch) -> None:
        """Scenario A — the trap: relocate via config.base_path, env var UNSET.

        Readers resolve the root only from the env var (-> default), the writer
        resolved /tmp/relocated-ci-A. on_session_ready MUST warn LOUD.
        """
        monkeypatch.delenv(_ENV_VAR, raising=False)
        coordinator = make_lifecycle_coordinator()
        with caplog.at_level(logging.WARNING):
            await mount_and_ready(coordinator, config={"base_path": "/tmp/relocated-ci-A"})
        msgs = _messages(caplog)
        assert "disagree" in msgs, f"expected a divergence warning; got:\n{msgs}"
        assert "/tmp/relocated-ci-A" in msgs

    async def test_silent_when_env_matches_writer(self, caplog, monkeypatch) -> None:
        """Scenario C (positive control) — env set to the same root the writer uses → NO warning."""
        monkeypatch.setenv(_ENV_VAR, "/tmp/relocated-ci-C")
        coordinator = make_lifecycle_coordinator()
        with caplog.at_level(logging.WARNING):
            await mount_and_ready(coordinator, config={"base_path": "/tmp/relocated-ci-C"})
        assert "disagree" not in _messages(caplog)

    async def test_unexpanded_placeholder_is_silent_default(self, caplog, monkeypatch) -> None:
        """Scenario B — host did NOT expand the ${VAR} binding, env unset.

        Writer falls back to default SILENTLY (no 'not absolute' noise) and, since
        readers also default, there is NO divergence warning either.
        """
        monkeypatch.delenv(_ENV_VAR, raising=False)
        coordinator = make_lifecycle_coordinator()
        with caplog.at_level(logging.WARNING):
            await mount_and_ready(
                coordinator,
                config={"base_path": "${AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH:}"},
            )
        msgs = _messages(caplog)
        assert "not absolute" not in msgs, f"unexpected noisy warning:\n{msgs}"
        assert "disagree" not in msgs, f"unexpected divergence warning:\n{msgs}"


class TestPositiveConfirmation:
    """Operator-visible (default INFO) confirmation of the active capture root."""

    async def test_confirmation_when_relocated_and_consistent(self, caplog, monkeypatch) -> None:
        """Relocation in effect + reader matches writer → INFO 'capturing to <root>' fires.

        log_level=INFO mirrors the behavior YAML default (``...:INFO``), i.e. the
        level a composed-bundle operator actually runs at.
        """
        monkeypatch.setenv(_ENV_VAR, "/tmp/relocated-ci-confirm")
        coordinator = make_lifecycle_coordinator()
        with caplog.at_level(logging.INFO):
            await mount_and_ready(
                coordinator,
                config={"base_path": "/tmp/relocated-ci-confirm", "log_level": "INFO"},
            )
        msgs = _messages(caplog)
        assert "capturing to" in msgs, f"expected positive confirmation; got:\n{msgs}"
        assert "/tmp/relocated-ci-confirm" in msgs

    async def test_no_confirmation_in_default_case(self, caplog, monkeypatch) -> None:
        """No relocation (default root) → stay silent even at INFO, no confirmation noise."""
        monkeypatch.delenv(_ENV_VAR, raising=False)
        coordinator = make_lifecycle_coordinator()
        with caplog.at_level(logging.INFO):
            await mount_and_ready(coordinator, config={"log_level": "INFO"})
        assert "capturing to" not in _messages(caplog)
