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

import hashlib  # noqa: F401 – available for scoring implementations
import json
import logging
import pathlib
import re
from collections import Counter, defaultdict  # noqa: F401 – available for scoring implementations
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

# Phase 1b threshold constants
S4A_MIN_TOOL_PRE: int = 20
S4A_MIN_MULTI_TOOL_RATIO: float = 0.30
S4A_MIN_TOP_SHAPE_SHARE: float = 0.15
S4B_MIN_TOOL_PRE: int = 40
S4B_MIN_INSTRUMENTATION_RATIO: float = 0.30
S4C_THRESHOLD: int = 4
S4D_THRESHOLD: int = 3
S7_THRESHOLD: int = 5
S8_THRESHOLD: int = 5
SCORE_4_1_VOLUME_THRESHOLD: int = 5

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


def score_s1(events_path: pathlib.Path | str) -> int:
    """S1: count compaction events."""
    return sum(1 for ev in _iter_events(events_path) if ev.get("event") == "context:compaction")


def score_s1_burst(events_path: pathlib.Path | str, *, window_min: int = 5) -> int:
    """S1 burst variant: maximum compaction events within a rolling window.

    Uses a two-pointer sweep in O(n) time.  The window is inclusive on both
    ends — two events exactly *window_min* minutes apart are counted together.
    """
    window = timedelta(minutes=window_min)
    timestamps: list[datetime] = []

    for ev in _iter_events(events_path):
        if ev.get("event") != "context:compaction":
            continue
        ts = ev.get("timestamp")
        try:
            timestamps.append(datetime.fromisoformat(ts.replace("Z", "+00:00")))
        except (ValueError, AttributeError):
            _LOG.warning("could not parse timestamp %r in context:compaction event", ts)
            continue

    if not timestamps:
        return 0

    timestamps.sort()
    max_count = 0
    left = 0
    for right in range(len(timestamps)):
        while timestamps[right] - timestamps[left] > window:
            left += 1
        max_count = max(max_count, right - left + 1)
    return max_count


_RESUME_EVENTS: frozenset[str] = frozenset({"session:resume", "session:restore"})
_WINDOW: timedelta = timedelta(seconds=S2_RESUME_WINDOW_SECONDS)


def score_s2(events_path: pathlib.Path | str) -> tuple[int, float]:
    """S2: rapid resume count and ratio.

    Returns a tuple (resume_count, ratio) where:
      - resume_count: total number of session:resume and session:restore events
      - ratio: fraction of resumes occurring within S2_RESUME_WINDOW_SECONDS after
               any context:compaction event (each resume counted at most once)
    """
    resume_count: int = 0
    resumes_after_compact: int = 0
    compaction_timestamps: list[datetime] = []

    for ev in _iter_events(events_path):
        event_type = ev.get("event")
        ts = ev.get("timestamp")

        try:
            event_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            _LOG.warning("could not parse timestamp %r in event %r", ts, event_type)
            continue

        if event_type == "context:compaction":
            compaction_timestamps.append(event_dt)
        elif event_type in _RESUME_EVENTS:
            resume_count += 1
            for cts in compaction_timestamps:
                delta = event_dt - cts
                if timedelta(0) <= delta <= _WINDOW:
                    resumes_after_compact += 1
                    break

    ratio: float = resumes_after_compact / resume_count if resume_count > 0 else 0.0
    return (resume_count, ratio)


def score_s3(events_path: pathlib.Path | str) -> int:
    """S3: iteration count."""
    return sum(
        1 for ev in _iter_events(events_path) if ev.get("event") == "orchestrator:iteration_start"
    )


def _classify_tool(tool_name: str, tool_input: dict) -> tuple[str, str]:
    """Classify a tool call into a (tool, cmd) pair for shape analysis.

    Rules:
    - bash      → ('bash', first_token_of_command)
    - read_file → ('read_file', file_extension)  e.g. '.py'
    - grep      → ('grep', type_field or '*')
    - other     → (tool_name, '*')
    """
    if tool_name == "bash":
        command = (tool_input or {}).get("command", "")
        tokens = command.split() if command else []
        first_token = tokens[0] if tokens else "*"
        return ("bash", first_token)
    elif tool_name == "read_file":
        file_path = (tool_input or {}).get("file_path", "")
        ext = pathlib.Path(file_path).suffix if file_path else ""
        return ("read_file", ext if ext else "*")
    elif tool_name == "grep":
        file_type = (tool_input or {}).get("type") or "*"
        return ("grep", file_type)
    else:
        return (tool_name, "*")


_EXPLORATION_CMDS: frozenset[str] = frozenset({"find", "grep", "ls", "rg", "cat", "head", "tail"})


def _is_exploration_shape(shape: tuple) -> bool:  # type: ignore[type-arg]
    """Return True if shape is an exploration shape.

    A shape qualifies if any (tool, cmd) pair in it satisfies:
    - tool == 'bash' with cmd in _EXPLORATION_CMDS, OR
    - tool == 'read_file'
    """
    for tool, cmd in shape:
        if tool == "bash" and cmd in _EXPLORATION_CMDS:
            return True
        if tool == "read_file":
            return True
    return False


def score_s4a(events_path: pathlib.Path | str) -> S4aResult:
    """S4a: multi-tool call shape repetition.

    Python-only signal — parallel_group_id shape multisets cannot be
    expressed in Cypher.  Fires when ALL four conditions hold:
      1. count(tool:pre) >= S4A_MIN_TOOL_PRE
      2. multi_tool_groups / total_groups >= S4A_MIN_MULTI_TOOL_RATIO
      3. top_shape_share >= S4A_MIN_TOP_SHAPE_SHARE
      4. top_shape is an exploration shape
    """
    # Collect tool:pre events grouped by parallel_group_id
    groups: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    total_tool_pre: int = 0

    for ev in _iter_events(events_path):
        if ev.get("event") != "tool:pre":
            continue
        total_tool_pre += 1
        data = ev.get("data", {})
        pg_id = data.get("parallel_group_id")
        if pg_id is None:
            continue
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}
        groups[pg_id].append((tool_name, tool_input))

    total_groups = len(groups)
    if total_groups == 0 or total_tool_pre < S4A_MIN_TOOL_PRE:
        return S4aResult()

    # Multi-tool groups: groups with more than one event
    multi_tool_groups = {k: v for k, v in groups.items() if len(v) > 1}
    multi_tool_ratio = len(multi_tool_groups) / total_groups

    # Build shape counter over multi-tool groups only
    shape_counter: Counter = Counter()
    for pg_events in multi_tool_groups.values():
        tool_classes = [_classify_tool(tn, ti) for tn, ti in pg_events]
        shape = tuple(sorted(tool_classes))
        shape_counter[shape] += 1

    if not shape_counter:
        return S4aResult(
            fires=False,
            multi_tool_ratio=multi_tool_ratio,
            top_shape_share=0.0,
            top_shape=(),
        )

    top_shape, top_count = shape_counter.most_common(1)[0]
    top_shape_share = top_count / total_groups

    fires = (
        multi_tool_ratio >= S4A_MIN_MULTI_TOOL_RATIO
        and top_shape_share >= S4A_MIN_TOP_SHAPE_SHARE
        and _is_exploration_shape(top_shape)
    )

    return S4aResult(
        fires=fires,
        multi_tool_ratio=multi_tool_ratio,
        top_shape_share=top_shape_share,
        top_shape=top_shape,
    )


def _is_instrumentation(command: str, prefixes: frozenset[str]) -> bool:
    """Return True if *command* starts with an instrumentation prefix.

    Algorithm:
    1. Split command on newlines and strip each line.
    2. Skip blank lines and comment lines (starting with '#').
    3. Get the first token of the first non-comment line.
    4. Return True if that token is in *prefixes*.
    """
    for raw_line in command.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            continue
        # First non-comment, non-blank line found
        tokens = line.split()
        first_token = tokens[0] if tokens else ""
        return first_token in prefixes
    return False


def score_s4b(
    events_path: pathlib.Path | str,
    *,
    prefixes: frozenset[str] = frozenset({"echo", "STEP", "Step", "Check", "Note"}),
) -> S4bResult:
    """S4b: instrumentation bash pattern detection.

    Python-only signal — fires when ALL three conditions hold:
      1. total tool:pre count >= S4B_MIN_TOOL_PRE (40)
      2. instrumentation_ratio >= S4B_MIN_INSTRUMENTATION_RATIO (0.30)
      3. orchestrator:iteration_start count >= S3_CANDIDATE_THRESHOLD (20)

    Where instrumentation_ratio = instrumentation_count / bash_count
    (0.0 when bash_count == 0).
    """
    total_tool_pre: int = 0
    bash_count: int = 0
    instrumentation_count: int = 0
    iteration_count: int = 0

    for ev in _iter_events(events_path):
        event_type = ev.get("event")
        if event_type == "orchestrator:iteration_start":
            iteration_count += 1
        elif event_type == "tool:pre":
            total_tool_pre += 1
            data = ev.get("data", {})
            if data.get("tool_name") == "bash":
                bash_count += 1
                command = (data.get("tool_input") or {}).get("command", "")
                if _is_instrumentation(command, prefixes):
                    instrumentation_count += 1

    instrumentation_ratio: float = instrumentation_count / bash_count if bash_count > 0 else 0.0

    fires = (
        total_tool_pre >= S4B_MIN_TOOL_PRE
        and instrumentation_ratio >= S4B_MIN_INSTRUMENTATION_RATIO
        and iteration_count >= S3_CANDIDATE_THRESHOLD
    )

    return S4bResult(
        fires=fires,
        instrumentation_ratio=instrumentation_ratio,
        instrumentation_count=instrumentation_count,
        total_bash_count=bash_count,
    )


def score_s4c(events_path: pathlib.Path | str) -> int:
    """S4c: duplicate tool-call input detection.

    Counts occurrences of each unique tool input fingerprint (md5 of tool_name + tool_input).
    Returns the maximum count across all fingerprints (0 if no tool:pre events).
    Fires when the maximum count is >= S4C_THRESHOLD (4).
    """
    counts: Counter = Counter()
    for ev in _iter_events(events_path):
        if ev.get("event") != "tool:pre":
            continue
        data = ev.get("data", {})
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}
        input_identity = hashlib.md5(
            (tool_name + ":" + json.dumps(tool_input, sort_keys=True)).encode(),
            usedforsecurity=False,
        ).hexdigest()
        counts[input_identity] += 1
    return max(counts.values(), default=0)


def score_s4d(events_path: pathlib.Path | str) -> int:
    """S4d: duplicate tool-call input-pair detection.

    Two-pass algorithm:
    1. Collect input fingerprints from tool:pre events, keyed by tool_call_id.
    2. Join with tool:post events on tool_call_id and compute (input_fp, output_identity) pairs.

    Returns the maximum count of any single (input, output) pair.
    Fires when the maximum count is >= S4D_THRESHOLD (3).
    """
    # First pass: collect tool:pre input fingerprints keyed by tool_call_id
    pre_fingerprints: dict[str, str] = {}
    for ev in _iter_events(events_path):
        if ev.get("event") != "tool:pre":
            continue
        data = ev.get("data", {})
        tool_call_id = data.get("tool_call_id")
        if not tool_call_id:
            continue
        tool_name = data.get("tool_name", "")
        tool_input = data.get("tool_input") or {}
        input_identity = hashlib.md5(
            (tool_name + ":" + json.dumps(tool_input, sort_keys=True)).encode(),
            usedforsecurity=False,
        ).hexdigest()
        pre_fingerprints[tool_call_id] = input_identity

    # Second pass: join with tool:post on tool_call_id, compute pair identity
    counts: Counter = Counter()
    for ev in _iter_events(events_path):
        if ev.get("event") != "tool:post":
            continue
        data = ev.get("data", {})
        tool_call_id = data.get("tool_call_id")
        if not tool_call_id or tool_call_id not in pre_fingerprints:
            continue
        result = data.get("result") or {}
        success = result.get("success", False)
        output_hash = hashlib.sha256(str(result.get("output", "")).encode()).hexdigest()[:16]
        output_identity = (success, output_hash)
        pair_identity = (pre_fingerprints[tool_call_id], output_identity)
        counts[pair_identity] += 1
    return max(counts.values(), default=0)


_STALE_HOURS: int = 2


def score_s5(metadata_path, ref_last_event_ts) -> bool:
    """S5: stale session detection (metadata timestamp lag).

    Returns True when:
      - metadata.json exists and is parseable
      - metadata['status'] == 'running'
      - the last parseable timestamp in the sibling events.jsonl is more than
        _STALE_HOURS hours before *ref_last_event_ts*

    Returns False for any other condition (missing file, parse error, non-running
    status, gap under threshold, or no parseable events).

    *ref_last_event_ts* may be a :class:`datetime` or an ISO-8601 :class:`str`.
    """
    meta_path = pathlib.Path(metadata_path)

    # Guard: metadata must exist and be parseable
    if not meta_path.exists():
        return False
    try:
        with meta_path.open(encoding="utf-8") as fh:
            meta = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return False

    # Guard: session must be in 'running' state
    if meta.get("status") != "running":
        return False

    # Parse ref_last_event_ts
    if isinstance(ref_last_event_ts, str):
        try:
            ref_dt = datetime.fromisoformat(ref_last_event_ts.replace("Z", "+00:00"))
        except ValueError:
            _LOG.warning("could not parse ref_last_event_ts %r", ref_last_event_ts)
            return False
    else:
        ref_dt = ref_last_event_ts

    # Find the last parseable timestamp in sibling events.jsonl
    events_path = meta_path.parent / "events.jsonl"
    last_event_dt: datetime | None = None
    for ev in _iter_events(events_path):
        ts = ev.get("timestamp")
        try:
            event_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            last_event_dt = event_dt
        except (ValueError, AttributeError):
            continue

    if last_event_dt is None:
        return False

    gap = ref_dt - last_event_dt
    return gap.total_seconds() > _STALE_HOURS * 3600


_CANCEL_EVENTS: frozenset[str] = frozenset({"session:cancelled", "user:interrupt"})


def score_s6(events_path: pathlib.Path | str) -> int:
    """S6: cancellation event count."""
    return sum(1 for ev in _iter_events(events_path) if ev.get("event") in _CANCEL_EVENTS)


def score_s7(events_path: pathlib.Path | str) -> int:
    """S7: maximum read_file tool:pre events in any single iteration.

    Iteration boundaries are marked by orchestrator:iteration_start events.
    Events before the first iteration_start are NOT counted.
    Returns the maximum count across all iterations (0 if no iterations or no
    read_file events within any iteration).
    """
    max_count: int = 0
    current_count: int = 0
    in_iteration: bool = False

    for ev in _iter_events(events_path):
        event = ev.get("event")
        if event == "orchestrator:iteration_start":
            max_count = max(max_count, current_count)
            current_count = 0
            in_iteration = True
        elif event == "tool:pre" and in_iteration:
            if ev.get("data", {}).get("tool_name") == "read_file":
                current_count += 1

    # Flush the last iteration
    max_count = max(max_count, current_count)
    return max_count


_MIN_PARALLEL_BASH: int = 3


def score_s8(events_path: pathlib.Path | str) -> int:
    """S8: maximum consecutive-iteration streak where each iteration has a parallel
    bash group of >= _MIN_PARALLEL_BASH bash tool:pre events.

    A 'parallel bash group' is defined as multiple tool:pre events sharing the
    same parallel_group_id all with tool_name='bash'.  An iteration qualifies if
    any single parallel group in it has >= _MIN_PARALLEL_BASH bash calls.

    Returns the maximum such consecutive streak across the session.
    """
    max_streak: int = 0
    current_streak: int = 0
    in_iteration: bool = False
    current_iter_pg_bash: dict[str, int] = {}

    def _iter_qualifies(pg_bash: dict[str, int]) -> bool:
        return any(count >= _MIN_PARALLEL_BASH for count in pg_bash.values())

    for ev in _iter_events(events_path):
        event = ev.get("event")
        if event == "orchestrator:iteration_start":
            if in_iteration:
                if _iter_qualifies(current_iter_pg_bash):
                    current_streak += 1
                else:
                    max_streak = max(max_streak, current_streak)
                    current_streak = 0
            in_iteration = True
            current_iter_pg_bash = {}
        elif event == "tool:pre" and in_iteration:
            data = ev.get("data", {})
            if data.get("tool_name") == "bash":
                pg_id = data.get("parallel_group_id")
                if pg_id:
                    current_iter_pg_bash[pg_id] = current_iter_pg_bash.get(pg_id, 0) + 1

    # Flush the last iteration
    if in_iteration:
        if _iter_qualifies(current_iter_pg_bash):
            current_streak += 1
        else:
            max_streak = max(max_streak, current_streak)
            current_streak = 0

    return max(max_streak, current_streak)


def score_s9a(events_path: pathlib.Path | str) -> int:
    """S9a: delegate call count."""
    return sum(
        1
        for ev in _iter_events(events_path)
        if ev.get("event") == "tool:pre" and ev.get("data", {}).get("tool_name") == "delegate"
    )


def score_s9b(events_path: pathlib.Path | str, *, size_threshold: int = 20_000) -> int:
    """S9b: maximum delegate result payload size.

    Iterates ``tool:post`` events where ``data.tool_name == 'delegate'``.

    For each such event, inspects ``data.result.output``:
    - If ``output`` is a dict containing a ``'response'`` key (standard delegate
      envelope), measures ``len(output['response'])``.
    - Otherwise measures ``len(str(output))``.

    Returns the maximum size found across all matching events (0 if none).
    The signal fires when the returned value is >= *size_threshold* (default 20,000).
    """
    max_size = 0
    for ev in _iter_events(events_path):
        if ev.get("event") != "tool:post":
            continue
        data = ev.get("data", {})
        if data.get("tool_name") != "delegate":
            continue
        result = data.get("result") or {}
        output = result.get("output")
        if isinstance(output, dict) and "response" in output:
            size = len(output["response"])
        else:
            size = len(str(output)) if output is not None else 0
        max_size = max(max_size, size)
    return max_size


_CODE_FENCE_RE: re.Pattern = re.compile(r"```.*?```", re.DOTALL)
_MAX_DENSITY: float = 0.05


def score_s9c_size(
    events_path: pathlib.Path | str,
    *,
    size_threshold: int = 30_000,
) -> bool:
    """S9c (size): synthesis-output narrative density check (Python-only).

    Fires when ANY ``tool:post`` event with ``tool_name == 'delegate'`` has a
    response text longer than *size_threshold* characters AND a code-fence
    character density below ``_MAX_DENSITY`` (5 %).

    Response text extraction follows the same envelope convention as
    :func:`score_s9b`:

    - If ``result.output`` is a dict containing a ``'response'`` key, use
      ``output['response']``.
    - Otherwise stringify ``output``.

    Code-fence density is computed as::

        fence_chars / len(text)

    where ``fence_chars`` is the sum of the lengths of all sub-strings matched
    by ``_CODE_FENCE_RE`` (i.e. the full fence block including the backtick
    delimiters and the body).

    Returns ``True`` as soon as a qualifying event is found; ``False`` if no
    such event exists.
    """
    for ev in _iter_events(events_path):
        if ev.get("event") != "tool:post":
            continue
        data = ev.get("data", {})
        if data.get("tool_name") != "delegate":
            continue
        result = data.get("result") or {}
        output = result.get("output")
        if isinstance(output, dict) and "response" in output:
            text = output["response"]
        else:
            text = str(output) if output is not None else ""
        if len(text) <= size_threshold:
            continue
        fence_bodies = _CODE_FENCE_RE.findall(text)
        fence_chars = sum(len(fb) for fb in fence_bodies)
        density = fence_chars / len(text)
        if density < _MAX_DENSITY:
            return True
    return False


def score_s9c_self(events_path: pathlib.Path | str) -> int:
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


def score_s9_combined(
    events_path: pathlib.Path | str,
    *,
    s9a_threshold: int = 5,
    s9b_threshold: int = 20_000,
    s9c_size_threshold: int = 30_000,
) -> bool:
    """S9 combined: fires when S9a, S9b, and S9c (size OR self) all fire together.

    Returns ``False`` early if either the S9a delegate count or the S9b maximum
    delegate result size falls below their respective thresholds.  Otherwise
    evaluates S9c via both sub-paths and returns the result:

    - ``score_s9c_size(events_path, size_threshold=s9c_size_threshold)`` fires, OR
    - ``score_s9c_self(events_path) >= 1``

    Note: The Cypher template (Q-S9-combined) can only test the S9a + S9b +
    S9c-self path.  The S9c-size sub-path requires the JSONL fallback because
    narrative density (code-fence ratio) has no Cypher representation.
    """
    if score_s9a(events_path) < s9a_threshold:
        return False
    if score_s9b(events_path) < s9b_threshold:
        return False
    s9c: bool = score_s9c_size(events_path, size_threshold=s9c_size_threshold) or (
        score_s9c_self(events_path) >= 1
    )
    return s9c


def score_4_1(
    session_scores: list[tuple[str, SignalScores]],
    *,
    compound_threshold: int = 2,
    volume_threshold: int = SCORE_4_1_VOLUME_THRESHOLD,
) -> dict:
    """Aggregate: produce a summary dict from a list of (session_id, SignalScores) pairs.

    Returns a dict with keys:
    - total_sessions: int
    - any_signal_rate: float  (fraction of sessions with compound_score >= 1)
    - compound_rate: float    (fraction of sessions with compound_score >= compound_threshold)
    - triple_rate: float      (fraction of sessions with compound_score >= 3)

    If total_sessions == 0, all rates are 0.0.
    """
    total = len(session_scores)
    if total == 0:
        return {
            "total_sessions": 0,
            "any_signal_rate": 0.0,
            "compound_rate": 0.0,
            "triple_rate": 0.0,
        }

    any_count = 0
    compound_count = 0
    triple_count = 0

    for _session_id, scores in session_scores:
        k = scores.compound_score()
        if k >= 1:
            any_count += 1
        if k >= compound_threshold:
            compound_count += 1
        if k >= 3:
            triple_count += 1

    return {
        "total_sessions": total,
        "any_signal_rate": any_count / total,
        "compound_rate": compound_count / total,
        "triple_rate": triple_count / total,
    }


def score_session(events_path: pathlib.Path | str) -> SignalScores:
    """Compute all signal scores for a session, returning a SignalScores instance."""
    raise NotImplementedError
