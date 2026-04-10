"""Tests for cmd_status (task-12).

Verifies the behavior of cmd_status in scripts/context-intelligence.py:
- Raises no NotImplementedError
- Returns int exit code (1 on unhealthy server, 0 on healthy server)
- Configures logging (INFO level)
- Resolves config via resolve_config
- Creates CIClient and calls health_check()
- When status != 'ok': prints JSON with status/server_url/error to stdout, returns 1
- When status == 'ok': queries session counts by workspace
  - Filtered by --workspace if provided
  - All workspaces ordered by count DESC otherwise
- Builds result dict with status/server_url/total_sessions/workspaces
- Prints as indented JSON to stdout, returns 0
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
SCRIPT_PATH = SCRIPTS_DIR / "context-intelligence.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_script_module():
    """Dynamically load scripts/context-intelligence.py as a module."""
    spec = importlib.util.spec_from_file_location("context_intelligence_cli", SCRIPT_PATH)
    assert spec is not None, f"Failed to create spec for {SCRIPT_PATH}"
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_args(
    *,
    workspace: str | None = None,
    server_url: str = "http://localhost:8000",
    api_key: str = "test-key",
) -> argparse.Namespace:
    """Build an argparse.Namespace for testing cmd_status."""
    return argparse.Namespace(
        workspace=workspace,
        server_url=server_url,
        api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Basic structure tests
# ---------------------------------------------------------------------------


class TestCmdStatusExists:
    """cmd_status must exist and be callable."""

    def test_cmd_status_exists(self):
        """cmd_status must exist in the module."""
        module = _load_script_module()
        assert hasattr(module, "cmd_status"), "Module must have cmd_status function"

    def test_cmd_status_is_callable(self):
        """cmd_status must be callable."""
        module = _load_script_module()
        assert callable(module.cmd_status), "cmd_status must be callable"

    def test_cmd_status_does_not_raise_not_implemented(self):
        """cmd_status must not raise NotImplementedError."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 0}

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", return_value=[]),
        ):
            args = _make_args()
            try:
                result = module.cmd_status(args)
                assert isinstance(result, int), f"cmd_status must return int, got {type(result)}"
            except NotImplementedError:
                raise AssertionError(
                    "cmd_status raised NotImplementedError — must be implemented in Task 12"
                )
            except SystemExit:
                pass


# ---------------------------------------------------------------------------
# Unhealthy server behavior
# ---------------------------------------------------------------------------


class TestCmdStatusUnhealthy:
    """When health_check returns status != 'ok', cmd_status returns 1 with error JSON."""

    def test_returns_1_on_unavailable(self, capsys):
        """Returns 1 when server status is 'unavailable'."""
        module = _load_script_module()

        mock_health = {"status": "unavailable", "session_count": 0}

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
        ):
            args = _make_args()
            result = module.cmd_status(args)
            assert result == 1, f"Expected 1 on unavailable server, got {result}"

    def test_returns_1_on_error_status(self, capsys):
        """Returns 1 when server status is 'error'."""
        module = _load_script_module()

        mock_health = {"status": "error", "error": "DB connection failed", "session_count": 0}

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
        ):
            args = _make_args()
            result = module.cmd_status(args)
            assert result == 1, f"Expected 1 on error status, got {result}"

    def test_prints_json_to_stdout_on_unhealthy(self, capsys):
        """Prints JSON to stdout when server is unhealthy."""
        module = _load_script_module()

        mock_health = {"status": "unavailable", "session_count": 0}

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
        ):
            args = _make_args()
            module.cmd_status(args)

        captured = capsys.readouterr()
        # stdout should be valid JSON
        try:
            output = json.loads(captured.out)
        except json.JSONDecodeError as e:
            raise AssertionError(
                f"cmd_status must print valid JSON to stdout, got: {captured.out!r}\nError: {e}"
            )

        assert isinstance(output, dict), "stdout JSON must be a dict"

    def test_unhealthy_json_has_status_field(self, capsys):
        """Unhealthy JSON output must have a 'status' field."""
        module = _load_script_module()

        mock_health = {"status": "unavailable", "session_count": 0}

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
        ):
            args = _make_args()
            module.cmd_status(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "status" in output, "Unhealthy JSON must have 'status' field"

    def test_unhealthy_json_has_server_url_field(self, capsys):
        """Unhealthy JSON output must have a 'server_url' field."""
        module = _load_script_module()

        mock_health = {"status": "unavailable", "session_count": 0}

        with (
            patch(
                "context_intelligence.config.resolve_config", return_value=("http://s:9000", "key")
            ),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
        ):
            args = _make_args(server_url="http://s:9000")
            module.cmd_status(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "server_url" in output, "Unhealthy JSON must have 'server_url' field"
        assert output["server_url"] == "http://s:9000", (
            f"server_url must match configured URL, got {output['server_url']!r}"
        )

    def test_unhealthy_json_has_error_field(self, capsys):
        """Unhealthy JSON output must have an 'error' field."""
        module = _load_script_module()

        mock_health = {"status": "unavailable", "session_count": 0}

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
        ):
            args = _make_args()
            module.cmd_status(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "error" in output, "Unhealthy JSON must have 'error' field"


# ---------------------------------------------------------------------------
# Healthy server behavior
# ---------------------------------------------------------------------------


class TestCmdStatusHealthy:
    """When health_check returns status='ok', cmd_status returns 0 with full JSON."""

    def test_returns_0_on_healthy(self, capsys):
        """Returns 0 when server status is 'ok'."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 10}
        mock_cypher_result = [
            {"workspace": "project-a", "count": 8},
            {"workspace": "project-b", "count": 2},
        ]

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", return_value=mock_cypher_result),
        ):
            args = _make_args()
            result = module.cmd_status(args)
            assert result == 0, f"Expected 0 on healthy server, got {result}"

    def test_prints_json_to_stdout_on_healthy(self, capsys):
        """Prints JSON to stdout when server is healthy."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 5}
        mock_cypher_result = [{"workspace": "test-ws", "count": 5}]

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", return_value=mock_cypher_result),
        ):
            args = _make_args()
            module.cmd_status(args)

        captured = capsys.readouterr()
        try:
            output = json.loads(captured.out)
        except json.JSONDecodeError as e:
            raise AssertionError(
                f"cmd_status must print valid JSON to stdout, got: {captured.out!r}\nError: {e}"
            )

        assert isinstance(output, dict), "stdout JSON must be a dict"

    def test_healthy_json_has_status_field(self, capsys):
        """Healthy JSON output must have a 'status' field with value 'ok'."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 5}

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", return_value=[]),
        ):
            args = _make_args()
            module.cmd_status(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "status" in output, "Healthy JSON must have 'status' field"
        assert output["status"] == "ok", f"status must be 'ok', got {output['status']!r}"

    def test_healthy_json_has_server_url_field(self, capsys):
        """Healthy JSON output must have a 'server_url' field."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 0}

        with (
            patch(
                "context_intelligence.config.resolve_config",
                return_value=("http://example:8080", "key"),
            ),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", return_value=[]),
        ):
            args = _make_args(server_url="http://example:8080")
            module.cmd_status(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "server_url" in output, "Healthy JSON must have 'server_url' field"
        assert output["server_url"] == "http://example:8080", (
            f"server_url must match, got {output['server_url']!r}"
        )

    def test_healthy_json_has_total_sessions_field(self, capsys):
        """Healthy JSON output must have a 'total_sessions' field."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 0}
        mock_cypher_result = [
            {"workspace": "ws-a", "count": 7},
            {"workspace": "ws-b", "count": 3},
        ]

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", return_value=mock_cypher_result),
        ):
            args = _make_args()
            module.cmd_status(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "total_sessions" in output, "Healthy JSON must have 'total_sessions' field"
        assert output["total_sessions"] == 10, (
            f"total_sessions must be 10 (7+3), got {output['total_sessions']}"
        )

    def test_healthy_json_has_workspaces_array(self, capsys):
        """Healthy JSON output must have a 'workspaces' array."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 0}
        mock_cypher_result = [
            {"workspace": "ws-a", "count": 4},
            {"workspace": "ws-b", "count": 1},
        ]

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", return_value=mock_cypher_result),
        ):
            args = _make_args()
            module.cmd_status(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert "workspaces" in output, "Healthy JSON must have 'workspaces' field"
        assert isinstance(output["workspaces"], list), "workspaces must be an array"

    def test_healthy_json_workspaces_contains_entries(self, capsys):
        """Workspaces array must contain workspace entries from cypher query."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 0}
        mock_cypher_result = [
            {"workspace": "project-alpha", "count": 15},
            {"workspace": "project-beta", "count": 5},
        ]

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", return_value=mock_cypher_result),
        ):
            args = _make_args()
            module.cmd_status(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        workspaces = output["workspaces"]
        assert len(workspaces) == 2, f"Expected 2 workspaces, got {len(workspaces)}"

    def test_healthy_json_is_indented(self, capsys):
        """Healthy JSON output must be pretty-printed (indented)."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 0}

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", return_value=[]),
        ):
            args = _make_args()
            module.cmd_status(args)

        captured = capsys.readouterr()
        # Indented JSON has newlines
        assert "\n" in captured.out, "Healthy JSON output must be indented (has newlines)"

    def test_healthy_total_sessions_zero_when_no_workspaces(self, capsys):
        """total_sessions must be 0 when no workspaces returned."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 0}

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", return_value=[]),
        ):
            args = _make_args()
            module.cmd_status(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output["total_sessions"] == 0, (
            f"total_sessions must be 0 when no workspaces, got {output['total_sessions']}"
        )


# ---------------------------------------------------------------------------
# Workspace filter tests
# ---------------------------------------------------------------------------


class TestCmdStatusWorkspaceFilter:
    """cmd_status must filter by --workspace when provided."""

    def test_workspace_filter_passes_workspace_to_cypher(self, capsys):
        """When --workspace is provided, cypher is called with that workspace scope."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 0}
        cypher_mock = MagicMock(return_value=[{"workspace": "my-proj", "count": 5}])

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", cypher_mock),
        ):
            args = _make_args(workspace="my-proj")
            module.cmd_status(args)

        # Verify cypher was called with the workspace filter
        assert cypher_mock.called, "cypher must be called when status is ok"
        call_args = cypher_mock.call_args
        # The workspace "my-proj" should be passed to the cypher call
        call_str = str(call_args)
        assert "my-proj" in call_str, (
            f"cypher call must include workspace 'my-proj', got: {call_str}"
        )

    def test_no_workspace_filter_uses_wildcard(self, capsys):
        """When no --workspace, cypher is called with '*' (all workspaces) scope."""
        module = _load_script_module()

        mock_health = {"status": "ok", "session_count": 0}
        cypher_mock = MagicMock(return_value=[])

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "health_check", return_value=mock_health),
            patch.object(module.CIClient, "cypher", cypher_mock),
        ):
            args = _make_args(workspace=None)
            module.cmd_status(args)

        assert cypher_mock.called, "cypher must be called when status is ok"
        call_args = cypher_mock.call_args
        call_str = str(call_args)
        assert "*" in call_str, (
            f"cypher call with no workspace filter must use '*', got: {call_str}"
        )


# ---------------------------------------------------------------------------
# Acceptance criteria
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    """Acceptance criteria: status --help shows required flags."""

    def test_status_help_shows_workspace(self):
        """status --help must show --workspace flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "status", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"--help must return 0, got {result.returncode}"
        assert "--workspace" in result.stdout, "--help must mention --workspace"

    def test_status_help_shows_server_url(self):
        """status --help must show --server-url flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "status", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"--help must return 0, got {result.returncode}"
        assert "--server-url" in result.stdout, "--help must mention --server-url"

    def test_status_help_shows_api_key(self):
        """status --help must show --api-key flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "status", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"--help must return 0, got {result.returncode}"
        assert "--api-key" in result.stdout, "--help must mention --api-key"
