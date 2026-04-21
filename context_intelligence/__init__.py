"""context_intelligence — structured session data library.

This package organises functionality across three architectural levels:

Level 1 — Pure Transforms
    Stateless functions that convert raw data into structured representations.
    No I/O, no side effects. These can be tested in complete isolation.

Level 2 — Network I/O
    Functions and classes that communicate with the context-intelligence server
    (graph store, blob storage). Depend only on Level 1 transforms.

Level 3 — Filesystem + Orchestration
    Code that reads session files from disk, drives upload pipelines, and
    coordinates the other levels. Depends on Levels 1 and 2.
"""

from __future__ import annotations

from context_intelligence.client import AsyncCIClient, CIClient
from context_intelligence.config import (
    AMPLIFIER_DIR,
    LOG_SCHEMA,
    SETTINGS_PATH,
    resolve_config,
)
from context_intelligence.reconstruct import (
    build_disk_only_metadata,
    discover_sessions,
    extract_events,
    extract_metadata,
    extract_transcript,
    sessions_dir_for_project,
    workspace_slug,
)

__all__ = [
    "AsyncCIClient",
    "CIClient",
    "resolve_config",
    "LOG_SCHEMA",
    "AMPLIFIER_DIR",
    "SETTINGS_PATH",
    "extract_events",
    "extract_transcript",
    "extract_metadata",
    "build_disk_only_metadata",
    "discover_sessions",
    "workspace_slug",
    "sessions_dir_for_project",
]
