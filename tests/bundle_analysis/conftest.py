"""Shared fixtures for bundle_analysis tests."""

from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


# ---------------------------------------------------------------------------
# CI client mock
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_ci_client() -> AsyncMock:
    """AsyncMock standing in for context_intelligence.client.AsyncCIClient.

    ``client.cypher`` is pre-configured as an ``AsyncMock`` returning ``[]``
    so callers can override the return value per-test without boilerplate.
    """
    client = AsyncMock()
    client.cypher = AsyncMock(return_value=[])
    return client


# ---------------------------------------------------------------------------
# Fake bundle cache
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_bundle_cache(tmp_path: Path) -> Path:
    """Return a tmp_path-backed directory shaped like ``~/.amplifier/cache/``.

    Layout
    ------
    cache_root/
        amplifier-foundation-abc123/
            bundle.md                  # bundle.name=foundation, version=0.1.0
            agents/
                explorer.md            # bundle.name=explorer, meta.name=explorer
                zen-architect.md       # bundle.name=zen-architect, meta.name=zen-architect
            modes/
                brainstorm.md          # mode.name=brainstorm, advertised=true
            behaviors/
                foundation.yaml        # bundle.name=foundation, agents.include=[foundation:explorer]
        amplifier-bundle-superpowers-def456/
            bundle.md                  # bundle.name=superpowers
            modes/
                brainstorm.md          # mode.name=brainstorm
    """
    cache_root = tmp_path / "cache"

    # ------------------------------------------------------------------
    # amplifier-foundation-abc123
    # ------------------------------------------------------------------
    foundation_dir = cache_root / "amplifier-foundation-abc123"
    foundation_dir.mkdir(parents=True)

    (foundation_dir / "bundle.md").write_text(
        textwrap.dedent("""\
            ---
            bundle:
              name: foundation
              version: 0.1.0
            ---
        """)
    )

    agents_dir = foundation_dir / "agents"
    agents_dir.mkdir()

    (agents_dir / "explorer.md").write_text(
        textwrap.dedent("""\
            ---
            bundle:
              name: explorer
            meta:
              name: explorer
            ---
        """)
    )

    (agents_dir / "zen-architect.md").write_text(
        textwrap.dedent("""\
            ---
            bundle:
              name: zen-architect
            meta:
              name: zen-architect
            ---
        """)
    )

    modes_dir = foundation_dir / "modes"
    modes_dir.mkdir()

    (modes_dir / "brainstorm.md").write_text(
        textwrap.dedent("""\
            ---
            mode:
              name: brainstorm
              advertised: true
            ---
        """)
    )

    behaviors_dir = foundation_dir / "behaviors"
    behaviors_dir.mkdir()

    (behaviors_dir / "foundation.yaml").write_text(
        textwrap.dedent("""\
            bundle:
              name: foundation
            agents:
              include:
                - foundation:explorer
        """)
    )

    # ------------------------------------------------------------------
    # amplifier-bundle-superpowers-def456  (minimal; no agents/ subdir)
    # ------------------------------------------------------------------
    superpowers_dir = cache_root / "amplifier-bundle-superpowers-def456"
    superpowers_dir.mkdir(parents=True)

    (superpowers_dir / "bundle.md").write_text(
        textwrap.dedent("""\
            ---
            bundle:
              name: superpowers
            ---
        """)
    )

    sup_modes_dir = superpowers_dir / "modes"
    sup_modes_dir.mkdir()

    (sup_modes_dir / "brainstorm.md").write_text(
        textwrap.dedent("""\
            ---
            mode:
              name: brainstorm
            ---
        """)
    )

    return cache_root


# ---------------------------------------------------------------------------
# Sample Cypher result rows
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_agent_rows() -> list[dict]:
    """Cypher result rows for S-1 (agent invocation query).

    Returns
    -------
    list[dict]
        ``[{"bundle": "foundation", "agent": "explorer", "invocations": 1}]``
    """
    return [{"bundle": "foundation", "agent": "explorer", "invocations": 1}]
