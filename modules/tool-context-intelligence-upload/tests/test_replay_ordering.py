"""Tests for the time-consistent global replay feed (faithful ordering).

Covers the new merged, globally timestamp-ordered event stream
(``_iter_merged_events``) and the pacing it drives in ``run_upload`` --
NOT any server-side change. Every test here that exercises ``run_upload``
mocks ``httpx.Client`` with a ``spec=["post"]`` mock, which makes any
access to a method other than ``.post`` (e.g. a ``.get()`` for a
hypothetical ``/status`` poll) raise ``AttributeError`` -- a hard,
mechanical guarantee that no such call exists, not just an absence
assertion.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from amplifier_module_tool_context_intelligence_upload.uploader import (
    _iter_merged_events,
    run_upload,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_session_with_events(
    tmp_path: Path,
    session_id: str,
    events: list[dict[str, Any]],
) -> tuple[Path, dict[str, Any]]:
    """Create a session dir with metadata.json and events.jsonl.

    Unlike test_uploader.py's ``_write_session`` helper, *events* here may
    carry a top-level ``timestamp`` field (the real context-intelligence-
    native shape) so ordering/pacing can be exercised.
    """
    session_dir = tmp_path / f"session-{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    metadata = {"session_id": session_id, "format": "context-intelligence"}
    (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (session_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events),
        encoding="utf-8",
    )
    return session_dir, metadata


def _event(
    event: str,
    timestamp: str | None = None,
    workspace: str = "ws",
    **data: Any,
) -> dict[str, Any]:
    rec: dict[str, Any] = {"event": event, "workspace": workspace, "data": data}
    if timestamp is not None:
        rec["timestamp"] = timestamp
    return rec


def _mock_response(status_code: int = 200) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    return response


def _spec_client() -> MagicMock:
    """A mock httpx.Client restricted to ONLY the ``post`` attribute.

    Any access to ``.get``, ``.request``, etc. raises AttributeError --
    a mechanical guarantee no non-POST / no /status call can silently
    succeed against this mock.
    """
    return MagicMock(spec=["post"])


# ---------------------------------------------------------------------------
# TestMergedStreamOrdering -- _iter_merged_events directly
# ---------------------------------------------------------------------------


class TestMergedStreamOrdering:
    """Tests for _iter_merged_events -- the global timestamp-ordered merge."""

    def test_globally_non_decreasing_timestamps_across_sessions(self, tmp_path: Path) -> None:
        """Interleaved-by-real-time sessions merge into one non-decreasing stream."""
        s1_dir, s1_meta = _write_session_with_events(
            tmp_path,
            "s1",
            [
                _event("a", "2026-01-01T00:00:00+00:00"),
                _event("c", "2026-01-01T00:00:05+00:00"),
                _event("e", "2026-01-01T00:00:09+00:00"),
            ],
        )
        s2_dir, s2_meta = _write_session_with_events(
            tmp_path,
            "s2",
            [
                _event("b", "2026-01-01T00:00:01+00:00"),
                _event("d", "2026-01-01T00:00:06+00:00"),
            ],
        )
        sessions = [(s1_dir, s1_meta), (s2_dir, s2_meta)]

        merged = list(_iter_merged_events(sessions))

        timestamps = [m.timestamp for m in merged]
        known_timestamps = [t for t in timestamps if t is not None]
        assert len(known_timestamps) == len(timestamps)
        assert known_timestamps == sorted(known_timestamps)
        # The true global interleave: a(0) b(1) c(5) d(6) e(9)
        assert [m.raw_line for m in merged] == [
            json.dumps(_event("a", "2026-01-01T00:00:00+00:00")),
            json.dumps(_event("b", "2026-01-01T00:00:01+00:00")),
            json.dumps(_event("c", "2026-01-01T00:00:05+00:00")),
            json.dumps(_event("d", "2026-01-01T00:00:06+00:00")),
            json.dumps(_event("e", "2026-01-01T00:00:09+00:00")),
        ]

    def test_missing_timestamps_sort_stably_after_known_ones(self, tmp_path: Path) -> None:
        """A line with no timestamp never crashes and sorts after known timestamps."""
        s1_dir, s1_meta = _write_session_with_events(
            tmp_path,
            "s1",
            [_event("known", "2026-01-01T00:00:00+00:00")],
        )
        s2_dir, s2_meta = _write_session_with_events(
            tmp_path,
            "s2",
            [_event("unknown")],  # no timestamp field at all
        )
        sessions = [(s1_dir, s1_meta), (s2_dir, s2_meta)]

        merged = list(_iter_merged_events(sessions))

        assert [json.loads(m.raw_line)["event"] for m in merged] == ["known", "unknown"]
        assert merged[1].timestamp is None

    def test_no_timestamps_anywhere_reproduces_original_list_order(self, tmp_path: Path) -> None:
        """When nothing has a timestamp, the tie-break reproduces the original

        parent-first, in-list-order feed (session_id tie-break sorts the
        same way the input list already does) -- the backward-compatible
        case every pre-existing fixture in test_uploader.py relies on.
        """
        s1_dir, s1_meta = _write_session_with_events(
            tmp_path, "sess-1", [_event("e0"), _event("e1")]
        )
        s2_dir, s2_meta = _write_session_with_events(
            tmp_path, "sess-2", [_event("e2"), _event("e3"), _event("e4")]
        )
        sessions = [(s1_dir, s1_meta), (s2_dir, s2_meta)]

        merged = list(_iter_merged_events(sessions))

        assert [json.loads(m.raw_line)["event"] for m in merged] == [
            "e0",
            "e1",
            "e2",
            "e3",
            "e4",
        ]


# ---------------------------------------------------------------------------
# TestParentChildInterleave -- run_upload end-to-end ordering (X1 / X2)
# ---------------------------------------------------------------------------


class TestParentChildInterleave:
    """The correctness case this fix targets: a spawned sub-session drains

    BEFORE its parent resumes, reproducing live capture timing.
    """

    def _build_parent_child_fixture(self, tmp_path: Path) -> list[tuple[Path, dict[str, Any]]]:
        # Parent: starts, spawns a child, and only resumes (session:end)
        # AFTER the child's timestamps -- the exact shape that stranded a
        # sub-session under the old whole-session, parent-first feed.
        parent_dir, parent_meta = _write_session_with_events(
            tmp_path,
            "parent-1",
            [
                _event("session:start", "2026-01-01T00:00:00+00:00", workspace="parent-ws"),
                _event("tool:delegate_start", "2026-01-01T00:00:01+00:00", workspace="parent-ws"),
                _event("session:end", "2026-01-01T00:00:10+00:00", workspace="parent-ws"),
            ],
        )
        # Child: fully contained between the parent's spawn and resume.
        child_dir, child_meta = _write_session_with_events(
            tmp_path,
            "child-1",
            [
                _event("session:start", "2026-01-01T00:00:02+00:00", workspace="child-ws"),
                _event("session:end", "2026-01-01T00:00:03+00:00", workspace="child-ws"),
            ],
        )
        # Sessions list is PARENT-FIRST (the discovery order BFS would
        # produce) -- the merge must still interleave by real time.
        return [(parent_dir, parent_meta), (child_dir, child_meta)]

    def test_child_drains_before_parent_resumes(self, tmp_path: Path) -> None:
        sessions = self._build_parent_child_fixture(tmp_path)
        tracker = MagicMock()
        captured: list[tuple[str, str]] = []

        with patch("httpx.Client") as mock_client_cls:
            mock_client = _spec_client()
            mock_client_cls.return_value.__enter__.return_value = mock_client

            def capture_post(url: str, **kwargs: Any) -> MagicMock:
                payload = kwargs.get("json", {})
                captured.append((payload["event"], payload["workspace"]))
                return _mock_response(200)

            mock_client.post.side_effect = capture_post

            result = run_upload(sessions, "https://server", "api-key", tracker)

        assert result.success is True
        order = captured
        parent_spawn = ("tool:delegate_start", "parent-ws")
        child_end = ("session:end", "child-ws")
        parent_end = ("session:end", "parent-ws")

        assert parent_spawn in order
        assert child_end in order
        assert parent_end in order

        # X1: parent's spawn event is fed BEFORE the child's session:end.
        assert order.index(parent_spawn) < order.index(child_end)
        # X2: the child's session:end is fed BEFORE the parent's session:end.
        assert order.index(child_end) < order.index(parent_end)

        # Full expected global order, spelled out explicitly.
        assert order == [
            ("session:start", "parent-ws"),
            ("tool:delegate_start", "parent-ws"),
            ("session:start", "child-ws"),
            ("session:end", "child-ws"),
            ("session:end", "parent-ws"),
        ]

    def test_child_and_parent_both_reported_uploaded(self, tmp_path: Path) -> None:
        """Both sessions still complete and are counted, despite interleaving."""
        sessions = self._build_parent_child_fixture(tmp_path)
        tracker = MagicMock()

        with patch("httpx.Client") as mock_client_cls:
            mock_client = _spec_client()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)

            result = run_upload(sessions, "https://server", "api-key", tracker)

        assert result.success is True
        assert result.sessions_uploaded == 2
        assert result.events_uploaded == 5
        # start_session called once per session on first appearance, plus
        # once more for the parent when the interleave transitions BACK to
        # it after the child's burst (see run_upload's tracker adaptation
        # note) -- 3 calls total: parent, child, parent-again.
        assert tracker.start_session.call_count == 3
        assert tracker.session_completed.call_count == 2


# ---------------------------------------------------------------------------
# TestPacing -- real inter-event gaps, capped and floored
# ---------------------------------------------------------------------------


class TestPacing:
    """time.sleep is called with the capped real gap, floored by event_delay_s."""

    def test_sleep_uses_capped_real_gap_no_floor(self, tmp_path: Path) -> None:
        """Gaps of 0.5s and 10s (capped to max_gap_s=2.0) with event_delay_s=0."""
        session_dir, metadata = _write_session_with_events(
            tmp_path,
            "s1",
            [
                _event("e0", "2026-01-01T00:00:00.000000+00:00"),
                _event("e1", "2026-01-01T00:00:00.500000+00:00"),  # +0.5s
                _event("e2", "2026-01-01T00:00:10.500000+00:00"),  # +10s -> capped
            ],
        )
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()
        sleeps: list[float] = []

        with (
            patch("httpx.Client") as mock_client_cls,
            patch(
                "amplifier_module_tool_context_intelligence_upload.uploader.time.sleep"
            ) as mock_sleep,
        ):
            mock_client = _spec_client()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)
            mock_sleep.side_effect = lambda s: sleeps.append(s)

            result = run_upload(sessions, "https://server", "api-key", tracker, max_gap_s=2.0)

        assert result.success is True
        # No sleep before the very first event; then 0.5s; then capped 2.0s.
        assert sleeps == [0.5, 2.0]

    def test_event_delay_s_floors_the_gap_based_sleep(self, tmp_path: Path) -> None:
        """A larger event_delay_s floor wins over a smaller capped gap."""
        session_dir, metadata = _write_session_with_events(
            tmp_path,
            "s1",
            [
                _event("e0", "2026-01-01T00:00:00.000000+00:00"),
                _event("e1", "2026-01-01T00:00:00.100000+00:00"),  # +0.1s gap
            ],
        )
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()
        sleeps: list[float] = []

        with (
            patch("httpx.Client") as mock_client_cls,
            patch(
                "amplifier_module_tool_context_intelligence_upload.uploader.time.sleep"
            ) as mock_sleep,
        ):
            mock_client = _spec_client()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)
            mock_sleep.side_effect = lambda s: sleeps.append(s)

            result = run_upload(
                sessions,
                "https://server",
                "api-key",
                tracker,
                event_delay_s=0.75,
                max_gap_s=2.0,
            )

        assert result.success is True
        # gap(0.1) < event_delay_s(0.75) -> floor wins.
        assert sleeps == [0.75]

    def test_no_previous_timestamp_means_no_sleep_before_first_event(self, tmp_path: Path) -> None:
        """Missing/unparseable timestamps degrade to a zero gap, never crash --

        and event_delay_s alone (with no real timestamps anywhere) still
        floors every inter-event sleep from the second event onward.
        """
        session_dir, metadata = _write_session_with_events(
            tmp_path,
            "s1",
            [_event("e0"), _event("e1"), _event("e2")],  # no timestamps at all
        )
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()
        sleeps: list[float] = []

        with (
            patch("httpx.Client") as mock_client_cls,
            patch(
                "amplifier_module_tool_context_intelligence_upload.uploader.time.sleep"
            ) as mock_sleep,
        ):
            mock_client = _spec_client()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)
            mock_sleep.side_effect = lambda s: sleeps.append(s)

            result = run_upload(sessions, "https://server", "api-key", tracker, event_delay_s=0.2)

        assert result.success is True
        # 3 events, no timestamps: no sleep before e0, then floor(0.2) before
        # e1 and again before e2.
        assert sleeps == [0.2, 0.2]

    def test_zero_event_delay_and_zero_gap_never_calls_sleep(self, tmp_path: Path) -> None:
        """The pacing change must not introduce sleeping where none existed

        before (event_delay_s=0.0 default, no timestamps -> gap always 0)."""
        session_dir, metadata = _write_session_with_events(
            tmp_path, "s1", [_event("e0"), _event("e1"), _event("e2")]
        )
        sessions = [(session_dir, metadata)]
        tracker = MagicMock()

        with (
            patch("httpx.Client") as mock_client_cls,
            patch(
                "amplifier_module_tool_context_intelligence_upload.uploader.time.sleep"
            ) as mock_sleep,
        ):
            mock_client = _spec_client()
            mock_client_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _mock_response(200)

            result = run_upload(sessions, "https://server", "api-key", tracker)

        assert result.success is True
        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# TestNoServerSignalOtherThanEvents -- the hard constraint
# ---------------------------------------------------------------------------


class TestNoServerSignalOtherThanEvents:
    """Mechanically guarantees the ONLY network call is POST {server_url}/events.

    ``_spec_client()`` restricts the mock to just ``.post`` -- any call to
    ``.get``, ``.request``, ``.head``, etc. (e.g. a hypothetical /status
    poll) raises AttributeError, failing the test loudly rather than
    silently succeeding against an auto-created MagicMock attribute.
    """

    def test_only_post_to_events_endpoint_is_ever_called(self, tmp_path: Path) -> None:
        sessions = [
            _write_session_with_events(
                tmp_path,
                "parent-1",
                [
                    _event("session:start", "2026-01-01T00:00:00+00:00"),
                    _event("tool:delegate_start", "2026-01-01T00:00:01+00:00"),
                    _event("session:end", "2026-01-01T00:00:10+00:00"),
                ],
            ),
            _write_session_with_events(
                tmp_path,
                "child-1",
                [
                    _event("session:start", "2026-01-01T00:00:02+00:00"),
                    _event("session:end", "2026-01-01T00:00:03+00:00"),
                ],
            ),
        ]
        tracker = MagicMock()
        urls_called: list[str] = []

        with patch("httpx.Client") as mock_client_cls:
            mock_client = _spec_client()
            mock_client_cls.return_value.__enter__.return_value = mock_client

            def capture_post(url: str, **kwargs: Any) -> MagicMock:
                urls_called.append(url)
                return _mock_response(200)

            mock_client.post.side_effect = capture_post

            result = run_upload(sessions, "https://server", "api-key", tracker)

        assert result.success is True
        assert len(urls_called) == 5
        assert all(url == "https://server/events" for url in urls_called)
        assert all("/status" not in url for url in urls_called)
        # mock_client.post is the ONLY attribute this mock exposes at all
        # (spec=["post"]) -- accessing anything else would already have
        # raised AttributeError above if the code under test had tried it.
        assert mock_client.post.call_count == 5
