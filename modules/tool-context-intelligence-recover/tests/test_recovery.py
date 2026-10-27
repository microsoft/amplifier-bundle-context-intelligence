"""Contract tests for compact, native-only recovery V2."""

from __future__ import annotations

import hashlib
import json
import os
import tracemalloc
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from amplifier_module_hook_context_intelligence.config_resolver import Destination

from amplifier_module_tool_context_intelligence_recover import cli, recovery
from amplifier_module_tool_context_intelligence_recover.recovery import (
    DestinationConfig,
    RecoveryRunner,
    discover_native_artifacts,
    resolve_scan_roots,
)


def _event(session_id: str | None, timestamp: str = "2026-09-15T00:00:00Z") -> bytes:
    data: dict[str, str] = {"timestamp": timestamp}
    if session_id is not None:
        data["session_id"] = session_id
    return (
        json.dumps({"event": "session:start", "workspace": "project", "data": data}).encode()
        + b"\n"
    )


def _capture(
    root: Path,
    session_id: str = "session-1",
    *,
    working_dir: str = "/workspace/app",
    parent_id: str = "",
    version: str = "1.0.0",
    body: bytes | None = None,
) -> Path:
    capture = root / "project" / "sessions" / session_id / "context-intelligence"
    capture.mkdir(parents=True)
    (capture / "metadata.json").write_text(
        json.dumps(
            {
                "format": "context-intelligence",
                "version": version,
                "session_id": session_id,
                "workspace": "project",
                "parent_id": parent_id,
                "working_dir": working_dir,
                "status": "completed",
                "ended_at": "2026-09-15T01:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    events = capture / "events.jsonl"
    events.write_bytes(body if body is not None else _event(session_id))
    return events


def _config(*destinations: Destination) -> DestinationConfig:
    return DestinationConfig(
        destinations={destination.name: destination for destination in destinations},
        invalid_names=(),
        base_path=Path("/not-used"),
    )


@pytest.fixture
def configured_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recovery.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setattr(
        recovery,
        "resolve_scan_roots",
        lambda _config, explicit: [Path(path).absolute() for path in explicit],
    )
    destination = Destination("default", "https://default.example", "key", ("**",), ())
    monkeypatch.setattr(recovery, "load_destination_config", lambda _path: _config(destination))
    monkeypatch.setattr(
        recovery,
        "load_effective_destination_config",
        lambda _working_dir, settings_path: (recovery.load_destination_config(settings_path), None),
    )


def _server(requests: list[httpx.Request], status: int = 202) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        return httpx.Response(status, json={"status": "queued"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _assert_indexed_next_cursor_plan(runner: RecoveryRunner) -> None:
    target = runner.checkpoint.connection.execute(
        "SELECT target_fingerprint FROM cursor LIMIT 1"
    ).fetchone()["target_fingerprint"]
    plan = list(
        runner.checkpoint.connection.execute(
            f"EXPLAIN QUERY PLAN {recovery._NEXT_CURSOR_SQL}", (target,)
        )
    )
    details = [str(row["detail"]) for row in plan]
    assert any("cursor_target_pending_order" in detail for detail in details)
    assert not any("USE TEMP B-TREE" in detail for detail in details)


def test_v2_checkpoint_is_compact_locked_and_leaves_v1_untouched(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(tmp_path)
    legacy = tmp_path / "state" / "job.sqlite3"
    legacy.parent.mkdir()
    legacy.write_bytes(b"v1 must remain untouched")
    runner = RecoveryRunner(state_dir=legacy.parent, job_id="job")
    try:
        summary = runner.run([tmp_path], dry_run=True)
        assert summary.legacy_checkpoint_present is True
        assert runner.checkpoint.path.name == "job.v2.sqlite3"
        assert legacy.read_bytes() == b"v1 must remain untouched"
        with pytest.raises(RuntimeError, match="already locked"):
            RecoveryRunner(state_dir=legacy.parent, job_id="job")
        tables = {
            row[0]
            for row in runner.checkpoint.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables == {"artifact", "cursor"}
        cursor_indexes = {
            row[1] for row in runner.checkpoint.connection.execute("PRAGMA index_list(cursor)")
        }
        assert "cursor_artifact_target_state" in cursor_indexes
        assert runner.checkpoint.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        stat_facts = runner.checkpoint.connection.execute(
            """
            SELECT source_size,source_mtime_ns,source_inode,
                   metadata_size,metadata_mtime_ns,metadata_inode
            FROM artifact
            """
        ).fetchone()
        assert all(value > 0 for value in stat_facts)
    finally:
        runner.close()


def test_discovery_is_file_led_exact_and_has_no_rglob_or_backlog_fetchall(tmp_path: Path) -> None:
    good = _capture(tmp_path)
    lookalike = tmp_path / "not-a-project" / "events.jsonl"
    lookalike.parent.mkdir(parents=True)
    lookalike.write_bytes(b"{}\n")

    assert list(discover_native_artifacts([tmp_path])) == [good]
    source = Path(recovery.__file__).read_text(encoding="utf-8")
    assert ".rglob(" not in source
    assert ".fetchall(" not in source
    assert "LIMIT 1" in source


def test_source_symlink_outside_owner_home_is_quarantined(
    tmp_path: Path, configured_roots: None
) -> None:
    events = _capture(tmp_path)
    outside = tmp_path.parent / "outside-events.jsonl"
    outside.write_bytes(events.read_bytes())
    events.unlink()
    events.symlink_to(outside)

    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="outside")
    try:
        summary = runner.run([tmp_path], dry_run=True)
    finally:
        runner.close()

    assert summary.inventory == {"quarantined": 1}


def test_100k_line_plan_has_only_compact_tables_and_bounded_peak_memory(
    tmp_path: Path, configured_roots: None
) -> None:
    events = _capture(tmp_path)
    events.write_bytes(_event("session-1") * 100_001)
    tracemalloc.start()
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="large")
    try:
        summary = runner.run([tmp_path], dry_run=True)
        _, peak = tracemalloc.get_traced_memory()
        db_bytes = runner.checkpoint.path.stat().st_size
        rows = runner.checkpoint.connection.execute(
            "SELECT (SELECT COUNT(*) FROM artifact), (SELECT COUNT(*) FROM cursor)"
        ).fetchone()
    finally:
        runner.close()
        tracemalloc.stop()

    assert summary.inventory == {"eligible": 1}
    assert tuple(rows) == (1, 1)
    assert db_bytes < 200_000
    assert peak < 8 * 1024 * 1024


def test_flat_1000_artifact_selection_uses_index_without_temp_sort(
    tmp_path: Path, configured_roots: None
) -> None:
    for number in range(1000):
        _capture(tmp_path, f"flat-{number:04}")

    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="flat-plan")
    try:
        runner.run([tmp_path], dry_run=True)
        _assert_indexed_next_cursor_plan(runner)
    finally:
        runner.close()


def test_progress_cycle_uses_cached_targets_without_checkpoint_aggregates(
    tmp_path: Path, configured_roots: None
) -> None:
    body = _event("session-1") + _event("session-1", "2026-09-15T00:00:01Z")
    for number in range(1000):
        _capture(
            tmp_path,
            f"watch-{number:04}",
            body=body.replace(b"session-1", f"watch-{number:04}".encode()),
        )
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="watch-sql", client=_server(requests)
    )
    try:
        runner.run([tmp_path], dry_run=True)
        traced: list[str] = []
        runner.checkpoint.connection.set_trace_callback(traced.append)
        summary = runner.run(refresh=False)
    finally:
        runner.close()

    assert summary.delivered == 1
    assert len([request for request in requests if request.url.path == "/recovery/events"]) == 1
    progress_sql = "\n".join(traced).upper()
    assert "SELECT DISTINCT" not in progress_sql
    assert "FROM ARTIFACT GROUP BY STATE" not in progress_sql


def test_unchanged_artifact_plan_reuses_durable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    original = recovery._stream_fingerprint
    calls = 0

    def counted(path: Path, metadata: dict[str, object]) -> tuple[int, str, str, str | None]:
        nonlocal calls
        calls += 1
        return original(path, metadata)

    monkeypatch.setattr(recovery, "_stream_fingerprint", counted)
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="fast-path")
    try:
        runner.run([tmp_path], dry_run=True)
        runner.run([tmp_path], dry_run=True)
    finally:
        runner.close()

    assert calls == 1


def test_accepted_artifact_plan_reuses_durable_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path, body=_event("session-1") + _event("session-1", "2026-09-15T00:00:01Z"))
    original = recovery._stream_fingerprint
    calls = 0

    def counted(path: Path, metadata: dict[str, object]) -> tuple[int, str, str, str | None]:
        nonlocal calls
        calls += 1
        return original(path, metadata)

    monkeypatch.setattr(recovery, "_stream_fingerprint", counted)
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="progress", client=_server(requests)
    )
    try:
        assert runner.run([tmp_path]).delivered == 1
        assert runner.run([tmp_path]).delivered == 1
        assert runner.run([tmp_path], dry_run=True).inventory == {"accepted": 1}
    finally:
        runner.close()

    assert calls == 1


def test_incremental_delivery_refreshes_only_touched_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    """One cursor advance must not re-derive state for every artifact in the plan."""
    _capture(tmp_path, "first")
    _capture(tmp_path, "second")
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="incremental-state", client=_server([])
    )
    try:
        runner.run([tmp_path], dry_run=True)
        original = runner.checkpoint.artifact_state_from_cursors
        refreshed: list[str] = []

        def counted(artifact_id: str) -> tuple[str, str]:
            refreshed.append(artifact_id)
            return original(artifact_id)

        monkeypatch.setattr(runner.checkpoint, "artifact_state_from_cursors", counted)
        assert runner.run(refresh=False).delivered == 1
    finally:
        runner.close()

    assert len(refreshed) == 1


def test_invalid_destination_remains_blocked_and_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    invalid = DestinationConfig({}, ("invalid",), Path("/not-used"))
    monkeypatch.setattr(
        recovery, "load_effective_destination_config", lambda *_args: (invalid, None)
    )
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="invalid-destination")
    try:
        summary = runner.run([tmp_path], dry_run=True)
        cursor = runner.checkpoint.connection.execute(
            "SELECT state,error FROM cursor WHERE destination='invalid'"
        ).fetchone()
    finally:
        runner.close()

    assert summary.inventory == {"blocked": 1}
    assert summary.pending is True
    assert tuple(cursor) == ("blocked", "invalid-destination")


def test_refresh_caches_effective_configuration_per_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path, "one")
    _capture(tmp_path, "two")
    original = recovery.load_effective_destination_config
    calls = 0

    def counted(
        working_dir: str, settings_path: Path
    ) -> tuple[DestinationConfig | None, str | None]:
        nonlocal calls
        calls += 1
        return original(working_dir, settings_path)

    monkeypatch.setattr(recovery, "load_effective_destination_config", counted)
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="route-cache")
    try:
        runner.run([tmp_path], dry_run=True)
    finally:
        runner.close()

    assert calls == 1


def test_progress_reuses_client_and_capability_until_the_next_plan_refresh(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(
        tmp_path,
        body=_event("session-1") + _event("session-1", "2026-09-15T00:00:01Z"),
    )
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="capability-cache", client=_server(requests)
    )
    try:
        assert runner.run([tmp_path]).delivered == 1
        assert runner.run(refresh=False).delivered == 1
    finally:
        runner.close()

    assert len([request for request in requests if request.url.path == "/version"]) == 1


def test_oversize_source_line_is_quarantined_without_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    events = _capture(tmp_path)
    monkeypatch.setattr(recovery, "_MAX_SOURCE_LINE_BYTES", 32)
    events.write_bytes(b"{" + b"x" * 64 + b"}\n")

    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="oversize")
    try:
        summary = runner.run([tmp_path], dry_run=True)
    finally:
        runner.close()

    assert summary.inventory == {"quarantined": 1}


def test_metadata_version_and_same_sized_middle_mutation_are_quarantined(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(tmp_path, "bad-version", version="2.0.0")
    events = _capture(
        tmp_path,
        "changed",
        body=_event("changed") + _event("changed", "2026-09-15T00:00:01Z"),
    )
    first = RecoveryRunner(state_dir=tmp_path / "state", job_id="source")
    try:
        assert first.run([tmp_path], dry_run=True).inventory == {"eligible": 1, "quarantined": 1}
    finally:
        first.close()

    original = events.read_bytes()
    events.write_bytes(original.replace(b"00:00:01Z", b"00:00:02Z"))
    requests: list[httpx.Request] = []
    second = RecoveryRunner(state_dir=tmp_path / "state", job_id="source", client=_server(requests))
    try:
        summary = second.run([tmp_path])
    finally:
        second.close()

    assert summary.inventory == {"quarantined": 2}
    assert requests == []


def test_mutation_after_plan_before_delivery_never_posts_stale_record(
    tmp_path: Path, configured_roots: None
) -> None:
    events = _capture(
        tmp_path,
        body=_event("session-1") + _event("session-1", "2026-09-15T00:00:01Z"),
    )
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="post-plan-mutation",
        client=_server(requests),
    )
    try:
        runner.run([tmp_path], dry_run=True)
        original = events.read_bytes()
        events.write_bytes(original.replace(b"00:00:01Z", b"00:00:02Z"))
        summary = runner.run(refresh=False)
    finally:
        runner.close()

    assert summary.inventory == {"quarantined": 1}
    assert [request for request in requests if request.url.path == "/recovery/events"] == []


def test_missing_line_session_id_is_injected_but_conflicting_id_is_not_sent(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(tmp_path, "injected", body=_event(None))
    _capture(tmp_path, "conflict", body=_event("not-conflict"))
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="session", client=_server(requests)
    )
    try:
        summary = runner.run([tmp_path])
    finally:
        runner.close()

    posted = [request for request in requests if request.url.path == "/recovery/events"]
    assert summary.inventory == {"accepted": 1, "quarantined": 1}
    assert json.loads(posted[0].content)["data"]["session_id"] == "injected"


def test_destinations_have_independent_cursors_and_one_post_each(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path, body=_event("session-1") + _event("session-1", "2026-09-15T00:00:01Z"))
    one = Destination("one", "https://one.example", "key", ("**",), ())
    two = Destination("two", "https://two.example", "key", ("**",), ())
    monkeypatch.setattr(recovery, "load_destination_config", lambda _path: _config(one, two))
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="fanout", client=_server(requests))
    try:
        summary = runner.run([tmp_path])
        rows = runner.checkpoint.connection.execute(
            "SELECT destination,ordinal,state FROM cursor ORDER BY destination"
        ).fetchall()
    finally:
        runner.close()

    assert summary.delivered == 2
    assert [(row["destination"], row["ordinal"], row["state"]) for row in rows] == [
        ("one", 1, "pending"),
        ("two", 1, "pending"),
    ]


def test_same_url_destinations_isolate_bad_and_good_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    bad = Destination("bad", "https://shared.example", "bad-key", ("**",), ())
    good = Destination("good", "https://shared.example", "good-key", ("**",), ())
    monkeypatch.setattr(recovery, "load_destination_config", lambda _path: _config(bad, good))
    monkeypatch.setattr(
        RecoveryRunner,
        "_destination_headers",
        staticmethod(lambda destination: {"Authorization": destination.api_key}),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "bad-key":
            return httpx.Response(401)
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        return httpx.Response(202)

    runner = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="same-url",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        summary = runner.run([tmp_path])
        states = {
            row["destination"]: row["state"]
            for row in runner.checkpoint.connection.execute("SELECT destination,state FROM cursor")
        }
    finally:
        runner.close()

    assert summary.delivered == 1
    assert states == {"bad": "blocked", "good": "complete"}
    assert any(request.url.path == "/recovery/events" for request in requests)


def test_credential_repair_reprobes_unchanged_logical_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    current = {"destination": Destination("target", "https://same.example", "bad", ("**",), ())}
    monkeypatch.setattr(
        recovery, "load_destination_config", lambda _path: _config(current["destination"])
    )
    monkeypatch.setattr(
        RecoveryRunner,
        "_destination_headers",
        staticmethod(lambda destination: {"Authorization": destination.api_key}),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "bad":
            return httpx.Response(401)
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        return httpx.Response(202)

    runner = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="credential-repair",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        assert runner.run([tmp_path]).inventory == {"blocked": 1}
        assert runner.run(refresh=False).delivered == 0
        current["destination"] = Destination("target", "https://same.example", "good", ("**",), ())
        repaired = runner.run([tmp_path])
        cursor = runner.checkpoint.connection.execute("SELECT state FROM cursor").fetchone()
    finally:
        runner.close()

    assert repaired.delivered == 1
    assert cursor["state"] == "complete"
    assert [request.headers.get("Authorization") for request in requests].count("bad") == 1
    assert [request.headers.get("Authorization") for request in requests].count("good") == 2


def test_plan_refresh_reloads_runner_injected_keys_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    key_name = "RECOVERY_ROTATED_TEST_KEY"
    monkeypatch.delenv(key_name, raising=False)
    keys_path = tmp_path / ".amplifier" / "keys.env"
    keys_path.parent.mkdir()
    keys_path.write_text(f"{key_name}=bad\n", encoding="utf-8")
    monkeypatch.setattr(recovery.Path, "home", classmethod(lambda _cls: tmp_path))
    hook_config = {
        "destinations": {
            "target": {
                "url": "https://keys.example",
                "api_key": "${RECOVERY_ROTATED_TEST_KEY}",
                "include": ["**"],
            }
        }
    }
    monkeypatch.setattr(
        recovery,
        "load_destination_config",
        lambda _path: recovery._destination_config_from_hook_config(hook_config),
    )
    monkeypatch.setattr(
        recovery,
        "load_effective_destination_config",
        lambda _working_dir, _settings: (
            recovery._destination_config_from_hook_config(hook_config),
            None,
        ),
    )
    monkeypatch.setattr(
        RecoveryRunner,
        "_destination_headers",
        staticmethod(lambda destination: {"Authorization": destination.api_key}),
    )
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("Authorization") == "bad":
            return httpx.Response(401)
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        return httpx.Response(202)

    runner = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="keys-rotation",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    try:
        assert runner.run([tmp_path]).inventory == {"blocked": 1}
        keys_path.write_text(f"{key_name}=good\n", encoding="utf-8")
        assert runner.run([tmp_path]).delivered == 1
    finally:
        runner.close()

    assert [request.headers.get("Authorization") for request in requests].count("bad") == 1
    assert [request.headers.get("Authorization") for request in requests].count("good") == 2


def test_target_or_active_policy_change_resets_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    effective = {
        "config": _config(Destination("target", "https://old.example", "key", ("**",), ()))
    }
    monkeypatch.setattr(
        recovery,
        "load_effective_destination_config",
        lambda _working_dir, _settings: (effective["config"], None),
    )
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="policy")
    try:
        runner.run([tmp_path], dry_run=True)
        runner.checkpoint.connection.execute(
            "UPDATE cursor SET ordinal=7,byte_offset=99,state='pending'"
        )
        effective["config"] = _config(
            Destination("target", "https://new.example", "key", ("**",), ())
        )
        runner.run([tmp_path], dry_run=True)
        target_reset = runner.checkpoint.connection.execute(
            "SELECT ordinal,byte_offset FROM cursor"
        ).fetchone()
        runner.checkpoint.connection.execute(
            "UPDATE cursor SET ordinal=3,byte_offset=33,state='pending'"
        )
        effective["config"] = _config(
            Destination("target", "https://new.example", "key", ("**", "**/team/"), ())
        )
        runner.run([tmp_path], dry_run=True)
        policy_reset = runner.checkpoint.connection.execute(
            "SELECT ordinal,byte_offset FROM cursor"
        ).fetchone()
    finally:
        runner.close()

    assert tuple(target_reset) == (0, 0)
    assert tuple(policy_reset) == (0, 0)


def test_filtered_and_removed_destinations_are_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    active = Destination("active", "https://active.example", "key", ("**",), ())
    filtered = Destination("filtered", "https://filtered.example", "key", ("never/",), ())
    effective = {"config": _config(active, filtered)}
    monkeypatch.setattr(
        recovery,
        "load_effective_destination_config",
        lambda _working_dir, _settings: (effective["config"], None),
    )
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="filters")
    try:
        runner.run([tmp_path], dry_run=True)
        initial = list(
            runner.checkpoint.connection.execute(
                "SELECT destination,state FROM cursor ORDER BY destination"
            )
        )
        effective["config"] = _config(active)
        runner.run([tmp_path], dry_run=True)
        retired = runner.checkpoint.connection.execute(
            "SELECT state FROM cursor WHERE destination='filtered'"
        ).fetchone()
    finally:
        runner.close()

    assert [(row["destination"], row["state"]) for row in initial] == [
        ("active", "pending"),
        ("filtered", "filtered"),
    ]
    assert retired["state"] == "not-required-by-current-policy"


def test_unreadable_effective_config_preserves_pending_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    _capture(tmp_path)
    destination = Destination("target", "https://target.example", "key", ("**",), ())
    available = {"value": True}

    def effective(
        _working_dir: str, _settings: Path
    ) -> tuple[DestinationConfig | None, str | None]:
        return (
            (_config(destination), None)
            if available["value"]
            else (None, "global-settings-unreadable")
        )

    monkeypatch.setattr(recovery, "load_effective_destination_config", effective)
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="unreadable")
    try:
        runner.run([tmp_path], dry_run=True)
        available["value"] = False
        summary = runner.run([tmp_path], dry_run=True)
        cursor = runner.checkpoint.connection.execute("SELECT ordinal,state FROM cursor").fetchone()
    finally:
        runner.close()

    assert summary.inventory == {"blocked": 1}
    assert tuple(cursor) == (0, "pending")


def test_parent_is_delivered_before_child_and_orphan_is_eligible(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(tmp_path, "z-parent")
    _capture(tmp_path, "a-child", parent_id="z-parent")
    _capture(tmp_path, "orphan", parent_id="absent")
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="parent", client=_server(requests))
    try:
        runner.run([tmp_path])
        runner.run([tmp_path])
        runner.run([tmp_path])
    finally:
        runner.close()

    sessions = [
        json.loads(request.content)["origin"]["session_id"]
        for request in requests
        if request.url.path == "/recovery/events"
    ]
    assert sessions.index("z-parent") < sessions.index("a-child")
    assert "orphan" in sessions


def test_deep_parent_chain_stays_schedulable_without_recursive_cycle_cte(
    tmp_path: Path, configured_roots: None
) -> None:
    parent_id = ""
    for number in range(32):
        session_id = f"session-{number:03}"
        _capture(tmp_path, session_id, parent_id=parent_id)
        parent_id = session_id
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="deep", client=_server(requests))
    try:
        summaries = [runner.run([tmp_path])]
        _assert_indexed_next_cursor_plan(runner)
        summaries.extend(runner.run(refresh=False) for _ in range(31))
    finally:
        runner.close()

    source = Path(recovery.__file__).read_text(encoding="utf-8")
    assert "WITH RECURSIVE" not in source
    assert sum(summary.delivered for summary in summaries) == 32
    assert summaries[-1].pending is False


def test_lost_ack_retries_identical_canonical_bytes_without_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, configured_roots: None
) -> None:
    artifact = _capture(tmp_path)
    first_payloads: list[bytes] = []

    def lost_ack(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        first_payloads.append(request.content)
        raise httpx.RemoteProtocolError("lost acknowledgement")

    first = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="retry",
        client=httpx.Client(transport=httpx.MockTransport(lost_ack)),
    )
    try:
        first_summary = first.run([tmp_path])
    finally:
        first.close()
    assert first_summary.pending is True
    assert first_summary.retry_after_s is not None

    second_payloads: list[bytes] = []

    def duplicate(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/version":
            return httpx.Response(200, json={"capabilities": ["native-recovery"]})
        second_payloads.append(request.content)
        return httpx.Response(202)

    second = RecoveryRunner(
        state_dir=tmp_path / "state",
        job_id="retry",
        client=httpx.Client(transport=httpx.MockTransport(duplicate)),
    )
    try:
        now = recovery.time.time
        monkeypatch.setattr(recovery.time, "time", lambda: now() + 10)
        assert second.run([tmp_path]).pending is False
    finally:
        second.close()

    assert first_payloads == second_payloads
    assert str(artifact) not in second_payloads[0].decode()


def test_429_defers_only_attempted_cursor_and_stops_after_one_post(
    tmp_path: Path, configured_roots: None
) -> None:
    _capture(tmp_path, "first")
    _capture(tmp_path, "second")
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(
        state_dir=tmp_path / "state", job_id="throttle", client=_server(requests, 429)
    )
    try:
        summary = runner.run([tmp_path])
        states = [
            row["state"]
            for row in runner.checkpoint.connection.execute(
                "SELECT state FROM cursor ORDER BY artifact_id"
            )
        ]
    finally:
        runner.close()

    assert len([request for request in requests if request.url.path == "/recovery/events"]) == 1
    assert sorted(states) == ["deferred", "pending"]
    assert summary.retry_after_s is not None


def test_command_preserves_once_nonzero_and_rejects_server_override(
    tmp_path: Path, configured_roots: None, capsys: pytest.CaptureFixture[str]
) -> None:
    _capture(tmp_path)
    with pytest.raises(SystemExit) as exited:
        cli.main(["--once", "--dry-run", "--path", str(tmp_path)])
    assert exited.value.code == 1
    assert json.loads(capsys.readouterr().out)["inventory"] == {"eligible": 1}
    with pytest.raises(SystemExit) as rejected:
        cli.main(["--server-url", "https://not-accepted.example"])
    assert rejected.value.code == 2


def test_scan_roots_deduplicate_and_reject_other_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home" / "owner"
    home.mkdir(parents=True)
    monkeypatch.setattr(recovery.Path, "home", classmethod(lambda _cls: home))
    config = DestinationConfig({}, (), home / ".amplifier" / "projects")
    monkeypatch.setattr(recovery, "DEFAULT_BASE_PATH", config.base_path)
    expected = [home / ".amplifier" / "projects", home / "captures"]
    explicit_paths = [home / ".amplifier" / "projects", home.parent, home / "captures"]
    assert resolve_scan_roots(config, explicit_paths) == expected


def test_prune_receipt_blocks_new_job_before_any_network(
    tmp_path: Path, configured_roots: None
) -> None:
    from context_intelligence.prune.coordination import receipt_path

    path = _capture(tmp_path)
    original = path.read_bytes()
    receipt_path(path).write_bytes(b"prepared or invalid")
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="pruned", client=_server(requests))
    try:
        summary = runner.run([tmp_path])
        row = runner.checkpoint.connection.execute("SELECT * FROM artifact").fetchone()
        assert row["state"] == "quarantined"
        assert "pruned-capture:" in row["reason"]
        assert summary.delivered == 0
        assert list(runner.checkpoint.connection.execute("SELECT * FROM cursor")) == []
    finally:
        runner.close()
    assert requests == [] and path.read_bytes() == original


def _checkpoint_rows(
    runner: RecoveryRunner,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    connection = runner.checkpoint.connection
    return (
        dict(connection.execute("SELECT * FROM artifact").fetchone()),
        [dict(row) for row in connection.execute("SELECT * FROM cursor")],
    )


def _seed_source_changed(runner: RecoveryRunner, root: Path, mutation: str) -> Path:
    """Create real completed cursors with a synthetic ACK, then detect changed bytes."""
    body = (
        json.dumps(
            {
                "event": "llm:request",
                "workspace": "project",
                "data": {
                    "timestamp": "2026-09-15T00:00:00Z",
                    "session_id": "session-1",
                    "raw": "SYNTHETIC-ORIGINAL",
                },
            }
        ).encode("utf-8")
        + b"\n"
    )
    path = _capture(root, body=body)
    metadata = path.parent / "metadata.json"
    original_metadata = metadata.read_bytes()
    seeded = runner.run([root])
    artifact, cursors = _checkpoint_rows(runner)
    assert seeded.inventory == {"accepted": 1}
    assert seeded.delivered == 1 and seeded.pending is False
    assert artifact["source_sha256"] == hashlib.sha256(body).hexdigest()
    assert artifact["metadata_sha256"] == hashlib.sha256(original_metadata).hexdigest()
    assert len(cursors) == 1
    assert (cursors[0]["state"], cursors[0]["ordinal"], cursors[0]["byte_offset"]) == (
        "complete",
        1,
        len(body),
    )
    if mutation == "events":
        changed = body.replace(b"SYNTHETIC-ORIGINAL", b"SYNTHETIC-MODIFIED")
        assert changed != body and len(changed) == len(body)
        path.write_bytes(changed)
    else:
        assert mutation == "metadata"
        changed = original_metadata.replace(b"01:00:00Z", b"02:00:00Z")
        assert changed != original_metadata and len(changed) == len(original_metadata)
        metadata.write_bytes(changed)
    detected = runner.run([root])
    changed_artifact, changed_cursors = _checkpoint_rows(runner)
    assert detected.inventory == {"quarantined": 1}
    assert detected.delivered == 0 and detected.pending is True
    assert changed_artifact["reason"] == "source-changed"
    assert changed_artifact["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert changed_artifact["metadata_sha256"] == hashlib.sha256(metadata.read_bytes()).hexdigest()
    changed_column = "source_sha256" if mutation == "events" else "metadata_sha256"
    assert changed_artifact[changed_column] != artifact[changed_column]
    assert changed_cursors == cursors
    return path


@contextmanager
def _maintenance_refusal(path: Path, refusal: str) -> Iterator[str]:
    """Repair only this synthetic sidecar; never unlink or move the stable lock."""
    from context_intelligence.prune.coordination import receipt_path

    lock = path.with_name(path.name + ".maintenance.lock")
    inode = lock.stat().st_ino
    if refusal == "receipt":
        sidecar = receipt_path(path)
        sidecar.write_bytes(b"synthetic refusal marker; source has NOT been pruned")
        reason = "pruned-capture: raw retention is irreversible; replay refused"
    else:
        assert refusal == "unsafe-lock"
        sidecar = path.with_name(path.name + ".extra-maintenance-link")
        os.link(lock, sidecar)
        assert lock.stat().st_nlink == 2
        reason = "unsafe capture maintenance lock"
    try:
        yield reason
    finally:
        sidecar.unlink()
        assert lock.stat().st_ino == inode and lock.stat().st_nlink == 1


def _seed_only_server(requests: list[httpx.Request]) -> httpx.Client:
    client = _server(requests)

    def reject_after_seed(request: httpx.Request) -> None:
        if len(requests) >= 2:
            pytest.fail(f"Unexpected request after seed: {request.method} {request.url.path}")

    client.event_hooks["request"].append(reject_after_seed)
    return client


@pytest.mark.parametrize("mutation", ["events", "metadata"])
@pytest.mark.parametrize("refusal", ["receipt", "unsafe-lock"])
def test_source_changed_quarantine_survives_maintenance_refusal_and_repair(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    refusal: str,
) -> None:
    requests: list[httpx.Request] = []
    root, state, job = tmp_path / "captures", tmp_path / "state", "permanent"
    with _seed_only_server(requests) as client:
        runner = RecoveryRunner(state_dir=state, job_id=job, client=client)
        try:
            path = _seed_source_changed(runner, root, mutation)
            before = _checkpoint_rows(runner)
            source = path.read_bytes()
            metadata = (path.parent / "metadata.json").read_bytes()
            with _maintenance_refusal(path, refusal):
                refused = runner.run([root])
                after_refusal = _checkpoint_rows(runner)
        finally:
            runner.close()

        # Exercise the public non-dry-run CLI after reopening the same real job.
        # The double supplies no further ACKs, including capability requests.
        client_type = httpx.Client

        def unexpected(request: httpx.Request) -> httpx.Response:
            pytest.fail(f"Unexpected CLI request: {request.method} {request.url.path}")

        monkeypatch.setattr(
            recovery.httpx,
            "Client",
            lambda **_kwargs: client_type(transport=httpx.MockTransport(unexpected)),
        )
        with pytest.raises(SystemExit) as exited:
            cli.main(["--path", str(root), "--state-dir", str(state), "--job-id", job, "--once"])
        report = json.loads(capsys.readouterr().out)
        assert exited.value.code == 1, f"Changed source falsely completed: {report}"
        assert report["inventory"] == {"quarantined": 1}
        assert report["delivered"] == 0 and report["pending"] is True
        assert refused.inventory == {"quarantined": 1}
        assert refused.delivered == 0 and refused.pending is True
        assert after_refusal == before

        reopened = RecoveryRunner(state_dir=state, job_id=job, client=client)
        try:
            assert _checkpoint_rows(reopened) == before
            subsequent = reopened.run([root])
            assert subsequent.inventory == {"quarantined": 1}
            assert subsequent.delivered == 0 and subsequent.pending is True
            assert _checkpoint_rows(reopened) == before
        finally:
            reopened.close()
        assert path.read_bytes() == source
        assert (path.parent / "metadata.json").read_bytes() == metadata
        assert [(r.method, r.url.path) for r in requests] == [
            ("GET", "/version"),
            ("POST", "/recovery/events"),
        ]
        assert json.loads(requests[1].content)["data"]["raw"] == "SYNTHETIC-ORIGINAL"


def test_busy_scan_preserves_source_changed_quarantine(
    tmp_path: Path, configured_roots: None
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    root = tmp_path / "captures"
    requests: list[httpx.Request] = []
    with _seed_only_server(requests) as client:
        runner = RecoveryRunner(
            state_dir=tmp_path / "state", job_id="permanent-busy", client=client
        )
        try:
            path = _seed_source_changed(runner, root, "events")
            before = _checkpoint_rows(runner)
            with capture_lock(path, exclusive=True):
                busy = runner.run([root])
                assert _checkpoint_rows(runner) == before
            assert busy.inventory == {"quarantined": 1}
            assert busy.delivered == 0 and busy.pending is True
            assert busy.retry_after_s == 1.0
            released = runner.run([root])
            assert released.inventory == {"quarantined": 1}
            assert released.delivered == 0 and released.pending is True
            assert released.retry_after_s is None
            assert _checkpoint_rows(runner) == before
        finally:
            runner.close()
    assert len(requests) == 2


@pytest.mark.parametrize("refusal", ["receipt", "unsafe-lock"])
def test_maintenance_refusal_replaces_retryable_reason_and_recovers(
    tmp_path: Path, configured_roots: None, refusal: str
) -> None:
    root = tmp_path / "captures"
    path = _capture(root)
    source = path.read_bytes()
    metadata = path.parent / "metadata.json"
    completed = metadata.read_bytes()
    incomplete = json.loads(completed)
    incomplete["status"] = "running"
    incomplete.pop("ended_at")
    metadata.write_text(json.dumps(incomplete), encoding="utf-8")
    requests: list[httpx.Request] = []
    with _server(requests) as client:
        runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="repairable", client=client)
        try:
            # Observe the scan's repairable diagnosis before run() derives
            # aggregate state from the (not-yet-created) delivery cursors.
            runner._scan_artifact(path)
            runner.checkpoint.commit()
            artifact, cursors = _checkpoint_rows(runner)
            assert artifact["state"] == "deferred"
            assert artifact["reason"] == "session-incomplete" and cursors == []
            with _maintenance_refusal(path, refusal) as reason:
                refused = runner.run([root])
                artifact, cursors = _checkpoint_rows(runner)
                assert refused.inventory == {"quarantined": 1}
                assert refused.delivered == 0 and refused.pending is True
                assert artifact["reason"] == reason and cursors == []
                assert requests == []
            metadata.write_bytes(completed)
            repaired = runner.run([root])
            artifact, cursors = _checkpoint_rows(runner)
            assert repaired.inventory == {"accepted": 1}
            assert repaired.delivered == 1 and repaired.pending is False
            assert artifact["reason"] == ""
            assert len(cursors) == 1
            assert (cursors[0]["state"], cursors[0]["ordinal"], cursors[0]["byte_offset"]) == (
                "complete",
                1,
                len(source),
            )
        finally:
            runner.close()
    assert path.read_bytes() == source and metadata.read_bytes() == completed
    assert [(r.method, r.url.path) for r in requests] == [
        ("GET", "/version"),
        ("POST", "/recovery/events"),
    ]


def test_busy_scan_preserves_existing_identity_routes_and_acknowledged_offsets(
    tmp_path: Path, configured_roots: None
) -> None:
    from context_intelligence.prune.coordination import capture_lock

    path = _capture(tmp_path, body=_event("session-1") + _event("session-1"))
    requests: list[httpx.Request] = []
    runner = RecoveryRunner(state_dir=tmp_path / "state", job_id="busy", client=_server(requests))
    try:
        runner.run([tmp_path])
        artifact = dict(runner.checkpoint.connection.execute("SELECT * FROM artifact").fetchone())
        cursor = dict(runner.checkpoint.connection.execute("SELECT * FROM cursor").fetchone())
        assert cursor["ordinal"] == 1 and cursor["byte_offset"] > 0
        with capture_lock(path, exclusive=True):
            summary = runner.run([tmp_path], dry_run=True)
            assert summary.pending and summary.retry_after_s == 1
            assert (
                dict(runner.checkpoint.connection.execute("SELECT * FROM artifact").fetchone())
                == artifact
            )
            assert (
                dict(runner.checkpoint.connection.execute("SELECT * FROM cursor").fetchone())
                == cursor
            )
        runner.run([tmp_path])
        after = runner.checkpoint.connection.execute("SELECT * FROM cursor").fetchone()
        assert after["ordinal"] == 2 and after["byte_offset"] > cursor["byte_offset"]
    finally:
        runner.close()


def test_prune_refuses_registered_real_v2_checkpoint_without_changing_cursors(
    tmp_path: Path, configured_roots: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import UTC, datetime, timedelta

    from context_intelligence.prune import PruneRequest, prune_file

    path = _capture(tmp_path)
    state = tmp_path / "state"
    runner = RecoveryRunner(state_dir=state, job_id="registered", client=_server([]))
    try:
        runner.run([tmp_path], dry_run=True)
        cursor_before = [
            dict(row) for row in runner.checkpoint.connection.execute("SELECT * FROM cursor")
        ]
    finally:
        runner.close()
    monkeypatch.setattr("context_intelligence.prune.recovery.default_recovery_root", lambda: state)
    monkeypatch.setattr(
        "context_intelligence.config.DEFAULT_RECOVERY_STATE_ROOT", tmp_path / "standard-recovery"
    )
    before = path.read_bytes()
    req = PruneRequest(tmp_path, apply=True, writers_stopped=True, min_idle=timedelta(0))
    result = prune_file(path, req, now=datetime.now(UTC))
    assert result.status == "skipped" and "skipped-ledger:" in (result.reason or "")
    runner = RecoveryRunner(state_dir=state, job_id="registered", client=_server([]))
    try:
        assert [
            dict(row) for row in runner.checkpoint.connection.execute("SELECT * FROM cursor")
        ] == cursor_before
    finally:
        runner.close()
    assert path.read_bytes() == before


def test_prune_keeps_current_cli_default_job_with_alternate_authority(
    tmp_path: Path,
    configured_roots: None,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import hashlib
    import sqlite3
    from contextlib import closing
    from datetime import UTC, datetime, timedelta

    from context_intelligence import config
    from context_intelligence.prune import PruneRequest, prune_file
    from context_intelligence.prune.coordination import receipt_path

    standard = tmp_path / ".local/state/context-intelligence/recovery"
    alternate = tmp_path / "alternate-authority"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(config, "DEFAULT_RECOVERY_STATE_ROOT", standard)
    monkeypatch.setattr(config, "SETTINGS_PATH", tmp_path / ".amplifier/settings.yaml")
    config.SETTINGS_PATH.parent.mkdir()
    config.SETTINGS_PATH.write_text(
        "overrides:\n  hook-context-intelligence:\n    config:\n"
        f"      recovery_state_root: {json.dumps(str(alternate))}\n",
        encoding="utf-8",
    )
    work = tmp_path / "workspace"
    work.mkdir()
    body = (
        json.dumps(
            {
                "event": "llm:request",
                "workspace": "project",
                "timestamp": "2026-01-01T00:00:00Z",
                "data": {
                    "session_id": "session-1",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "raw": "SYNTHETIC ONLY",
                },
            }
        ).encode()
        + b"\n"
    )
    root = tmp_path / "captures"
    path = _capture(root, working_dir=str(work), body=body)
    with pytest.raises(SystemExit) as exited:
        cli.main(["--path", str(root), "--dry-run"])
    assert exited.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["inventory"]["eligible"] == 1
    assert report["delivered"] == 0 and report["pending"] is True
    db = standard / "default/default.v2.sqlite3"

    def rows():
        with closing(sqlite3.connect(db)) as connection:
            return (
                list(connection.execute("SELECT * FROM artifact")),
                list(connection.execute("SELECT * FROM cursor")),
            )

    before_rows = rows()
    with closing(sqlite3.connect(db)) as connection:
        assert connection.execute("SELECT artifact_id FROM artifact").fetchone() == (
            hashlib.sha256(str(path.absolute()).encode()).hexdigest(),
        )
        assert connection.execute("SELECT source_sha256 FROM artifact").fetchone() == (
            hashlib.sha256(body).hexdigest(),
        )
        assert connection.execute("SELECT ordinal,byte_offset,state FROM cursor").fetchone() == (
            0,
            0,
            "pending",
        )
    lock = Path(str(db) + ".lock")
    before = [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (db, lock)]
    request = PruneRequest(root, apply=True, writers_stopped=True, min_idle=timedelta(0))
    result = prune_file(path, request, now=datetime.now(UTC))
    assert result.status == "skipped" and (result.reason or "").startswith("skipped-ledger:")
    assert path.read_bytes() == body and not receipt_path(path).exists()
    assert rows() == before_rows
    assert [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in (db, lock)] == before
