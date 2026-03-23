"""TDD test: cleanup() uses log.debug (not log.exception) when close() fails.

This test is written BEFORE the fix — it should FAIL with the current
log.exception() call and PASS after we change it to log.debug().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_cleanup_uses_debug_not_exception_when_close_fails():
    """When logging_handler.close() raises, cleanup() must call log.debug,
    NOT log.exception — the failure is not user-actionable."""
    from amplifier_module_hook_context_intelligence import mount

    coordinator = MagicMock()
    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(return_value=MagicMock())
    coordinator.collect_contributions = AsyncMock(return_value=[])
    coordinator.get_capability = MagicMock(return_value=None)
    coordinator.register_capability = MagicMock()

    cleanup = await mount(coordinator, config={})
    assert callable(cleanup), "mount() should return a cleanup callable"

    # Patch the logger used in the module
    with (
        patch("amplifier_module_hook_context_intelligence.log") as mock_log,
        patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.LoggingHandler.close",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated close failure"),
        ),
    ):
        await cleanup()

        # log.debug MUST be called with the cleanup message
        mock_log.debug.assert_called_once_with("LoggingHandler.close() failed during cleanup")
        # log.exception must NOT be called
        mock_log.exception.assert_not_called()
