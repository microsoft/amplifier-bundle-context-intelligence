"""FOLD DISCIPLINE GATE — must run first (alphabetically sorted before all other test modules).

Proves the D1 contract fix is in place AND that the single-server path still works
end-to-end. Any fan-out test that fails while these pass has a fan-out bug, not a
refactor regression.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Test 1: static assertion — no ambient reads in config_resolver.py
# ---------------------------------------------------------------------------
def test_config_resolver_has_no_ambient_reads() -> None:
    """config_resolver.py must not read os.environ or settings.yaml (D1 contract fix).

    This is the hard gate for the fold discipline: the contract fix must pass
    before any fan-out test runs.
    """
    module_path = (
        Path(__file__).parent.parent
        / "amplifier_module_hook_context_intelligence"
        / "config_resolver.py"
    )
    source = module_path.read_text(encoding="utf-8")

    assert "os.environ" not in source, (
        "config_resolver.py must NOT read os.environ — the hook is a pure mount-config consumer"
    )
    assert "_parse_settings_yaml" not in source, (
        "config_resolver.py must NOT call _parse_settings_yaml — "
        "settings.yaml is read by the app layer, not the hook"
    )
    assert "SETTINGS_PATH" not in source, (
        "config_resolver.py must NOT reference SETTINGS_PATH — "
        "settings.yaml is read by the app layer, not the hook"
    )
    assert "import os\n" not in source and "\nimport os\n" not in source, (
        "config_resolver.py must NOT import os — the ambient read was removed"
    )


# ---------------------------------------------------------------------------
# Helpers shared across tests 2 & 3
# ---------------------------------------------------------------------------
def _make_coordinator(working_dir: str | None = "/tmp/x") -> MagicMock:
    coordinator = MagicMock()
    coordinator.config = {}
    capabilities: dict[str, Any] = {}

    def _register_capability(name: str, value: Any) -> None:
        capabilities[name] = value

    coordinator.register_capability = MagicMock(side_effect=_register_capability)

    def _get_capability(name: str) -> Any:
        if name == "session.working_dir":
            return working_dir
        return capabilities.get(name)

    coordinator.get_capability = MagicMock(side_effect=_get_capability)
    coordinator.hooks = MagicMock()
    coordinator.hooks.register = MagicMock(return_value=MagicMock())
    coordinator.collect_contributions = AsyncMock(return_value=[])
    coordinator._capabilities = capabilities
    return coordinator


# ---------------------------------------------------------------------------
# Test 2: pure-consumer round-trip — existing single-server still routes
# ---------------------------------------------------------------------------
async def test_pure_consumer_existing_single_server_still_routes() -> None:
    """Pure-mount-config single-server path is intact after D1 contract fix.

    Builds a ConfigResolver with only mount config (no env, no settings.yaml),
    confirms the synthesized 'default' destination, then runs mount() + on_session_ready()
    and asserts exactly one dispatcher is installed targeting the configured URL.
    """
    from amplifier_module_hook_context_intelligence.config_resolver import (
        ConfigResolver,
        Destination,
    )
    from amplifier_module_hook_context_intelligence import mount, on_session_ready

    config = {
        "context_intelligence_server_url": "http://h:8000",
        "context_intelligence_api_key": "k",
    }
    coordinator = _make_coordinator(working_dir="/tmp/x")

    # Resolver: destinations synthesized from legacy scalar
    resolver = ConfigResolver(config, coordinator)
    dests = resolver.destinations
    assert len(dests) == 1, f"Expected 1 destination, got {len(dests)}: {dests}"
    assert "default" in dests
    default_dest: Destination = dests["default"]
    assert default_dest.url == "http://h:8000"
    assert default_dest.api_key == "k"
    assert default_dest.include == ("**",)

    # Full mount + on_session_ready: should install exactly one dispatcher
    cleanup = await mount(coordinator, config=config)
    state = coordinator._capabilities.get("context_intelligence._hook_state")
    assert state is not None, "_hook_state must be registered"

    logging_handler = state["logging_handler"]
    assert logging_handler._dispatchers == [], "dispatchers should be empty before on_session_ready"

    await on_session_ready(coordinator)

    assert len(logging_handler._dispatchers) == 1, (
        f"Expected 1 dispatcher after on_session_ready, got {len(logging_handler._dispatchers)}"
    )
    dispatcher = logging_handler._dispatchers[0]
    assert dispatcher._url == "http://h:8000", f"Dispatcher URL mismatch: {dispatcher._url}"

    await cleanup()


# ---------------------------------------------------------------------------
# Test 3: empty config → local-only, no dispatchers
# ---------------------------------------------------------------------------
async def test_no_config_is_local_only(caplog: pytest.LogCaptureFixture) -> None:
    """Empty config → destinations == {} → zero dispatchers → INFO 'no destinations'."""
    import logging

    from amplifier_module_hook_context_intelligence.config_resolver import ConfigResolver
    from amplifier_module_hook_context_intelligence import mount, on_session_ready

    config: dict[str, Any] = {}
    coordinator = _make_coordinator(working_dir="/tmp/y")

    resolver = ConfigResolver(config, coordinator)
    assert resolver.destinations == {}, f"Expected empty destinations, got {resolver.destinations}"

    cleanup = await mount(coordinator, config=config)
    state = coordinator._capabilities["context_intelligence._hook_state"]
    logging_handler = state["logging_handler"]

    with caplog.at_level(logging.INFO, logger="amplifier_module_hook_context_intelligence"):
        await on_session_ready(coordinator)

    assert logging_handler._dispatchers == [], "No dispatchers should be installed for empty config"
    # INFO line about "no destinations" should appear
    assert any("no destinations" in r.message.lower() for r in caplog.records), (
        "Expected INFO message about no destinations configured"
    )

    await cleanup()
