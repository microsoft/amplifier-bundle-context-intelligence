"""Tests verifying graph schema updates to orchestrator-run-assembly.dot (Task 5.1).

These tests ensure:
1. HAS_EVENT edge comes from both OrchestratorRun (run-scoped) and Session (session-scoped).
2. The Event node description indicates run-scoped behavior.
3. The ToolExecution CREATE node mentions the tool_call_id disambiguator in node_id.
"""

from pathlib import Path

import pytest

CONTEXT_DIR = Path(__file__).parent.parent / "context"
DOT_FILE = CONTEXT_DIR / "orchestrator-run-assembly.dot"


@pytest.fixture(scope="module")
def dot_content():
    """Load orchestrator-run-assembly.dot content."""
    assert DOT_FILE.exists(), f"DOT file not found: {DOT_FILE}"
    return DOT_FILE.read_text()


# -- Change 1: HAS_EVENT edges from both OrchestratorRun and Session --


def test_has_event_edge_from_run_run_scoped(dot_content):
    """r_run must have a HAS_EVENT edge labelled 'run-scoped'."""
    assert "r_run -> r_event" in dot_content, (
        "Expected 'r_run -> r_event' edge for run-scoped HAS_EVENT"
    )
    assert "run-scoped" in dot_content, (
        "Expected 'run-scoped' label on HAS_EVENT edge from OrchestratorRun"
    )


def test_has_event_edge_from_session_session_scoped(dot_content):
    """r_session must still have a HAS_EVENT edge labelled 'session-scoped'."""
    assert "r_session -> r_event" in dot_content, (
        "Expected 'r_session -> r_event' edge for session-scoped HAS_EVENT"
    )
    assert "session-scoped" in dot_content, (
        "Expected 'session-scoped' label on HAS_EVENT edge from Session"
    )


# -- Change 2: Event node description indicates run-scoped behavior --


def test_event_node_label_mentions_run_scoped(dot_content):
    """r_event node label must describe 'run-scoped if active run exists' behaviour."""
    assert "run-scoped if" in dot_content, (
        "Expected 'run-scoped if' in r_event node label"
    )
    assert "active run exists" in dot_content, (
        "Expected 'active run exists' in r_event node label"
    )


# -- Change 3: ToolExecution CREATE node mentions tool_call_id disambiguator --


def test_tool_execution_create_mentions_tool_call_id_disambiguator(dot_content):
    """tp_create node must mention that node_id includes tool_call_id as disambiguator."""
    assert "node_id includes tool_call_id" in dot_content, (
        "Expected 'node_id includes tool_call_id' in tp_create node label"
    )
    assert "disambiguator" in dot_content, (
        "Expected 'disambiguator' in tp_create node label"
    )
