"""Offline lifecycle regressions for session:end emitted after hook cleanup.

The HTTP sink only observes delivery and supplies scheduling barriers. These
tests prove component lifetime behavior, not acceptance by a real CI server.
"""

from __future__ import annotations

import asyncio
import errno
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    LoggingHandler,
    _DestinationDispatcher,
)


class _Sink:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.clients: list[httpx.AsyncClient] = []
        self.request_started = asyncio.Event()
        self.release_request = asyncio.Event()
        self.release_request.set()
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()
        self.release_close.set()
        self.closes = 0
        self.request_cancelled = False


class _Transport(httpx.AsyncBaseTransport):
    def __init__(self, sink: _Sink) -> None:
        self.sink = sink

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.sink.events.append(json.loads(request.content)["event"])
        self.sink.request_started.set()
        try:
            await self.sink.release_request.wait()
        except asyncio.CancelledError:
            self.sink.request_cancelled = True
            raise
        return httpx.Response(200)

    async def aclose(self) -> None:
        self.sink.closes += 1
        self.sink.close_started.set()
        await self.sink.release_close.wait()


@pytest.fixture
def sink(monkeypatch: pytest.MonkeyPatch) -> _Sink:
    sink = _Sink()
    client_type = httpx.AsyncClient

    def client(**kwargs: Any) -> httpx.AsyncClient:
        result = client_type(transport=_Transport(sink), **kwargs)
        sink.clients.append(result)
        return result

    # Every client, including an incorrectly recreated one, stays offline.
    monkeypatch.setattr(httpx, "AsyncClient", client)
    return sink


def _dispatcher(tmp_path: Path) -> _DestinationDispatcher:
    return _DestinationDispatcher(
        name="lifecycle-test",
        url="https://ci.invalid",
        api_key="fixture-key",
        workspace="fixture",
        dispatch_timeout=1.0,
        failure_threshold=3,
        queue_capacity=8,
        close_drain_timeout=0.1,
        storage_path=tmp_path,
    )


@pytest.fixture
async def dispatcher(sink: _Sink, tmp_path: Path) -> Any:
    dispatcher = _dispatcher(tmp_path)
    yield dispatcher
    sink.release_request.set()
    sink.release_close.set()
    await dispatcher.close()


@pytest.mark.parametrize("used_client", [False, True])
async def test_session_end_after_cleanup_stays_local_without_reopening(
    dispatcher: _DestinationDispatcher, sink: _Sink, tmp_path: Path, used_client: bool
) -> None:
    """Core cleanup-before-end order retains JSONL, but cannot deliver remotely.

    The same boundary covers callbacks already scheduled when cleanup starts.
    Remote terminal-event delivery requires the emitter to run before cleanup.
    """
    session_dir = tmp_path / "session" / "context-intelligence"
    handler = LoggingHandler(
        SimpleNamespace(session_dir=lambda _: session_dir, working_dir="fixture")
    )
    await handler.set_dispatchers([dispatcher])
    if used_client:
        await handler("session:start", {"session_id": "session", "timestamp": "t0"})
        await asyncio.wait_for(sink.request_started.wait(), timeout=1)
    previous_worker = dispatcher._worker_task
    deliver = asyncio.Event()

    async def queued_hook() -> None:
        await deliver.wait()
        await handler("session:end", {"session_id": "session", "timestamp": "t1"})

    callback = asyncio.create_task(queued_hook())
    try:
        await handler.close()
        deliver.set()
        await callback
        await asyncio.sleep(0)  # let any erroneously spawned worker run

        records = [
            json.loads(line) for line in (session_dir / "events.jsonl").read_text().splitlines()
        ]
        assert records[-1]["event"] == "session:end"
        assert sink.events == (["session:start"] if used_client else [])
        assert len(sink.clients) == int(used_client)
        assert all(client.is_closed for client in sink.clients)
        assert previous_worker is None or previous_worker.done()
        assert dispatcher._worker_task is None
    finally:
        deliver.set()
        await callback


async def test_accepted_events_drain_in_order_and_close_once(
    dispatcher: _DestinationDispatcher, sink: _Sink
) -> None:
    assert dispatcher.enqueue("session:start", {"session_id": "s1"})
    assert dispatcher.enqueue("session:end", {"session_id": "s1"})
    worker = dispatcher._worker_task

    await dispatcher.close()
    await dispatcher.close()

    assert sink.events == ["session:start", "session:end"]
    assert sink.closes == 1
    assert all(client.is_closed for client in sink.clients)
    assert worker is not None and worker.done()
    assert dispatcher._worker_task is None
    await asyncio.wait_for(dispatcher._queue.join(), timeout=1)


async def test_closed_dispatcher_rejects_admission_without_starting_worker(
    dispatcher: _DestinationDispatcher, sink: _Sink
) -> None:
    await dispatcher.close()

    assert dispatcher.enqueue("session:end", {"session_id": "s1"}) is False
    assert dispatcher._queue.empty()
    assert dispatcher._worker_task is None
    assert sink.clients == []


async def test_close_rejects_new_events_while_draining_accepted_event(
    dispatcher: _DestinationDispatcher, sink: _Sink, monkeypatch: pytest.MonkeyPatch
) -> None:
    sink.release_request.clear()
    draining = asyncio.Event()
    join = dispatcher._queue.join

    async def observed_join() -> None:
        draining.set()
        await join()

    monkeypatch.setattr(dispatcher._queue, "join", observed_join)
    assert dispatcher.enqueue("session:start", {"session_id": "s1"})
    await asyncio.wait_for(sink.request_started.wait(), timeout=1)
    closer = asyncio.create_task(dispatcher.close())
    try:
        await asyncio.wait_for(draining.wait(), timeout=1)
        assert dispatcher.enqueue("session:end", {"session_id": "s1"}) is False
    finally:
        sink.release_request.set()
        await closer

    assert sink.events == ["session:start"]
    assert sink.closes == 1
    assert dispatcher._worker_task is None


async def test_concurrent_close_waiters_share_client_cleanup(
    dispatcher: _DestinationDispatcher, sink: _Sink
) -> None:
    sink.release_close.clear()
    assert dispatcher.enqueue("session:end", {"session_id": "s1"})
    first = asyncio.create_task(dispatcher.close())
    await asyncio.wait_for(sink.close_started.wait(), timeout=1)
    second_started = asyncio.Event()

    async def second_close() -> None:
        second_started.set()
        await dispatcher.close()

    second = asyncio.create_task(second_close())
    try:
        await second_started.wait()
        assert not first.done()
        assert not second.done(), "every close waiter must wait for the shared cleanup"
    finally:
        sink.release_close.set()
        await asyncio.gather(first, second)

    assert sink.closes == 1
    assert dispatcher._worker_task is None


async def test_cancelled_close_waiter_does_not_cancel_cleanup(
    dispatcher: _DestinationDispatcher, sink: _Sink
) -> None:
    sink.release_request.clear()
    assert dispatcher.enqueue("session:end", {"session_id": "s1"})
    await asyncio.wait_for(sink.request_started.wait(), timeout=1)
    worker = dispatcher._worker_task
    closer = asyncio.create_task(dispatcher.close())
    await asyncio.sleep(0)  # enter close before cancelling its caller
    closer.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closer

    # Cleanup must finish without needing another close call to rescue it.
    sink.release_request.set()
    await asyncio.wait_for(sink.close_started.wait(), timeout=1)
    await dispatcher.close()

    assert sink.events == ["session:end"]
    assert sink.closes == 1
    assert worker is not None and worker.done()
    assert dispatcher._worker_task is None
    assert dispatcher.enqueue("after-cancel", {"session_id": "s1"}) is False


async def test_late_dispatcher_install_cannot_reopen_closed_handler(
    dispatcher: _DestinationDispatcher, sink: _Sink, tmp_path: Path
) -> None:
    """A delayed ready/filter callback must not install new post-cleanup workers."""
    handler = LoggingHandler(SimpleNamespace(session_dir=lambda _: tmp_path, working_dir="fixture"))
    await handler.set_dispatchers([dispatcher])
    await handler.close()
    replacement = _dispatcher(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="closed"):
            await handler.set_dispatchers([replacement])
        assert replacement.enqueue("session:end", {"session_id": "s1"}) is False
        await handler("session:end", {"session_id": "s1"})
        assert json.loads((tmp_path / "events.jsonl").read_text())["event"] == "session:end"
        assert sink.clients == []
        assert replacement._worker_task is None
    finally:
        await replacement.close()
        await handler.close()


async def test_live_dispatcher_replacement_still_delivers(
    dispatcher: _DestinationDispatcher, sink: _Sink, tmp_path: Path
) -> None:
    handler = LoggingHandler(SimpleNamespace(session_dir=lambda _: tmp_path, working_dir="fixture"))
    await handler.set_dispatchers([dispatcher])
    await handler("session:start", {"session_id": "s1"})
    previous_worker = dispatcher._worker_task
    replacement = _dispatcher(tmp_path)
    try:
        await handler.set_dispatchers([replacement])
        assert previous_worker is not None and previous_worker.done()
        await handler("session:end", {"session_id": "s1"})
        await handler.close()

        assert sink.events == ["session:start", "session:end"]
        assert sink.closes == 2
        assert replacement._worker_task is None
        assert all(client.is_closed for client in sink.clients)
    finally:
        await replacement.close()
        await handler.close()


async def test_closed_dispatch_is_not_reported_as_delivery_when_disk_is_full(
    dispatcher: _DestinationDispatcher,
    sink: _Sink,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = LoggingHandler(SimpleNamespace(session_dir=lambda _: tmp_path, working_dir="fixture"))
    await handler.set_dispatchers([dispatcher])
    await handler.close()

    def disk_full(*args: Any) -> None:
        raise OSError(errno.ENOSPC, "fixture disk full")

    monkeypatch.setattr(handler, "_write_session_to_disk", disk_full)
    result = await handler("session:end", {"session_id": "s1"})

    assert not handler._disk_episode_delivered
    assert result.user_message_level == "warning"
    assert result.user_message is not None
    assert sink.clients == []
    assert dispatcher._worker_task is None


async def test_drain_timeout_cancels_inflight_and_cannot_restart(
    dispatcher: _DestinationDispatcher,
    sink: _Sink,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    issues: list[tuple[str, str]] = []
    monkeypatch.setattr(dispatcher, "_record_forwarding_issue", lambda *args: issues.append(args))
    sink.release_request.clear()
    assert dispatcher.enqueue("session:end", {"session_id": "s1"})
    await asyncio.wait_for(sink.request_started.wait(), timeout=1)
    worker = dispatcher._worker_task

    first = asyncio.create_task(dispatcher.close())
    await asyncio.sleep(0)
    second = asyncio.create_task(dispatcher.close())
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await asyncio.wait_for(second, timeout=1)
    await dispatcher.close()

    assert sink.request_cancelled
    assert worker is not None and worker.done()
    assert dispatcher._worker_task is None
    assert sink.closes == 1
    assert dispatcher.enqueue("after-timeout", {"session_id": "s1"}) is False
    assert sink.events == ["session:end"]
    assert [kind for kind, _ in issues] == ["shutdown_undelivered"]
    assert sum("shutdown:" in record.message for record in caplog.records) == 1
