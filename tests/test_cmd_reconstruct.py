"""Tests for cmd_reconstruct (task-11).

Verifies the behavior of cmd_reconstruct in scripts/context-intelligence.py:
- Raises no NotImplementedError
- Returns int exit code (0 on success, 1 on errors)
- Configures logging (DEBUG if verbose, INFO otherwise)
- Resolves config via resolve_config
- Determines what to reconstruct based on --events-only/--transcript-only/--metadata-only
- Derives workspace slug and sessions dir from --project-dir
- Discovers sessions via discover_sessions
- Filters sessions by --session prefix when provided
- Processes graph sessions for events.jsonl/transcript.jsonl/metadata.json
- Respects skip-existing logic (unless --force)
- Supports --dry-run (no files written)
- Processes disk-only sessions using build_disk_only_metadata
- Prints summary (written/skipped/errors/elapsed)
- Returns 1 if errors, 0 otherwise
"""

from __future__ import annotations

import argparse
import importlib.util
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
    project_dir: str = "/tmp/test-project",
    events_only: bool = False,
    transcript_only: bool = False,
    metadata_only: bool = False,
    force: bool = False,
    dry_run: bool = False,
    resolve_blobs: bool = False,
    session: str | None = None,
    verbose: bool = False,
    server_url: str = "http://localhost:8000",
    api_key: str = "test-key",
) -> argparse.Namespace:
    """Build an argparse.Namespace for testing cmd_reconstruct."""
    return argparse.Namespace(
        project_dir=project_dir,
        events_only=events_only,
        transcript_only=transcript_only,
        metadata_only=metadata_only,
        force=force,
        dry_run=dry_run,
        resolve_blobs=resolve_blobs,
        session=session,
        verbose=verbose,
        server_url=server_url,
        api_key=api_key,
    )


# ---------------------------------------------------------------------------
# Basic structure tests
# ---------------------------------------------------------------------------


class TestCmdReconstructExists:
    """cmd_reconstruct must exist and be callable."""

    def test_cmd_reconstruct_exists(self):
        """cmd_reconstruct must exist in the module."""
        module = _load_script_module()
        assert hasattr(module, "cmd_reconstruct"), "Module must have cmd_reconstruct function"

    def test_cmd_reconstruct_is_callable(self):
        """cmd_reconstruct must be callable."""
        module = _load_script_module()
        assert callable(module.cmd_reconstruct), "cmd_reconstruct must be callable"

    def test_cmd_reconstruct_does_not_raise_not_implemented(self):
        """cmd_reconstruct must not raise NotImplementedError."""
        module = _load_script_module()

        # Patch all external calls so we can test the function runs
        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([], []),
            ),
        ):
            args = _make_args()
            try:
                result = module.cmd_reconstruct(args)
                # If it returns, it must return an int
                assert isinstance(result, int), (
                    f"cmd_reconstruct must return int, got {type(result)}"
                )
            except NotImplementedError:
                raise AssertionError(
                    "cmd_reconstruct raised NotImplementedError — must be implemented in Task 11"
                )
            except SystemExit:
                pass  # SystemExit is acceptable (e.g. no sessions found → sys.exit(0))


# ---------------------------------------------------------------------------
# Return code tests
# ---------------------------------------------------------------------------


class TestCmdReconstructReturnCode:
    """cmd_reconstruct must return 0 on success, 1 on errors."""

    def test_returns_zero_on_no_sessions(self):
        """Returns 0 when no sessions are found (via sys.exit(0) or return 0)."""
        module = _load_script_module()

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([], []),
            ),
        ):
            args = _make_args()
            try:
                result = module.cmd_reconstruct(args)
                assert result == 0, f"Expected 0 on no sessions, got {result}"
            except SystemExit as e:
                assert e.code == 0 or e.code is None, (
                    f"Expected sys.exit(0), got sys.exit({e.code!r})"
                )

    def test_returns_zero_on_successful_processing(self):
        """Returns 0 when sessions are processed without errors."""
        module = _load_script_module()

        mock_session = {
            "s.node_id": "abc123def456",
            "s.status": "complete",
            "s.started_at": "2024-01-01T00:00:00",
        }

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([mock_session], []),
            ),
            patch(
                "context_intelligence.reconstruct.events.extract_events",
                return_value=[{"event": "test"}],
            ),
            patch(
                "context_intelligence.reconstruct.transcript.extract_transcript",
                return_value=[{"role": "user", "content": "hello"}],
            ),
            patch(
                "context_intelligence.reconstruct.metadata.extract_metadata",
                return_value={"session_id": "abc123def456"},
            ),
        ):
            args = _make_args(dry_run=True)  # dry_run to avoid filesystem writes
            try:
                result = module.cmd_reconstruct(args)
                assert result == 0, f"Expected 0 on success, got {result}"
            except SystemExit as e:
                assert e.code == 0 or e.code is None, (
                    f"Expected sys.exit(0), got sys.exit({e.code!r})"
                )

    def test_returns_one_on_extract_errors(self):
        """Returns 1 when extraction errors occur."""
        module = _load_script_module()

        mock_session = {
            "s.node_id": "abc123def456",
            "s.status": "complete",
            "s.started_at": "2024-01-01T00:00:00",
        }

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([mock_session], []),
            ),
            patch(
                "context_intelligence.reconstruct.events.extract_events",
                side_effect=RuntimeError("network error"),
            ),
            patch(
                "context_intelligence.reconstruct.transcript.extract_transcript",
                side_effect=RuntimeError("network error"),
            ),
            patch(
                "context_intelligence.reconstruct.metadata.extract_metadata",
                side_effect=RuntimeError("network error"),
            ),
        ):
            args = _make_args()
            try:
                result = module.cmd_reconstruct(args)
                assert result == 1, f"Expected 1 on errors, got {result}"
            except SystemExit as e:
                assert e.code == 1, f"Expected sys.exit(1), got sys.exit({e.code!r})"


# ---------------------------------------------------------------------------
# What-to-reconstruct logic
# ---------------------------------------------------------------------------


class TestWhatToReconstruct:
    """cmd_reconstruct must respect --events-only / --transcript-only / --metadata-only flags."""

    def test_events_only_calls_extract_events_not_others(self):
        """With --events-only, only extract_events should be called."""
        module = _load_script_module()

        mock_session = {
            "s.node_id": "abc123def456",
            "s.status": "complete",
            "s.started_at": "2024-01-01T00:00:00",
        }

        extract_events_mock = MagicMock(return_value=[{"event": "test"}])
        extract_transcript_mock = MagicMock(return_value=[])
        extract_metadata_mock = MagicMock(return_value={})

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([mock_session], []),
            ),
            patch(
                "context_intelligence.reconstruct.events.extract_events",
                extract_events_mock,
            ),
            patch(
                "context_intelligence.reconstruct.transcript.extract_transcript",
                extract_transcript_mock,
            ),
            patch(
                "context_intelligence.reconstruct.metadata.extract_metadata",
                extract_metadata_mock,
            ),
        ):
            args = _make_args(events_only=True, dry_run=True)
            try:
                module.cmd_reconstruct(args)
            except SystemExit:
                pass

        extract_events_mock.assert_called_once()
        extract_transcript_mock.assert_not_called()
        extract_metadata_mock.assert_not_called()

    def test_transcript_only_calls_extract_transcript_not_others(self):
        """With --transcript-only, only extract_transcript should be called."""
        module = _load_script_module()

        mock_session = {
            "s.node_id": "abc123def456",
            "s.status": "complete",
            "s.started_at": "2024-01-01T00:00:00",
        }

        extract_events_mock = MagicMock(return_value=[])
        extract_transcript_mock = MagicMock(return_value=[{"role": "user"}])
        extract_metadata_mock = MagicMock(return_value={})

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([mock_session], []),
            ),
            patch(
                "context_intelligence.reconstruct.events.extract_events",
                extract_events_mock,
            ),
            patch(
                "context_intelligence.reconstruct.transcript.extract_transcript",
                extract_transcript_mock,
            ),
            patch(
                "context_intelligence.reconstruct.metadata.extract_metadata",
                extract_metadata_mock,
            ),
        ):
            args = _make_args(transcript_only=True, dry_run=True)
            try:
                module.cmd_reconstruct(args)
            except SystemExit:
                pass

        extract_events_mock.assert_not_called()
        extract_transcript_mock.assert_called_once()
        extract_metadata_mock.assert_not_called()

    def test_metadata_only_calls_extract_metadata_not_others(self):
        """With --metadata-only, only extract_metadata should be called."""
        module = _load_script_module()

        mock_session = {
            "s.node_id": "abc123def456",
            "s.status": "complete",
            "s.started_at": "2024-01-01T00:00:00",
        }

        extract_events_mock = MagicMock(return_value=[])
        extract_transcript_mock = MagicMock(return_value=[])
        extract_metadata_mock = MagicMock(return_value={"session_id": "abc123"})

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([mock_session], []),
            ),
            patch(
                "context_intelligence.reconstruct.events.extract_events",
                extract_events_mock,
            ),
            patch(
                "context_intelligence.reconstruct.transcript.extract_transcript",
                extract_transcript_mock,
            ),
            patch(
                "context_intelligence.reconstruct.metadata.extract_metadata",
                extract_metadata_mock,
            ),
        ):
            args = _make_args(metadata_only=True, dry_run=True)
            try:
                module.cmd_reconstruct(args)
            except SystemExit:
                pass

        extract_events_mock.assert_not_called()
        extract_transcript_mock.assert_not_called()
        extract_metadata_mock.assert_called_once()

    def test_no_only_flags_calls_all_three(self):
        """With no --*-only flags, all three extractors should be called."""
        module = _load_script_module()

        mock_session = {
            "s.node_id": "abc123def456",
            "s.status": "complete",
            "s.started_at": "2024-01-01T00:00:00",
        }

        extract_events_mock = MagicMock(return_value=[{"event": "test"}])
        extract_transcript_mock = MagicMock(return_value=[{"role": "user"}])
        extract_metadata_mock = MagicMock(return_value={"session_id": "abc123"})

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([mock_session], []),
            ),
            patch(
                "context_intelligence.reconstruct.events.extract_events",
                extract_events_mock,
            ),
            patch(
                "context_intelligence.reconstruct.transcript.extract_transcript",
                extract_transcript_mock,
            ),
            patch(
                "context_intelligence.reconstruct.metadata.extract_metadata",
                extract_metadata_mock,
            ),
        ):
            args = _make_args(dry_run=True)
            try:
                module.cmd_reconstruct(args)
            except SystemExit:
                pass

        extract_events_mock.assert_called_once()
        extract_transcript_mock.assert_called_once()
        extract_metadata_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Session filtering
# ---------------------------------------------------------------------------


class TestSessionFiltering:
    """cmd_reconstruct must filter sessions by --session prefix."""

    def test_session_filter_limits_to_matching_sessions(self):
        """With --session prefix, only matching sessions are processed."""
        module = _load_script_module()

        sessions = [
            {
                "s.node_id": "abc123def456",
                "s.status": "complete",
                "s.started_at": "2024-01-01T00:00:00",
            },
            {
                "s.node_id": "xyz789000000",
                "s.status": "complete",
                "s.started_at": "2024-01-02T00:00:00",
            },
        ]

        extract_events_mock = MagicMock(return_value=[{"event": "test"}])

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=(sessions, []),
            ),
            patch(
                "context_intelligence.reconstruct.events.extract_events",
                extract_events_mock,
            ),
            patch(
                "context_intelligence.reconstruct.transcript.extract_transcript",
                return_value=[],
            ),
            patch(
                "context_intelligence.reconstruct.metadata.extract_metadata",
                return_value={},
            ),
        ):
            # Filter to only sessions starting with "abc"
            args = _make_args(session="abc", dry_run=True)
            try:
                module.cmd_reconstruct(args)
            except SystemExit:
                pass

        # Should only be called once for the session starting with "abc"
        assert extract_events_mock.call_count == 1, (
            f"Expected 1 call (filtered), got {extract_events_mock.call_count}"
        )
        # Verify the call was for the matching session
        call_args = extract_events_mock.call_args
        assert "abc123def456" in str(call_args), f"Expected call for abc123def456, got {call_args}"


# ---------------------------------------------------------------------------
# Skip-existing logic
# ---------------------------------------------------------------------------


class TestSkipExisting:
    """cmd_reconstruct must skip existing files unless --force is set."""

    def test_skips_events_jsonl_if_exists(self, tmp_path):
        """Skips events.jsonl reconstruction if file already exists."""
        module = _load_script_module()

        # Create a fake workspace slug path
        session_id = "abc123def456abc123def456abc123def456abc12"

        mock_session = {
            "s.node_id": session_id,
            "s.status": "complete",
            "s.started_at": "2024-01-01T00:00:00",
        }

        # We need to intercept sessions_dir_for_project to return our tmp_path
        # and pre-create the events.jsonl file
        fake_session_dir = tmp_path / "sessions" / session_id
        fake_session_dir.mkdir(parents=True)
        events_file = fake_session_dir / "events.jsonl"
        events_file.write_text('{"event":"existing"}\n')

        extract_events_mock = MagicMock(return_value=[{"event": "test"}])

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([mock_session], []),
            ),
            patch(
                "context_intelligence.reconstruct.discover.workspace_slug",
                return_value="test-workspace",
            ),
            patch(
                "context_intelligence.reconstruct.discover.sessions_dir_for_project",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "context_intelligence.reconstruct.events.extract_events",
                extract_events_mock,
            ),
            patch(
                "context_intelligence.reconstruct.transcript.extract_transcript",
                return_value=[],
            ),
            patch(
                "context_intelligence.reconstruct.metadata.extract_metadata",
                return_value={},
            ),
        ):
            args = _make_args(events_only=True)
            try:
                module.cmd_reconstruct(args)
            except SystemExit:
                pass

        # extract_events should NOT be called since file already exists
        extract_events_mock.assert_not_called()

    def test_force_overwrites_existing_events_jsonl(self, tmp_path):
        """With --force, existing events.jsonl is overwritten."""
        module = _load_script_module()

        session_id = "abc123def456abc123def456abc123def456abc12"

        mock_session = {
            "s.node_id": session_id,
            "s.status": "complete",
            "s.started_at": "2024-01-01T00:00:00",
        }

        fake_session_dir = tmp_path / "sessions" / session_id
        fake_session_dir.mkdir(parents=True)
        events_file = fake_session_dir / "events.jsonl"
        events_file.write_text('{"event":"existing"}\n')

        extract_events_mock = MagicMock(return_value=[{"event": "new"}])

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([mock_session], []),
            ),
            patch(
                "context_intelligence.reconstruct.discover.workspace_slug",
                return_value="test-workspace",
            ),
            patch(
                "context_intelligence.reconstruct.discover.sessions_dir_for_project",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "context_intelligence.reconstruct.events.extract_events",
                extract_events_mock,
            ),
            patch(
                "context_intelligence.reconstruct.transcript.extract_transcript",
                return_value=[],
            ),
            patch(
                "context_intelligence.reconstruct.metadata.extract_metadata",
                return_value={},
            ),
        ):
            args = _make_args(events_only=True, force=True)
            try:
                module.cmd_reconstruct(args)
            except SystemExit:
                pass

        # extract_events SHOULD be called because --force is set
        extract_events_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Dry-run support
# ---------------------------------------------------------------------------


class TestDryRun:
    """cmd_reconstruct must not write files in --dry-run mode."""

    def test_dry_run_does_not_write_files(self, tmp_path):
        """With --dry-run, no files are written even when extraction succeeds."""
        module = _load_script_module()

        session_id = "abc123def456abc123def456abc123def456abc12"

        mock_session = {
            "s.node_id": session_id,
            "s.status": "complete",
            "s.started_at": "2024-01-01T00:00:00",
        }

        fake_sessions_dir = tmp_path / "sessions"
        fake_sessions_dir.mkdir(parents=True)

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([mock_session], []),
            ),
            patch(
                "context_intelligence.reconstruct.discover.workspace_slug",
                return_value="test-workspace",
            ),
            patch(
                "context_intelligence.reconstruct.discover.sessions_dir_for_project",
                return_value=fake_sessions_dir,
            ),
            patch(
                "context_intelligence.reconstruct.events.extract_events",
                return_value=[{"event": "test"}],
            ),
            patch(
                "context_intelligence.reconstruct.transcript.extract_transcript",
                return_value=[{"role": "user"}],
            ),
            patch(
                "context_intelligence.reconstruct.metadata.extract_metadata",
                return_value={"session_id": session_id},
            ),
        ):
            args = _make_args(dry_run=True)
            try:
                module.cmd_reconstruct(args)
            except SystemExit:
                pass

        # No files should be written in the session directory
        session_dir = fake_sessions_dir / session_id
        assert not (session_dir / "events.jsonl").exists(), (
            "events.jsonl must not be written in dry-run mode"
        )
        assert not (session_dir / "transcript.jsonl").exists(), (
            "transcript.jsonl must not be written in dry-run mode"
        )
        assert not (session_dir / "metadata.json").exists(), (
            "metadata.json must not be written in dry-run mode"
        )


# ---------------------------------------------------------------------------
# Disk-only session processing
# ---------------------------------------------------------------------------


class TestDiskOnlySessions:
    """cmd_reconstruct must process disk-only sessions using build_disk_only_metadata."""

    def test_disk_only_sessions_call_build_disk_only_metadata(self, tmp_path):
        """Disk-only sessions trigger build_disk_only_metadata for metadata."""
        module = _load_script_module()

        disk_only_id = "disk-only-session-id"
        fake_sessions_dir = tmp_path / "sessions"
        (fake_sessions_dir / disk_only_id).mkdir(parents=True)

        build_disk_only_mock = MagicMock(return_value={"session_id": disk_only_id})

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([], [disk_only_id]),
            ),
            patch(
                "context_intelligence.reconstruct.discover.workspace_slug",
                return_value="test-workspace",
            ),
            patch(
                "context_intelligence.reconstruct.discover.sessions_dir_for_project",
                return_value=fake_sessions_dir,
            ),
            patch(
                "context_intelligence.reconstruct.metadata.build_disk_only_metadata",
                build_disk_only_mock,
            ),
        ):
            args = _make_args(dry_run=True)  # metadata is included (no --*-only flags)
            try:
                module.cmd_reconstruct(args)
            except SystemExit:
                pass

        build_disk_only_mock.assert_called_once()

    def test_disk_only_sessions_skipped_when_metadata_only_false(self, tmp_path):
        """Disk-only sessions are skipped if metadata is not being reconstructed."""
        module = _load_script_module()

        disk_only_id = "disk-only-session-id"
        fake_sessions_dir = tmp_path / "sessions"
        (fake_sessions_dir / disk_only_id).mkdir(parents=True)

        build_disk_only_mock = MagicMock(return_value={"session_id": disk_only_id})

        with (
            patch("context_intelligence.config.resolve_config", return_value=("http://s", "key")),
            patch(
                "context_intelligence.reconstruct.discover.discover_sessions",
                return_value=([], [disk_only_id]),
            ),
            patch(
                "context_intelligence.reconstruct.discover.workspace_slug",
                return_value="test-workspace",
            ),
            patch(
                "context_intelligence.reconstruct.discover.sessions_dir_for_project",
                return_value=fake_sessions_dir,
            ),
            patch(
                "context_intelligence.reconstruct.metadata.build_disk_only_metadata",
                build_disk_only_mock,
            ),
        ):
            # --events-only means metadata is NOT being reconstructed
            args = _make_args(events_only=True, dry_run=True)
            try:
                module.cmd_reconstruct(args)
            except SystemExit:
                pass

        build_disk_only_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Acceptance criteria
# ---------------------------------------------------------------------------


class TestAcceptanceCriteria:
    """Acceptance criteria: reconstruct --help shows required flags."""

    def test_reconstruct_help_shows_project_dir(self):
        """reconstruct --help must show --project-dir flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "reconstruct", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"--help must return 0, got {result.returncode}"
        assert "--project-dir" in result.stdout, "--help must mention --project-dir"

    def test_reconstruct_help_shows_events_only(self):
        """reconstruct --help must show --events-only flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "reconstruct", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--events-only" in result.stdout, "--help must mention --events-only"

    def test_reconstruct_help_shows_force(self):
        """reconstruct --help must show --force flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "reconstruct", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--force" in result.stdout, "--help must mention --force"

    def test_reconstruct_help_shows_dry_run(self):
        """reconstruct --help must show --dry-run flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "reconstruct", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--dry-run" in result.stdout, "--help must mention --dry-run"

    def test_reconstruct_help_shows_resolve_blobs(self):
        """reconstruct --help must show --resolve-blobs flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "reconstruct", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--resolve-blobs" in result.stdout, "--help must mention --resolve-blobs"

    def test_reconstruct_help_shows_session(self):
        """reconstruct --help must show --session flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "reconstruct", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--session" in result.stdout, "--help must mention --session"

    def test_reconstruct_help_shows_verbose(self):
        """reconstruct --help must show --verbose flag."""
        import subprocess

        result = subprocess.run(
            [sys.executable, str(SCRIPT_PATH), "reconstruct", "--help"],
            capture_output=True,
            text=True,
        )
        assert "--verbose" in result.stdout or "-v" in result.stdout, (
            "--help must mention --verbose"
        )
