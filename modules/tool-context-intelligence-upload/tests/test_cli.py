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


def _make_upload_result(sessions_uploaded: int = 1, events_uploaded: int = 0) -> MagicMock:
    """Build a MagicMock standing in for run_upload()'s UploadResult return value.

    events_skipped/events_unmapped are pinned to 0 (rather than left as
    auto-speccing MagicMock attributes) because main()'s exit-code logic
    (C4) sums them with discovery.live_skipped -- an unpinned MagicMock
    supports arithmetic via its magic methods and would always be truthy,
    silently forcing exit code 3 for every caller of this helper.
    """
    mock_result = MagicMock()
    mock_result.success = True
    mock_result.events_skipped = 0
    mock_result.events_unmapped = 0
    mock_result.to_dict.return_value = {
        "status": "completed",
        "sessions_uploaded": sessions_uploaded,
        "events_uploaded": events_uploaded,
    }
    return mock_result


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
# --format argparse
# ---------------------------------------------------------------------------


class TestFormatArgparse:
    """The --format flag must be defined with correct argparse properties."""

    def test_parser_accepts_format_flag(self):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            [
                "--path",
                "/tmp",
                "--server-url",
                "http://localhost",
                "--api-key",
                "k",
                "--format",
                "logging-hook",
            ]
        )
        assert args.format == "logging-hook"

        default_args = _build_parser().parse_args(
            ["--path", "/tmp", "--server-url", "http://localhost", "--api-key", "k"]
        )
        assert default_args.format == "context-intelligence"

    def test_parser_rejects_unknown_format(self):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(
                [
                    "--path",
                    "/tmp",
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "k",
                    "--format",
                    "bogus",
                ]
            )
        assert exc_info.value.code == 2


def test_help_mentions_format():
    """Both _COMPACT_HELP and _DETAILED_HELP must document --format and logging-hook."""
    from amplifier_module_tool_context_intelligence_upload.cli import (
        _COMPACT_HELP,
        _DETAILED_HELP,
    )

    assert "--format" in _COMPACT_HELP
    assert "logging-hook" in _COMPACT_HELP
    assert "--format" in _DETAILED_HELP
    assert "logging-hook" in _DETAILED_HELP


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


# ---------------------------------------------------------------------------
# main() -- --server-url precedence over env-based default (DTU isolation)
# ---------------------------------------------------------------------------


class TestDtuServerUrlPrecedence:
    """CLI --server-url must win over the env-based server URL default.

    This locks the precedence property that dtu/verify.sh and its callers
    (Tasks 6/8) rely on: when a Digital Twin Universe container runs the
    upload CLI, the host's environment-based server URL default must NOT
    leak in -- an explicit --server-url must always win.

    NOTE ON NAMING: the task spec that requested this test named the env
    var ``AMPLIFIER_CONTEXT_INTELLIGENCE_PRIVATE_SERVER_URL``. A grep across
    this package (``PKG/``) confirms no such variable is read anywhere:

        $ grep -rn "PRIVATE_SERVER_URL" amplifier-bundle-context-intelligence
        (no matches)

    The only env-based default that actually exists is
    ``AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL``, consulted by
    ``context_intelligence.config.resolve_config`` (see
    ``TestEnvVarConfigResolution`` above, which already covers the general
    case). This test exercises that real env var with DTU-flavored values
    (a host production URL vs. a DTU container URL) so the precedence
    property is locked under the specific naming this task cares about.
    """

    def test_server_url_overrides_env_default(self, tmp_path, monkeypatch, capsys):
        """--server-url wins over AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL", "http://HOST-PROD:8000")
        monkeypatch.setenv("AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY", "env-key")

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 0,
        }
        fake_sessions = [(tmp_path, {"session_id": "s1"})]

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://dtu-container:8000",
                    "--api-key",
                    "dtu-key",
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
        _, kwargs = mock_run_upload.call_args
        assert kwargs["server_url"] == "http://dtu-container:8000"


# ---------------------------------------------------------------------------
# main() -- --format discover/parse pair selection (Task 2)
# ---------------------------------------------------------------------------


class TestFormatDispatch:
    """main() must select the discover/parse pair based on --format.

    DECISION C1 (loud reject): --no-replay + --format logging-hook must exit 2
    before any discovery/upload happens.
    DECISION D2 (hard-lock): the logging-hook (legacy) path always dedups --
    replay=False unconditionally, with no override.
    """

    def test_main_logging_hook_selects_pair(self, tmp_path, capsys):
        """--format logging-hook calls discover_legacy and passes its parse_fn
        from FORMATS['logging-hook'] through to run_upload."""
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod
        from amplifier_module_tool_context_intelligence_upload.formats import FORMATS
        from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
            LegacyDiscovery,
        )

        fake_sessions = [
            (tmp_path, {"session_id": "s1", "format": "logging-hook", "workspace": "ws"})
        ]
        fake_discovery = LegacyDiscovery(
            sessions=fake_sessions,
            candidates_seen=1,
            live_skipped=0,
            unresolved_workspace=0,
        )
        mock_result = _make_upload_result()

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
                    "--format",
                    "logging-hook",
                ],
            ),
            patch.object(cli_mod, "discover_legacy", return_value=fake_discovery) as mock_discover,
            patch.object(cli_mod, "run_upload", return_value=mock_result) as mock_run_upload,
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_mod.main()

        assert exc_info.value.code == 0
        mock_discover.assert_called_once()
        _, kwargs = mock_run_upload.call_args
        assert kwargs["parse_fn"] is FORMATS["logging-hook"][1]

    def test_main_logging_hook_locks_replay_false(self, tmp_path, capsys):
        """The logging-hook path always calls run_upload with replay=False,
        regardless of --no-replay (which is not even passed here)."""
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod
        from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
            LegacyDiscovery,
        )

        fake_sessions = [
            (tmp_path, {"session_id": "s1", "format": "logging-hook", "workspace": "ws"})
        ]
        fake_discovery = LegacyDiscovery(
            sessions=fake_sessions,
            candidates_seen=1,
            live_skipped=0,
            unresolved_workspace=0,
        )
        mock_result = _make_upload_result()

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
                    "--format",
                    "logging-hook",
                ],
            ),
            patch.object(cli_mod, "discover_legacy", return_value=fake_discovery),
            patch.object(cli_mod, "run_upload", return_value=mock_result) as mock_run_upload,
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_mod.main()

        assert exc_info.value.code == 0
        _, kwargs = mock_run_upload.call_args
        assert kwargs["replay"] is False

    def test_main_logging_hook_rejects_no_replay(self, tmp_path, capsys):
        """--format logging-hook + --no-replay exits 2 with a loud stderr
        message, and never reaches discovery or run_upload (fail fast)."""
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod

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
                    "--format",
                    "logging-hook",
                    "--no-replay",
                ],
            ),
            patch.object(cli_mod, "discover_legacy") as mock_discover,
            patch.object(cli_mod, "run_upload") as mock_run_upload,
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_mod.main()

        assert exc_info.value.code == 2
        captured = capsys.readouterr()
        assert "--no-replay is not valid with --format logging-hook" in captured.err
        mock_discover.assert_not_called()
        mock_run_upload.assert_not_called()

    def test_main_default_path_still_honors_no_replay(self, tmp_path, capsys):
        """The default --format context-intelligence path is unaffected by the
        logging-hook hard-lock: --no-replay still sets replay=False."""
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = _make_upload_result()

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
            patch.object(
                cli_mod, "resolve_upload_sessions", return_value=_fake_scope(fake_sessions)
            ),
            patch.object(cli_mod, "run_upload", return_value=mock_result) as mock_run_upload,
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_mod.main()

        assert exc_info.value.code == 0
        _, kwargs = mock_run_upload.call_args
        assert kwargs["replay"] is False


# ---------------------------------------------------------------------------
# main() -- operator reconciliation summary (Task 3)
# ---------------------------------------------------------------------------


class TestReconciliationSummary:
    """main() must print the independently-measured reconciliation summary
    to stderr after run_upload() returns and before the result JSON is
    written to stdout."""

    def test_main_prints_reconciliation_summary(self, tmp_path, capsys):
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod
        from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
            LegacyDiscovery,
        )
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        # Build a REAL one-session tree with a 5-non-blank-line events.jsonl so
        # read_total is an independent, on-disk-derived count -- not a
        # rederivation of ingested + skipped.
        session_dir = tmp_path / "session1"
        session_dir.mkdir()
        (session_dir / "events.jsonl").write_text(
            "\n".join(f'{{"line": {i}}}' for i in range(5)) + "\n",
            encoding="utf-8",
        )
        metadata = {"session_id": "s1", "format": "logging-hook", "workspace": "ws"}
        fake_sessions = [(session_dir, metadata)]
        fake_discovery = LegacyDiscovery(
            sessions=fake_sessions,
            candidates_seen=1,
            live_skipped=2,
            unresolved_workspace=0,
        )
        upload_result = UploadResult(
            success=True,
            sessions_uploaded=1,
            events_uploaded=4,
            events_skipped=1,
            events_unmapped=0,
        )

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
                    "--format",
                    "logging-hook",
                ],
            ),
            patch.object(cli_mod, "discover_legacy", return_value=fake_discovery),
            patch.object(cli_mod, "run_upload", return_value=upload_result),
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_mod.main()

        # Task 4b (C4): 1 event skipped + 2 live-skipped sessions means this
        # run completed WITH issues -- exit 3, not 0.
        assert exc_info.value.code == 3
        captured = capsys.readouterr()
        assert "reconciliation:" in captured.err
        assert "5 read" in captured.err
        assert "4 ingested" in captured.err
        assert "1 skipped" in captured.err
        assert "0 unmapped" in captured.err
        assert "2 live-sessions-skipped" in captured.err
        assert "already-present" not in captured.err

    def test_main_reconciliation_read_is_independent_line_count(self, tmp_path, capsys):
        """read must follow the events.jsonl FILE, not ingested + skipped --
        adding a 6th line changes 'read' even though ingested/skipped are
        unchanged, proving it's not a derived echo."""
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod
        from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
            LegacyDiscovery,
        )
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        session_dir = tmp_path / "session1"
        session_dir.mkdir()
        (session_dir / "events.jsonl").write_text(
            "\n".join(f'{{"line": {i}}}' for i in range(6)) + "\n",
            encoding="utf-8",
        )
        metadata = {"session_id": "s1", "format": "logging-hook", "workspace": "ws"}
        fake_sessions = [(session_dir, metadata)]
        fake_discovery = LegacyDiscovery(
            sessions=fake_sessions,
            candidates_seen=1,
            live_skipped=2,
            unresolved_workspace=0,
        )
        upload_result = UploadResult(
            success=True,
            sessions_uploaded=1,
            events_uploaded=4,
            events_skipped=1,
            events_unmapped=0,
        )

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
                    "--format",
                    "logging-hook",
                ],
            ),
            patch.object(cli_mod, "discover_legacy", return_value=fake_discovery),
            patch.object(cli_mod, "run_upload", return_value=upload_result),
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit),
        ):
            cli_mod.main()

        captured = capsys.readouterr()
        assert "6 read" in captured.err

    def test_main_reconciliation_read_excludes_blank_lines(self, tmp_path, capsys):
        """read must count non-blank event lines only, so the arithmetic
        (read == ingested + skipped + unmapped) reconciles exactly even when
        events.jsonl has interior and trailing blank lines.

        Prior to the fix, _count_lines counted every physical line
        (including blanks), so `read` could exceed
        ingested + skipped + unmapped purely from blank/trailing lines --
        defeating the operator-trust arithmetic this summary exists for.
        """
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod
        from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
            LegacyDiscovery,
        )
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        session_dir = tmp_path / "session1"
        session_dir.mkdir()
        # 5 non-blank event lines interleaved with blank/whitespace-only lines
        # (interior AND trailing) -- these must NOT be counted as "read".
        (session_dir / "events.jsonl").write_text(
            '{"line": 0}\n\n{"line": 1}\n{"line": 2}\n   \n{"line": 3}\n{"line": 4}\n\n\n',
            encoding="utf-8",
        )
        metadata = {"session_id": "s1", "format": "logging-hook", "workspace": "ws"}
        fake_sessions = [(session_dir, metadata)]
        fake_discovery = LegacyDiscovery(
            sessions=fake_sessions,
            candidates_seen=1,
            live_skipped=0,
            unresolved_workspace=0,
        )
        # ingested + skipped + unmapped == 5, matching the 5 non-blank lines.
        upload_result = UploadResult(
            success=True,
            sessions_uploaded=1,
            events_uploaded=3,
            events_skipped=1,
            events_unmapped=1,
        )

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
                    "--format",
                    "logging-hook",
                ],
            ),
            patch.object(cli_mod, "discover_legacy", return_value=fake_discovery),
            patch.object(cli_mod, "run_upload", return_value=upload_result),
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit),
        ):
            cli_mod.main()

        captured = capsys.readouterr()
        assert "reconciliation:" in captured.err
        # read == ingested + skipped + unmapped == 3 + 1 + 1 == 5, exactly.
        assert "5 read" in captured.err
        assert "3 ingested" in captured.err
        assert "1 skipped" in captured.err
        assert "1 unmapped" in captured.err

    def test_main_reconciliation_default_format_zero_live_sessions_skipped(self, tmp_path, capsys):
        """The default context-intelligence path has no discovery object, so
        live-sessions-skipped must be 0 without raising."""
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        session_dir = tmp_path / "session1"
        session_dir.mkdir()
        (session_dir / "events.jsonl").write_text('{"line": 0}\n', encoding="utf-8")
        fake_sessions = [(session_dir, {"session_id": "s1"})]
        upload_result = UploadResult(
            success=True,
            sessions_uploaded=1,
            events_uploaded=1,
            events_skipped=0,
            events_unmapped=0,
        )

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
            patch.object(
                cli_mod, "resolve_upload_sessions", return_value=_fake_scope(fake_sessions)
            ),
            patch.object(cli_mod, "run_upload", return_value=upload_result),
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit),
        ):
            cli_mod.main()

        captured = capsys.readouterr()
        assert "reconciliation:" in captured.err
        assert "0 live-sessions-skipped" in captured.err


# ---------------------------------------------------------------------------
# main() -- operator signals: zero-discovery warning, live-skip ids,
# partial-success exit code (Task 4b)
# ---------------------------------------------------------------------------


class TestOperatorSignals:
    """logging-hook path emits three operator-facing, machine-checkable
    signals: a zero-discovery warning (C2), a live-skip session id list
    (C3), and a partial-success exit code 3 (C4). All three are
    logging-hook-only -- the default context-intelligence path's output and
    exit semantics are byte-unchanged (GATE 2)."""

    def test_main_logging_hook_warns_on_zero_discovery(self, tmp_path, capsys):
        """Zero legacy sessions discovered emits a stderr warning naming the
        target path, printed before the 'scope:' summary line."""
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod
        from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
            LegacyDiscovery,
        )

        fake_discovery = LegacyDiscovery(
            sessions=[],
            candidates_seen=0,
            live_skipped=0,
            unresolved_workspace=0,
        )

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
                    "--format",
                    "logging-hook",
                ],
            ),
            patch.object(cli_mod, "discover_legacy", return_value=fake_discovery),
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit),
        ):
            cli_mod.main()

        captured = capsys.readouterr()
        assert "no legacy (hooks-logging) sessions found" in captured.err
        assert str(tmp_path) in captured.err
        assert "check --path and --format" in captured.err
        warn_idx = captured.err.index("no legacy (hooks-logging) sessions found")
        scope_idx = captured.err.index("scope:")
        assert warn_idx < scope_idx

    def test_main_logging_hook_lists_live_skipped_ids(self, tmp_path, capsys):
        """discovery.live_skipped_ids are printed to stderr as a
        comma-joined note, after the 'scope:' summary line."""
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod
        from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
            LegacyDiscovery,
        )
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        session_dir = tmp_path / "session1"
        session_dir.mkdir()
        (session_dir / "events.jsonl").write_text('{"line": 0}\n', encoding="utf-8")
        metadata = {"session_id": "s1", "format": "logging-hook", "workspace": "ws"}
        fake_discovery = LegacyDiscovery(
            sessions=[(session_dir, metadata)],
            candidates_seen=3,
            live_skipped=2,
            unresolved_workspace=0,
            live_skipped_ids=["live-a", "live-b"],
        )
        upload_result = UploadResult(
            success=True,
            sessions_uploaded=1,
            events_uploaded=1,
            events_skipped=0,
            events_unmapped=0,
        )

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
                    "--format",
                    "logging-hook",
                ],
            ),
            patch.object(cli_mod, "discover_legacy", return_value=fake_discovery),
            patch.object(cli_mod, "run_upload", return_value=upload_result),
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit),
        ):
            cli_mod.main()

        captured = capsys.readouterr()
        assert "live-a" in captured.err
        assert "live-b" in captured.err
        assert "note: 2 live/in-progress session(s) skipped:" in captured.err
        scope_idx = captured.err.index("scope:")
        note_idx = captured.err.index("note: 2 live/in-progress session(s) skipped:")
        assert scope_idx < note_idx

    def test_main_logging_hook_partial_success_exit_code(self, tmp_path, capsys):
        """logging-hook exits 3 (completed WITH issues) when events_skipped,
        events_unmapped, or discovery.live_skipped is nonzero -- even when
        upload_result.success is True."""
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod
        from amplifier_module_tool_context_intelligence_upload.logging_hook_format import (
            LegacyDiscovery,
        )
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        session_dir = tmp_path / "session1"
        session_dir.mkdir()
        (session_dir / "events.jsonl").write_text('{"line": 0}\n', encoding="utf-8")
        metadata = {"session_id": "s1", "format": "logging-hook", "workspace": "ws"}
        fake_discovery = LegacyDiscovery(
            sessions=[(session_dir, metadata)],
            candidates_seen=1,
            live_skipped=0,
            unresolved_workspace=0,
        )
        upload_result = UploadResult(
            success=True,
            sessions_uploaded=1,
            events_uploaded=0,
            events_skipped=1,
            events_unmapped=0,
        )

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
                    "--format",
                    "logging-hook",
                ],
            ),
            patch.object(cli_mod, "discover_legacy", return_value=fake_discovery),
            patch.object(cli_mod, "run_upload", return_value=upload_result),
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_mod.main()

        assert exc_info.value.code == 3

    def test_main_default_path_exit_semantics_unchanged(self, tmp_path, capsys):
        """The default context-intelligence path never returns exit 3 --
        even with events_skipped > 0, success=True still exits 0 (byte-
        unchanged default-path exit semantics, GATE 2)."""
        from amplifier_module_tool_context_intelligence_upload import cli as cli_mod
        from amplifier_module_tool_context_intelligence_upload.uploader import UploadResult

        session_dir = tmp_path / "session1"
        session_dir.mkdir()
        (session_dir / "events.jsonl").write_text('{"line": 0}\n', encoding="utf-8")
        fake_sessions = [(session_dir, {"session_id": "s1"})]
        upload_result = UploadResult(
            success=True,
            sessions_uploaded=1,
            events_uploaded=0,
            events_skipped=5,
            events_unmapped=5,
        )

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
            patch.object(
                cli_mod, "resolve_upload_sessions", return_value=_fake_scope(fake_sessions)
            ),
            patch.object(cli_mod, "run_upload", return_value=upload_result),
            patch.object(cli_mod, "ProgressTracker"),
            pytest.raises(SystemExit) as exc_info,
        ):
            cli_mod.main()

        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# --destination / --auto-approve / optional --path argparse
# ---------------------------------------------------------------------------


class TestDestinationAndAutoApproveArgparse:
    """The two new flags, and --path becoming optional."""

    def test_destination_defaults_to_none(self):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args([])
        assert args.destination is None

    def test_destination_flag_is_parsed(self):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(["--destination", "team"])
        assert args.destination == "team"

    def test_auto_approve_defaults_to_false(self):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args([])
        assert args.auto_approve is False

    @pytest.mark.parametrize("flag", ["-y", "--auto-approve"])
    def test_auto_approve_flag_and_alias_set_true(self, flag):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args([flag])
        assert args.auto_approve is True

    def test_path_is_optional_and_defaults_to_none(self):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args([])
        assert args.path is None

    def test_path_is_still_accepted_when_given(self):
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(["--path", "/tmp"])
        assert args.path == "/tmp"
