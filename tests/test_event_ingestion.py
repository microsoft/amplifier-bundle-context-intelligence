"""Ingestion contracts reconciled with the live-server evidence in docs/lanes/public-event-ingestion."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import threading
from unittest.mock import patch

import httpx
import pytest

from context_intelligence.client import AsyncCIClient, CIClientError
from context_intelligence import build_event_payload


def envelope():
    return build_event_payload(
        "host:diagnostic",
        "fixture-workspace",
        {
            "session_id": "fixture-session",
            "timestamp": "2026-09-18T22:00:00+00:00",
            "text": "Résumé",
        },
    )


def test_payload_is_detached_canonical_and_compatible_with_hook_v1():
    path = (
        Path(__file__).parents[1]
        / "modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/upload.py"
    )
    spec = importlib.util.spec_from_file_location("hook_upload", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = {"nested": {"value": "Résumé"}, "session_id": "fixture"}
    payload = build_event_payload("host:diagnostic", "fixture", data)
    assert (
        payload["idempotency_key"]
        == module.build_payload("host:diagnostic", "fixture", data)["idempotency_key"]
    )
    assert (
        build_event_payload(
            "host:diagnostic", "fixture", dict(reversed(list(data.items()))), "/other"
        )["idempotency_key"]
        == payload["idempotency_key"]
    )
    assert "working_dir" not in payload
    data["nested"]["value"] = "changed"  # type: ignore[index]
    assert payload["data"]["nested"]["value"] == "Résumé"
    assert json.loads(json.dumps(payload)) == payload


@pytest.mark.parametrize(
    "event,workspace,data,directory",
    [
        ("", "w", {}, None),
        ("e", " ", {}, None),
        ("e", "w", [], None),
        ("e", "w", {}, " "),
        ("e", "w", {"value": float("nan")}, None),
    ],
)
def test_payload_rejects_unusable_json_or_blank_required_fields(event, workspace, data, directory):
    with pytest.raises(ValueError):
        build_event_payload(event, workspace, data, directory)


def transport(handler):
    original = httpx.AsyncClient
    return patch(
        "context_intelligence.client.httpx.AsyncClient",
        side_effect=lambda **kw: original(transport=httpx.MockTransport(handler), **kw),
    )


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "not-an-envelope",
        {"data": {"unserializable": {1, 2}}},
        {"data": {"value": float("nan")}},
        {"data": {"value": float("inf")}},
        {"data": {"timestamp": datetime(2026, 9, 18, tzinfo=timezone.utc)}},
    ],
)
async def test_unusable_caller_envelope_has_classified_error_before_auth_or_network(payload):
    class Strategy:
        def headers(self):
            pytest.fail("Invalid payload must not request credentials")

    def handle(request):
        pytest.fail("Invalid payload must not reach the network")

    with transport(handle), pytest.raises(CIClientError) as caught:
        await AsyncCIClient("http://server.invalid", auth_strategy=Strategy()).ingest(payload)
    assert caught.value.error_type == "invalid_payload"
    assert caught.value.url == "http://server.invalid/events"


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"value": None},
        {"value": [True, False, 1, 1.25]},
        {"nested": {"z": "☀ Résumé", "a": {"value": -5}}},
        {
            "event_id": "distinct-occurrence",
            "session_id": "root",
            "timestamp": "2026-09-18T00:00:00Z",
        },
    ],
)
def test_hook_v1_keys_match_across_supported_json_shapes(data):
    path = (
        Path(__file__).parents[1]
        / "modules/hook-context-intelligence/amplifier_module_hook_context_intelligence/upload.py"
    )
    spec = importlib.util.spec_from_file_location("hook_upload_shapes", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (
        build_event_payload("host:diagnostic", "fixture", data)["idempotency_key"]
        == module.build_payload("host:diagnostic", "fixture", data, "/hookdir")["idempotency_key"]
    )


@pytest.mark.parametrize("status", ["queued", "duplicate"])
async def test_ingest_preserves_real_server_acceptance_receipts_and_custom_envelope(status):
    captured = []
    payload = {
        **envelope(),
        "idempotency_key": "caller-owned-stable-id",
        "extension": {"future": True},
    }

    def handle(request):
        captured.append(request)
        return httpx.Response(202, json={"status": status, "session_id": "fixture-session"})

    with transport(handle):
        result = await AsyncCIClient("http://server.invalid/", "synthetic-key").ingest(payload)
    assert result == {"status": status, "session_id": "fixture-session"}
    assert len(captured) == 1
    assert json.loads(captured[0].content) == payload
    assert captured[0].headers["authorization"] == "Bearer synthetic-key"


@pytest.mark.parametrize(
    "code,body",
    [
        (200, {"status": "queued"}),
        (202, {"status": "complete"}),
        (202, {"status": []}),
        (202, {"status": "queued", "session_id": 3}),
        (202, []),
        (202, "malformed"),
    ],
)
async def test_unrecognized_receipt_is_not_success(code, body):
    def handle(request):
        return (
            httpx.Response(code, content=body)
            if isinstance(body, str)
            else httpx.Response(code, json=body)
        )

    with transport(handle), pytest.raises(CIClientError) as caught:
        await AsyncCIClient("http://server.invalid", "synthetic-key").ingest(envelope())
    assert caught.value.error_type == "decode_error"


@pytest.mark.parametrize("status", [302, 401, 403, 429, 503])
async def test_rejection_is_attempted_once_and_redirects_are_not_followed(status):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={"detail": "fixture rejection"},
            headers={"Location": "https://elsewhere.invalid", "Retry-After": "7"},
        )

    with transport(handle), pytest.raises(CIClientError) as caught:
        await AsyncCIClient("http://server.invalid", "synthetic-key").ingest(envelope())
    assert len(calls) == 1 and caught.value.status_code == status
    assert caught.value.error_type == "http_status"
    assert caught.value.retry_after == 7


@pytest.mark.parametrize(
    "failure,error_type", [(httpx.ReadTimeout, "timeout"), (httpx.ConnectError, "connection_error")]
)
async def test_transport_failure_does_not_retry(failure, error_type):
    calls = []

    def handle(request):
        calls.append(request)
        raise failure("fixture failure", request=request)

    with transport(handle), pytest.raises(CIClientError) as caught:
        await AsyncCIClient("http://server.invalid", "synthetic-key").ingest(envelope())
    assert caught.value.error_type == error_type and len(calls) == 1


async def test_auth_refresh_does_not_block_loop_or_allow_mutating_inflight_payload():
    started = threading.Event()
    release = threading.Event()
    captured = []

    class Strategy:
        def headers(self):
            started.set()
            assert release.wait(3)
            return {"Authorization": "Bearer refreshed-fixture"}

    def handle(request):
        captured.append(request)
        return httpx.Response(202, json={"status": "queued"})

    payload = envelope()
    with transport(handle):
        task = asyncio.create_task(
            AsyncCIClient("http://server.invalid", auth_strategy=Strategy()).ingest(payload)
        )
        try:
            async with asyncio.timeout(2):
                while not started.is_set():
                    await asyncio.sleep(0.001)
            payload["data"]["text"] = "changed while resolving auth"
            release.set()
            await task
        finally:
            release.set()
    assert json.loads(captured[0].content)["data"]["text"] == "Résumé"
    assert captured[0].headers["authorization"] == "Bearer refreshed-fixture"


async def test_invalid_auth_fails_before_network():
    class Strategy:
        def headers(self):
            raise ValueError("fixture credential unavailable")

    def handle(request):
        pytest.fail("No request for unusable auth")

    with transport(handle), pytest.raises(CIClientError) as caught:
        await AsyncCIClient("http://server.invalid", auth_strategy=Strategy()).ingest(envelope())
    assert caught.value.error_type == "auth_error"
