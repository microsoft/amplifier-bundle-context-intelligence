"""Shared pytest configuration and fixtures for tool-context-intelligence-upload tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
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


@pytest.fixture
def clean_environ() -> Iterator[None]:
    """Snapshot and restore os.environ around a test.

    keys_env.load_keys_env_into_environ writes directly to os.environ (it
    calls os.environ[key] = value), not through pytest's monkeypatch. Since
    monkeypatch only knows how to roll back changes it made itself, it cannot
    undo mutations performed by this module. Any test that exercises
    load_keys_env_into_environ must request this fixture instead (or in
    addition) so the real process environment is snapshotted before the test
    and restored afterward, regardless of what keys were added, changed, or
    removed.
    """
    saved = dict(os.environ)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)
