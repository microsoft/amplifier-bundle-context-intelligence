"""Shared test fixtures for the context-intelligence hook module."""

from __future__ import annotations

import pytest

from amplifier_module_hook_context_intelligence.services import HookStateService


@pytest.fixture
def services() -> HookStateService:
    """A fresh HookStateService with empty config for testing."""
    return HookStateService(raw_config={})
