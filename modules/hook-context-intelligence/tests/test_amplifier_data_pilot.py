"""Tests for the amplifier-data floor pilot (dual-write + regeneration-equivalence).

These tests prove the pilot's two contracts:
  1. Byte-identical regeneration (amplifier-data E1) over real CI event lines.
  2. Fail-safe behavior: when amplifier-data is absent or a write fails, the
     pilot is an inert no-op and never raises into CI's hot path.

The amplifier-data-dependent tests skip cleanly when the dependency is not
installed, so they never break a default CI install.
"""

from __future__ import annotations

import json

import pytest

from amplifier_module_hook_context_intelligence.amplifier_data_pilot import (
    DualWriteStore,
    EquivalenceReport,
    verify_events_jsonl,
)
from amplifier_module_hook_context_intelligence.upload import _canonical_json

amplifier_data = pytest.importorskip(
    "amplifier_data", reason="amplifier-data not installed; floor pilot is optional"
)


def _canonical_line(event: str, data: dict, workspace: str = "ws") -> str:
    record = {
        "event": event,
        "workspace": workspace,
        "timestamp": data.get("timestamp", ""),
        "data": data,
    }
    return _canonical_json(record)


def _sample_lines() -> list[str]:
    return [
        _canonical_line(
            "session:start",
            {"session_id": "s1", "parent_id": "", "timestamp": "2026-06-07T00:00:00.000+00:00"},
        ),
        _canonical_line(
            "tool:pre",
            {
                "session_id": "s1",
                "timestamp": "2026-06-07T00:00:01.000+00:00",
                "tool_name": "bash",
                "tool_call_id": "tc1",
                "tool_input": {"command": "ls -la"},
            },
        ),
        _canonical_line(
            "llm:response",
            {
                "session_id": "s1",
                "timestamp": "2026-06-07T00:00:02.000+00:00",
                "model": "claude",
                "usage": {"input_tokens": 10, "output_tokens": 20},
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Contract 1: byte-identical regeneration
# ---------------------------------------------------------------------------
def test_dualwrite_regenerates_byte_identical() -> None:
    store = DualWriteStore(enabled=True)
    assert store.enabled

    for line in _sample_lines():
        ref = store.record_line(line)
        assert ref is not None

    report = store.verify_recorded()
    assert report.byte_identical is True
    assert report.total == 3
    assert report.matched == 3
    assert report.mismatched == 0
    assert report.errors == 0
    store.close()


def test_content_addressing_dedup() -> None:
    """Identical content yields an identical ref (natural dedup, brief §1)."""
    store = DualWriteStore(enabled=True)
    line = _sample_lines()[0]
    ref_a = store.record_line(line)
    ref_b = store.record_line(line)
    assert ref_a == ref_b

    report = store.verify_recorded()
    assert report.total == 2
    assert report.distinct_refs == 1
    assert report.byte_identical is True
    store.close()


def test_verify_events_jsonl_over_file(tmp_path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(_sample_lines()) + "\n", encoding="utf-8")

    report = verify_events_jsonl(events)
    assert report.byte_identical is True
    assert report.total == 3
    assert report.matched == 3


def test_verify_events_jsonl_accepts_session_dir(tmp_path) -> None:
    (tmp_path / "events.jsonl").write_text("\n".join(_sample_lines()) + "\n", encoding="utf-8")
    report = verify_events_jsonl(tmp_path)  # directory -> events.jsonl
    assert report.total == 3
    assert report.byte_identical is True


def test_verify_events_jsonl_durable_restart(tmp_path) -> None:
    """E1 across a close+reopen: regenerate from the persisted log alone."""
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(_sample_lines()) + "\n", encoding="utf-8")
    store_path = tmp_path / "pilot.store"

    report = verify_events_jsonl(events, store_path=store_path, test_restart=True)
    assert report.byte_identical is True
    assert report.total == 3


def test_real_payload_shape_roundtrips(tmp_path) -> None:
    """A line with nested/unicode content regenerates exactly."""
    data = {
        "session_id": "s2",
        "timestamp": "2026-06-07T00:00:03.000+00:00",
        "prompt": 'héllo — ünicode ☃ "quotes" and \\backslash',
        "nested": {"a": [1, 2, {"b": None, "c": True}]},
    }
    line = _canonical_line("prompt:submit", data)
    events = tmp_path / "events.jsonl"
    events.write_text(line + "\n", encoding="utf-8")

    report = verify_events_jsonl(events)
    assert report.byte_identical is True
    # sanity: the canonical line is valid JSON round-trippable
    assert json.loads(line)["data"]["prompt"].startswith("héllo")


# ---------------------------------------------------------------------------
# Contract 2: fail-safe no-op
# ---------------------------------------------------------------------------
def test_disabled_store_is_noop() -> None:
    store = DualWriteStore(enabled=False)
    assert store.enabled is False
    assert store.record_line(_sample_lines()[0]) is None
    report = store.verify_recorded()
    assert isinstance(report, EquivalenceReport)
    assert report.total == 0
    assert report.byte_identical is False  # nothing recorded
    store.close()  # must not raise


def test_record_after_write_failure_disables_without_raising(monkeypatch) -> None:
    store = DualWriteStore(enabled=True)
    assert store.enabled

    def boom(_payload):
        raise RuntimeError("simulated store failure")

    monkeypatch.setattr(store._store, "write_cell", boom)
    # Must swallow the error, disable the pilot, and never raise.
    assert store.record_line(_sample_lines()[0]) is None
    assert store.enabled is False
    store.close()
