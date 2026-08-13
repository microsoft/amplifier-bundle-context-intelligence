"""Tests for the shared post-discovery destination filter (Phase 2).

The filter decides which discovered sessions a selected destination should
receive, using the SAME helpers the live hook used at capture time
(``fanout.normalize_match_key`` + ``fanout.destination_is_active``), so
upload-time filtering and capture-time fan-out agree exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_module_tool_context_intelligence_upload.session_filter import (
    default_scan_root,
    resolve_session_working_dir,
)


def test_default_scan_root_is_the_amplifier_projects_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Auto-discovery scans ~/.amplifier/projects (the app-cli project root)."""
    monkeypatch.setenv("HOME", str(tmp_path))

    assert default_scan_root() == tmp_path / ".amplifier" / "projects"


def test_default_scan_root_does_not_require_the_directory_to_exist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It is a pure path computation -- no filesystem side effects."""
    monkeypatch.setenv("HOME", str(tmp_path))

    root = default_scan_root()

    assert not root.exists()


# ---------------------------------------------------------------------------
# Working-dir precedence: the SESSION'S OWN recorded dir wins, never --path
# ---------------------------------------------------------------------------


def test_recorded_working_dir_wins_over_a_backup_path_fallback(tmp_path: Path) -> None:
    """REQUIRED: --path may point at a BACKUP/copy folder that is not where the
    session ran. The recorded working_dir must decide filter matching, so a
    --path value must NOT change the result when metadata has a working_dir.
    """
    metadata = {
        "session_id": "s1",
        "format": "context-intelligence",
        "working_dir": "/Users/me/project",
    }

    resolved = resolve_session_working_dir(
        tmp_path, metadata, path_fallback="/backups/2026-08-13/copy-of-projects"
    )

    assert resolved == "/Users/me/project"


def test_recorded_working_dir_wins_over_the_workspace_slug(tmp_path: Path) -> None:
    """When both are present the exact path beats the lossy slug."""
    metadata = {
        "format": "logging-hook",
        "workspace": "-Users-me-stale-slug",
        "working_dir": "/Users/me/project",
    }

    assert resolve_session_working_dir(tmp_path, metadata, None) == "/Users/me/project"


def test_workspace_slug_is_unslugged_when_no_working_dir_is_recorded(tmp_path: Path) -> None:
    """Deep fallback: a slug-only session gets an approximate path."""
    metadata = {"format": "logging-hook", "workspace": "-Users-me-project"}

    assert resolve_session_working_dir(tmp_path, metadata, None) == "/Users/me/project"


def test_slug_only_session_prefers_its_slug_over_the_path_fallback(tmp_path: Path) -> None:
    """Even an APPROXIMATE session-owned path outranks --path."""
    metadata = {"format": "logging-hook", "workspace": "-Users-me-project"}

    resolved = resolve_session_working_dir(tmp_path, metadata, path_fallback="/backups/copy")

    assert resolved == "/Users/me/project"


@pytest.mark.parametrize("slug", ["", "default", "-"])
def test_non_derivable_slug_falls_through_to_the_path_fallback(tmp_path: Path, slug: str) -> None:
    """A slug carrying no information is not a working dir."""
    metadata = {"format": "logging-hook", "workspace": slug}

    assert resolve_session_working_dir(tmp_path, metadata, "/some/path") == "/some/path"


def test_path_fallback_is_used_only_as_a_last_resort(tmp_path: Path) -> None:
    """No recorded dir and no slug -> the --path value is the only signal left."""
    metadata = {"session_id": "s1", "format": "logging-hook"}

    assert resolve_session_working_dir(tmp_path, metadata, "/some/path") == "/some/path"


def test_returns_none_when_nothing_is_derivable(tmp_path: Path) -> None:
    """No metadata, no path -> None. Callers must NOT drop such a session."""
    assert resolve_session_working_dir(tmp_path, {}, None) is None


@pytest.mark.parametrize("empty", ["", None])
def test_empty_recorded_working_dir_is_treated_as_absent(
    tmp_path: Path, empty: str | None
) -> None:
    """An empty/None working_dir carries no information -- fall through."""
    metadata = {"format": "logging-hook", "working_dir": empty, "workspace": "-Users-me-project"}

    assert resolve_session_working_dir(tmp_path, metadata, None) == "/Users/me/project"
