"""Tests for context_intelligence.reconstruct.discover (task-8).

Covers:
- Module imports correctly (workspace_slug, sessions_dir_for_project, discover_sessions,
  DiskScanResult)
- workspace_slug() converts absolute path to slug (replacing / with -).
- sessions_dir_for_project() returns DEFAULT_BASE_PATH/slug/'sessions'.
- discover_sessions() queries graph for sessions in workspace.
- discover_sessions() returns graph rows and a DiskScanResult.
- discover_sessions() uses the events.jsonl marker as the sole capture discriminator.
- discover_sessions() INCLUDES subsession directories (0000…) — §D.1 subsession inclusion.
- discover_sessions() returns root_exists=False for absent roots — §D.3 absent-root guard.
- Imports: CIClient from client, AMPLIFIER_DIR from config.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_capture(sessions_dir: Path, session_id: str) -> None:
    """Create the canonical capture structure for *session_id* under *sessions_dir*.

    A qualifying capture requires:
        ``sessions_dir/<session_id>/context-intelligence/events.jsonl``
    """
    cap_dir = sessions_dir / session_id / "context-intelligence"
    cap_dir.mkdir(parents=True, exist_ok=True)
    (cap_dir / "events.jsonl").write_text("")


class TestImport:
    """Module must be importable with the required public API."""

    def test_workspace_slug_import(self):
        """workspace_slug must be importable from context_intelligence.reconstruct.discover."""
        from context_intelligence.reconstruct.discover import workspace_slug  # noqa: F401

    def test_sessions_dir_for_project_import(self):
        """sessions_dir_for_project must be importable from context_intelligence.reconstruct.discover."""
        from context_intelligence.reconstruct.discover import sessions_dir_for_project  # noqa: F401

    def test_discover_sessions_import(self):
        """discover_sessions must be importable from context_intelligence.reconstruct.discover."""
        from context_intelligence.reconstruct.discover import discover_sessions  # noqa: F401

    def test_disk_scan_result_import(self):
        """DiskScanResult must be importable from context_intelligence.reconstruct.discover."""
        from context_intelligence.reconstruct.discover import DiskScanResult  # noqa: F401

    def test_acceptance_criteria_command(self):
        """Simulate the acceptance criteria import command."""
        from context_intelligence.reconstruct.discover import (
            DiskScanResult,
            discover_sessions,
            sessions_dir_for_project,
            workspace_slug,
        )

        assert workspace_slug is not None
        assert sessions_dir_for_project is not None
        assert discover_sessions is not None
        assert DiskScanResult is not None

    def test_uses_ciclient_from_client(self):
        """CIClient must be importable from the client module (imported dependency)."""
        from context_intelligence.client import CIClient  # noqa: F401

    def test_uses_amplifier_dir_from_config(self):
        """AMPLIFIER_DIR must be importable from the config module (imported dependency)."""
        from context_intelligence.config import AMPLIFIER_DIR  # noqa: F401


class TestWorkspaceSlug:
    """workspace_slug() converts absolute path to slug by replacing / with -."""

    def test_converts_slashes_to_dashes(self):
        """An absolute path has each / replaced with -."""
        from context_intelligence.reconstruct.discover import workspace_slug

        result = workspace_slug("/home/bkrabach/dev/attractor-dev-machine")
        assert result == "-home-bkrabach-dev-attractor-dev-machine"

    def test_root_path(self):
        """Root path '/' becomes just '-'."""
        from context_intelligence.reconstruct.discover import workspace_slug

        result = workspace_slug("/")
        assert result == "-"

    def test_single_component_path(self):
        """A single-component path like /home becomes -home."""
        from context_intelligence.reconstruct.discover import workspace_slug

        result = workspace_slug("/home")
        assert result == "-home"

    def test_deep_path(self):
        """A deep path replaces all slashes."""
        from context_intelligence.reconstruct.discover import workspace_slug

        result = workspace_slug("/a/b/c/d")
        assert result == "-a-b-c-d"

    def test_returns_string(self):
        """workspace_slug returns a str."""
        from context_intelligence.reconstruct.discover import workspace_slug

        result = workspace_slug("/some/path")
        assert isinstance(result, str)


class TestSessionsDirForProject:
    """sessions_dir_for_project() returns AMPLIFIER_DIR/'projects'/slug/'sessions'."""

    def test_returns_expected_path(self):
        """Returns AMPLIFIER_DIR / 'projects' / slug / 'sessions'."""
        from context_intelligence.config import AMPLIFIER_DIR
        from context_intelligence.reconstruct.discover import (
            sessions_dir_for_project,
            workspace_slug,
        )

        project_dir = "/home/bkrabach/dev/myproject"
        slug = workspace_slug(project_dir)
        expected = AMPLIFIER_DIR / "projects" / slug / "sessions"
        result = sessions_dir_for_project(project_dir)
        assert result == expected

    def test_returns_path_object(self):
        """sessions_dir_for_project returns a Path object."""
        from context_intelligence.reconstruct.discover import sessions_dir_for_project

        result = sessions_dir_for_project("/some/project")
        assert isinstance(result, Path)

    def test_ends_with_sessions(self):
        """The returned path ends with a 'sessions' component."""
        from context_intelligence.reconstruct.discover import sessions_dir_for_project

        result = sessions_dir_for_project("/some/project")
        assert result.name == "sessions"

    def test_slug_incorporated(self):
        """The slug derived from the project path appears in the result."""
        from context_intelligence.reconstruct.discover import (
            sessions_dir_for_project,
            workspace_slug,
        )

        project_dir = "/home/user/myrepo"
        slug = workspace_slug(project_dir)
        result = sessions_dir_for_project(project_dir)
        # The slug should be a component of the path
        assert slug in result.parts


class TestDiscoverSessions:
    """discover_sessions() queries graph and scans disk for sessions."""

    def _make_client(self, rows=None):
        """Create a mock CIClient that returns the given rows from cypher()."""
        client = MagicMock()
        client.cypher.return_value = rows if rows is not None else []
        return client

    def test_returns_tuple(self):
        """discover_sessions returns a 2-tuple."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            result = discover_sessions(client, "test-workspace", Path(tmpdir))
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_second_element_is_disk_scan_result(self):
        """Second element is a DiskScanResult, not a bare list."""
        from context_intelligence.reconstruct.discover import DiskScanResult, discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            _, scan = discover_sessions(client, "test-workspace", Path(tmpdir))
        assert isinstance(scan, DiskScanResult)

    def test_returns_graph_rows(self):
        """First element is the list of rows returned by client.cypher()."""
        from context_intelligence.reconstruct.discover import discover_sessions

        fake_rows = [
            {
                "s.node_id": "abc123",
                "s.status": "completed",
                "s.started_at": "2024-01-01T00:00:00Z",
                "s.ended_at": "2024-01-01T01:00:00Z",
            },
        ]
        client = self._make_client(fake_rows)
        with tempfile.TemporaryDirectory() as tmpdir:
            graph_sessions, scan = discover_sessions(client, "test-workspace", Path(tmpdir))
        assert graph_sessions == fake_rows

    def test_calls_cypher_with_workspace(self):
        """client.cypher() is called with the workspace parameter."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        workspace = "my-workspace"
        with tempfile.TemporaryDirectory() as tmpdir:
            discover_sessions(client, workspace, Path(tmpdir))
        assert client.cypher.called
        call_kwargs = client.cypher.call_args
        # workspace should appear as kwarg or positional
        assert workspace in str(call_kwargs)

    def test_cypher_query_contains_session_fields(self):
        """The cypher query requests s.node_id, s.status, s.started_at, s.ended_at."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            discover_sessions(client, "test-workspace", Path(tmpdir))
        query_arg = client.cypher.call_args[0][0]
        assert "s.node_id" in query_arg
        assert "s.status" in query_arg
        assert "s.started_at" in query_arg
        assert "s.ended_at" in query_arg

    def test_cypher_query_ordered_by_started_at(self):
        """The cypher query includes ORDER BY s.started_at."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            discover_sessions(client, "test-workspace", Path(tmpdir))
        query_arg = client.cypher.call_args[0][0]
        assert "ORDER BY s.started_at" in query_arg

    def test_disk_only_includes_dirs_not_in_graph(self):
        """Directories with events.jsonl on disk but not in graph_ids appear in disk_only_ids."""
        from context_intelligence.reconstruct.discover import discover_sessions

        fake_rows = [
            {
                "s.node_id": "graph-session-1",
                "s.status": "completed",
                "s.started_at": "2024-01-01",
                "s.ended_at": "2024-01-02",
            },
        ]
        client = self._make_client(fake_rows)
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            # Create a capture that is on disk but not in graph
            _make_capture(sessions_dir, "disk-only-session")
            # Create a capture that is also in graph
            _make_capture(sessions_dir, "graph-session-1")

            _, scan = discover_sessions(client, "test-workspace", sessions_dir)

        assert "disk-only-session" in scan.disk_only_ids
        assert "graph-session-1" not in scan.disk_only_ids

    def test_candidate_ids_includes_graph_sessions(self):
        """candidate_ids is the full set regardless of graph membership."""
        from context_intelligence.reconstruct.discover import discover_sessions

        fake_rows = [
            {
                "s.node_id": "graph-sess",
                "s.status": "completed",
                "s.started_at": "2024-01-01",
                "s.ended_at": "2024-01-02",
            },
        ]
        client = self._make_client(fake_rows)
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            _make_capture(sessions_dir, "disk-only-session")
            _make_capture(sessions_dir, "graph-sess")

            _, scan = discover_sessions(client, "test-workspace", sessions_dir)

        assert "disk-only-session" in scan.candidate_ids
        assert "graph-sess" in scan.candidate_ids
        assert len(scan.candidate_ids) == 2
        assert len(scan.disk_only_ids) == 1

    def test_disk_only_INCLUDES_subsession_dirs(self):
        """Subsession directories (0000… prefix) ARE included — §D.1 subsession inclusion.

        The old 0000… skip silently dropped real captures; this test pins the
        corrected behaviour: subsessions have captures and must be counted.
        """
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            # Subsession capture — MUST be included
            _make_capture(sessions_dir, "0000000000000000-abc123_some-agent")
            # Normal root-session capture — also included
            _make_capture(sessions_dir, "real-session-abc")

            _, scan = discover_sessions(client, "test-workspace", sessions_dir)

        assert "0000000000000000-abc123_some-agent" in scan.disk_only_ids
        assert "real-session-abc" in scan.disk_only_ids

    def test_bare_dir_without_events_jsonl_excluded(self):
        """A directory without context-intelligence/events.jsonl is NOT a capture."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            # Bare dir — no events.jsonl
            (sessions_dir / "bare-dir").mkdir()
            # Capture with correct marker
            _make_capture(sessions_dir, "real-capture")

            _, scan = discover_sessions(client, "test-workspace", sessions_dir)

        assert "bare-dir" not in scan.disk_only_ids
        assert "bare-dir" not in scan.candidate_ids
        assert "real-capture" in scan.disk_only_ids

    def test_amplifier_core_bait_dir_excluded(self):
        """Amplifier-core sessions/<id>/metadata.json (no context-intelligence/) are excluded."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            # Amplifier-core bait: has metadata.json but no context-intelligence/events.jsonl
            bait_dir = sessions_dir / "core-session-bait"
            bait_dir.mkdir()
            (bait_dir / "metadata.json").write_text("{}")
            # Capture with correct marker
            _make_capture(sessions_dir, "real-capture")

            _, scan = discover_sessions(client, "test-workspace", sessions_dir)

        assert "core-session-bait" not in scan.candidate_ids
        assert "real-capture" in scan.candidate_ids

    def test_absent_root_returns_root_exists_false(self):
        """When sessions_dir does not exist, root_exists is False — §D.3 absent-root guard."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        nonexistent = Path("/tmp/this-path-should-not-exist-8675309")
        _, scan = discover_sessions(client, "test-workspace", nonexistent)

        assert scan.root_exists is False
        assert scan.disk_only_ids == []
        assert scan.candidate_ids == []

    def test_absent_root_is_distinct_from_found_zero(self):
        """root_exists=False is distinct from root_exists=True with no captures."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])

        # Absent root
        nonexistent = Path("/tmp/this-path-should-not-exist-8675309")
        _, absent_scan = discover_sessions(client, "test-workspace", nonexistent)

        # Existing root but no captures
        with tempfile.TemporaryDirectory() as tmpdir:
            _, empty_scan = discover_sessions(client, "test-workspace", Path(tmpdir))

        assert absent_scan.root_exists is False
        assert empty_scan.root_exists is True
        assert empty_scan.disk_only_ids == []

    def test_found_zero_root_exists_true(self):
        """An existing root with no captures: root_exists=True and empty lists."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            _, scan = discover_sessions(client, "test-workspace", Path(tmpdir))

        assert scan.root_exists is True
        assert scan.disk_only_ids == []
        assert scan.candidate_ids == []

    def test_disk_only_is_list_of_strings(self):
        """disk_only_ids is a list of strings (session IDs)."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            _make_capture(sessions_dir, "some-session")
            _, scan = discover_sessions(client, "test-workspace", sessions_dir)

        assert isinstance(scan.disk_only_ids, list)
        assert all(isinstance(s, str) for s in scan.disk_only_ids)

    def test_empty_sessions_dir(self):
        """An empty sessions_dir returns empty lists (root_exists=True)."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            _, scan = discover_sessions(client, "test-workspace", Path(tmpdir))
        assert scan.root_exists is True
        assert scan.disk_only_ids == []
        assert scan.candidate_ids == []

    def test_files_in_sessions_dir_not_included(self):
        """Regular files (not directories) in sessions_dir are not included."""
        from context_intelligence.reconstruct.discover import discover_sessions

        client = self._make_client([])
        with tempfile.TemporaryDirectory() as tmpdir:
            sessions_dir = Path(tmpdir)
            (sessions_dir / "not-a-session.txt").write_text("hello")
            _, scan = discover_sessions(client, "test-workspace", sessions_dir)
        assert scan.disk_only_ids == []
        assert scan.candidate_ids == []
