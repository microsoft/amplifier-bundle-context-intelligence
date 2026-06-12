"""Tests T45–T53, T66: migrate.py + cli.py — hermetic integration tests."""

from __future__ import annotations

import tarfile
import time
import os
from pathlib import Path
from unittest.mock import MagicMock, patch


from amplifier_module_tool_context_intelligence_migrate.ledger import read_ledger
from amplifier_module_tool_context_intelligence_migrate.migrate import (
    archive_originals,
    run_migration,
)

from .conftest import build_pre_ci_session


# ---------------------------------------------------------------------------
# Helpers — mock factories
# ---------------------------------------------------------------------------


def _make_upload_result(success: bool = True) -> MagicMock:
    result = MagicMock()
    result.success = success
    return result


def _make_verify_result(passed: bool = True, count: int = 1) -> MagicMock:
    result = MagicMock()
    result.passed = passed
    result.event_count_graph = count
    result.message = "" if passed else "Gate A failed: count mismatch"
    return result


# ---------------------------------------------------------------------------
# T45: archive_originals_creates_tar
# ---------------------------------------------------------------------------


def test_archive_originals_creates_tar(tmp_path: Path) -> None:
    """T45: archive_originals creates a tar containing events.jsonl."""
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    (session_dir / "events.jsonl").write_text('{"event":"tool:pre"}\n', encoding="utf-8")
    (session_dir / "transcript.jsonl").write_text("{}", encoding="utf-8")

    archive_dir = tmp_path / "archive"
    tar_path = archive_originals(
        session_dir,
        archive_dir,
        project_slug="my-proj",
        session_id="s1",
    )

    assert tar_path.exists()
    with tarfile.open(tar_path) as tf:
        names = tf.getnames()
    assert "events.jsonl" in names
    # transcript.jsonl must NOT be in the archive
    assert "transcript.jsonl" not in names


# ---------------------------------------------------------------------------
# T46: archive_originals_never_archives_transcript
# ---------------------------------------------------------------------------


def test_archive_originals_never_archives_transcript(tmp_path: Path) -> None:
    """T46: archive_originals never touches transcript.jsonl, metadata.json, config.md."""
    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    (session_dir / "events.jsonl").write_text('{"event":"tool:pre"}\n', encoding="utf-8")
    (session_dir / "transcript.jsonl").write_text("{}", encoding="utf-8")
    (session_dir / "metadata.json").write_text("{}", encoding="utf-8")
    (session_dir / "config.md").write_text("# config", encoding="utf-8")

    tar_path = archive_originals(
        session_dir,
        tmp_path / "archive",
        project_slug="proj",
        session_id="s1",
    )

    with tarfile.open(tar_path) as tf:
        names = tf.getnames()
    assert "transcript.jsonl" not in names
    assert "metadata.json" not in names
    assert "config.md" not in names


# ---------------------------------------------------------------------------
# T47: run_migration_dry_run_returns_no_deletions
# ---------------------------------------------------------------------------


def test_run_migration_dry_run_returns_no_deletions(tmp_path: Path) -> None:
    """T47: dry_run=True returns a report with no deletions and no file changes."""
    build_pre_ci_session(tmp_path, project_slug="p", session_id="s1")

    with (
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.preflight",
            return_value=MagicMock(ok=True),
        ),
    ):
        report = run_migration(
            projects_root=tmp_path,
            server_url="http://mock:1234",
            api_key="key",
            dry_run=True,
            safety_window_hours=0.0,
            ledger_path=tmp_path / "ledger.jsonl",
            archive_dir=tmp_path / "archive",
        )

    assert report.deleted == 0
    # Legacy events.jsonl should still exist
    legacy = tmp_path / "p" / "sessions" / "s1" / "events.jsonl"
    assert legacy.exists()


# ---------------------------------------------------------------------------
# T48: run_migration_preflight_fail_aborts
# ---------------------------------------------------------------------------


def test_run_migration_preflight_fail_aborts(tmp_path: Path) -> None:
    """T48: run_migration returns immediately when preflight fails."""
    with patch(
        "amplifier_module_tool_context_intelligence_migrate.migrate.preflight",
        return_value=MagicMock(ok=False, reason="server down"),
    ):
        report = run_migration(
            projects_root=tmp_path,
            server_url="http://mock:1234",
            api_key="key",
            dry_run=False,
            safety_window_hours=0.0,
            ledger_path=tmp_path / "ledger.jsonl",
            archive_dir=tmp_path / "archive",
            assume_yes=True,
        )

    assert report.deleted == 0
    assert report.failed == 0  # nothing was even attempted


# ---------------------------------------------------------------------------
# T49: run_migration_deletes_legacy_on_success
# ---------------------------------------------------------------------------


def test_run_migration_deletes_legacy_on_success(tmp_path: Path) -> None:
    """T49: Legacy events.jsonl is deleted after all gates pass."""
    session_dir = build_pre_ci_session(tmp_path, project_slug="p", session_id="s1")
    legacy_path = session_dir / "events.jsonl"
    # Backdate so it's not "live"
    old_mtime = time.time() - 48 * 3600
    os.utime(legacy_path, (old_mtime, old_mtime))

    upload_result = _make_upload_result(success=True)
    verify_result = _make_verify_result(passed=True, count=2)  # 2 events in fixture

    with (
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.preflight",
            return_value=MagicMock(ok=True),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.run_upload",
            return_value=upload_result,
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.verify_session",
            return_value=verify_result,
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.discover_and_sort",
            return_value=[],
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.ProgressTracker",
            return_value=MagicMock(),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.progress_file_path",
            return_value=tmp_path / "prog.json",
        ),
    ):
        report = run_migration(
            projects_root=tmp_path,
            server_url="http://mock:1234",
            api_key="key",
            dry_run=False,
            safety_window_hours=0.0,
            ledger_path=tmp_path / "ledger.jsonl",
            archive_dir=tmp_path / "archive",
            assume_yes=True,
        )

    assert not legacy_path.exists(), "Legacy events.jsonl should have been deleted"
    assert report.deleted >= 1


# ---------------------------------------------------------------------------
# T50: run_migration_no_delete_if_verify_fails
# ---------------------------------------------------------------------------


def test_run_migration_no_delete_if_verify_fails(tmp_path: Path) -> None:
    """T50: Legacy events.jsonl is NOT deleted when verify fails."""
    session_dir = build_pre_ci_session(tmp_path, project_slug="p", session_id="s2")
    legacy_path = session_dir / "events.jsonl"
    old_mtime = time.time() - 48 * 3600
    os.utime(legacy_path, (old_mtime, old_mtime))

    verify_result = _make_verify_result(passed=False, count=0)

    with (
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.preflight",
            return_value=MagicMock(ok=True),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.run_upload",
            return_value=_make_upload_result(success=True),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.verify_session",
            return_value=verify_result,
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.discover_and_sort",
            return_value=[],
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.ProgressTracker",
            return_value=MagicMock(),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.progress_file_path",
            return_value=tmp_path / "prog.json",
        ),
    ):
        report = run_migration(
            projects_root=tmp_path,
            server_url="http://mock:1234",
            api_key="key",
            dry_run=False,
            safety_window_hours=0.0,
            ledger_path=tmp_path / "ledger.jsonl",
            archive_dir=tmp_path / "archive",
            assume_yes=True,
        )

    assert legacy_path.exists(), "Legacy events.jsonl must NOT be deleted when verify fails"
    assert report.failed >= 1


# ---------------------------------------------------------------------------
# T51: run_migration_idempotent_skips_complete
# ---------------------------------------------------------------------------


def test_run_migration_idempotent_skips_complete(tmp_path: Path) -> None:
    """T51: Second run skips sessions already marked complete in the ledger."""
    session_dir = build_pre_ci_session(tmp_path, project_slug="p", session_id="s3")
    legacy_path = session_dir / "events.jsonl"
    old_mtime = time.time() - 48 * 3600
    os.utime(legacy_path, (old_mtime, old_mtime))

    # Pre-populate ledger with a "deleted" entry
    from amplifier_module_tool_context_intelligence_migrate.ledger import append_entry
    from datetime import datetime, timezone

    ledger_path = tmp_path / "ledger.jsonl"
    append_entry(
        ledger_path,
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": "s3",
            "project_slug": "p",
            "bucket": "pre_ci",
            "phase": "deleted",
            "workspace": "",
            "jsonl_lines": None,
            "graph_count": None,
            "archive_path": None,
            "error": None,
        },
    )

    with (
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.preflight",
            return_value=MagicMock(ok=True),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.run_upload",
        ) as mock_upload,
    ):
        report = run_migration(
            projects_root=tmp_path,
            server_url="http://mock:1234",
            api_key="key",
            dry_run=False,
            safety_window_hours=0.0,
            ledger_path=ledger_path,
            archive_dir=tmp_path / "archive",
            assume_yes=True,
        )

    # Upload should NOT have been called (session was already complete)
    mock_upload.assert_not_called()
    assert report.skipped >= 1


# ---------------------------------------------------------------------------
# T52: run_migration_writes_ledger_entries
# ---------------------------------------------------------------------------


def test_run_migration_writes_ledger_entries(tmp_path: Path) -> None:
    """T52: run_migration writes ledger entries for each phase."""
    session_dir = build_pre_ci_session(tmp_path, project_slug="p", session_id="s4")
    legacy_path = session_dir / "events.jsonl"
    old_mtime = time.time() - 48 * 3600
    os.utime(legacy_path, (old_mtime, old_mtime))

    ledger_path = tmp_path / "ledger.jsonl"

    with (
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.preflight",
            return_value=MagicMock(ok=True),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.run_upload",
            return_value=_make_upload_result(success=True),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.verify_session",
            return_value=_make_verify_result(passed=True, count=2),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.discover_and_sort",
            return_value=[],
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.ProgressTracker",
            return_value=MagicMock(),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.progress_file_path",
            return_value=tmp_path / "prog.json",
        ),
    ):
        run_migration(
            projects_root=tmp_path,
            server_url="http://mock:1234",
            api_key="key",
            dry_run=False,
            safety_window_hours=0.0,
            ledger_path=ledger_path,
            archive_dir=tmp_path / "archive",
            assume_yes=True,
        )

    entries = read_ledger(ledger_path)
    assert len(entries) >= 1
    phases = [e["phase"] for e in entries if e["session_id"] == "s4"]
    assert len(phases) >= 1  # at least one phase was recorded


# ---------------------------------------------------------------------------
# T53: cli_entry_point_exists
# ---------------------------------------------------------------------------


def test_cli_entry_point_exists() -> None:
    """T53: context-intelligence-migrate CLI entry point is importable."""
    from amplifier_module_tool_context_intelligence_migrate.cli import main

    assert callable(main)


# ---------------------------------------------------------------------------
# T66: no 'deleted' ledger entry when unlink does not happen
# ---------------------------------------------------------------------------


def test_run_migration_no_deleted_ledger_when_file_absent_at_step_5f(
    tmp_path: Path,
) -> None:
    """T66: No 'deleted' ledger entry is written when legacy file is gone before step 5f.

    Simulates the race-condition / duplicate-run scenario: the legacy file is
    deleted externally (here: via an is_content_superset side-effect) after
    transform + archive + upload + verify succeed, but before step 5f runs.
    After the fix, step 5f sees legacy_events.exists() == False, sets
    deleted_file=False, and skips the 'deleted' ledger entry entirely.
    """
    session_dir = build_pre_ci_session(tmp_path, project_slug="p", session_id="s_absent")
    legacy_path = session_dir / "events.jsonl"
    old_mtime = time.time() - 48 * 3600
    os.utime(legacy_path, (old_mtime, old_mtime))

    upload_result = _make_upload_result(success=True)
    verify_result = _make_verify_result(passed=True, count=2)
    ledger_path = tmp_path / "ledger.jsonl"

    def superset_side_effect(legacy: Path, ci: Path) -> bool:
        """Simulate external deletion that occurs after superset check succeeds."""
        if legacy.exists():
            legacy.unlink()
        return True  # superset check passes

    with (
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.preflight",
            return_value=MagicMock(ok=True),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.run_upload",
            return_value=upload_result,
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.verify_session",
            return_value=verify_result,
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.is_content_superset",
            side_effect=superset_side_effect,
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.discover_and_sort",
            return_value=[],
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.ProgressTracker",
            return_value=MagicMock(),
        ),
        patch(
            "amplifier_module_tool_context_intelligence_migrate.migrate.progress_file_path",
            return_value=tmp_path / "prog.json",
        ),
    ):
        run_migration(
            projects_root=tmp_path,
            server_url="http://mock:1234",
            api_key="key",
            dry_run=False,
            safety_window_hours=0.0,
            ledger_path=ledger_path,
            archive_dir=tmp_path / "archive",
            assume_yes=True,
        )

    entries = read_ledger(ledger_path)
    phases = [e["phase"] for e in entries if e["session_id"] == "s_absent"]
    assert "deleted" not in phases, (
        f"'deleted' phase must NOT be written when unlink did not happen; got phases={phases}"
    )
