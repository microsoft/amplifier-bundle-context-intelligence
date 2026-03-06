"""Shared test fixtures for the context-intelligence hook module."""

from __future__ import annotations

import pytest

from amplifier_module_hook_context_intelligence.services import HookStateService


@pytest.fixture
def services() -> HookStateService:
    """A fresh HookStateService wired to an in-memory DuckDB store.

    Uses explicit config so the factory never tries to import file_store
    during DuckDB-focused tests.
    """
    return HookStateService(
        raw_config={"graph_store": {"type": "duckdb", "config": {"connection": ":memory:"}}}
    )
