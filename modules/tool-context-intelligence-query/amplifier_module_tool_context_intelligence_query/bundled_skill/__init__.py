"""Vendored offline skill bodies for the analytics path.

DO NOT DELETE THE ``.md`` FILE(S) IN THIS PACKAGE. They are load-bearing.

Why this exists (the safe-default invariant)
---------------------------------------------
The bundle ships ``skills/context-intelligence-graph-query/SKILL.md`` as a
deliberately pessimistic **"Server Unavailable" stub** — it tells the
graph-analyst the graph is unreachable and to delegate to ``session-navigator``.
That stub is the *safe default*: a freshly installed bundle with **no** server
configured must never tell the agent "the graph is available" and invite Cypher
queries against a server that isn't there.

When skill sync is ENABLED (the default), ``skill_sync.on_session_ready``
overwrites that stub on session start with the real, full graph-query body
fetched from the live server (``GET /skills/context-intelligence-graph-query``).

When skill sync is DISABLED (``skill_sync_enabled: false`` — the per-turn
network opt-out for headless / single-command-series workflows) **and a server
URL is configured**, we still must not leave the agent holding the "Server
Unavailable" stub while the graph is actually usable. Instead we **swap** in the
vendored real body from this package — a local file copy, zero network. That is
the only reason this vendored body exists.

Provenance / how to refresh
---------------------------
``context-intelligence-graph-query.md`` is a byte-for-byte copy of the canonical
skill body served by the context-intelligence server, sourced from
``microsoft/amplifier-context-intelligence`` at
``context_intelligence_server/skills/context-intelligence-graph-query/SKILL.md``.
Its SHA-256 is pinned by ``EXPECTED_BUNDLED_SKILL_SHA256`` below and asserted by
``tests/test_bundled_skill.py`` (fail-loud: the test breaks if the file is
missing from the wheel or drifts). To refresh: copy the latest canonical
``SKILL.md`` over the vendored file, update the pinned hash, and re-run the
tests + the DTU 4-cell proof.

This package is the reincarnation of the ``legacy_content`` fallback that a
prior refactor deleted. It was re-introduced on purpose; a future "cleanup"
that deletes it will silently reintroduce the crippled-graph-analyst regression
issue #283 fixed. The DTU profile
``context-intelligence-skill-sync-disabled-behavioral-test.yaml`` and the unit
suite exist to make that deletion fail loud.
"""

from __future__ import annotations

#: SHA-256 of ``context-intelligence-graph-query.md`` — the vendored canonical
#: graph-query skill body (v2.0.0). Pinned so wheel-inclusion + drift is asserted
#: by tests rather than discovered in production.
EXPECTED_BUNDLED_SKILL_SHA256 = "d03a3f20df49b6ac05bdc92098e55edefaeae3a49c7457932703b9cceafa0533"

__all__ = ["EXPECTED_BUNDLED_SKILL_SHA256"]
