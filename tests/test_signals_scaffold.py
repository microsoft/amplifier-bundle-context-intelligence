"""Tests for context_intelligence/signals.py scaffold (task-1).

Verifies:
- Module is importable
- All threshold constants are present and have correct values
- Dataclasses S4aResult, S4bResult, SignalScores exist with correct fields and defaults
- S4aResult.to_dict() serialises top_shape as a list
- SignalScores.to_dict() works
- SignalScores.compound_score() counts the correct signals
- _iter_events helper works: file-not-found, valid JSON, blank lines, invalid JSON
- All stub functions raise NotImplementedError
"""

from __future__ import annotations

import inspect
import logging

import pytest


# ---------------------------------------------------------------------------
# Module import
# ---------------------------------------------------------------------------


class TestModuleImport:
    """signals module must be importable."""

    def test_module_importable(self):
        """context_intelligence.signals must import without errors."""
        import context_intelligence.signals  # noqa: F401


class TestThresholdConstants:
    """All threshold constants must be present with exact values."""

    @pytest.fixture(autouse=True)
    def _mod(self):
        import context_intelligence.signals as sig

        self.sig = sig

    def test_s1_candidate_threshold(self):
        assert self.sig.S1_CANDIDATE_THRESHOLD == 3

    def test_s1_severe_threshold(self):
        assert self.sig.S1_SEVERE_THRESHOLD == 10

    def test_s1_burst_threshold(self):
        assert self.sig.S1_BURST_THRESHOLD == 3

    def test_s2_count_threshold(self):
        assert self.sig.S2_COUNT_THRESHOLD == 3

    def test_s2_ratio_threshold(self):
        assert self.sig.S2_RATIO_THRESHOLD == 0.5

    def test_s2_resume_window_seconds(self):
        assert self.sig.S2_RESUME_WINDOW_SECONDS == 30

    def test_s3_candidate_threshold(self):
        assert self.sig.S3_CANDIDATE_THRESHOLD == 20

    def test_s3_severe_threshold(self):
        assert self.sig.S3_SEVERE_THRESHOLD == 40

    def test_s9a_threshold(self):
        assert self.sig.S9A_THRESHOLD == 5


class TestS4aResult:
    """S4aResult dataclass must have correct fields, defaults, and to_dict()."""

    @pytest.fixture(autouse=True)
    def _cls(self):
        from context_intelligence.signals import S4aResult

        self.cls = S4aResult

    def test_default_fires_false(self):
        obj = self.cls()
        assert obj.fires is False

    def test_default_multi_tool_ratio_zero(self):
        obj = self.cls()
        assert obj.multi_tool_ratio == 0.0

    def test_default_top_shape_share_zero(self):
        obj = self.cls()
        assert obj.top_shape_share == 0.0

    def test_default_top_shape_empty_tuple(self):
        obj = self.cls()
        assert obj.top_shape == ()

    def test_to_dict_returns_dict(self):
        obj = self.cls()
        d = obj.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_top_shape_is_list(self):
        obj = self.cls(top_shape=("a", "b"))
        d = obj.to_dict()
        assert isinstance(d["top_shape"], list)
        assert d["top_shape"] == ["a", "b"]

    def test_to_dict_fires_field_present(self):
        obj = self.cls(fires=True)
        d = obj.to_dict()
        assert d["fires"] is True

    def test_to_dict_ratios_present(self):
        obj = self.cls(multi_tool_ratio=0.75, top_shape_share=0.5)
        d = obj.to_dict()
        assert d["multi_tool_ratio"] == 0.75
        assert d["top_shape_share"] == 0.5


class TestS4bResult:
    """S4bResult dataclass must have correct fields, defaults, and to_dict()."""

    @pytest.fixture(autouse=True)
    def _cls(self):
        from context_intelligence.signals import S4bResult

        self.cls = S4bResult

    def test_default_fires_false(self):
        obj = self.cls()
        assert obj.fires is False

    def test_default_instrumentation_ratio_zero(self):
        obj = self.cls()
        assert obj.instrumentation_ratio == 0.0

    def test_default_instrumentation_count_zero(self):
        obj = self.cls()
        assert obj.instrumentation_count == 0

    def test_default_total_bash_count_zero(self):
        obj = self.cls()
        assert obj.total_bash_count == 0

    def test_to_dict_returns_dict(self):
        obj = self.cls()
        d = obj.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_contains_all_fields(self):
        obj = self.cls(
            fires=True, instrumentation_ratio=0.5, instrumentation_count=2, total_bash_count=4
        )
        d = obj.to_dict()
        assert d["fires"] is True
        assert d["instrumentation_ratio"] == 0.5
        assert d["instrumentation_count"] == 2
        assert d["total_bash_count"] == 4


class TestSignalScores:
    """SignalScores dataclass must have all 18 signal fields with correct defaults."""

    @pytest.fixture(autouse=True)
    def _cls(self):
        from context_intelligence.signals import S4aResult, S4bResult, SignalScores

        self.cls = SignalScores
        self.S4aResult = S4aResult
        self.S4bResult = S4bResult

    def _default(self):
        return self.cls()

    def test_s1_compaction_count_default(self):
        assert self._default().s1_compaction_count == 0

    def test_s1_burst_max_default(self):
        assert self._default().s1_burst_max == 0

    def test_s2_resume_count_default(self):
        assert self._default().s2_resume_count == 0

    def test_s2_resume_ratio_default(self):
        assert self._default().s2_resume_ratio == 0.0

    def test_s3_iteration_count_default(self):
        assert self._default().s3_iteration_count == 0

    def test_s4a_default_is_s4a_result(self):
        obj = self._default()
        assert isinstance(obj.s4a, self.S4aResult)

    def test_s4b_default_is_s4b_result(self):
        obj = self._default()
        assert isinstance(obj.s4b, self.S4bResult)

    def test_s4c_max_dup_input_default(self):
        assert self._default().s4c_max_dup_input == 0

    def test_s4d_max_dup_pair_default(self):
        assert self._default().s4d_max_dup_pair == 0

    def test_s5_stale_default(self):
        assert self._default().s5_stale is False

    def test_s6_cancel_count_default(self):
        assert self._default().s6_cancel_count == 0

    def test_s7_max_reads_per_iter_default(self):
        assert self._default().s7_max_reads_per_iter == 0

    def test_s8_max_bash_burst_len_default(self):
        assert self._default().s8_max_bash_burst_len == 0

    def test_s9a_delegate_count_default(self):
        assert self._default().s9a_delegate_count == 0

    def test_s9b_max_delegate_result_size_default(self):
        assert self._default().s9b_max_delegate_result_size == 0

    def test_s9c_size_fires_default(self):
        assert self._default().s9c_size_fires is False

    def test_s9c_self_count_default(self):
        assert self._default().s9c_self_count == 0

    def test_s9_combined_fires_default(self):
        assert self._default().s9_combined_fires is False

    def test_to_dict_returns_dict(self):
        d = self._default().to_dict()
        assert isinstance(d, dict)

    def test_to_dict_has_s4a_key(self):
        d = self._default().to_dict()
        assert "s4a" in d

    def test_to_dict_has_s4b_key(self):
        d = self._default().to_dict()
        assert "s4b" in d


class TestCompoundScore:
    """compound_score() must count each of the 17 signals correctly."""

    @pytest.fixture(autouse=True)
    def _imports(self):
        from context_intelligence.signals import S4aResult, S4bResult, SignalScores

        self.cls = SignalScores
        self.S4aResult = S4aResult
        self.S4bResult = S4bResult

    def test_empty_scores_zero(self):
        assert self.cls().compound_score() == 0

    def test_s1_compaction_at_threshold(self):
        obj = self.cls(s1_compaction_count=3)  # >= S1_CANDIDATE_THRESHOLD=3
        assert obj.compound_score() == 1

    def test_s1_compaction_below_threshold(self):
        obj = self.cls(s1_compaction_count=2)
        assert obj.compound_score() == 0

    def test_s1_burst_at_threshold(self):
        obj = self.cls(s1_burst_max=3)  # >= S1_BURST_THRESHOLD=3
        assert obj.compound_score() == 1

    def test_s2_count_at_threshold(self):
        obj = self.cls(s2_resume_count=3)  # >= S2_COUNT_THRESHOLD=3
        assert obj.compound_score() == 1

    def test_s2_ratio_above_threshold(self):
        obj = self.cls(s2_resume_ratio=0.6)  # > S2_RATIO_THRESHOLD=0.5
        assert obj.compound_score() == 1

    def test_s2_ratio_at_threshold_not_counted(self):
        obj = self.cls(s2_resume_ratio=0.5)  # == 0.5, not >
        assert obj.compound_score() == 0

    def test_s2_both_conditions_still_counts_once(self):
        obj = self.cls(s2_resume_count=5, s2_resume_ratio=0.8)
        assert obj.compound_score() == 1

    def test_s3_at_threshold(self):
        obj = self.cls(s3_iteration_count=20)  # >= S3_CANDIDATE_THRESHOLD=20
        assert obj.compound_score() == 1

    def test_s4a_fires(self):
        obj = self.cls(s4a=self.S4aResult(fires=True))
        assert obj.compound_score() == 1

    def test_s4b_fires(self):
        obj = self.cls(s4b=self.S4bResult(fires=True))
        assert obj.compound_score() == 1

    def test_s4c_at_threshold(self):
        obj = self.cls(s4c_max_dup_input=4)  # >= 4
        assert obj.compound_score() == 1

    def test_s4c_below_threshold(self):
        obj = self.cls(s4c_max_dup_input=3)
        assert obj.compound_score() == 0

    def test_s4d_at_threshold(self):
        obj = self.cls(s4d_max_dup_pair=3)  # >= 3
        assert obj.compound_score() == 1

    def test_s5_stale(self):
        obj = self.cls(s5_stale=True)
        assert obj.compound_score() == 1

    def test_s6_cancel_count_at_threshold(self):
        obj = self.cls(s6_cancel_count=1)  # >= 1
        assert obj.compound_score() == 1

    def test_s7_reads_at_threshold(self):
        obj = self.cls(s7_max_reads_per_iter=5)  # >= 5
        assert obj.compound_score() == 1

    def test_s8_bash_burst_at_threshold(self):
        obj = self.cls(s8_max_bash_burst_len=3)  # >= 3
        assert obj.compound_score() == 1

    def test_s9a_at_threshold(self):
        obj = self.cls(s9a_delegate_count=5)  # >= S9A_THRESHOLD=5
        assert obj.compound_score() == 1

    def test_s9b_at_threshold(self):
        obj = self.cls(s9b_max_delegate_result_size=20_000)  # >= 20_000
        assert obj.compound_score() == 1

    def test_s9c_size_fires(self):
        obj = self.cls(s9c_size_fires=True)
        assert obj.compound_score() == 1

    def test_s9c_self_count_at_threshold(self):
        obj = self.cls(s9c_self_count=1)  # >= 1
        assert obj.compound_score() == 1

    def test_s9_combined_fires(self):
        obj = self.cls(s9_combined_fires=True)
        assert obj.compound_score() == 1

    def test_all_signals_fires_count_17(self):
        """All 17 conditions must add up to 17."""
        from context_intelligence.signals import S4aResult, S4bResult, SignalScores

        obj = SignalScores(
            s1_compaction_count=3,
            s1_burst_max=3,
            s2_resume_count=3,
            s3_iteration_count=20,
            s4a=S4aResult(fires=True),
            s4b=S4bResult(fires=True),
            s4c_max_dup_input=4,
            s4d_max_dup_pair=3,
            s5_stale=True,
            s6_cancel_count=1,
            s7_max_reads_per_iter=5,
            s8_max_bash_burst_len=3,
            s9a_delegate_count=5,
            s9b_max_delegate_result_size=20_000,
            s9c_size_fires=True,
            s9c_self_count=1,
            s9_combined_fires=True,
        )
        assert obj.compound_score() == 17


class TestIterEvents:
    """_iter_events() generator must behave per spec."""

    @pytest.fixture(autouse=True)
    def _fn(self):
        from context_intelligence.signals import _iter_events

        self.fn = _iter_events

    def test_nonexistent_file_returns_empty(self, tmp_path):
        path = tmp_path / "nonexistent.jsonl"
        results = list(self.fn(path))
        assert results == []

    def test_nonexistent_file_logs_warning(self, tmp_path, caplog):
        path = tmp_path / "nonexistent.jsonl"
        with caplog.at_level(logging.WARNING, logger="context_intelligence.signals"):
            list(self.fn(path))
        assert len(caplog.records) >= 1

    def test_valid_json_lines_yielded(self, tmp_path):
        p = tmp_path / "events.jsonl"
        p.write_text('{"type":"a"}\n{"type":"b"}\n', encoding="utf-8")
        results = list(self.fn(p))
        assert results == [{"type": "a"}, {"type": "b"}]

    def test_blank_lines_skipped(self, tmp_path):
        p = tmp_path / "events.jsonl"
        p.write_text('{"type":"a"}\n\n{"type":"b"}\n', encoding="utf-8")
        results = list(self.fn(p))
        assert len(results) == 2

    def test_invalid_json_skipped_with_warning(self, tmp_path, caplog):
        p = tmp_path / "events.jsonl"
        p.write_text('{"type":"a"}\nNOT_JSON\n{"type":"b"}\n', encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="context_intelligence.signals"):
            results = list(self.fn(p))
        assert results == [{"type": "a"}, {"type": "b"}]
        # Should have logged a warning about invalid JSON
        assert any("skipping invalid JSON" in r.message for r in caplog.records)

    def test_accepts_str_path(self, tmp_path):
        p = tmp_path / "events.jsonl"
        p.write_text('{"x":1}\n', encoding="utf-8")
        results = list(self.fn(str(p)))
        assert results == [{"x": 1}]

    def test_accepts_pathlib_path(self, tmp_path):
        p = tmp_path / "events.jsonl"
        p.write_text('{"x":1}\n', encoding="utf-8")
        results = list(self.fn(p))
        assert results == [{"x": 1}]


class TestStubFunctions:
    """All stub functions must raise NotImplementedError."""

    @pytest.fixture(autouse=True)
    def _mod(self):
        import context_intelligence.signals as sig

        self.sig = sig

    def _check_stub(self, fn_name, *args, **kwargs):
        fn = getattr(self.sig, fn_name)
        with pytest.raises(NotImplementedError):
            fn(*args, **kwargs)

    def test_score_s1_raises(self):
        self._check_stub("score_s1", None)

    def test_score_s1_burst_raises(self):
        self._check_stub("score_s1_burst", None)

    def test_score_s2_raises(self):
        self._check_stub("score_s2", None)

    def test_score_s3_raises(self):
        self._check_stub("score_s3", None)

    def test_score_s4a_raises(self):
        self._check_stub("score_s4a", None)

    def test_score_s4b_raises(self):
        self._check_stub("score_s4b", None)

    def test_score_s4c_raises(self):
        self._check_stub("score_s4c", None)

    def test_score_s4d_raises(self):
        self._check_stub("score_s4d", None)

    def test_score_s5_raises(self):
        self._check_stub("score_s5", None, ref_last_event_ts="2024-01-01T00:00:00")

    def test_score_s6_raises(self):
        self._check_stub("score_s6", None)

    def test_score_s7_raises(self):
        self._check_stub("score_s7", None)

    def test_score_s8_raises(self):
        self._check_stub("score_s8", None)

    def test_score_s9a_raises(self):
        self._check_stub("score_s9a", None)

    def test_score_s9b_raises(self):
        self._check_stub("score_s9b", None)

    def test_score_s9c_size_raises(self):
        self._check_stub("score_s9c_size", None)

    def test_score_s9c_self_raises(self):
        self._check_stub("score_s9c_self", None)

    def test_score_s9_combined_raises(self):
        self._check_stub("score_s9_combined", None)

    def test_score_4_1_raises(self):
        self._check_stub("score_4_1", [])

    def test_score_session_raises(self):
        self._check_stub("score_session", None)


class TestFunctionSignatures:
    """Spot-check key function signatures."""

    @pytest.fixture(autouse=True)
    def _mod(self):
        import context_intelligence.signals as sig

        self.sig = sig

    def test_score_s1_burst_has_window_min_kw(self):
        sig_obj = inspect.signature(self.sig.score_s1_burst)
        assert "window_min" in sig_obj.parameters
        assert sig_obj.parameters["window_min"].default == 5

    def test_score_s4b_has_prefixes_kw(self):
        sig_obj = inspect.signature(self.sig.score_s4b)
        assert "prefixes" in sig_obj.parameters
        default = sig_obj.parameters["prefixes"].default
        assert isinstance(default, frozenset)
        assert "echo" in default
        assert "STEP" in default

    def test_score_s5_has_ref_last_event_ts_param(self):
        sig_obj = inspect.signature(self.sig.score_s5)
        assert "ref_last_event_ts" in sig_obj.parameters

    def test_score_session_exists(self):
        assert callable(self.sig.score_session)

    def test_score_4_1_exists(self):
        assert callable(self.sig.score_4_1)


class TestModuleDocstring:
    """Module docstring must mention key public API items."""

    def test_has_docstring(self):
        import context_intelligence.signals as sig

        assert sig.__doc__ is not None
        assert len(sig.__doc__.strip()) > 0

    def test_docstring_mentions_score_session(self):
        import context_intelligence.signals as sig

        assert "score_session" in (sig.__doc__ or "")

    def test_docstring_mentions_iter_events(self):
        import context_intelligence.signals as sig

        assert "_iter_events" in (sig.__doc__ or "")
