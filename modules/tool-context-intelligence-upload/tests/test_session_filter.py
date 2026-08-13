"""Tests for the shared post-discovery destination filter (Phase 2).

The filter decides which discovered sessions a selected destination should
receive, using the SAME helpers the live hook used at capture time
(``fanout.normalize_match_key`` + ``fanout.destination_is_active``), so
upload-time filtering and capture-time fan-out agree exactly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_module_hook_context_intelligence.config_resolver import Destination
from amplifier_module_hook_context_intelligence.fanout import (
    destination_is_active,
    normalize_match_key,
)

from amplifier_module_tool_context_intelligence_upload.session_filter import (
    default_scan_root,
    filter_sessions,
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
def test_empty_recorded_working_dir_is_treated_as_absent(tmp_path: Path, empty: str | None) -> None:
    """An empty/None working_dir carries no information -- fall through."""
    metadata = {"format": "logging-hook", "working_dir": empty, "workspace": "-Users-me-project"}

    assert resolve_session_working_dir(tmp_path, metadata, None) == "/Users/me/project"


# ---------------------------------------------------------------------------
# filter_sessions: exact parity with the hook's capture-time decision
# ---------------------------------------------------------------------------

TEAM_DEST = Destination(
    name="team",
    url="https://ci.example.test",
    api_key="secret",
    include=("/Users/me/**",),
    exclude=("**/client-secret/**",),
)


def test_included_session_is_kept_and_excluded_session_is_dropped(tmp_path: Path) -> None:
    included = (tmp_path / "a", {"working_dir": "/Users/me/project"})
    excluded = (tmp_path / "b", {"working_dir": "/Users/me/client-secret/app"})

    kept, filtered_out = filter_sessions([included, excluded], TEAM_DEST, None)

    assert kept == [included]
    assert filtered_out == 1


def test_filter_decision_matches_the_hook_oracle_exactly(tmp_path: Path) -> None:
    """Parity check: the oracle is the hook's own destination_is_active, called
    directly here -- NOT a re-implementation of the matching rules.
    """
    working_dirs = [
        "/Users/me/project",
        "/Users/me/client-secret/app",
        "/opt/other",
        "/Users/me/nested/deep/repo",
    ]
    sessions = [(tmp_path / f"s{i}", {"working_dir": wd}) for i, wd in enumerate(working_dirs)]

    kept, filtered_out = filter_sessions(sessions, TEAM_DEST, None)

    expected_kept = [
        session
        for session in sessions
        if destination_is_active(TEAM_DEST, normalize_match_key(session[1]["working_dir"]))
    ]
    assert kept == expected_kept
    assert filtered_out == len(sessions) - len(expected_kept)


def test_filtered_out_count_is_the_number_of_dropped_sessions(tmp_path: Path) -> None:
    sessions = [
        (tmp_path / "a", {"working_dir": "/Users/me/project"}),
        (tmp_path / "b", {"working_dir": "/opt/one"}),
        (tmp_path / "c", {"working_dir": "/opt/two"}),
        (tmp_path / "d", {"working_dir": "/opt/three"}),
    ]

    kept, filtered_out = filter_sessions(sessions, TEAM_DEST, None)

    assert len(kept) == 1
    assert filtered_out == 3


def test_empty_input_yields_empty_output(tmp_path: Path) -> None:
    assert filter_sessions([], TEAM_DEST, None) == ([], 0)


def test_kept_sessions_preserve_input_order_and_identity(tmp_path: Path) -> None:
    """The filter selects; it never reorders or rebuilds tuples."""
    sessions = [
        (tmp_path / "first", {"working_dir": "/Users/me/first"}),
        (tmp_path / "skipme", {"working_dir": "/opt/nope"}),
        (tmp_path / "second", {"working_dir": "/Users/me/second"}),
    ]

    kept, _ = filter_sessions(sessions, TEAM_DEST, None)

    assert kept == [sessions[0], sessions[2]]
    assert kept[0][1] is sessions[0][1]


def test_a_destination_with_no_include_patterns_matches_nothing(tmp_path: Path) -> None:
    """Hook semantics (config_resolver.py:101-103): empty include matches nothing."""
    dest = Destination(name="d", url="https://x.test", api_key="k")
    sessions = [(tmp_path / "a", {"working_dir": "/Users/me/project"})]

    kept, filtered_out = filter_sessions(sessions, dest, None)

    assert kept == []
    assert filtered_out == 1


# ---------------------------------------------------------------------------
# Never silently drop; treat CI-native and legacy sessions uniformly
# ---------------------------------------------------------------------------


def test_session_with_no_derivable_working_dir_is_included_not_dropped(tmp_path: Path) -> None:
    """An undecidable session is surfaced, never silently discarded."""
    undecidable = (tmp_path / "mystery", {"session_id": "s1", "format": "logging-hook"})

    kept, filtered_out = filter_sessions([undecidable], TEAM_DEST, None)

    assert kept == [undecidable]
    assert filtered_out == 0


def test_undecidable_sessions_are_included_alongside_matched_ones(tmp_path: Path) -> None:
    matched = (tmp_path / "a", {"working_dir": "/Users/me/project"})
    undecidable = (tmp_path / "b", {})
    excluded = (tmp_path / "c", {"working_dir": "/opt/other"})

    kept, filtered_out = filter_sessions([matched, undecidable, excluded], TEAM_DEST, None)

    assert kept == [matched, undecidable]
    assert filtered_out == 1


def test_ci_native_and_legacy_sessions_are_filtered_by_the_same_rule(tmp_path: Path) -> None:
    """Format is irrelevant to the decision -- only the resolved working dir
    matters, whether it came from a recorded path or an unslugged slug.
    """
    ci_kept = (
        tmp_path / "ci-in",
        {"format": "context-intelligence", "working_dir": "/Users/me/project"},
    )
    ci_dropped = (
        tmp_path / "ci-out",
        {"format": "context-intelligence", "working_dir": "/opt/other"},
    )
    legacy_kept = (
        tmp_path / "legacy-in",
        {"format": "logging-hook", "workspace": "-Users-me-project"},
    )
    legacy_dropped = (
        tmp_path / "legacy-out",
        {"format": "logging-hook", "workspace": "-opt-other"},
    )

    kept, filtered_out = filter_sessions(
        [ci_kept, ci_dropped, legacy_kept, legacy_dropped], TEAM_DEST, None
    )

    assert kept == [ci_kept, legacy_kept]
    assert filtered_out == 2


def test_a_backup_path_fallback_never_overrides_recorded_dirs_in_a_mixed_list(
    tmp_path: Path,
) -> None:
    """End-to-end of the D decision: pass a --path pointing at a backup folder
    that the destination WOULD match, and confirm it changes nothing for
    sessions that recorded their own working dir.
    """
    backup_path = "/Users/me/backups-of-everything"
    assert destination_is_active(TEAM_DEST, normalize_match_key(backup_path)) is True

    recorded_excluded = (tmp_path / "a", {"working_dir": "/opt/other"})
    slug_excluded = (tmp_path / "b", {"workspace": "-opt-other"})

    kept, filtered_out = filter_sessions([recorded_excluded, slug_excluded], TEAM_DEST, backup_path)

    assert kept == []
    assert filtered_out == 2
