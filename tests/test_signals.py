"""Tests for context_intelligence/signals.py — import, dataclass, and _iter_events tests.

Three test classes:
- TestImport: verifies the module and all public symbols are importable
- TestDataclasses: verifies S4aResult, S4bResult, and SignalScores dataclass behavior
- TestIterEvents: verifies _iter_events() generator against fixture and edge cases
"""

from __future__ import annotations

import pathlib

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


class TestImport:
    """Verify the signals module and all its public symbols are importable."""

    def test_module_importable(self):
        """context_intelligence.signals must import without errors."""
        import context_intelligence.signals  # noqa: F401

    def test_score_session_importable(self):
        from context_intelligence.signals import score_session  # noqa: F401

    def test_score_s1_importable(self):
        from context_intelligence.signals import score_s1  # noqa: F401

    def test_score_s1_burst_importable(self):
        from context_intelligence.signals import score_s1_burst  # noqa: F401

    def test_score_s2_importable(self):
        from context_intelligence.signals import score_s2  # noqa: F401

    def test_score_s3_importable(self):
        from context_intelligence.signals import score_s3  # noqa: F401

    def test_score_s4a_importable(self):
        from context_intelligence.signals import score_s4a  # noqa: F401

    def test_score_s4b_importable(self):
        from context_intelligence.signals import score_s4b  # noqa: F401

    def test_score_s4c_importable(self):
        from context_intelligence.signals import score_s4c  # noqa: F401

    def test_score_s4d_importable(self):
        from context_intelligence.signals import score_s4d  # noqa: F401

    def test_score_s5_importable(self):
        from context_intelligence.signals import score_s5  # noqa: F401

    def test_score_s6_importable(self):
        from context_intelligence.signals import score_s6  # noqa: F401

    def test_score_s7_importable(self):
        from context_intelligence.signals import score_s7  # noqa: F401

    def test_score_s8_importable(self):
        from context_intelligence.signals import score_s8  # noqa: F401

    def test_score_s9a_importable(self):
        from context_intelligence.signals import score_s9a  # noqa: F401

    def test_score_s9b_importable(self):
        from context_intelligence.signals import score_s9b  # noqa: F401

    def test_score_s9c_size_importable(self):
        from context_intelligence.signals import score_s9c_size  # noqa: F401

    def test_score_s9c_self_importable(self):
        from context_intelligence.signals import score_s9c_self  # noqa: F401

    def test_score_s9_combined_importable(self):
        from context_intelligence.signals import score_s9_combined  # noqa: F401

    def test_score_4_1_importable(self):
        from context_intelligence.signals import score_4_1  # noqa: F401

    def test_iter_events_importable(self):
        from context_intelligence.signals import _iter_events  # noqa: F401

    def test_threshold_constants_importable(self):
        """All threshold constants must be importable and equal their expected values."""
        from context_intelligence.signals import (
            S1_BURST_THRESHOLD,
            S1_CANDIDATE_THRESHOLD,
            S1_SEVERE_THRESHOLD,
            S2_COUNT_THRESHOLD,
            S2_RATIO_THRESHOLD,
            S3_CANDIDATE_THRESHOLD,
            S3_SEVERE_THRESHOLD,
            S9A_THRESHOLD,
        )

        assert S1_BURST_THRESHOLD == 3
        assert S1_CANDIDATE_THRESHOLD == 3
        assert S1_SEVERE_THRESHOLD == 10
        assert S2_COUNT_THRESHOLD == 3
        assert S2_RATIO_THRESHOLD == 0.5
        assert S3_CANDIDATE_THRESHOLD == 20
        assert S3_SEVERE_THRESHOLD == 40
        assert S9A_THRESHOLD == 5


class TestDataclasses:
    """Verify S4aResult, S4bResult, and SignalScores dataclass behavior."""

    def test_s4a_result_defaults(self):
        """S4aResult default-constructed values must match spec."""
        from context_intelligence.signals import S4aResult

        obj = S4aResult()
        assert obj.fires is False
        assert obj.multi_tool_ratio == 0.0
        assert obj.top_shape_share == 0.0
        assert obj.top_shape == ()

    def test_s4a_result_to_dict(self):
        """S4aResult.to_dict() must serialise top_shape tuple as a list."""
        from context_intelligence.signals import S4aResult

        obj = S4aResult(top_shape=("read_file", "bash"))
        d = obj.to_dict()
        assert isinstance(d["top_shape"], list)
        assert d["top_shape"] == ["read_file", "bash"]

    def test_s4b_result_defaults(self):
        """S4bResult default-constructed values must match spec."""
        from context_intelligence.signals import S4bResult

        obj = S4bResult()
        assert obj.fires is False
        assert obj.instrumentation_ratio == 0.0
        assert obj.instrumentation_count == 0
        assert obj.total_bash_count == 0

    def test_s4b_result_to_dict(self):
        """S4bResult.to_dict() must include all fields with correct values."""
        from context_intelligence.signals import S4bResult

        obj = S4bResult(
            fires=True,
            instrumentation_ratio=0.5,
            instrumentation_count=2,
            total_bash_count=4,
        )
        d = obj.to_dict()
        assert isinstance(d, dict)
        assert d["fires"] is True
        assert d["instrumentation_ratio"] == 0.5
        assert d["instrumentation_count"] == 2
        assert d["total_bash_count"] == 4

    def test_signal_scores_defaults(self):
        """All numeric fields must default to 0, all boolean fields to False."""
        from context_intelligence.signals import SignalScores

        obj = SignalScores()
        # Numeric fields
        assert obj.s1_compaction_count == 0
        assert obj.s1_burst_max == 0
        assert obj.s2_resume_count == 0
        assert obj.s2_resume_ratio == 0.0
        assert obj.s3_iteration_count == 0
        assert obj.s4c_max_dup_input == 0
        assert obj.s4d_max_dup_pair == 0
        assert obj.s6_cancel_count == 0
        assert obj.s7_max_reads_per_iter == 0
        assert obj.s8_max_bash_burst_len == 0
        assert obj.s9a_delegate_count == 0
        assert obj.s9b_max_delegate_result_size == 0
        assert obj.s9c_self_count == 0
        # Boolean fields
        assert obj.s5_stale is False
        assert obj.s9c_size_fires is False
        assert obj.s9_combined_fires is False

    def test_signal_scores_s4_defaults(self):
        """s4a and s4b fields must be S4aResult and S4bResult instances respectively."""
        from context_intelligence.signals import S4aResult, S4bResult, SignalScores

        obj = SignalScores()
        assert isinstance(obj.s4a, S4aResult)
        assert isinstance(obj.s4b, S4bResult)

    def test_signal_scores_to_dict_keys(self):
        """to_dict() must return exactly the 18 expected top-level keys."""
        from context_intelligence.signals import SignalScores

        d = SignalScores().to_dict()
        expected_keys = {
            "s1_compaction_count",
            "s1_burst_max",
            "s2_resume_count",
            "s2_resume_ratio",
            "s3_iteration_count",
            "s4a",
            "s4b",
            "s4c_max_dup_input",
            "s4d_max_dup_pair",
            "s5_stale",
            "s6_cancel_count",
            "s7_max_reads_per_iter",
            "s8_max_bash_burst_len",
            "s9a_delegate_count",
            "s9b_max_delegate_result_size",
            "s9c_size_fires",
            "s9c_self_count",
            "s9_combined_fires",
        }
        assert set(d.keys()) == expected_keys

    def test_signal_scores_to_dict_s4a_is_dict(self):
        """to_dict()['s4a'] must be a dict containing a 'fires' key."""
        from context_intelligence.signals import SignalScores

        d = SignalScores().to_dict()
        assert isinstance(d["s4a"], dict)
        assert "fires" in d["s4a"]

    def test_compound_score_all_zero_returns_zero(self):
        """A default SignalScores must have compound_score() == 0."""
        from context_intelligence.signals import SignalScores

        assert SignalScores().compound_score() == 0

    def test_compound_score_counts_s6(self):
        """s6_cancel_count=1 must cause compound_score() >= 1."""
        from context_intelligence.signals import SignalScores

        assert SignalScores(s6_cancel_count=1).compound_score() >= 1

    def test_compound_score_counts_s9a(self):
        """s9a_delegate_count=5 (at S9A_THRESHOLD) must cause compound_score() >= 1."""
        from context_intelligence.signals import SignalScores

        assert SignalScores(s9a_delegate_count=5).compound_score() >= 1


class TestIterEvents:
    """Verify _iter_events() generator behavior against fixture and edge cases."""

    def test_clean_session_yields_three_events(self):
        """clean_session.jsonl has exactly 3 events."""
        from context_intelligence.signals import _iter_events

        events = list(_iter_events(FIXTURES / "clean_session.jsonl"))
        assert len(events) == 3

    def test_clean_session_event_names_in_order(self):
        """Events must appear in the expected order by name."""
        from context_intelligence.signals import _iter_events

        events = list(_iter_events(FIXTURES / "clean_session.jsonl"))
        assert events[0]["event"] == "session:start"
        assert events[1]["event"] == "orchestrator:iteration_start"
        assert events[2]["event"] == "tool:pre"

    def test_clean_session_dicts_have_workspace_field(self):
        """Every event dict in clean_session.jsonl must have a 'workspace' key."""
        from context_intelligence.signals import _iter_events

        events = list(_iter_events(FIXTURES / "clean_session.jsonl"))
        for event in events:
            assert "workspace" in event

    def test_nonexistent_path_yields_nothing(self):
        """A path that does not exist must produce an empty list."""
        from context_intelligence.signals import _iter_events

        events = list(_iter_events(FIXTURES / "nonexistent_file_that_does_not_exist.jsonl"))
        assert events == []

    def test_skips_blank_lines(self, tmp_path):
        """Blank lines between JSON objects must be silently skipped."""
        from context_intelligence.signals import _iter_events

        p = tmp_path / "events.jsonl"
        # 4 lines total: JSON, blank, JSON, blank
        p.write_text('{"type": "a"}\n\n{"type": "b"}\n\n', encoding="utf-8")
        events = list(_iter_events(p))
        assert len(events) == 2

    def test_skips_invalid_json_lines(self, tmp_path):
        """Lines containing invalid JSON must be silently skipped."""
        from context_intelligence.signals import _iter_events

        p = tmp_path / "events.jsonl"
        p.write_text('{"type": "a"}\nNOT VALID JSON\n{"type": "b"}\n', encoding="utf-8")
        events = list(_iter_events(p))
        assert len(events) == 2

    def test_accepts_string_path(self):
        """_iter_events must accept a str path in addition to pathlib.Path."""
        from context_intelligence.signals import _iter_events

        events = list(_iter_events(str(FIXTURES / "clean_session.jsonl")))
        assert len(events) == 3


class TestScoreS6:
    """Verify score_s6() — cancellation / interrupt event counter."""

    def test_returns_zero_for_clean_session(self):
        """clean_session.jsonl has no cancel events — score must be 0."""
        from context_intelligence.signals import score_s6

        assert score_s6(FIXTURES / "clean_session.jsonl") == 0

    def test_returns_two_for_cancel_session(self):
        """cancel_session.jsonl has one user:interrupt and one session:cancelled — score must be 2."""
        from context_intelligence.signals import score_s6

        assert score_s6(FIXTURES / "cancel_session.jsonl") == 2

    def test_returns_zero_for_nonexistent_path(self):
        """A non-existent path must not raise and must return 0."""
        from context_intelligence.signals import score_s6

        result = score_s6(FIXTURES / "nonexistent_cancel_file_xyz.jsonl")
        assert result == 0

    def test_counts_only_cancel_events(self, tmp_path):
        """Only user:interrupt events are counted; other event types are ignored."""
        from context_intelligence.signals import score_s6

        p = tmp_path / "mixed.jsonl"
        p.write_text(
            '{"event": "user:interrupt", "timestamp": "2026-05-01T12:00:00.000Z", "workspace": "test"}\n'
            '{"event": "tool:pre", "timestamp": "2026-05-01T12:00:01.000Z", "workspace": "test"}\n',
            encoding="utf-8",
        )
        assert score_s6(p) == 1


class TestScoreS9cSelf:
    """Verify score_s9c_self() — recursive self-delegation counter."""

    def test_returns_zero_for_clean_session(self):
        """clean_session.jsonl has no delegate tool:pre events — score must be 0."""
        from context_intelligence.signals import score_s9c_self

        assert score_s9c_self(FIXTURES / "clean_session.jsonl") == 0

    def test_returns_two_for_s9a_session(self):
        """s9a_session.jsonl has exactly 2 self-delegation events (d02, d04) — score must be 2."""
        from context_intelligence.signals import score_s9c_self

        assert score_s9c_self(FIXTURES / "s9a_session.jsonl") == 2

    def test_returns_zero_for_nonexistent_path(self):
        """A non-existent path must not raise and must return 0."""
        from context_intelligence.signals import score_s9c_self

        result = score_s9c_self(FIXTURES / "nonexistent_s9c_self_file_xyz.jsonl")
        assert result == 0

    def test_ignores_non_delegate_tool_pre(self, tmp_path):
        """tool:pre events with tool_name != 'delegate' must not be counted."""
        from context_intelligence.signals import score_s9c_self

        p = tmp_path / "bash_only.jsonl"
        p.write_text(
            '{"event": "tool:pre", "timestamp": "2026-05-01T11:00:00.000Z", "workspace": "test",'
            ' "data": {"tool_name": "bash", "tool_input": {"command": "ls"}, "session_id": "t1"}}\n',
            encoding="utf-8",
        )
        assert score_s9c_self(p) == 0

    def test_ignores_delegate_with_other_agent(self, tmp_path):
        """tool:pre delegate events where agent != 'self' must not be counted."""
        from context_intelligence.signals import score_s9c_self

        p = tmp_path / "delegate_other.jsonl"
        p.write_text(
            '{"event": "tool:pre", "timestamp": "2026-05-01T11:00:00.000Z", "workspace": "test",'
            ' "data": {"tool_name": "delegate", "tool_input": {"agent": "foundation:explorer"}, "session_id": "t1"}}\n',
            encoding="utf-8",
        )
        assert score_s9c_self(p) == 0


class TestScoreS1:
    """Verify score_s1() — compaction event counter."""

    def test_returns_zero_for_clean_session(self):
        """clean_session.jsonl has no context:compaction events — score must be 0."""
        from context_intelligence.signals import score_s1

        assert score_s1(FIXTURES / "clean_session.jsonl") == 0

    def test_returns_five_for_s1_session(self):
        """s1_session.jsonl has exactly 5 context:compaction events — score must be 5."""
        from context_intelligence.signals import score_s1

        assert score_s1(FIXTURES / "s1_session.jsonl") == 5

    def test_returns_zero_for_nonexistent_path(self):
        """A non-existent path must not raise and must return 0."""
        from context_intelligence.signals import score_s1

        result = score_s1(FIXTURES / "nonexistent_s1_file_xyz.jsonl")
        assert result == 0

    def test_candidate_threshold(self):
        """s1_session.jsonl score must be >= S1_CANDIDATE_THRESHOLD (3)."""
        from context_intelligence.signals import S1_CANDIDATE_THRESHOLD, score_s1

        assert score_s1(FIXTURES / "s1_session.jsonl") >= S1_CANDIDATE_THRESHOLD

    def test_does_not_count_other_events(self, tmp_path):
        """Only context:compaction events are counted; other event types are ignored."""
        from context_intelligence.signals import score_s1

        p = tmp_path / "mixed.jsonl"
        p.write_text(
            '{"event": "session:start", "timestamp": "2026-05-01T10:00:00.000Z", "workspace": "test", "data": {"session_id": "t1"}}\n'
            '{"event": "context:compaction", "timestamp": "2026-05-01T10:02:00.000Z", "workspace": "test", "data": {"session_id": "t1"}}\n'
            '{"event": "orchestrator:iteration_start", "timestamp": "2026-05-01T10:01:00.000Z", "workspace": "test", "data": {"session_id": "t1", "iteration": 1}}\n',
            encoding="utf-8",
        )
        assert score_s1(p) == 1
