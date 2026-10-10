"""Network-unavailable delivery is OFFLINE (quiet, queued, auto-resuming), not an auth failure.

Regression for: during a DNS outage, an Entra-authenticated destination printed
thousands of lines (azure.identity tracebacks, "run `az login`", "forwarding
paused after sustained auth failures") to the terminal after the prompt
completed, although the login was valid and no data was lost.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DestinationDispatcher,
)
from amplifier_module_hook_context_intelligence.handlers.offline import (
    capture_credential_logs,
    is_offline_error,
)

MODULE_LOGGER = "amplifier_module_hook_context_intelligence.handlers.logging_handler"
AZ_TRACEBACK = (
    "AzureCliCredential.get_token failed: ERROR: The command failed with an unexpected "
    "error. Here is the traceback:\nTraceback (most recent call last):\n"
    "socket.gaierror: [Errno 8] nodename nor servname provided, or not known\n"
    "Failed to resolve 'login.microsoftonline.com'"
)


class CredentialUnavailableError(Exception):
    """Stand-in for azure.identity.CredentialUnavailableError (matched by behaviour)."""


def _dispatcher(tmp_path: Path) -> _DestinationDispatcher:
    return _DestinationDispatcher(
        name="made-team-shared",
        url="https://apim.example.net/context-intelligence",
        api_key="unused",
        workspace="ws",
        dispatch_timeout=5.0,
        failure_threshold=3,
        queue_capacity=64,
        close_drain_timeout=0.5,
        backoff_initial=0.001,
        backoff_max=0.001,
        backoff_jitter=False,
        forwarding_log_dir=tmp_path,
    )


def _response(status: int) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    return r


def _install(d: _DestinationDispatcher, headers: Any, post: Any) -> AsyncMock:
    d._strategy = MagicMock()
    d._strategy.headers.side_effect = headers
    client = MagicMock()
    client.is_closed = False
    client.aclose = AsyncMock()
    client.post = post if isinstance(post, AsyncMock) else AsyncMock(side_effect=post)
    d._client = client
    return client.post


async def _wait(cond: Any, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not cond():
        assert loop.time() < end, "condition not reached"
        await asyncio.sleep(0.005)


def _records(tmp_path: Path) -> list[dict[str, Any]]:
    import json

    out: list[dict[str, Any]] = []
    for f in tmp_path.glob("forwarding-*.jsonl"):
        out += [json.loads(line) for line in f.read_text().splitlines()]
    return out


class _TerminalHandler(logging.Handler):
    """Stands in for the user's terminal: root handler at WARNING."""

    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.fixture
def terminal() -> Any:
    h = _TerminalHandler()
    root = logging.getLogger()
    old = root.level
    root.addHandler(h)
    root.setLevel(logging.WARNING)
    yield h
    root.removeHandler(h)
    root.setLevel(old)


class TestClassification:
    def test_dns_failure_is_offline(self) -> None:
        assert is_offline_error(socket.gaierror(8, "nodename nor servname"))

    def test_oserror_22_is_offline(self) -> None:
        assert is_offline_error(OSError(22, "Invalid argument"))

    def test_generic_credential_error_with_network_cause_in_logs_is_offline(self) -> None:
        exc = CredentialUnavailableError("Failed to invoke the Azure CLI")
        assert is_offline_error(exc, AZ_TRACEBACK)

    def test_generic_credential_error_without_evidence_is_not_offline(self) -> None:
        assert not is_offline_error(CredentialUnavailableError("Failed to invoke the Azure CLI"))

    def test_aadsts_wins_over_network_words(self) -> None:
        exc = CredentialUnavailableError("AADSTS700082: The refresh token has expired (timeout)")
        assert not is_offline_error(exc)

    def test_plain_oserror_such_as_enoent_is_not_offline(self) -> None:
        assert not is_offline_error(FileNotFoundError(2, "No such file"))


class TestCaptureCredentialLogs:
    def test_azure_warning_is_captured_not_propagated(self, terminal: _TerminalHandler) -> None:
        with capture_credential_logs() as captured:
            logging.getLogger("azure.identity._internal").warning(AZ_TRACEBACK)
            logging.getLogger("msal.application").warning("msal noise")
        assert any("Failed to resolve" in m for m in captured)
        assert terminal.lines == []
        # state restored: a later azure warning reaches the terminal again
        logging.getLogger("azure.identity").warning("after")
        assert terminal.lines == ["after"]


class TestOfflineQueueing:
    async def test_dns_failure_from_credential_queues_quietly_then_resumes(
        self, tmp_path: Path, terminal: _TerminalHandler
    ) -> None:
        d = _dispatcher(tmp_path)
        online = {"v": False}

        def headers() -> dict[str, str]:
            if online["v"]:
                return {"Authorization": "Bearer t"}
            # azure-identity warns with the whole az traceback, then raises generically
            logging.getLogger("azure.identity._credentials.azure_cli").warning(AZ_TRACEBACK)
            logging.getLogger("azure.identity._credentials.chained").warning(
                "AzureCliCredential.get_token failed: Failed to invoke the Azure CLI"
            )
            raise CredentialUnavailableError("Failed to invoke the Azure CLI")

        post = _install(d, headers, AsyncMock(return_value=_response(200)))
        for i in range(3):
            assert d.enqueue("evt", {"session_id": "s", "n": i})

        await _wait(lambda: d._consecutive_failures >= 5)  # several offline retries
        assert d._offline is True
        assert post.await_count == 0  # nothing posted, nothing lost: still queued/in flight
        assert d._breaker_open is False and d._auth_token_failed is False
        assert d._overflow_dropped == 0

        # Terminal: exactly one concise offline line; no az login / traceback.
        assert len(terminal.lines) == 1, terminal.lines
        assert "offline (network)" in terminal.lines[0]
        assert "az login" not in "\n".join(terminal.lines)
        assert "Traceback" not in "\n".join(terminal.lines)
        # Details went to the durable diagnostics file.
        kinds = [r["kind"] for r in _records(tmp_path)]
        assert kinds.count("offline") == 1
        assert "auth_token_unavailable" not in kinds

        # Recovery: backlog delivered in order, one resume line.
        online["v"] = True
        await _wait(lambda: post.await_count == 3)
        await _wait(lambda: d._offline is False)
        resume = [ln for ln in terminal.lines if "back online" in ln]
        assert len(resume) == 1
        assert len(terminal.lines) == 2
        await d.close()

    async def test_httpx_dns_failure_on_post_is_offline_too(
        self, tmp_path: Path, terminal: _TerminalHandler
    ) -> None:
        import httpx

        d = _dispatcher(tmp_path)
        calls = {"n": 0}

        async def post(*_a: Any, **_k: Any) -> MagicMock:
            calls["n"] += 1
            if calls["n"] < 4:
                raise httpx.ConnectError("[Errno 8] nodename nor servname provided")
            return _response(200)

        _install(d, lambda: {"Authorization": "Bearer t"}, post)
        d.enqueue("evt", {"session_id": "s"})
        await _wait(lambda: calls["n"] >= 4 and d._offline is False)
        assert [ln for ln in terminal.lines if "offline (network)" in ln] != []
        assert len(terminal.lines) == 2
        await d.close()

    async def test_raw_oserror_22_on_post_is_offline(self, tmp_path: Path) -> None:
        d = _dispatcher(tmp_path)
        calls = {"n": 0}

        async def post(*_a: Any, **_k: Any) -> MagicMock:
            calls["n"] += 1
            if calls["n"] < 3:
                raise OSError(22, "Invalid argument")
            return _response(200)

        _install(d, lambda: {"Authorization": "Bearer t"}, post)
        d.enqueue("evt", {"session_id": "s"})
        await _wait(lambda: calls["n"] >= 3 and d._offline is False)
        await d.close()


class TestGenuineAuthFailure:
    async def test_aadsts_warns_once_with_az_login_and_no_traceback(
        self, tmp_path: Path, terminal: _TerminalHandler
    ) -> None:
        d = _dispatcher(tmp_path)

        def headers() -> dict[str, str]:
            logging.getLogger("azure.identity").warning("AADSTS70043 refresh token expired")
            raise CredentialUnavailableError("AADSTS70043: The refresh token has expired")

        _install(d, headers, AsyncMock(return_value=_response(200)))
        for i in range(5):
            d.enqueue("evt", {"session_id": "s", "n": i})
        await _wait(lambda: d._queue.empty() and d._current is None)

        assert d._offline is False
        auth_lines = [ln for ln in terminal.lines if "az login" in ln]
        assert len(auth_lines) == 1, terminal.lines
        assert not any("AADSTS70043 refresh" in ln for ln in terminal.lines)  # azure log captured
        assert not any("Traceback" in ln for ln in terminal.lines)
        await d.close()

    async def test_http_401_still_counts_as_auth_failure_not_offline(self, tmp_path: Path) -> None:
        d = _dispatcher(tmp_path)
        _install(d, lambda: {"Authorization": "Bearer t"}, AsyncMock(return_value=_response(401)))
        d.enqueue("evt", {"session_id": "s"})
        await _wait(lambda: d._auth_failures >= 1)
        assert d._offline is False
        assert d._last_status == 401
        await d.close()
