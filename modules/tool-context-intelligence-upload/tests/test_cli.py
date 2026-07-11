"""Tests for cli.py — argparse CLI with two custom help levels."""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock, patch

import pytest

from amplifier_module_tool_context_intelligence_upload.session_graph import (
    ScopeError,
    UploadScope,
)


def _fake_scope(
    sessions: list,
    mode: str = "whole",
    dangling_parent_ids: list | None = None,
) -> UploadScope:
    """Build an UploadScope fixture for mocking resolve_upload_sessions in CLI tests."""
    root_ids = [meta["session_id"] for _, meta in sessions]
    return UploadScope(
        sessions=sessions,
        mode=mode,
        selected_root_ids=root_ids,
        total_discovered=len(sessions),
        selected_count=len(sessions),
        dangling_parent_ids=dangling_parent_ids or [],
    )


# ---------------------------------------------------------------------------
# -h compact help
# ---------------------------------------------------------------------------


class TestCompactHelp:
    """The -h flag must print compact help to stdout and exit 0."""

    def test_minus_h_exits_zero(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(["-h"])
        assert exc_info.value.code == 0

    def test_minus_h_stdout_contains_required_strings(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["-h"])
        captured = capsys.readouterr()
        assert "context-intelligence-upload" in captured.out
        assert "--path" in captured.out
        assert "--server-url" in captured.out
        assert "--api-key" in captured.out

    def test_compact_help_usage_line_contains_no_replay(self, capsys):
        """_COMPACT_HELP usage line must include [--no-replay]."""
        from amplifier_module_tool_context_intelligence_upload.cli import _COMPACT_HELP

        assert "[--no-replay]" in _COMPACT_HELP

    def test_compact_help_flags_block_contains_no_replay_entry(self, capsys):
        """_COMPACT_HELP flags block must contain --no-replay entry with idempotency mention."""
        from amplifier_module_tool_context_intelligence_upload.cli import _COMPACT_HELP

        assert "--no-replay" in _COMPACT_HELP
        assert "idempotency" in _COMPACT_HELP.lower()


# ---------------------------------------------------------------------------
# --no-replay argparse
# ---------------------------------------------------------------------------


class TestNoReplayArgparse:
    """The --no-replay flag must be defined with correct argparse properties."""

    def test_no_replay_default_is_false(self):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            ["--path", "/tmp", "--server-url", "http://localhost", "--api-key", "k"]
        )
        assert args.no_replay is False

    def test_no_replay_flag_sets_no_replay_true(self):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            [
                "--path",
                "/tmp",
                "--server-url",
                "http://localhost",
                "--api-key",
                "k",
                "--no-replay",
            ]
        )
        assert args.no_replay is True


# ---------------------------------------------------------------------------
# --help detailed help
# ---------------------------------------------------------------------------


class TestDetailedHelp:
    """The --help flag must print detailed help to stdout and exit 0."""

    @pytest.fixture
    def detailed_help_output(self, capsys) -> str:
        """Capture and return the full --help output once per test."""
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit):
            _build_parser().parse_args(["--help"])
        return capsys.readouterr().out

    def test_double_dash_help_exits_zero(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_double_dash_help_contains_examples(self, detailed_help_output):
        assert "Replay a single session directory" in detailed_help_output
        assert "Replay an entire project tree" in detailed_help_output
        assert "Target a recovery server" in detailed_help_output

    def test_double_dash_help_contains_progress_schema_fields(self, detailed_help_output):
        """Progress schema section must contain 'failed_at' and 'sessions_total' fields."""
        assert "failed_at" in detailed_help_output
        assert "sessions_total" in detailed_help_output

    def test_double_dash_help_contains_exit_codes(self, detailed_help_output):
        assert "EXIT CODES" in detailed_help_output

    def test_double_dash_help_contains_idempotency(self, detailed_help_output):
        assert "IDEMPOTENCY" in detailed_help_output

    def test_double_dash_help_contains_workspace_and_metadata_validation(
        self, detailed_help_output
    ):
        assert "WORKSPACE" in detailed_help_output
        assert "METADATA VALIDATION" in detailed_help_output

    def test_double_dash_help_contains_finding_server_url_section(self, detailed_help_output):
        """FINDING SERVER_URL AND API_KEY section must be present in detailed help."""
        assert "FINDING SERVER_URL AND API_KEY" in detailed_help_output

    def test_finding_server_url_section_appears_before_examples(self, detailed_help_output):
        """FINDING SERVER_URL AND API_KEY section must appear before the EXAMPLES section."""
        finding_pos = detailed_help_output.find("FINDING SERVER_URL AND API_KEY")
        assert finding_pos != -1, "FINDING SERVER_URL AND API_KEY not found in help output"
        examples_pos = detailed_help_output.find("EXAMPLES")
        assert examples_pos != -1, "EXAMPLES not found in help output"
        assert finding_pos < examples_pos, (
            "FINDING SERVER_URL AND API_KEY must appear before EXAMPLES"
        )

    def test_no_abbreviated_ci_example_com(self, detailed_help_output):
        """ci.example.com must not appear; use context-intelligence.example.com."""
        assert "ci.example.com" not in detailed_help_output

    def test_no_abbreviated_ci_api_key(self, detailed_help_output):
        """$CI_API_KEY must not appear; use $AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY."""
        assert "$CI_API_KEY" not in detailed_help_output

    def test_context_intelligence_example_com_present(self, detailed_help_output):
        """context-intelligence.example.com must appear in detailed help."""
        assert "context-intelligence.example.com" in detailed_help_output

    def test_amplifier_context_intelligence_api_key_present(self, detailed_help_output):
        """$AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY must appear in detailed help."""
        assert "$AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY" in detailed_help_output


# ---------------------------------------------------------------------------
# Missing required arguments → exit 2
# ---------------------------------------------------------------------------


class TestMissingRequiredArgs:
    """Missing required args should exit with code 2."""

    def test_missing_path_exits_2(self, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(["--server-url", "http://localhost", "--api-key", "key"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# main() — auto-generated job_id
# ---------------------------------------------------------------------------


class TestJobIdAutoGeneration:
    """When --job-id is not provided, a UUID4 is auto-generated and printed to stderr."""

    def test_auto_generates_job_id_when_not_provided(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                side_effect=ScopeError("no sessions found"),
            ),
            pytest.raises(SystemExit),
        ):
            main()
        captured = capsys.readouterr()
        # A UUID4 should have appeared on stderr
        uuid_pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
        assert re.search(uuid_pattern, captured.err, re.IGNORECASE), (
            f"No UUID4 found in stderr: {captured.err!r}"
        )
        assert "progress=" in captured.err, f"'progress=' not found in stderr: {captured.err!r}"

    def test_provided_job_id_not_auto_generated(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                    "--job-id",
                    "my-custom-job",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                side_effect=ScopeError("no sessions found"),
            ),
            pytest.raises(SystemExit),
        ):
            main()
        captured = capsys.readouterr()
        # When job_id is explicitly provided, it should NOT be printed to stderr
        assert "my-custom-job" not in captured.err


# ---------------------------------------------------------------------------
# main() — end-to-end CLI flows
# ---------------------------------------------------------------------------


class TestCliEndToEnd:
    """End-to-end CLI flows: nonexistent path, no sessions, successful upload."""

    def test_nonexistent_path_exits_2(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        nonexistent = tmp_path / "does_not_exist"
        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(nonexistent),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_no_sessions_found_exits_2_with_error(self, tmp_path, capsys):
        """Scope resolution fails loud (ScopeError) when nothing is discovered
        under PATH \u2014 this supersedes the old graceful exit-0 behavior."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                side_effect=ScopeError(f"no context-intelligence sessions found under {tmp_path}"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err.lower()
        assert captured.out == ""

    def test_successful_upload(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 5,
        }

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        result = json.loads(captured.out)
        assert result["status"] == "completed"
        assert result["sessions_uploaded"] == 1

    def test_no_replay_flag_passes_replay_false_to_run_upload(self, tmp_path, capsys):
        """When --no-replay is passed, run_upload is called with replay=False."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 0,
        }

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                    "--no-replay",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ) as mock_run_upload,
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        # run_upload was called with replay=False (forwarded from --no-replay)
        _, kwargs = mock_run_upload.call_args
        assert kwargs.get("replay") is False

    def test_default_passes_replay_true_to_run_upload(self, tmp_path, capsys):
        """When --no-replay is NOT passed, run_upload is called with replay=True."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 0,
        }

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ) as mock_run_upload,
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        # run_upload was called with replay=True (the default)
        _, kwargs = mock_run_upload.call_args
        assert kwargs.get("replay") is True


# ---------------------------------------------------------------------------
# main() — env var config resolution (resolve_config integration)
# ---------------------------------------------------------------------------


class TestEnvVarConfigResolution:
    """When --server-url and --api-key are omitted, resolve_config() should
    fall back to AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL and
    AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY environment variables."""

    def test_env_vars_used_when_flags_omitted(self, tmp_path, capsys):
        """main() succeeds with env vars and no --server-url/--api-key flags."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        env_overrides = {
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env-server:8100",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY": "env-secret-key",
        }
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 5,
        }
        fake_sessions = [(tmp_path, {"session_id": "s1"})]

        with (
            patch.dict("os.environ", env_overrides, clear=False),
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    # NOTE: no --server-url, no --api-key
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ) as mock_upload,
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        # Verify the env var values were passed to run_upload
        call_kwargs = mock_upload.call_args
        assert call_kwargs.kwargs["server_url"] == "http://env-server:8100"
        assert call_kwargs.kwargs["api_key"] == "env-secret-key"

    def test_flags_override_env_vars(self, tmp_path, capsys):
        """Explicit --server-url/--api-key flags take priority over env vars."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        env_overrides = {
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env-server:8100",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY": "env-secret-key",
        }
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 5,
        }
        fake_sessions = [(tmp_path, {"session_id": "s1"})]

        with (
            patch.dict("os.environ", env_overrides, clear=False),
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://flag-server:9999",
                    "--api-key",
                    "flag-key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ) as mock_upload,
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        call_kwargs = mock_upload.call_args
        assert call_kwargs.kwargs["server_url"] == "http://flag-server:9999"
        assert call_kwargs.kwargs["api_key"] == "flag-key"

    def test_exits_when_server_url_missing_everywhere(self, tmp_path, monkeypatch):
        """SystemExit when --server-url omitted AND no env var AND no settings.yaml."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", raising=False)
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", raising=False)

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--api-key",
                    "some-key",
                ],
            ),
            patch("context_intelligence.config.SETTINGS_PATH", tmp_path / "nosettings.yaml"),
            pytest.raises(SystemExit),
        ):
            main()

    def test_exits_when_api_key_missing_everywhere(self, tmp_path, monkeypatch):
        """SystemExit when --api-key omitted AND no env var AND no settings.yaml."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", raising=False)
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", raising=False)

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                ],
            ),
            patch("context_intelligence.config.SETTINGS_PATH", tmp_path / "nosettings.yaml"),
            pytest.raises(SystemExit),
        ):
            main()


# ---------------------------------------------------------------------------
# main() \u2014 scope resolution wiring (loud scope line, dangling note, ScopeError)
# ---------------------------------------------------------------------------


class TestScopeResolutionWiring:
    """CLI must emit a loud resolved-scope line and handle ScopeError \u2192 exit 2."""

    def _run(self, tmp_path, scope, mock_result=None):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        if mock_result is None:
            mock_result = MagicMock()
            mock_result.success = True
            mock_result.to_dict.return_value = {
                "status": "completed",
                "sessions_uploaded": scope.selected_count,
                "events_uploaded": 0,
            }

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=scope,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()
        return exc_info

    def test_scope_line_printed_to_stderr(self, tmp_path, capsys):
        fake_sessions = [(tmp_path, {"session_id": "root"}), (tmp_path, {"session_id": "child"})]
        scope = _fake_scope(fake_sessions, mode="single")

        self._run(tmp_path, scope)

        captured = capsys.readouterr()
        assert "scope:" in captured.err
        assert "mode=single" in captured.err
        assert "root(s)=root" in captured.err
        assert "uploading 2 of 2" in captured.err

    def test_dangling_parent_note_printed_when_present(self, tmp_path, capsys):
        fake_sessions = [(tmp_path, {"session_id": "mid"}), (tmp_path, {"session_id": "leaf"})]
        scope = _fake_scope(fake_sessions, mode="single", dangling_parent_ids=["root"])

        self._run(tmp_path, scope)

        captured = capsys.readouterr()
        assert "note:" in captured.err
        assert "root" in captured.err
        assert "placeholder" in captured.err.lower()

    def test_no_dangling_note_when_absent(self, tmp_path, capsys):
        fake_sessions = [(tmp_path, {"session_id": "root"})]
        scope = _fake_scope(fake_sessions, mode="whole")

        self._run(tmp_path, scope)

        captured = capsys.readouterr()
        assert "note:" not in captured.err

    def test_scope_error_exits_2_with_error_message(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload.cli import main

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "key",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                side_effect=ScopeError(f"no context-intelligence sessions found under {tmp_path}"),
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "error:" in captured.err
        assert "no context-intelligence sessions found" in captured.err
        assert captured.out == ""
