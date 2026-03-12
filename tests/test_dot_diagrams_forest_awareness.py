"""Tests verifying forest-aware updates to DOT diagram files.

These tests ensure the four DOT diagrams in context/ include all required
forest awareness labels per the specification.
"""

from pathlib import Path

import pytest

CONTEXT_DIR = Path(__file__).parent.parent / "context"


@pytest.fixture(autouse=True, scope="session")
def _context_dir_exists():
    """Guard: fail fast if CONTEXT_DIR doesn't exist (e.g. file moved)."""
    assert CONTEXT_DIR.is_dir(), f"CONTEXT_DIR not found: {CONTEXT_DIR}"


# -- graph-store-lifecycle.dot ----------------------------------------------


@pytest.fixture
def lifecycle_dot():
    """Load graph-store-lifecycle.dot content."""
    return (CONTEXT_DIR / "graph-store-lifecycle.dot").read_text()


def test_lifecycle_opening_state_shows_forest_name_resolution(lifecycle_dot):
    """Opening state label must show forest name resolution steps."""
    assert "resolve" in lifecycle_dot
    assert "graph_forest_name" in lifecycle_dot
    # Full label content: Read config -> resolve graph_forest_name
    assert "Read config" in lifecycle_dot


def test_lifecycle_opening_state_has_create_connect_backend(lifecycle_dot):
    """Opening state must mention Create/connect backend."""
    assert "Create/connect backend" in lifecycle_dot


def test_lifecycle_opening_state_has_create_if_not_exists(lifecycle_dot):
    """Opening state must retain CREATE IF NOT EXISTS."""
    assert "CREATE IF NOT EXISTS" in lifecycle_dot


def test_lifecycle_mount_transition_reads_forest_name(lifecycle_dot):
    """unmounted -> opening transition must show factory reads graph_forest_name from config."""
    assert "factory reads" in lifecycle_dot
    assert "graph_forest_name" in lifecycle_dot
    assert "from config" in lifecycle_dot


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


# -- read-path.dot ---------------------------------------------------------


@pytest.fixture
def read_path_dot():
    """Load read-path.dot content."""
    return (CONTEXT_DIR / "read-path.dot").read_text()


def test_read_path_analysis_node_forest_scoped(read_path_dot):
    """Analysis node must include 'forest-scoped' in its label."""
    assert "forest-scoped" in read_path_dot


def test_read_path_analysis_node_retains_execute_query(read_path_dot):
    """Analysis node must retain execute_query() and Cypher."""
    assert "execute_query()" in read_path_dot
    assert "Cypher" in read_path_dot


def test_read_path_note_forest_node_exists(read_path_dot):
    """Must have a note_forest node."""
    assert "note_forest" in read_path_dot


def test_read_path_note_forest_none_own(read_path_dot):
    """note_forest must document None -> own forest."""
    assert "None" in read_path_dot
    assert "own" in read_path_dot.lower()


def test_read_path_note_forest_explicit(read_path_dot):
    """note_forest must document explicit -> that forest."""
    assert "explicit" in read_path_dot.lower()
    assert "that forest" in read_path_dot.lower()


def test_read_path_note_forest_star_cross(read_path_dot):
    """note_forest must document '*' -> cross-forest."""
    content = read_path_dot
    assert "'*'" in content or '"*"' in content or "`*`" in content
    assert "cross-forest" in content.lower()


def test_read_path_note_forest_invisible_edge(read_path_dot):
    """note_forest must be connected with an invisible edge."""
    lines = read_path_dot.splitlines()
    assert any("note_forest" in line and "invis" in line for line in lines)


# -- write-path.dot --------------------------------------------------------


@pytest.fixture
def write_path_dot():
    """Load write-path.dot content."""
    return (CONTEXT_DIR / "write-path.dot").read_text()


def test_write_path_neo4j_shows_forest_name(write_path_dot):
    """Neo4j node must mention graph_forest_name."""
    assert "graph_forest_name" in write_path_dot


def test_write_path_neo4j_shows_stamped_on_nodes_edges(write_path_dot):
    """Neo4j node must show 'stamped on all nodes/edges'."""
    assert "stamped on all nodes/edges" in write_path_dot
