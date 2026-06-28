"""Shared pytest configuration and fixtures for tool-context-intelligence-query tests."""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _reset_auth_singleton() -> Any:
    """Clear the auth module singleton and token cache before/after each test.

    Ensures the process-level _singleton_credential and _MODULE_CACHE do not
    leak between tests, so patches of _make_cli_credential are effective and
    cached tokens from one test don't pollute the next.
    """
    from context_intelligence import auth as _auth_mod

    _auth_mod.reset()
    yield
    _auth_mod.reset()
