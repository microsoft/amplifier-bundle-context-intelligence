"""Smoke tests for the legacy hooks-logging transform module.

Confirms the module moved verbatim from amplifier-ci-migrate still imports
cleanly and round-trips a single legacy record correctly in its new home.
"""

from __future__ import annotations

import json

import pytest
from amplifier_module_hook_context_intelligence.config_resolver import (
    _slugify_path as _hook_slugify_path,
)

from amplifier_module_tool_context_intelligence_upload.legacy_transform import (
    LegacyEventError,
    MissingTimestampError,
    SchemaVersionError,
    WorkspaceDerivationError,
    _reassemble_data,
    _slugify_path,
    _transform_line,
    assert_supported_schema,
    assert_timestamp_present,
    derive_workspace,
    reassemble_event_data,
)

from ._legacy_fixtures import make_legacy_record

# Re-export symbols so linters don't flag unused imports required by the spec's
# import-surface smoke check.
__all__ = ["SchemaVersionError", "WorkspaceDerivationError", "_reassemble_data", "_slugify_path"]


def test_import_and_reassemble_smoke() -> None:
    rec = make_legacy_record(event="tool:pre", session_id="s1")
    event, data = reassemble_event_data(rec)
    assert event == "tool:pre"
    assert data["session_id"] == "s1"


def test_reassemble_restores_promoted_keys() -> None:
    rec = make_legacy_record(session_id="abc", status="ok")
    rec["data"] = {"tool_name": "bash"}
    data = _reassemble_data(rec)
    assert data["session_id"] == "abc" and data["status"] == "ok"


def test_reassemble_drops_logging_artifacts() -> None:
    rec = make_legacy_record(event="tool:pre")
    data = _reassemble_data(rec)
    assert "lvl" not in data
    assert "schema" not in data
    assert "event" not in data


def test_reassemble_error_key() -> None:
    rec = make_legacy_record()
    rec["error"] = "some error"
    data = _reassemble_data(rec)
    assert data["error"] == "some error"


def test_reassemble_maps_ts_to_data_timestamp() -> None:
    rec = make_legacy_record(ts="2026-03-18T00:00:00.123+00:00")
    _event, data = reassemble_event_data(rec)
    assert "timestamp" in data
    assert data["timestamp"] == "2026-03-18T00:00:00.123+00:00"


def test_reassemble_timestamp_key_present_even_without_ts() -> None:
    rec = make_legacy_record()
    del rec["ts"]
    _event, data = reassemble_event_data(rec)
    assert "timestamp" in data
    assert data["timestamp"] == ""


def test_assert_timestamp_present_raises_on_empty() -> None:
    with pytest.raises(MissingTimestampError):
        assert_timestamp_present({"timestamp": ""})


def test_assert_timestamp_present_ok_when_populated() -> None:
    assert_timestamp_present({"timestamp": "2026-03-18T00:00:00Z"})


def test_reassemble_missing_event_raises_legacy_event_error() -> None:
    rec = make_legacy_record()
    del rec["event"]
    with pytest.raises(LegacyEventError):
        reassemble_event_data(rec)


# ---------------------------------------------------------------------------
# Schema guard (DECISION D1): tolerate 1.x drift, fail loud on unknown
# schema name or unknown MAJOR version.
# ---------------------------------------------------------------------------


def test_schema_guard_raises_on_unknown_major() -> None:
    rec = make_legacy_record()
    rec["schema"] = {"name": "amplifier.log", "ver": "2.0.0"}
    with pytest.raises(SchemaVersionError):
        assert_supported_schema(rec)


def test_schema_guard_raises_on_unknown_name() -> None:
    rec = make_legacy_record()
    rec["schema"] = {"name": "something.else", "ver": "1.0.0"}
    with pytest.raises(SchemaVersionError):
        assert_supported_schema(rec)


def test_schema_guard_tolerates_patch_bump_with_warning(capsys: pytest.CaptureFixture[str]) -> None:
    rec = make_legacy_record()
    rec["schema"] = {"name": "amplifier.log", "ver": "1.0.1"}
    assert_supported_schema(rec)
    captured = capsys.readouterr()
    assert "drift" in captured.err


def test_schema_guard_tolerates_minor_bump_with_warning(capsys: pytest.CaptureFixture[str]) -> None:
    rec = make_legacy_record()
    rec["schema"] = {"name": "amplifier.log", "ver": "1.2.0"}
    assert_supported_schema(rec)
    captured = capsys.readouterr()
    assert "drift" in captured.err


def test_schema_guard_tolerates_extra_key_with_warning(capsys: pytest.CaptureFixture[str]) -> None:
    rec = make_legacy_record()
    rec["schema"] = {"name": "amplifier.log", "ver": "1.0.0", "extra": "field"}
    assert_supported_schema(rec)
    captured = capsys.readouterr()
    assert "drift" in captured.err


def test_schema_guard_raises_on_missing_schema() -> None:
    rec = make_legacy_record()
    del rec["schema"]
    with pytest.raises(SchemaVersionError):
        assert_supported_schema(rec)


def test_schema_guard_passes_clean_baseline_silently(capsys: pytest.CaptureFixture[str]) -> None:
    rec = make_legacy_record()
    assert_supported_schema(rec)
    captured = capsys.readouterr()
    assert "drift" not in captured.err

    line = json.dumps(rec)
    result = _transform_line(line, workspace="ws")
    assert result


# ---------------------------------------------------------------------------
# _slugify_path: EXACT parity with the live CI hook's own slugifier,
# ``amplifier_module_hook_context_intelligence.config_resolver._slugify_path``
# (which itself delegates to ``context_intelligence.reconstruct.discover.
# workspace_slug`` and then applies Windows normalisation + a leading-dash /
# empty-input fallback). The hook is the ORACLE: legacy_transform must produce
# byte-identical output to it for every input, including Windows-origin paths
# and empty input -- there is no separate "correct" derivation, only "matches
# the hook or doesn't". Ground-truth parity against real hook-written data is
# proven in test_ground_truth_parity.py.
# ---------------------------------------------------------------------------


def test_slugify_absolute_posix_unchanged() -> None:
    assert _slugify_path("/Users/me/project") == "-Users-me-project"


def test_slugify_strips_trailing_slash() -> None:
    assert _slugify_path("/Users/me/project/") == "-Users-me-project"


def test_slugify_hyphenated_segments_are_not_escaped() -> None:
    # The CI hook does NOT escape literal hyphens; a hyphenated path segment
    # keeps its single hyphens. (Previously escaped to "--", which forked
    # migrated data into a different workspace than the hook writes.)
    assert (
        _slugify_path("/mnt/ws/context-intelligence-pr-24") == "-mnt-ws-context-intelligence-pr-24"
    )


def test_slug_matches_hook_for_windows_path() -> None:
    """Regression test for the Windows-origin divergence (commit befaea0).

    The live hook's ``config_resolver._slugify_path`` normalises backslashes
    and drive-letter colons; the old ``legacy_transform._slugify_path``
    instead rejected any non-POSIX-absolute path outright, so migrated
    Windows sessions were silently skipped instead of landing where the hook
    would put them. This asserts exact parity, using the hook function itself
    as the oracle (never a hardcoded expected string).
    """
    windows_path = "C:\\Users\\me\\project"
    assert derive_workspace(windows_path) == _hook_slugify_path(windows_path)


@pytest.mark.parametrize(
    "raw_path",
    [
        "/mnt/x/y",  # POSIX absolute
        "C:\\a\\b",  # Windows absolute (backslashes)
        "D:/a/b",  # Windows absolute, forward slashes (mixed style)
        "/mnt/ws/context-intelligence-pr-24",  # hyphenated segment
    ],
)
def test_slug_matches_hook_across_inputs(raw_path: str) -> None:
    """derive_workspace must equal the hook's own slugifier for every input.

    The hook function is the oracle -- expected values are never hardcoded,
    only compared against ``config_resolver._slugify_path`` directly, so this
    test cannot pass by coincidence and cannot drift from the hook.
    """
    assert derive_workspace(raw_path) == _hook_slugify_path(raw_path)


def test_slugify_root_matches_hook() -> None:
    # The hook does NOT reject a root path -- workspace_slug("/") == "-", and
    # the hook returns it as-is (truthy, already dash-prefixed). No raise.
    assert _slugify_path("/") == _hook_slugify_path("/")


def test_derive_workspace_empty_matches_hook_default() -> None:
    # The hook returns its default project slug for empty input rather than
    # failing -- match that exactly instead of raising.
    assert derive_workspace("") == _hook_slugify_path("")


def test_derive_workspace_relative_matches_hook() -> None:
    # The hook resolves relative paths via os.path.abspath (cwd-dependent) --
    # it does not reject them. Compare against the oracle rather than a
    # hardcoded string so the test is correct regardless of cwd.
    assert derive_workspace("me/project") == _hook_slugify_path("me/project")
