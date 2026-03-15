"""Shared test fixtures for the context-intelligence hook module."""

from __future__ import annotations

# Neo4j fixtures, HookStateService, and reference graph helpers have been
# removed — all graph-creation code is now server-side.
# This file is intentionally minimal: individual test modules provide their
# own fixtures via local helpers or pytest tmp_path.
