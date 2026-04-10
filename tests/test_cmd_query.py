"""Tests for cmd_query (task-13).

Verifies the behavior of cmd_query in scripts/context-intelligence.py:
- Raises no NotImplementedError
- Returns int exit code (0 on success)
- Configures logging (WARNING level to keep stdout clean)
- Resolves config via resolve_config
- Creates CIClient and calls cypher() with args.cypher and workspace=args.workspace
- Prints results as indented JSON to stdout
- Returns 0
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
    cypher: str = "MATCH (n) RETURN n LIMIT 1",
    workspace: str = "*",
    server_url: str = "http://localhost:8000",
    api_key: str = "test-key",
) -> argparse.Namespace:
    """Build an argparse.Namespace for testing cmd_query."""
    return argparse.Namespace(
        cypher=cypher,
        workspace=workspace,
        server_url=server_url,
        api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Basic structure tests
# ---------------------------------------------------------------------------


class TestCmdQueryExists:
    """cmd_query must exist and be callable."""

    def test_cmd_query_exists(self):
        """cmd_query must exist in the module."""
        module = _load_script_module()
        assert hasattr(module, "cmd_query"), "Module must have cmd_query function"

    def test_cmd_query_is_callable(self):
        """cmd_query must be callable."""
        module = _load_script_module()
        assert callable(module.cmd_query), "cmd_query must be callable"

    def test_cmd_query_does_not_raise_not_implemented(self):
        """cmd_query must not raise NotImplementedError."""
        module = _load_script_module()

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "cypher", return_value=[]),
        ):
            args = _make_args()
            try:
                result = module.cmd_query(args)
                assert isinstance(result, int), f"cmd_query must return int, got {type(result)}"
            except NotImplementedError:
                raise AssertionError(
                    "cmd_query raised NotImplementedError — must be implemented in Task 13"
                )
            except SystemExit:
                pass


# ---------------------------------------------------------------------------
# Return code tests
# ---------------------------------------------------------------------------


class TestCmdQueryReturnCode:
    """cmd_query must return 0 on success."""

    def test_returns_0(self):
        """Returns 0 on successful query."""
        module = _load_script_module()

        mock_cypher_result = [{"n": {"id": 1}}, {"n": {"id": 2}}]

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "cypher", return_value=mock_cypher_result),
        ):
            args = _make_args()
            result = module.cmd_query(args)
            assert result == 0, f"Expected 0 on successful query, got {result}"

    def test_returns_0_on_empty_result(self):
        """Returns 0 even when cypher returns no results."""
        module = _load_script_module()

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "cypher", return_value=[]),
        ):
            args = _make_args()
            result = module.cmd_query(args)
            assert result == 0, f"Expected 0 on empty query result, got {result}"


# ---------------------------------------------------------------------------
# Cypher invocation tests
# ---------------------------------------------------------------------------


class TestCmdQueryCypherCall:
    """cmd_query must call client.cypher with the correct arguments."""

    def test_cypher_called_with_query_string(self):
        """cypher must be called with args.cypher as the query string."""
        module = _load_script_module()

        cypher_mock = MagicMock(return_value=[])
        test_query = "MATCH (s:Session) RETURN s LIMIT 5"

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "cypher", cypher_mock),
        ):
            args = _make_args(cypher=test_query)
            module.cmd_query(args)

        assert cypher_mock.called, "cypher must be called"
        call_args = cypher_mock.call_args
        # First positional argument should be the query string
        assert call_args[0][0] == test_query, (
            f"cypher must be called with query string '{test_query}', got: {call_args}"
        )

    def test_cypher_called_with_workspace(self):
        """cypher must be called with workspace=args.workspace."""
        module = _load_script_module()

        cypher_mock = MagicMock(return_value=[])
        test_workspace = "my-project"

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "cypher", cypher_mock),
        ):
            args = _make_args(workspace=test_workspace)
            module.cmd_query(args)

        assert cypher_mock.called, "cypher must be called"
        call_args = cypher_mock.call_args
        call_str = str(call_args)
        assert test_workspace in call_str, (
            f"cypher must be called with workspace '{test_workspace}', got: {call_str}"
        )

    def test_cypher_called_with_default_wildcard_workspace(self):
        """When workspace is '*', cypher is called with workspace='*'."""
        module = _load_script_module()

        cypher_mock = MagicMock(return_value=[])

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "cypher", cypher_mock),
        ):
            args = _make_args(workspace="*")
            module.cmd_query(args)

        assert cypher_mock.called, "cypher must be called"
        call_args = cypher_mock.call_args
        call_str = str(call_args)
        assert "*" in call_str, f"cypher call with default workspace must use '*', got: {call_str}"


# ---------------------------------------------------------------------------
# JSON output tests
# ---------------------------------------------------------------------------


class TestCmdQueryOutput:
    """cmd_query must print results as indented JSON to stdout."""

    def test_prints_json_to_stdout(self, capsys):
        """Prints JSON to stdout."""
        module = _load_script_module()

        mock_result = [{"workspace": "test", "count": 5}]

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "cypher", return_value=mock_result),
        ):
            args = _make_args()
            module.cmd_query(args)

        captured = capsys.readouterr()
        try:
            output = json.loads(captured.out)
        except json.JSONDecodeError as e:
            raise AssertionError(
                f"cmd_query must print valid JSON to stdout, got: {captured.out!r}\nError: {e}"
            )

        assert isinstance(output, list), (
            f"stdout JSON must be a list (cypher results), got {type(output)}"
        )

    def test_prints_indented_json(self, capsys):
        """JSON output must be pretty-printed (indented)."""
        module = _load_script_module()

        mock_result = [{"key": "value"}]

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "cypher", return_value=mock_result),
        ):
            args = _make_args()
            module.cmd_query(args)

        captured = capsys.readouterr()
        # Indented JSON has newlines
        assert "\n" in captured.out, "JSON output must be indented (has newlines)"

    def test_output_matches_cypher_results(self, capsys):
        """Output JSON must match the cypher query results exactly."""
        module = _load_script_module()

        mock_result = [
            {"id": "session-1", "status": "complete"},
            {"id": "session-2", "status": "active"},
        ]

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "cypher", return_value=mock_result),
        ):
            args = _make_args()
            module.cmd_query(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output == mock_result, (
            f"Output must match cypher results exactly.\nExpected: {mock_result}\nGot: {output}"
        )

    def test_prints_empty_list_for_no_results(self, capsys):
        """Prints empty JSON list when cypher returns no results."""
        module = _load_script_module()

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch.object(module.CIClient, "cypher", return_value=[]),
        ):
            args = _make_args()
            module.cmd_query(args)

        captured = capsys.readouterr()
        output = json.loads(captured.out)
        assert output == [], f"Output must be empty list when no results, got: {output}"


# ---------------------------------------------------------------------------
# Config resolution tests
# ---------------------------------------------------------------------------


class TestCmdQueryConfigResolution:
    """cmd_query must resolve config via resolve_config."""

    def test_calls_resolve_config(self):
        """resolve_config must be called with server_url and api_key from args."""
        module = _load_script_module()

        resolve_mock = MagicMock(return_value=("http://s", "key"))

        with (
            patch("context_intelligence.config.resolve_config", resolve_mock),
            patch.object(module.CIClient, "cypher", return_value=[]),
        ):
            args = _make_args(server_url="http://myserver:9999", api_key="my-api-key")
            module.cmd_query(args)

        assert resolve_mock.called, "resolve_config must be called"

    def test_uses_resolved_server_url(self):
        """CIClient must be created with the URL from resolve_config."""
        module = _load_script_module()

        resolved_url = "http://resolved-server:8080"
        with (
            patch("context_intelligence.config.resolve_config", return_value=(resolved_url, "key")),
            patch.object(module.CIClient, "__init__", return_value=None) as init_mock,
            patch.object(module.CIClient, "cypher", return_value=[]),
        ):
            args = _make_args()
            module.cmd_query(args)

        # CIClient.__init__ was called with the resolved URL
        assert init_mock.called, "CIClient.__init__ must be called"
        call_str = str(init_mock.call_args)
        assert resolved_url in call_str, (
            f"CIClient must be created with resolved URL '{resolved_url}', got: {call_str}"
        )


# ---------------------------------------------------------------------------
# Acceptance criteria
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    """Acceptance criteria: query --help shows required arguments."""

    def test_query_help_shows_cypher_argument(self):
        """query --help must show the positional cypher argument."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "query", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"--help must return 0, got {result.returncode}"
        assert "cypher" in result.stdout, "--help must mention 'cypher' positional argument"

    def test_query_help_shows_workspace_flag(self):
        """query --help must show --workspace flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "query", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"--help must return 0, got {result.returncode}"
        assert "--workspace" in result.stdout, "--help must mention --workspace flag"

    def test_query_help_shows_server_url(self):
        """query --help must show --server-url flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "query", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"--help must return 0, got {result.returncode}"
        assert "--server-url" in result.stdout, "--help must mention --server-url"

    def test_query_help_shows_api_key(self):
        """query --help must show --api-key flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "query", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"--help must return 0, got {result.returncode}"
        assert "--api-key" in result.stdout, "--help must mention --api-key"
