"""Tests for _post outcome constants and HTTP/exception classification (Task 4).

Classification table (Option X — clean two-way split):
- DELIVERED: any < 400 HTTP response; RuntimeError("client closed")
- TRANSIENT (retry w/ backoff): httpx.ConnectError, ConnectTimeout, ReadTimeout,
  PoolTimeout, RemoteProtocolError; HTTP 5xx; HTTP 429; HTTP 401
- PERMANENT (loud skip): HTTP 403 -> 'check credentials';
  HTTP 400/413/422 and any other 4xx -> 'malformed event, skipped'
- Unclassified (bare Exception, TypeError): propagate out of _post
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
import httpx

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DELIVERED,
    _PERMANENT,
    _TRANSIENT,
    _DestinationDispatcher,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _dispatcher(**overrides: object) -> _DestinationDispatcher:
    """Create a _DestinationDispatcher with minimal defaults."""
    defaults: dict[str, object] = dict(
        name="test",
        url="http://localhost:8080",
        api_key="test-key",
        workspace="ws",
        dispatch_timeout=10.0,
        failure_threshold=3,
        queue_capacity=256,
        close_drain_timeout=0.5,
    )
    defaults.update(overrides)
    return _DestinationDispatcher(**defaults)  # type: ignore[arg-type]


def _mock_response(status_code: int) -> MagicMock:
    """Create a mock HTTP response with the given status_code attribute."""
    r = MagicMock()
    r.status_code = status_code
    return r


def _mock_client_for_response(response: MagicMock) -> AsyncMock:
    """Create a mock httpx.AsyncClient that returns response on .post()."""
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(return_value=response)
    return client


def _mock_client_for_exc(exc: BaseException) -> AsyncMock:
    """Create a mock httpx.AsyncClient that raises exc on .post()."""
    client = AsyncMock()
    client.is_closed = False
    client.post = AsyncMock(side_effect=exc)
    return client


# ---------------------------------------------------------------------------
# Outcome constants
# ---------------------------------------------------------------------------

class TestOutcomeConstants:
    """_DELIVERED, _TRANSIENT, _PERMANENT are importable, non-None, and distinct."""

    def test_delivered_importable(self) -> None:
        assert _DELIVERED is not None

    def test_transient_importable(self) -> None:
        assert _TRANSIENT is not None

    def test_permanent_importable(self) -> None:
        assert _PERMANENT is not None

    def test_all_distinct(self) -> None:
        assert _DELIVERED != _TRANSIENT
        assert _DELIVERED != _PERMANENT
        assert _TRANSIENT != _PERMANENT


# ---------------------------------------------------------------------------
# 2xx → _DELIVERED
# ---------------------------------------------------------------------------

class TestDelivered:
    """Any HTTP response with status < 400 returns _DELIVERED."""

    @pytest.mark.parametrize("status_code", [200, 201, 204])
    async def test_2xx_delivered(self, status_code: int) -> None:
        d = _dispatcher()
        d._client = _mock_client_for_response(_mock_response(status_code))

        result = await d._post("test:event", {"session_id": "s1"})

        assert result == _DELIVERED

    @pytest.mark.parametrize("status_code", [200, 201, 204])
    async def test_2xx_sets_last_status(self, status_code: int) -> None:
        d = _dispatcher()
        d._client = _mock_client_for_response(_mock_response(status_code))

        await d._post("test:event", {"session_id": "s1"})

        assert d._last_status == status_code

    async def test_client_closed_runtime_error_returns_delivered(self) -> None:
        """RuntimeError('client has been closed') is treated as _DELIVERED (teardown path)."""
        d = _dispatcher()
        d._client = _mock_client_for_exc(
            RuntimeError("Cannot send a request, as the client has been closed.")
        )

        result = await d._post("test:event", {"session_id": "s1"})

        assert result == _DELIVERED


# ---------------------------------------------------------------------------
# 5xx / 429 / 401 → _TRANSIENT
# ---------------------------------------------------------------------------

class TestTransientHttp:
    """HTTP 5xx, 429, and 401 return _TRANSIENT."""

    @pytest.mark.parametrize("status_code", [401, 429, 500, 502, 503])
    async def test_transient_status_codes(self, status_code: int) -> None:
        d = _dispatcher()
        d._client = _mock_client_for_response(_mock_response(status_code))

        result = await d._post("test:event", {"session_id": "s1"})

        assert result == _TRANSIENT

    @pytest.mark.parametrize("status_code", [401, 429, 500, 502, 503])
    async def test_transient_sets_last_status(self, status_code: int) -> None:
        d = _dispatcher()
        d._client = _mock_client_for_response(_mock_response(status_code))

        await d._post("test:event", {"session_id": "s1"})

        assert d._last_status == status_code


# ---------------------------------------------------------------------------
# httpx network exceptions → _TRANSIENT
# ---------------------------------------------------------------------------

class TestTransientNetworkExceptions:
    """Listed httpx transport exceptions return _TRANSIENT."""

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("connection refused"),
            httpx.ConnectTimeout("connect timeout"),
            httpx.ReadTimeout("read timeout"),
            httpx.PoolTimeout("pool timeout"),
            httpx.RemoteProtocolError("bad response"),
        ],
        ids=["ConnectError", "ConnectTimeout", "ReadTimeout", "PoolTimeout", "RemoteProtocolError"],
    )
    async def test_httpx_exception_transient(self, exc: httpx.TransportError) -> None:
        d = _dispatcher()
        d._client = _mock_client_for_exc(exc)

        result = await d._post("test:event", {"session_id": "s1"})

        assert result == _TRANSIENT

    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("connection refused"),
            httpx.ConnectTimeout("connect timeout"),
            httpx.ReadTimeout("read timeout"),
            httpx.PoolTimeout("pool timeout"),
            httpx.RemoteProtocolError("bad response"),
        ],
        ids=["ConnectError", "ConnectTimeout", "ReadTimeout", "PoolTimeout", "RemoteProtocolError"],
    )
    async def test_httpx_exception_does_not_set_last_status(
        self, exc: httpx.TransportError
    ) -> None:
        """Network exceptions do not produce an HTTP response, so _last_status stays None."""
        d = _dispatcher()
        assert d._last_status is None
        d._client = _mock_client_for_exc(exc)

        await d._post("test:event", {"session_id": "s1"})

        assert d._last_status is None


# ---------------------------------------------------------------------------
# 403 / 400 / 413 / 422 / other 4xx → _PERMANENT
# ---------------------------------------------------------------------------

class TestPermanent:
    """HTTP 403, 400, 413, 422, and any other 4xx return _PERMANENT."""

    @pytest.mark.parametrize("status_code", [400, 403, 413, 422, 404, 410, 451])
    async def test_4xx_permanent(self, status_code: int) -> None:
        d = _dispatcher()
        d._client = _mock_client_for_response(_mock_response(status_code))

        result = await d._post("test:event", {"session_id": "s1"})

        assert result == _PERMANENT

    @pytest.mark.parametrize("status_code", [400, 403, 413, 422, 404, 410])
    async def test_permanent_sets_last_status(self, status_code: int) -> None:
        d = _dispatcher()
        d._client = _mock_client_for_response(_mock_response(status_code))

        await d._post("test:event", {"session_id": "s1"})

        assert d._last_status == status_code


# ---------------------------------------------------------------------------
# Unclassified exceptions propagate
# ---------------------------------------------------------------------------

class TestUnclassifiedExceptionsPropagates:
    """Bare Exception and TypeError propagate out of _post (not caught)."""

    async def test_bare_exception_propagates(self) -> None:
        d = _dispatcher()
        d._client = _mock_client_for_exc(Exception("unexpected failure"))

        with pytest.raises(Exception, match="unexpected failure"):
            await d._post("test:event", {"session_id": "s1"})

    async def test_type_error_propagates(self) -> None:
        d = _dispatcher()
        d._client = _mock_client_for_exc(TypeError("bad type"))

        with pytest.raises(TypeError, match="bad type"):
            await d._post("test:event", {"session_id": "s1"})

    async def test_runtime_error_without_closed_propagates(self) -> None:
        """RuntimeError that does NOT contain 'closed' propagates."""
        d = _dispatcher()
        d._client = _mock_client_for_exc(RuntimeError("some other runtime error"))

        with pytest.raises(RuntimeError, match="some other runtime error"):
            await d._post("test:event", {"session_id": "s1"})
