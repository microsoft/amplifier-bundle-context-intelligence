"""Signal-scoring module for the context-intelligence bundle.

Public API:

    score_session       — compute all signal scores for a session, returning SignalScores
    score_s1            — S1: compaction event count
    score_s1_burst      — S1 variant: burst detection within a rolling window
    score_s2            — S2: rapid resume / re-attach count and ratio (returns tuple[int,float])
    score_s3            — S3: iteration count (agent loop cycles)
    score_s4a           — S4a: multi-tool call shape repetition
    score_s4b           — S4b: instrumentation bash pattern detection
    score_s4c           — S4c: duplicate tool-call input detection
    score_s4d           — S4d: duplicate tool-call input-pair detection
    score_s5            — S5: stale session detection (metadata timestamp lag)
    score_s6            — S6: cancellation event count
    score_s7            — S7: max file reads per iteration
    score_s8            — S8: max consecutive bash burst length
    score_s9a           — S9a: delegate call count
    score_s9b           — S9b: maximum delegate result payload size
    score_s9c_size      — S9c (size): fires when result payload exceeds threshold
    score_s9c_self      — S9c (self): self-delegation cycle count
    score_s9_combined   — S9 combined: fires when multiple S9 sub-signals fire together
    score_4_1           — Aggregate: produces a summary dict from a list of SignalScores

Internal helpers:

    _iter_events        — generator that yields parsed JSON event dicts from a JSONL file
"""

from __future__ import annotations

import json
import logging
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta  # noqa: F401 – available for scoring implementations
from typing import Any  # noqa: F401 – available for scoring implementations

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Threshold constants
# ---------------------------------------------------------------------------

S1_CANDIDATE_THRESHOLD: int = 3
S1_SEVERE_THRESHOLD: int = 10
S1_BURST_THRESHOLD: int = 3

S2_COUNT_THRESHOLD: int = 3
S2_RATIO_THRESHOLD: float = 0.5
S2_RESUME_WINDOW_SECONDS: int = 30

S3_CANDIDATE_THRESHOLD: int = 20
S3_SEVERE_THRESHOLD: int = 40

S9A_THRESHOLD: int = 5

# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------


@dataclass
class S4aResult:
    """Result of the S4a multi-tool shape repetition signal."""

    fires: bool = False
    multi_tool_ratio: float = 0.0
    top_shape_share: float = 0.0
    top_shape: tuple = ()  # type: ignore[type-arg]

    def to_dict(self) -> dict:
        return {
            "fires": self.fires,
            "multi_tool_ratio": self.multi_tool_ratio,
            "top_shape_share": self.top_shape_share,
            "top_shape": list(self.top_shape),
        }


@dataclass
class S4bResult:
    """Result of the S4b instrumentation bash pattern signal."""

    fires: bool = False
    instrumentation_ratio: float = 0.0
    instrumentation_count: int = 0
    total_bash_count: int = 0

    def to_dict(self) -> dict:
        return {
            "fires": self.fires,
            "instrumentation_ratio": self.instrumentation_ratio,
            "instrumentation_count": self.instrumentation_count,
            "total_bash_count": self.total_bash_count,
        }


@dataclass
class SignalScores:
    """Aggregated signal scores for a single session."""

    s1_compaction_count: int = 0
    s1_burst_max: int = 0
    s2_resume_count: int = 0
    s2_resume_ratio: float = 0.0
    s3_iteration_count: int = 0
    s4a: S4aResult = field(default_factory=S4aResult)
    s4b: S4bResult = field(default_factory=S4bResult)
    s4c_max_dup_input: int = 0
    s4d_max_dup_pair: int = 0
    s5_stale: bool = False
    s6_cancel_count: int = 0
    s7_max_reads_per_iter: int = 0
    s8_max_bash_burst_len: int = 0
    s9a_delegate_count: int = 0
    s9b_max_delegate_result_size: int = 0
    s9c_size_fires: bool = False
    s9c_self_count: int = 0
    s9_combined_fires: bool = False

    def to_dict(self) -> dict:
        return {
            "s1_compaction_count": self.s1_compaction_count,
            "s1_burst_max": self.s1_burst_max,
            "s2_resume_count": self.s2_resume_count,
            "s2_resume_ratio": self.s2_resume_ratio,
            "s3_iteration_count": self.s3_iteration_count,
            "s4a": self.s4a.to_dict(),
            "s4b": self.s4b.to_dict(),
            "s4c_max_dup_input": self.s4c_max_dup_input,
            "s4d_max_dup_pair": self.s4d_max_dup_pair,
            "s5_stale": self.s5_stale,
            "s6_cancel_count": self.s6_cancel_count,
            "s7_max_reads_per_iter": self.s7_max_reads_per_iter,
            "s8_max_bash_burst_len": self.s8_max_bash_burst_len,
            "s9a_delegate_count": self.s9a_delegate_count,
            "s9b_max_delegate_result_size": self.s9b_max_delegate_result_size,
            "s9c_size_fires": self.s9c_size_fires,
            "s9c_self_count": self.s9c_self_count,
            "s9_combined_fires": self.s9_combined_fires,
        }

    def compound_score(self) -> int:
        """Count how many distinct signal conditions are active.

        Each condition below contributes exactly 1 to the total, regardless of
        how far above the threshold the raw value is.  Returns an int in the
        range [0, 17].
        """
        count = 0
        if self.s1_compaction_count >= S1_CANDIDATE_THRESHOLD:
            count += 1
        if self.s1_burst_max >= S1_BURST_THRESHOLD:
            count += 1
        if self.s2_resume_count >= S2_COUNT_THRESHOLD or self.s2_resume_ratio > S2_RATIO_THRESHOLD:
            count += 1
        if self.s3_iteration_count >= S3_CANDIDATE_THRESHOLD:
            count += 1
        if self.s4a.fires:
            count += 1
        if self.s4b.fires:
            count += 1
        if self.s4c_max_dup_input >= 4:
            count += 1
        if self.s4d_max_dup_pair >= 3:
            count += 1
        if self.s5_stale:
            count += 1
        if self.s6_cancel_count >= 1:
            count += 1
        if self.s7_max_reads_per_iter >= 5:
            count += 1
        if self.s8_max_bash_burst_len >= 3:
            count += 1
        if self.s9a_delegate_count >= S9A_THRESHOLD:
            count += 1
        if self.s9b_max_delegate_result_size >= 20_000:
            count += 1
        if self.s9c_size_fires:
            count += 1
        if self.s9c_self_count >= 1:
            count += 1
        if self.s9_combined_fires:
            count += 1
        return count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _iter_events(events_path: pathlib.Path | str):
    """Yield parsed JSON event dicts from a JSONL *events_path*.

    Behaviour:
    - Accepts both :class:`pathlib.Path` and :class:`str` inputs.
    - If the file does not exist, logs a ``WARNING`` and returns immediately.
    - Blank lines (after stripping) are silently skipped.
    - Lines that fail JSON parsing log a ``WARNING`` and are skipped.
    """
    path = pathlib.Path(events_path)
    if not path.exists():
        _LOG.warning("events file not found: %s", path)
        return

    with path.open(encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                _LOG.warning("skipping invalid JSON on line %d of %s", lineno, path)
                continue


# ---------------------------------------------------------------------------
# Signal scoring stubs  (NotImplementedError — implementations in later tasks)
# ---------------------------------------------------------------------------


def score_s1(events_path) -> int:
    """S1: count compaction events."""
    return sum(1 for ev in _iter_events(events_path) if ev.get("event") == "context:compaction")


def score_s1_burst(events_path, *, window_min: int = 5) -> int:
    """S1 burst variant: maximum compaction events within a rolling window."""
    raise NotImplementedError


def score_s2(events_path) -> tuple[int, float]:
    """S2: rapid resume count and ratio."""
    raise NotImplementedError


def score_s3(events_path) -> int:
    """S3: iteration count."""
    raise NotImplementedError


def score_s4a(events_path) -> S4aResult:
    """S4a: multi-tool call shape repetition."""
    raise NotImplementedError


def score_s4b(
    events_path,
    *,
    prefixes: frozenset[str] = frozenset({"echo", "STEP", "Step", "Check", "Note"}),
) -> S4bResult:
    """S4b: instrumentation bash pattern detection."""
    raise NotImplementedError


def score_s4c(events_path) -> int:
    """S4c: duplicate tool-call input detection."""
    raise NotImplementedError


def score_s4d(events_path) -> int:
    """S4d: duplicate tool-call input-pair detection."""
    raise NotImplementedError


def score_s5(metadata_path, ref_last_event_ts: str) -> bool:
    """S5: stale session detection."""
    raise NotImplementedError


_CANCEL_EVENTS: frozenset[str] = frozenset({"session:cancelled", "user:interrupt"})


def score_s6(events_path) -> int:
    """S6: cancellation event count."""
    return sum(1 for ev in _iter_events(events_path) if ev.get("event") in _CANCEL_EVENTS)


def score_s7(events_path) -> int:
    """S7: maximum file reads per iteration."""
    raise NotImplementedError


def score_s8(events_path) -> int:
    """S8: maximum consecutive bash burst length."""
    raise NotImplementedError


def score_s9a(events_path) -> int:
    """S9a: delegate call count."""
    raise NotImplementedError


def score_s9b(events_path) -> int:
    """S9b: maximum delegate result payload size."""
    raise NotImplementedError


def score_s9c_size(events_path) -> bool:
    """S9c (size): fires when result payload exceeds threshold."""
    raise NotImplementedError


def score_s9c_self(events_path) -> int:
    """S9c (self): self-delegation cycle count.

    Counts tool:pre events where tool_name == 'delegate' AND
    tool_input.agent == 'self'.  The ``(data.get('tool_input') or {})``
    guard handles the case where tool_input is explicitly None.
    """
    count = 0
    for ev in _iter_events(events_path):
        if ev.get("event") != "tool:pre":
            continue
        data = ev.get("data", {})
        if (
            data.get("tool_name") == "delegate"
            and (data.get("tool_input") or {}).get("agent") == "self"
        ):
            count += 1
    return count


def score_s9_combined(events_path) -> bool:
    """S9 combined: fires when multiple S9 sub-signals fire together."""
    raise NotImplementedError


def score_4_1(scores: list[SignalScores]) -> dict:
    """Aggregate: produce a summary dict from a list of SignalScores."""
    raise NotImplementedError


def score_session(events_path) -> SignalScores:
    """Compute all signal scores for a session, returning a SignalScores instance."""
    raise NotImplementedError
