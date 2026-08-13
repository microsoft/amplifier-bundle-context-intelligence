"""Tests for the shared post-discovery destination filter (Phase 2).

The filter decides which discovered sessions a selected destination should
receive, using the SAME helpers the live hook used at capture time
(``fanout.normalize_match_key`` + ``fanout.destination_is_active``), so
upload-time filtering and capture-time fan-out agree exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_module_tool_context_intelligence_upload.session_filter import default_scan_root


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
