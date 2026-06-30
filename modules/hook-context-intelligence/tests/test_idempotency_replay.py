"""Idempotency regression test — Task 8.

Proves that the idempotency_key computed by _compute_idempotency_key is identical
across every retry attempt for the same event, including the lost-ACK path where
httpx.RemoteProtocolError is raised after the server has already processed the
request.

Proof boundary (stated here and in shipping docs):

  'same key across retries' is proven in-process:
    _compute_idempotency_key over {event, workspace, data} is deterministic SHA-256,
    and data is never mutated once enqueued (the immutability contract on enqueue()),
    so every retry attempt sends the same idempotency_key.

  'server records the event only ONCE' is NOT proven here:
    That guarantee depends on the real server's dedup logic and is proven only by
    the real-server E2E test (Task 12 + a follow-up remote run).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DestinationDispatcher,
)
from amplifier_module_hook_context_intelligence.upload import _compute_idempotency_key


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _dispatcher(
    name: str = "test",
    workspace: str | None = "ws",
) -> _DestinationDispatcher:
    """Build a dispatcher with standard test parameters."""
    return _DestinationDispatcher(
        name=name,
        url="http://localhost:8080",
        api_key="test-key",
        workspace=workspace,
        dispatch_timeout=10.0,
        failure_threshold=3,
        queue_capacity=256,
        close_drain_timeout=2.0,
    )


def _make_response(status_code: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    return r


def _mock_client(side_effects: list[Any]) -> AsyncMock:
    """Build an AsyncMock httpx client whose .post() follows the given side-effects."""
    client = AsyncMock()
    client.is_closed = False
    client.post.side_effect = side_effects
    return client


# ---------------------------------------------------------------------------
# TestIdempotencyKeyStability
# ---------------------------------------------------------------------------


class TestIdempotencyKeyStability:
    """Regression tests for idempotency key stability across retries.

    Proof boundary (stated here and in shipping docs):

      'same key across retries' is proven in-process:
        _compute_idempotency_key over {event, workspace, data} is deterministic,
        and data is never mutated once enqueued, so every retry sends the same key.

      'server records the event only ONCE' is NOT proven here:
        Depends on the real server's dedup. Proven only by Task 12 + remote run.
    """

    async def test_idempotency_key_identical_across_n_transient_retries(self) -> None:
        """Key is identical across N retries on transient HTTP failures.

        Verifies that build_payload / _compute_idempotency_key produces the same
        idempotency_key on every retry attempt when facing transient HTTP failures
        (503). Payloads are captured from the AsyncMock client's call_args_list.

        RED proof performed during authoring: temporarily injecting a mutation of
        data inside _post (e.g. data['_injected'] = True) causes the keys to
        diverge and the assertion below fails for the right reason. After reverting
        the injection, the test passes cleanly against the production code.
        """
        N = 5
        d = _dispatcher()
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        client = _mock_client(
            [
                *[_make_response(503) for _ in range(N)],  # N transient failures
                _make_response(200),  # final delivery
            ]
        )
        d._client = client

        event = "session:start"
        data: dict[str, Any] = {
            "session_id": "abc-123",
            "timestamp": "2024-01-01T00:00:00Z",
            "agent_name": "test-agent",
        }

        d.enqueue(event, data)
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # All N+1 attempts must have happened
        assert client.post.await_count == N + 1

        # Extract idempotency_key from every payload sent
        keys = [
            call.kwargs["json"]["idempotency_key"]
            for call in client.post.call_args_list
        ]
        assert len(keys) == N + 1

        # ALL keys must be identical — same event + same data + no mutations
        assert len(set(keys)) == 1, (
            f"Expected all {N + 1} retries to send the same idempotency_key, "
            f"but got {len(set(keys))} distinct keys: {keys}"
        )

        # Cross-check: key must equal what _compute_idempotency_key produces directly
        expected_key = _compute_idempotency_key(event, d._workspace, data)
        assert keys[0] == expected_key

        await d.close()

    async def test_idempotency_key_identical_on_lost_ack(self) -> None:
        """Lost-ACK (RemoteProtocolError after server processed) retries with same key.

        The lost-ACK scenario:
          1. Client sends the event.
          2. Server processes it successfully and starts writing the response.
          3. The connection drops mid-response — httpx raises RemoteProtocolError.
          4. _post catches RemoteProtocolError and returns _TRANSIENT.
          5. The worker retries with the SAME event data.
          6. The retry MUST send the SAME idempotency_key so the server can dedup.

        Proof boundary:
          'same key sent on retry' is proven here in-process.
          'server records only once' requires a real-server E2E test (Task 12).

        RED proof performed during authoring: temporarily patching _post to mutate
        data["_mutated"] = True after the RemoteProtocolError causes the retry to
        send a different key, and this assertion fails for the right reason. After
        reverting the patch, the test passes cleanly.
        """
        d = _dispatcher()
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        client = _mock_client(
            [
                # Attempt 1: server processed, but response lost mid-stream
                httpx.RemoteProtocolError("connection closed after server processed"),
                # Attempt 2: clean retry succeeds
                _make_response(200),
            ]
        )
        d._client = client

        event = "tool:call"
        data: dict[str, Any] = {
            "session_id": "xyz-789",
            "tool": "bash",
            "timestamp": "2024-06-01T12:00:00Z",
        }

        d.enqueue(event, data)
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # Both attempts must have happened (1 lost-ACK + 1 success)
        assert client.post.await_count == 2

        attempt_1_key = client.post.call_args_list[0].kwargs["json"]["idempotency_key"]
        attempt_2_key = client.post.call_args_list[1].kwargs["json"]["idempotency_key"]

        assert attempt_1_key == attempt_2_key, (
            "Lost-ACK retry must send the SAME idempotency_key as the original "
            f"attempt. Got attempt_1={attempt_1_key!r}, attempt_2={attempt_2_key!r}"
        )

        # Cross-check against the canonical function
        expected_key = _compute_idempotency_key(event, d._workspace, data)
        assert attempt_1_key == expected_key
        assert attempt_2_key == expected_key

        await d.close()

    def test_data_mutation_changes_key_sensitivity(self) -> None:
        """Mutation sensitivity: mutating data CHANGES the key (proves test is sensitive).

        Demonstrates that _compute_idempotency_key IS sensitive to data mutations.
        If _post or _worker were to mutate data between retries, the key would change
        and the stability tests above would catch it.

        This is the RED proof complement: confirms the mechanism underlying the
        stability tests is sound — the test CAN detect in-place mutations.
        """
        data: dict[str, Any] = {"session_id": "s1", "timestamp": "2024-01-01"}
        key_original = _compute_idempotency_key("session:start", "ws", data)

        # Simulate the kind of mutation that would occur if data were not immutable
        mutated_data = {**data, "_injected_mutation": True}
        key_after_mutation = _compute_idempotency_key(
            "session:start", "ws", mutated_data
        )

        assert key_original != key_after_mutation, (
            "Idempotency key must change when data changes — confirming that the "
            "stability tests would catch any in-place mutation between retries"
        )

    async def test_data_not_mutated_during_retry_cycle(self) -> None:
        """Data dict is not mutated by _worker or _post during a retry cycle.

        Confirms the immutability contract on enqueue(): after the data dict is
        enqueued, _worker and _post never modify it in place. The original dict
        snapshot before enqueue must equal the dict content after delivery.
        """
        d = _dispatcher()
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        client = _mock_client(
            [
                _make_response(503),  # transient
                _make_response(503),  # transient
                _make_response(200),  # delivered
            ]
        )
        d._client = client

        data: dict[str, Any] = {
            "session_id": "immutable-test",
            "x": 42,
            "nested": {"y": 99},
        }
        # Snapshot BEFORE enqueue
        data_snapshot = {
            "session_id": "immutable-test",
            "x": 42,
            "nested": {"y": 99},
        }

        d.enqueue("test:event", data)
        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        # The original data dict must be identical to the pre-enqueue snapshot
        assert data == data_snapshot, (
            f"data dict was mutated during worker retry cycle!\n"
            f"  Before: {data_snapshot}\n"
            f"  After:  {data}"
        )

        await d.close()

    async def test_multiple_events_each_have_stable_independent_keys(self) -> None:
        """Multiple events queued concurrently each retain their own stable keys.

        Enqueues two distinct events. Each must produce its own unique,
        stable idempotency_key. Verifies that retries for event-1 don't bleed
        into event-2's key, and vice versa.
        """
        d = _dispatcher()
        d._sleep_backoff = AsyncMock()  # type: ignore[method-assign]

        client = _mock_client(
            [
                _make_response(503),  # e1 attempt 1: transient
                _make_response(200),  # e1 attempt 2: delivered
                _make_response(200),  # e2 attempt 1: delivered
            ]
        )
        d._client = client

        event_1 = "session:start"
        data_1: dict[str, Any] = {"session_id": "s1", "ts": "2024-01-01"}
        event_2 = "session:end"
        data_2: dict[str, Any] = {"session_id": "s1", "ts": "2024-01-02"}

        d.enqueue(event_1, data_1)
        d.enqueue(event_2, data_2)

        await asyncio.wait_for(d._queue.join(), timeout=2.0)

        assert client.post.await_count == 3

        key_e1_attempt_1 = client.post.call_args_list[0].kwargs["json"]["idempotency_key"]
        key_e1_attempt_2 = client.post.call_args_list[1].kwargs["json"]["idempotency_key"]
        key_e2 = client.post.call_args_list[2].kwargs["json"]["idempotency_key"]

        # e1's two attempts must have identical keys
        assert key_e1_attempt_1 == key_e1_attempt_2, (
            "e1's retry must send the same idempotency_key as the original attempt"
        )

        # e2's key must differ from e1's (different event + data)
        assert key_e1_attempt_1 != key_e2, (
            "Different events must produce different idempotency keys"
        )

        # Cross-check against canonical function
        expected_key_e1 = _compute_idempotency_key(event_1, d._workspace, data_1)
        expected_key_e2 = _compute_idempotency_key(event_2, d._workspace, data_2)
        assert key_e1_attempt_1 == expected_key_e1
        assert key_e2 == expected_key_e2

        await d.close()
