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


def test_delegation_strategy_dot_exists():
    """delegation-strategy.dot must exist — documents agent delegation chain."""
    assert (CONTEXT_DIR / "delegation-strategy.dot").exists()


def test_delegation_strategy_dot_has_correct_digraph_name():
    """delegation-strategy.dot must declare a digraph named delegation_strategy."""
    content = (CONTEXT_DIR / "delegation-strategy.dot").read_text()
    assert "digraph delegation_strategy" in content


def test_delegation_strategy_dot_has_rankdir_tb():
    """delegation-strategy.dot must use rankdir=TB layout."""
    content = (CONTEXT_DIR / "delegation-strategy.dot").read_text()
    assert "rankdir=TB" in content


def test_delegation_strategy_dot_has_helvetica_font():
    """delegation-strategy.dot must use Helvetica fonts."""
    content = (CONTEXT_DIR / "delegation-strategy.dot").read_text()
    assert "Helvetica" in content


def test_delegation_strategy_dot_has_all_box_nodes():
    """delegation-strategy.dot must define all 4 box nodes."""
    content = (CONTEXT_DIR / "delegation-strategy.dot").read_text()
    # caller node (gray)
    assert "caller" in content
    assert "#E8E8E8" in content
    # graph_analyst node (blue)
    assert "graph_analyst" in content
    assert "#4A90D9" in content
    # navigator node (green)
    assert "navigator" in content
    assert "#7AB648" in content
    # session_analyst node (orange, dashed)
    assert "session_analyst" in content
    assert "#F5A623" in content
    assert "dashed" in content


def test_delegation_strategy_dot_has_diamond_decision_nodes():
    """delegation-strategy.dot must define 2 diamond decision nodes."""
    content = (CONTEXT_DIR / "delegation-strategy.dot").read_text()
    assert "server_check" in content
    assert "local_check" in content
    assert "diamond" in content
    assert "#FAFAFA" in content


def test_delegation_strategy_dot_has_all_required_edges():
    """delegation-strategy.dot must define all required edges with labels."""
    content = (CONTEXT_DIR / "delegation-strategy.dot").read_text()
    # caller -> graph_analyst
    assert "caller" in content and "graph_analyst" in content
    assert "always delegate here first" in content
    # server_check -> navigator
    assert "server unreachable" in content or "no relevant data" in content
    # local_check -> session_analyst
    assert "resolve locally" in content or "can't resolve locally" in content


def test_delegation_strategy_dot_has_legend_subgraph():
    """delegation-strategy.dot must include a cluster_legend subgraph."""
    content = (CONTEXT_DIR / "delegation-strategy.dot").read_text()
    assert "cluster_legend" in content
    assert "Primary entry point" in content
    assert "Local fallback" in content
    assert "Final fallback" in content


def test_delegation_strategy_dot_size_approximately_1500_bytes():
    """delegation-strategy.dot should be approximately 1.5KB (≥ 1000 bytes)."""
    size = (CONTEXT_DIR / "delegation-strategy.dot").stat().st_size
    assert size >= 1000, f"File too small: {size} bytes (expected ~1500)"
