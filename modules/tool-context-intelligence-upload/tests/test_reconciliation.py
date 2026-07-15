"""Tests for reconciliation.py — independent-counts-only summary.

No algebraically-dead field: `already_present` is removed because it was
identically 0 by construction (read = ingested + skipped was always true
at the call site), and `unmapped` must be a real, independently-measured
count, not a hardcoded constant.
"""

from __future__ import annotations

from amplifier_module_tool_context_intelligence_upload.reconciliation import (
    reconciliation_summary,
)


def test_summary_all_ingested() -> None:
    line = reconciliation_summary(read=10, ingested=10, skipped=0, unmapped=0)
    assert line == "10 read, 10 ingested, 0 skipped, 0 unmapped, 0 live-sessions-skipped"


def test_summary_with_skips_and_unmapped() -> None:
    line = reconciliation_summary(read=10, ingested=6, skipped=3, unmapped=1)
    assert line == "10 read, 6 ingested, 3 skipped, 1 unmapped, 0 live-sessions-skipped"


def test_summary_reports_live_sessions_skipped() -> None:
    line = reconciliation_summary(
        read=10, ingested=6, skipped=3, unmapped=1, live_sessions_skipped=2
    )
    assert "2 live-sessions-skipped" in line


def test_summary_has_no_already_present_field() -> None:
    line = reconciliation_summary(read=10, ingested=10, skipped=0, unmapped=0)
    assert "already-present" not in line
