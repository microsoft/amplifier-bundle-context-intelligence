"""Tests verifying DOT diagram files in context/ reflect current architecture.

After the thin-forwarder refactoring, several graph-store DOT files were removed.
The remaining tests verify:
1. Historical stale files stay deleted.
2. Current architecture DOT files exist and contain expected content.
"""

from pathlib import Path

import pytest

CONTEXT_DIR = Path(__file__).parent.parent / "context"


@pytest.fixture(autouse=True, scope="session")
def _context_dir_exists():
    """Guard: fail fast if CONTEXT_DIR doesn't exist (e.g. file moved)."""
    assert CONTEXT_DIR.is_dir(), f"CONTEXT_DIR not found: {CONTEXT_DIR}"


# -- Historical stale files must stay deleted --------------------------------


def test_historical_cxdb_dot_file_is_deleted():
    """hook-event-discovery-and-dispatch.dot is a HISTORICAL file and must not exist.

    This file documented the old CXDB architecture and has been removed as part of
    the event registration refactoring. Its absence is enforced here so it cannot
    be accidentally re-introduced.
    """
    stale_file = CONTEXT_DIR / "hook-event-discovery-and-dispatch.dot"
    assert not stale_file.exists(), (
        f"Historical DOT file must be deleted: {stale_file}\n"
        "This file is labelled 'HISTORICAL REFERENCE — CXDB Architecture' "
        "and was removed in the event registration refactoring."
    )


def test_graph_store_lifecycle_dot_is_deleted():
    """graph-store-lifecycle.dot was removed in the thin-forwarder refactoring."""
    assert not (CONTEXT_DIR / "graph-store-lifecycle.dot").exists(), (
        "graph-store-lifecycle.dot belongs to the old graph-store architecture "
        "and must not exist in the thin-forwarder bundle."
    )


def test_read_path_dot_is_deleted():
    """read-path.dot was removed in the thin-forwarder refactoring."""
    assert not (CONTEXT_DIR / "read-path.dot").exists(), (
        "read-path.dot belongs to the old graph-store architecture "
        "and must not exist in the thin-forwarder bundle."
    )


def test_write_path_dot_is_deleted():
    """write-path.dot was removed in the thin-forwarder refactoring."""
    assert not (CONTEXT_DIR / "write-path.dot").exists(), (
        "write-path.dot belongs to the old graph-store architecture "
        "and must not exist in the thin-forwarder bundle."
    )


def test_orchestrator_run_assembly_dot_is_deleted():
    """orchestrator-run-assembly.dot was removed in the thin-forwarder refactoring."""
    assert not (CONTEXT_DIR / "orchestrator-run-assembly.dot").exists(), (
        "orchestrator-run-assembly.dot belongs to the old graph-store architecture "
        "and must not exist in the thin-forwarder bundle."
    )


def test_graph_store_protocol_md_is_deleted():
    """graph-store-protocol.md was removed in the thin-forwarder refactoring."""
    assert not (CONTEXT_DIR / "graph-store-protocol.md").exists(), (
        "graph-store-protocol.md belongs to the old graph-store architecture "
        "and must not exist in the thin-forwarder bundle."
    )


# -- Current architecture DOT files must exist --------------------------------


def test_session_disk_layout_dot_exists():
    """session-disk-layout.dot must exist — documents on-disk directory structure."""
    assert (CONTEXT_DIR / "session-disk-layout.dot").exists()


def test_config_resolution_dot_exists():
    """config-resolution.dot must exist — documents ConfigResolver fallback chains."""
    assert (CONTEXT_DIR / "config-resolution.dot").exists()
