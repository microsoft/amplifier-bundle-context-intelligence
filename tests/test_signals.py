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


class TestScoreS9a:
    """Verify score_s9a() — delegate call counter."""

    def test_returns_zero_for_clean_session(self):
        """clean_session.jsonl has only a bash tool:pre, not delegate — score must be 0."""
        from context_intelligence.signals import score_s9a

        assert score_s9a(FIXTURES / "clean_session.jsonl") == 0

    def test_returns_six_for_s9a_session(self):
        """s9a_session.jsonl has exactly 6 delegate tool:pre events — score must be 6."""
        from context_intelligence.signals import score_s9a

        assert score_s9a(FIXTURES / "s9a_session.jsonl") == 6

    def test_fires_threshold(self):
        """s9a_session.jsonl score (6) must be >= S9A_THRESHOLD (5)."""
        from context_intelligence.signals import S9A_THRESHOLD, score_s9a

        assert score_s9a(FIXTURES / "s9a_session.jsonl") >= S9A_THRESHOLD

    def test_returns_zero_for_nonexistent_path(self):
        """A non-existent path must not raise and must return 0."""
        from context_intelligence.signals import score_s9a

        result = score_s9a(FIXTURES / "nonexistent_s9a_file_xyz.jsonl")
        assert result == 0

    def test_ignores_non_delegate_tool_pre(self, tmp_path):
        """tool:pre events with tool_name != 'delegate' must not be counted."""
        from context_intelligence.signals import score_s9a

        p = tmp_path / "bash_read.jsonl"
        p.write_text(
            '{"event": "tool:pre", "timestamp": "2026-05-01T11:00:00.000Z", "workspace": "test",'
            ' "data": {"tool_name": "bash", "tool_input": {"command": "ls"}, "session_id": "t1"}}\n'
            '{"event": "tool:pre", "timestamp": "2026-05-01T11:00:01.000Z", "workspace": "test",'
            ' "data": {"tool_name": "read_file", "tool_input": {"file_path": "/tmp/x"}, "session_id": "t1"}}\n',
            encoding="utf-8",
        )
        assert score_s9a(p) == 0


class TestScoreS3:
    """Verify score_s3() — orchestrator:iteration_start event counter."""

    def test_returns_one_for_clean_session(self):
        """clean_session.jsonl has exactly 1 orchestrator:iteration_start event — score must be 1."""
        from context_intelligence.signals import score_s3

        assert score_s3(FIXTURES / "clean_session.jsonl") == 1

    def test_returns_eight_for_s9a_session(self):
        """s9a_session.jsonl has exactly 8 orchestrator:iteration_start events — score must be 8."""
        from context_intelligence.signals import score_s3

        assert score_s3(FIXTURES / "s9a_session.jsonl") == 8

    def test_does_not_fire_at_eight(self):
        """s9a_session.jsonl score (8) must be below S3_CANDIDATE_THRESHOLD (20)."""
        from context_intelligence.signals import S3_CANDIDATE_THRESHOLD, score_s3

        assert score_s3(FIXTURES / "s9a_session.jsonl") < S3_CANDIDATE_THRESHOLD

    def test_returns_zero_for_nonexistent_path(self):
        """A non-existent path must not raise and must return 0."""
        from context_intelligence.signals import score_s3

        result = score_s3(FIXTURES / "nonexistent_s3_file_xyz.jsonl")
        assert result == 0

    def test_does_not_count_other_events(self, tmp_path):
        """Only orchestrator:iteration_start events are counted; other event types are ignored."""
        from context_intelligence.signals import score_s3

        p = tmp_path / "mixed.jsonl"
        p.write_text(
            '{"event": "orchestrator:iteration_start", "timestamp": "2026-05-01T10:01:00.000Z", "workspace": "test", "data": {"session_id": "t1", "iteration": 1}}\n'
            '{"event": "session:start", "timestamp": "2026-05-01T10:00:00.000Z", "workspace": "test", "data": {"session_id": "t1"}}\n'
            '{"event": "orchestrator:iteration_start", "timestamp": "2026-05-01T10:03:00.000Z", "workspace": "test", "data": {"session_id": "t1", "iteration": 2}}\n',
            encoding="utf-8",
        )
        assert score_s3(p) == 2

    def test_candidate_threshold_values(self):
        """S3_CANDIDATE_THRESHOLD must be 20 and S3_SEVERE_THRESHOLD must be 40."""
        from context_intelligence.signals import S3_CANDIDATE_THRESHOLD, S3_SEVERE_THRESHOLD

        assert S3_CANDIDATE_THRESHOLD == 20
        assert S3_SEVERE_THRESHOLD == 40


class TestScoreS2:
    """Verify score_s2() — resume count and post-compaction ratio."""

    def test_returns_zero_count_and_zero_ratio_for_clean_session(self):
        """clean_session.jsonl has no resume events — score must be (0, 0.0)."""
        from context_intelligence.signals import score_s2

        count, ratio = score_s2(FIXTURES / "clean_session.jsonl")
        assert count == 0
        assert ratio == 0.0

    def test_returns_three_count_for_s1_session(self):
        """s1_session.jsonl has exactly 3 session:resume events — count must be 3."""
        from context_intelligence.signals import score_s2

        count, ratio = score_s2(FIXTURES / "s1_session.jsonl")
        assert count == 3

    def test_count_fires_threshold(self):
        """s1_session.jsonl count (3) must be >= S2_COUNT_THRESHOLD (3)."""
        from context_intelligence.signals import S2_COUNT_THRESHOLD, score_s2

        count, ratio = score_s2(FIXTURES / "s1_session.jsonl")
        assert count >= S2_COUNT_THRESHOLD

    def test_ratio_exceeds_threshold_for_s1_session(self):
        """s1_session.jsonl ratio must be > S2_RATIO_THRESHOLD (0.5)."""
        from context_intelligence.signals import S2_RATIO_THRESHOLD, score_s2

        count, ratio = score_s2(FIXTURES / "s1_session.jsonl")
        assert ratio > S2_RATIO_THRESHOLD

    def test_ratio_approximates_two_thirds(self):
        """s1_session.jsonl ratio must be approximately 2/3 (within 0.01)."""
        from context_intelligence.signals import score_s2

        count, ratio = score_s2(FIXTURES / "s1_session.jsonl")
        assert abs(ratio - 2 / 3) < 0.01

    def test_returns_zero_for_nonexistent_path(self):
        """A non-existent path must not raise and must return (0, 0.0)."""
        from context_intelligence.signals import score_s2

        count, ratio = score_s2(FIXTURES / "nonexistent_s2_file_xyz.jsonl")
        assert count == 0
        assert ratio == 0.0

    def test_ratio_is_zero_when_no_compactions(self, tmp_path):
        """3 resume events and no compactions — count=3, ratio=0.0."""
        import json

        from context_intelligence.signals import score_s2

        p = tmp_path / "resumes_only.jsonl"
        lines = [
            json.dumps(
                {
                    "event": "session:resume",
                    "timestamp": "2026-05-01T10:01:00.000Z",
                    "workspace": "test",
                    "data": {"session_id": "t1"},
                }
            ),
            json.dumps(
                {
                    "event": "session:resume",
                    "timestamp": "2026-05-01T10:02:00.000Z",
                    "workspace": "test",
                    "data": {"session_id": "t1"},
                }
            ),
            json.dumps(
                {
                    "event": "session:resume",
                    "timestamp": "2026-05-01T10:03:00.000Z",
                    "workspace": "test",
                    "data": {"session_id": "t1"},
                }
            ),
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        count, ratio = score_s2(p)
        assert count == 3
        assert ratio == 0.0

    def test_counts_session_restore_events(self, tmp_path):
        """session:restore events must be counted as resume events — count=1."""
        import json

        from context_intelligence.signals import score_s2

        p = tmp_path / "restore.jsonl"
        p.write_text(
            json.dumps(
                {
                    "event": "session:restore",
                    "timestamp": "2026-05-01T10:01:00.000Z",
                    "workspace": "test",
                    "data": {"session_id": "t1"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        count, ratio = score_s2(p)
        assert count == 1


class TestScoreS1Burst:
    """Verify score_s1_burst() — sliding-window compaction burst detector."""

    def test_returns_zero_for_clean_session(self):
        """clean_session.jsonl has no compaction events — burst score must be 0."""
        from context_intelligence.signals import score_s1_burst

        assert score_s1_burst(FIXTURES / "clean_session.jsonl") == 0

    def test_returns_three_for_s1_session_default_window(self):
        """s1_session.jsonl has 3 compactions within 5 min (10:02, 10:03, 10:06:30) — score must be 3."""
        from context_intelligence.signals import score_s1_burst

        assert score_s1_burst(FIXTURES / "s1_session.jsonl") == 3

    def test_fires_threshold(self):
        """s1_session.jsonl burst score (3) must be >= S1_BURST_THRESHOLD (3)."""
        from context_intelligence.signals import S1_BURST_THRESHOLD, score_s1_burst

        assert score_s1_burst(FIXTURES / "s1_session.jsonl") >= S1_BURST_THRESHOLD

    def test_returns_zero_for_nonexistent_path(self):
        """A non-existent path must not raise and must return 0."""
        from context_intelligence.signals import score_s1_burst

        result = score_s1_burst(FIXTURES / "nonexistent_s1_burst_file_xyz.jsonl")
        assert result == 0

    def test_narrow_window_excludes_distant_compactions(self):
        """window_min=2: only 10:02:00 and 10:03:00 fit (diff=1 min); 10:06:30 is too far — result must be 2."""
        from context_intelligence.signals import score_s1_burst

        assert score_s1_burst(FIXTURES / "s1_session.jsonl", window_min=2) == 2

    def test_single_compaction_returns_one(self, tmp_path):
        """A session with exactly one compaction event must return 1."""
        import json

        from context_intelligence.signals import score_s1_burst

        p = tmp_path / "single.jsonl"
        p.write_text(
            json.dumps(
                {
                    "event": "context:compaction",
                    "timestamp": "2026-05-01T10:00:00.000Z",
                    "workspace": "test",
                    "data": {"session_id": "t1"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert score_s1_burst(p) == 1

    def test_window_boundary_inclusive(self, tmp_path):
        """Two compactions exactly window_min minutes apart must both be counted (window is inclusive)."""
        import json

        from context_intelligence.signals import score_s1_burst

        p = tmp_path / "boundary.jsonl"
        lines = [
            json.dumps(
                {
                    "event": "context:compaction",
                    "timestamp": "2026-05-01T10:00:00.000Z",
                    "workspace": "test",
                    "data": {"session_id": "t1"},
                }
            ),
            json.dumps(
                {
                    "event": "context:compaction",
                    "timestamp": "2026-05-01T10:05:00.000Z",
                    "workspace": "test",
                    "data": {"session_id": "t1"},
                }
            ),
        ]
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Exactly 5 minutes apart with window_min=5 — inclusive boundary means both fit
        assert score_s1_burst(p, window_min=5) == 2

    def test_no_compactions_returns_zero(self, tmp_path):
        """A session with only a session:start event (no compactions) must return 0."""
        import json

        from context_intelligence.signals import score_s1_burst

        p = tmp_path / "no_compactions.jsonl"
        p.write_text(
            json.dumps(
                {
                    "event": "session:start",
                    "timestamp": "2026-05-01T10:00:00.000Z",
                    "workspace": "test",
                    "data": {"session_id": "t1"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        assert score_s1_burst(p) == 0


METADATA_FIXTURES = FIXTURES / "metadata"


class TestScoreS5:
    """Verify score_s5() — stale session detector."""

    def test_returns_true_for_stale_running_fixture(self):
        """s5_stale fixture has status='running' and last event 2026-01-01T08:01:05Z.
        Any ref_ts well after that (e.g. 2026-01-01T12:00:00Z) must return True.
        """
        from context_intelligence.signals import score_s5

        metadata_path = METADATA_FIXTURES / "s5_stale" / "metadata.json"
        ref_ts = "2026-01-01T12:00:00.000Z"
        assert score_s5(metadata_path, ref_ts) is True

    def test_returns_false_for_completed_session(self):
        """s5_active fixture has status='completed' — must return False regardless of timestamps."""
        from context_intelligence.signals import score_s5

        metadata_path = METADATA_FIXTURES / "s5_active" / "metadata.json"
        ref_ts = "2026-01-01T12:00:00.000Z"
        assert score_s5(metadata_path, ref_ts) is False

    def test_returns_false_when_gap_under_threshold(self, tmp_path):
        """status='running' but last event only 30 min before ref_ts — must return False."""
        import json

        from context_intelligence.signals import score_s5

        meta = tmp_path / "metadata.json"
        meta.write_text(
            json.dumps({"session_id": "t1", "status": "running", "workspace": "test"}),
            encoding="utf-8",
        )
        events = tmp_path / "events.jsonl"
        events.write_text(
            json.dumps(
                {
                    "event": "tool:pre",
                    "timestamp": "2026-06-01T10:30:00.000Z",
                    "workspace": "test",
                    "data": {"session_id": "t1"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        # ref_ts is 30 min after last event — gap is 30 min, below 2h threshold
        ref_ts = "2026-06-01T11:00:00.000Z"
        assert score_s5(meta, ref_ts) is False

    def test_returns_false_for_nonexistent_metadata(self, tmp_path):
        """A metadata_path that does not exist must return False without raising."""
        from context_intelligence.signals import score_s5

        meta = tmp_path / "nonexistent_metadata.json"
        ref_ts = "2026-01-01T12:00:00.000Z"
        assert score_s5(meta, ref_ts) is False

    def test_accepts_string_ref_timestamp(self):
        """score_s5 must accept ref_last_event_ts as an ISO-8601 string."""
        from context_intelligence.signals import score_s5

        metadata_path = METADATA_FIXTURES / "s5_stale" / "metadata.json"
        # Pass as string (not datetime)
        result = score_s5(metadata_path, "2026-12-01T12:00:00.000Z")
        assert result is True


class TestScoreS4c:
    """Verify score_s4c() — exact-duplicate tool input fingerprint counter."""

    def test_returns_zero_for_clean_session(self):
        """clean_session.jsonl has all unique tool inputs — score must be below S4C_THRESHOLD."""
        from context_intelligence.signals import S4C_THRESHOLD, score_s4c

        # clean_session has only 1 unique bash tool:pre — no duplicates, score is below threshold
        result = score_s4c(FIXTURES / "clean_session.jsonl")
        assert result < S4C_THRESHOLD

    def test_returns_five_for_s4c_session(self):
        """s4c_session.jsonl has 5 identical 'ls -la /workspace' inputs — score must be 5."""
        from context_intelligence.signals import score_s4c

        assert score_s4c(FIXTURES / "s4c_session.jsonl") == 5

    def test_fires_at_threshold(self):
        """s4c_session.jsonl score (5) must be >= S4C_THRESHOLD (4)."""
        from context_intelligence.signals import S4C_THRESHOLD, score_s4c

        assert score_s4c(FIXTURES / "s4c_session.jsonl") >= S4C_THRESHOLD

    def test_returns_zero_for_nonexistent_path(self):
        """A non-existent path must not raise and must return 0."""
        from context_intelligence.signals import score_s4c

        result = score_s4c(FIXTURES / "nonexistent_s4c_file_xyz.jsonl")
        assert result == 0

    def test_different_inputs_not_counted_together(self):
        """Different tool inputs must each have their own count (not summed together)."""
        from context_intelligence.signals import score_s4c

        # s4c_session has 5 identical 'ls -la /workspace' + 1 'pwd' — max is 5, not 6
        assert score_s4c(FIXTURES / "s4c_session.jsonl") == 5

    def test_ignores_tool_post_events(self, tmp_path):
        """tool:post events must not be counted — only tool:pre events are fingerprinted."""
        from context_intelligence.signals import score_s4c

        import json

        p = tmp_path / "with_post.jsonl"
        lines = []
        # 3 identical tool:pre events
        for i in range(3):
            lines.append(
                json.dumps(
                    {
                        "event": "tool:pre",
                        "timestamp": f"2026-05-01T12:00:0{i}.000Z",
                        "workspace": "test",
                        "data": {
                            "tool_name": "bash",
                            "tool_input": {"command": "ls"},
                            "tool_call_id": f"tc-{i:03d}",
                            "session_id": "t1",
                        },
                    }
                )
            )
            # Corresponding tool:post — should be ignored
            lines.append(
                json.dumps(
                    {
                        "event": "tool:post",
                        "timestamp": f"2026-05-01T12:00:0{i}.500Z",
                        "workspace": "test",
                        "data": {
                            "tool_call_id": f"tc-{i:03d}",
                            "session_id": "t1",
                            "result": {"success": True, "output": "file.txt"},
                        },
                    }
                )
            )
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Only 3 tool:pre events counted; tool:post events ignored → max count is 3
        assert score_s4c(p) == 3


class TestScoreS4d:
    """Verify score_s4d() — no-progress (input, output) pair repetition counter."""

    def test_returns_zero_for_clean_session(self):
        """clean_session.jsonl has no tool:post events — score must be 0."""
        from context_intelligence.signals import score_s4d

        assert score_s4d(FIXTURES / "clean_session.jsonl") == 0

    def test_returns_four_for_s4d_session(self):
        """s4d_session.jsonl has 4 identical (input, output) pairs — score must be 4."""
        from context_intelligence.signals import score_s4d

        assert score_s4d(FIXTURES / "s4d_session.jsonl") == 4

    def test_fires_at_threshold(self):
        """s4d_session.jsonl score (4) must be >= S4D_THRESHOLD (3)."""
        from context_intelligence.signals import S4D_THRESHOLD, score_s4d

        assert score_s4d(FIXTURES / "s4d_session.jsonl") >= S4D_THRESHOLD

    def test_returns_zero_for_nonexistent_path(self):
        """A non-existent path must not raise and must return 0."""
        from context_intelligence.signals import score_s4d

        result = score_s4d(FIXTURES / "nonexistent_s4d_file_xyz.jsonl")
        assert result == 0

    def test_same_input_different_outputs_not_counted(self, tmp_path):
        """Same tool input but different outputs must NOT be grouped into the same count."""
        from context_intelligence.signals import score_s4d

        import json

        p = tmp_path / "mixed_outputs.jsonl"
        lines = []
        outputs = ["output_a", "output_b", "output_a", "output_b"]
        for i, out in enumerate(outputs):
            lines.append(
                json.dumps(
                    {
                        "event": "tool:pre",
                        "timestamp": f"2026-05-01T13:00:0{i * 2}.000Z",
                        "workspace": "test",
                        "data": {
                            "tool_name": "bash",
                            "tool_input": {"command": "ls"},
                            "tool_call_id": f"tx-{i:03d}",
                            "session_id": "t1",
                        },
                    }
                )
            )
            lines.append(
                json.dumps(
                    {
                        "event": "tool:post",
                        "timestamp": f"2026-05-01T13:00:0{i * 2 + 1}.000Z",
                        "workspace": "test",
                        "data": {
                            "tool_call_id": f"tx-{i:03d}",
                            "session_id": "t1",
                            "result": {"success": True, "output": out},
                        },
                    }
                )
            )
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        # output_a appears 2 times, output_b appears 2 times — max is 2, not 4
        assert score_s4d(p) == 2
