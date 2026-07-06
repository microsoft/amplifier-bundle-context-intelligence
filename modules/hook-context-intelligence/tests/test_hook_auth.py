"""Tests for hook dual-auth (slice 2-C).

Covers:
- Destination dataclass gains auth_mode / auth_resource fields
- validate_destinations() per-target XOR: entra→auth_resource, static→api_key
- _DestinationDispatcher uses auth strategy per-request (not baked into client)
- Entra dispatcher calls strategy.headers() on each post, not at construction
- Static dispatcher backwards-compatible (api_key bearer)
- Mixed fleet: one static + one entra destination, each validated independently
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
    _DELIVERED,
    _TRANSIENT,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolver(config: dict) -> object:
    from amplifier_module_hook_context_intelligence.config_resolver import HookConfigResolver

    coord = MagicMock()
    coord.config = {}
    coord.get_capability = MagicMock(return_value=None)
    return HookConfigResolver(config, coord)


class FakeToken:
    # expires_on far future so cached tokens are never considered stale in tests
    def __init__(self, token: str, expires_on: float = 9_999_999_999.0) -> None:
        self.token = token
        self.expires_on = expires_on


class FakeCredential:
    def __init__(self, token: str = "fake-entra-token") -> None:
        self._token = token
        self.calls: list[tuple[Any, ...]] = []

    def get_token(self, *scopes: str, **kwargs: Any) -> FakeToken:
        self.calls.append(scopes)
        return FakeToken(self._token)


# ---------------------------------------------------------------------------
# Destination dataclass has auth_mode / auth_resource
# ---------------------------------------------------------------------------


class TestDestinationDataclass:
    """Destination has auth_mode / auth_resource fields."""

    def test_default_auth_mode_is_static(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "team": {"url": "http://ci:8000", "api_key": "sk-key", "include": ["**"]},
                }
            }
        )
        d = r.destinations["team"]  # type: ignore[attr-defined]
        assert d.auth_mode == "static"

    def test_default_auth_resource_is_empty(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "team": {"url": "http://ci:8000", "api_key": "sk-key", "include": ["**"]},
                }
            }
        )
        d = r.destinations["team"]  # type: ignore[attr-defined]
        assert d.auth_resource == ""

    def test_entra_dest_stores_auth_resource(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "azure": {
                        "url": "http://ci:8000",
                        "auth_mode": "entra",
                        "auth_resource": "api://53aa4ffd",
                        "include": ["**"],
                    },
                }
            }
        )
        d = r.destinations["azure"]  # type: ignore[attr-defined]
        assert d.auth_mode == "entra"
        assert d.auth_resource == "api://53aa4ffd"


# ---------------------------------------------------------------------------
# validate_destinations() XOR validation
# ---------------------------------------------------------------------------


class TestValidateDestinationsXOR:
    """Per-target XOR: entra requires auth_resource, static requires api_key."""

    def test_static_valid_passes(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "local": {"url": "http://ci:8000", "api_key": "sk", "include": ["**"]},
                }
            }
        )
        result = r.validate_destinations()  # type: ignore[attr-defined]
        assert "local" in result

    def test_entra_valid_passes(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "azure": {
                        "url": "http://ci:8000",
                        "auth_mode": "entra",
                        "auth_resource": "api://53aa4ffd",
                        "include": ["**"],
                    },
                }
            }
        )
        result = r.validate_destinations()  # type: ignore[attr-defined]
        assert "azure" in result

    def test_entra_missing_auth_resource_raises(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "azure": {
                        "url": "http://ci:8000",
                        "auth_mode": "entra",
                        # no auth_resource
                        "include": ["**"],
                    },
                }
            }
        )
        with pytest.raises(ValueError, match="azure.*missing auth_resource"):
            r.validate_destinations()  # type: ignore[attr-defined]

    def test_entra_does_not_require_api_key(self) -> None:
        """Entra mode with valid auth_resource and no api_key must NOT raise."""
        r = _resolver(
            {
                "destinations": {
                    "azure": {
                        "url": "http://ci:8000",
                        "auth_mode": "entra",
                        "auth_resource": "api://53aa4ffd",
                        # no api_key — must be OK
                        "include": ["**"],
                    },
                }
            }
        )
        result = r.validate_destinations()  # type: ignore[attr-defined]
        assert "azure" in result

    def test_unknown_auth_mode_raises(self) -> None:
        r = _resolver(
            {
                "destinations": {
                    "weird": {
                        "url": "http://ci:8000",
                        "auth_mode": "kerberos",
                        "api_key": "k",
                        "include": ["**"],
                    },
                }
            }
        )
        with pytest.raises(ValueError, match="kerberos"):
            r.validate_destinations()  # type: ignore[attr-defined]

    def test_mixed_fleet_valid(self) -> None:
        """Static + entra destinations coexist; each validates independently."""
        r = _resolver(
            {
                "destinations": {
                    "local": {"url": "http://local:8000", "api_key": "sk", "include": ["local/**"]},
                    "azure": {
                        "url": "http://azure:8000",
                        "auth_mode": "entra",
                        "auth_resource": "api://53aa4ffd",
                        "include": ["**"],
                    },
                }
            }
        )
        result = r.validate_destinations()  # type: ignore[attr-defined]
        assert set(result.keys()) == {"local", "azure"}

    def test_mixed_fleet_entra_invalid_raises(self) -> None:
        """Mixed fleet raises if entra dest is missing auth_resource."""
        r = _resolver(
            {
                "destinations": {
                    "local": {"url": "http://local:8000", "api_key": "sk", "include": ["**"]},
                    "azure": {
                        "url": "http://azure:8000",
                        "auth_mode": "entra",
                        # missing auth_resource
                        "include": ["**"],
                    },
                }
            }
        )
        with pytest.raises(ValueError, match="azure"):
            r.validate_destinations()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# _DestinationDispatcher uses strategy per-request (not baked into client)
# ---------------------------------------------------------------------------


def _make_dispatcher(
    *,
    auth_mode: str = "static",
    api_key: str = "static-key",
    auth_resource: str = "",
    credential: Any = None,
) -> object:
    """Build a _DestinationDispatcher with injected credential for entra or static key."""
    from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
        _DestinationDispatcher,
    )
    from context_intelligence.auth import build_auth_strategy

    strategy = build_auth_strategy(
        auth_mode=auth_mode,
        api_key=api_key,
        auth_resource=auth_resource,
        credential=credential,
    )
    # The import inside __init__ is a local import so we patch at the source:
    # context_intelligence.auth.build_auth_strategy is the canonical location.
    with patch("context_intelligence.auth.build_auth_strategy", return_value=strategy):
        d = _DestinationDispatcher(
            name="test",
            url="http://localhost:38000",
            api_key=api_key,
            workspace="ws",
            dispatch_timeout=10.0,
            failure_threshold=3,
            queue_capacity=256,
            close_drain_timeout=0.5,
            auth_mode=auth_mode,
            auth_resource=auth_resource,
        )
    return d


class TestDispatcherEntraPerRequestHeader:
    """_DestinationDispatcher calls strategy.headers() per-request, not at client construction."""

    async def test_entra_header_passed_per_post(self) -> None:
        """Each _post call passes the header from strategy.headers() to client.post."""
        fake_cred = FakeCredential("entra-bearer-abc")
        d = _make_dispatcher(
            auth_mode="entra", auth_resource="api://53aa4ffd", credential=fake_cred
        )

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(status_code=200)
        d._client = mock_client  # type: ignore[attr-defined]

        await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]

        # post must have been called with the entra header
        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["headers"] == {"Authorization": "Bearer entra-bearer-abc"}

    async def test_static_header_passed_per_post(self) -> None:
        """Static _post also passes headers on each call."""
        d = _make_dispatcher(auth_mode="static", api_key="sk-static")

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(status_code=200)
        d._client = mock_client  # type: ignore[attr-defined]

        await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]

        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["headers"] == {"Authorization": "Bearer sk-static"}

    async def test_entra_client_has_no_baked_auth_header(self) -> None:
        """The lazy client created by _post has NO Authorization header at construction."""

        fake_cred = FakeCredential("some-token")
        d = _make_dispatcher(
            auth_mode="entra", auth_resource="api://53aa4ffd", credential=fake_cred
        )

        mock_response = MagicMock(status_code=200)

        with patch("httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_instance.is_closed = False
            mock_instance.post.return_value = mock_response
            mock_cls.return_value = mock_instance

            await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]

        # Check the AsyncClient() constructor was called WITHOUT headers kwarg
        _, client_kwargs = mock_cls.call_args
        assert "headers" not in client_kwargs, (
            "Authorization must NOT be baked into the httpx.AsyncClient at construction — "
            "it must be passed per-request so Entra tokens can refresh."
        )

    async def test_entra_headers_called_each_post_not_once(self) -> None:
        """strategy.headers() is called once per _post call, so token can refresh."""
        from context_intelligence.auth import EntraTokenAuth

        fake_cred = FakeCredential("rotating-token")
        strategy = EntraTokenAuth(fake_cred, "api://53aa4ffd")

        with patch("context_intelligence.auth.build_auth_strategy", return_value=strategy):
            from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
                _DestinationDispatcher,
            )

            d = _DestinationDispatcher(
                name="t",
                url="http://h:8000",
                api_key="",
                workspace="ws",
                dispatch_timeout=10.0,
                failure_threshold=3,
                queue_capacity=256,
                close_drain_timeout=0.5,
                auth_mode="entra",
                auth_resource="api://53aa4ffd",
            )

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(status_code=200)
        d._client = mock_client

        # Post 3 events
        for i in range(3):
            await d._post(f"event:{i}", {"session_id": "s1"})

        # strategy.headers() is called once per post (not baked at construction time),
        # but the in-process cache means get_token is only called on the FIRST miss.
        # Subsequent posts serve the cached token — expected call count is 1, not 3.
        assert len(fake_cred.calls) == 1, (
            f"Expected 1 get_token call (cached after first), got {len(fake_cred.calls)}"
        )


# ---------------------------------------------------------------------------
# Backward compat: existing static dispatcher tests still pass
# ---------------------------------------------------------------------------


class TestDispatcherStaticBackwardCompat:
    """Static dispatcher with api_key still works exactly as before."""

    async def test_static_dispatcher_succeeds(self) -> None:
        d = _make_dispatcher(auth_mode="static", api_key="sk-backward-compat")

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_response = MagicMock(status_code=200)
        mock_client.post.return_value = mock_response
        d._client = mock_client  # type: ignore[attr-defined]

        await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]

        mock_client.post.assert_awaited_once()
        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-backward-compat"


# ---------------------------------------------------------------------------
# Auth-strategy exception handling: expired `az login` must not silently drop
# events (see logging_handler.py _post / _worker).
#
# Root cause: self._strategy.headers() was called OUTSIDE _post()'s try block.
# When az login expires, azure-identity's AzureCliCredential.get_token() raises
# CredentialUnavailableError / ClientAuthenticationError. That exception was not
# in _post()'s except tuple, so it propagated to the worker's generic
# `except Exception`, which logs "worker_unclassified_exception: poisoned event
# dropped" and PERMANENTLY drops the event -- no retry. This is worse than the
# graceful "retrying with backoff" path used for network/HTTP errors.
# ---------------------------------------------------------------------------


class _RaisingCredential:
    """Stand-in for an azure-identity credential whose get_token() raises when
    `az login` has expired.

    azure-identity is an OPTIONAL dependency (not installed in this test venv;
    auth.py imports it LAZILY), so we cannot import the real
    CredentialUnavailableError / ClientAuthenticationError here. A plain
    Exception subclass is sufficient to exercise the _post() auth-exception
    handling path, since the fix catches auth-header production broadly at
    that single well-defined boundary (see logging_handler._post).
    """

    class SimulatedAuthError(Exception):
        """Stands in for azure.core.exceptions.CredentialUnavailableError."""

    def __init__(
        self, fail_times: int | None = None, recover_token: str = "recovered-token"
    ) -> None:
        # fail_times=None means "always fail"; otherwise fail on the first
        # `fail_times` calls, then succeed (simulates a mid-session `az login`).
        self._fail_times = fail_times
        self.calls = 0
        self._recover_token = recover_token

    def get_token(self, *scopes: str, **kwargs: Any) -> FakeToken:
        self.calls += 1
        if self._fail_times is None or self.calls <= self._fail_times:
            raise self.SimulatedAuthError(
                "SIMULATED: az login expired / CredentialUnavailableError"
            )
        return FakeToken(self._recover_token)


class TestAuthExceptionEventLoss:
    """_post() must classify auth-strategy (headers()) exceptions as _TRANSIENT
    so the worker retries with backoff, instead of letting them propagate to
    the worker's unclassified-exception handler which permanently drops the
    in-flight event.
    """

    async def test_headers_exception_returns_transient_not_raise(self) -> None:
        """Expired az login (credential.get_token raises) must not crash _post();
        it must return _TRANSIENT so the worker retries instead of dropping."""
        cred = _RaisingCredential()
        d = _make_dispatcher(auth_mode="entra", auth_resource="api://53aa4ffd", credential=cred)

        mock_client = AsyncMock()
        mock_client.is_closed = False
        d._client = mock_client  # type: ignore[attr-defined]

        result = await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]

        assert result == _TRANSIENT
        # The HTTP POST must never be attempted -- we never had a header to send.
        mock_client.post.assert_not_awaited()

    async def test_headers_exception_clears_last_status_not_401(self) -> None:
        """The auth-strategy failure is NOT an HTTP 401 -- _last_status must be
        cleared to None. If left as a stale prior 401, the worker's
        `is_auth_401 = self._last_status == 401` check would miscount this
        failure toward the 401 give-up ceiling (_AUTH_GIVEUP_ATTEMPTS) and
        eventually PERMANENTLY SKIP the event -- exactly the silent loss this
        fix removes."""
        cred = _RaisingCredential()
        d = _make_dispatcher(auth_mode="entra", auth_resource="api://53aa4ffd", credential=cred)
        d._last_status = 401  # type: ignore[attr-defined]  # stale 401 from a prior event

        mock_client = AsyncMock()
        mock_client.is_closed = False
        d._client = mock_client  # type: ignore[attr-defined]

        result = await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]

        assert result == _TRANSIENT
        assert d._last_status is None  # type: ignore[attr-defined]

    async def test_headers_exception_recovers_after_az_login(self) -> None:
        """First call: credential raises (transient, not dropped). After a
        mid-session `az login` refreshes the credential, the next _post() call
        succeeds -- proves graceful recovery, not a permanent drop."""
        cred = _RaisingCredential(fail_times=1)
        d = _make_dispatcher(auth_mode="entra", auth_resource="api://53aa4ffd", credential=cred)

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(status_code=200)
        d._client = mock_client  # type: ignore[attr-defined]

        first = await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]
        assert first == _TRANSIENT

        second = await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]
        assert second == _DELIVERED

    async def test_static_auth_unaffected_by_exception_handling(self) -> None:
        """Static ApiKeyAuth.headers() is a pure f-string that never raises --
        confirms the new try/except around headers() changes nothing for the
        static path (mirrors TestDispatcherStaticBackwardCompat)."""
        d = _make_dispatcher(auth_mode="static", api_key="sk-static")

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(status_code=200)
        d._client = mock_client  # type: ignore[attr-defined]

        result = await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]

        assert result == _DELIVERED
        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-static"

    async def test_non_auth_exception_returns_transient_and_names_type(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A NON-auth bug inside headers() (e.g. a TypeError from a real
        programming error) must ALSO return _TRANSIENT -- the broad `except
        Exception` is intentional because azure-identity is optional and its
        error types are not importable here. But the broad catch must be
        HONEST: the emitted WARNING must name the caught exception's TYPE so a
        masked programming bug is VISIBLE rather than being silently
        reclassified as an expired `az login`. This locks in tester-breaker's
        'nothing asserts a non-auth exception isn't silently swallowed'."""

        class _BuggyStrategy:
            def headers(self) -> dict[str, str]:
                raise TypeError("simulated programming bug inside headers()")

        d = _make_dispatcher(auth_mode="static", api_key="sk")
        d._strategy = _BuggyStrategy()  # type: ignore[attr-defined]

        mock_client = AsyncMock()
        mock_client.is_closed = False
        d._client = mock_client  # type: ignore[attr-defined]

        with caplog.at_level(logging.WARNING):
            result = await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]

        # Broad catch keeps the event recoverable, not dropped.
        assert result == _TRANSIENT
        # No header was produced, so the POST must never be attempted.
        mock_client.post.assert_not_awaited()
        # The exception TYPE is named in the emitted log -> masked bug is visible.
        assert "TypeError" in caplog.text


class TestAuthExceptionWorkerIntegration:
    """End-to-end proof through the REAL _worker() loop (ROB's gate): an event
    whose first auth-header attempt fails (expired `az login`) is retried with
    backoff and ultimately DELIVERED once the credential recovers -- NOT dropped
    via the worker's 'poisoned event dropped' unclassified-exception path.

    This proves the _TRANSIENT returned from the headers() failure actually
    round-trips through the worker's retry loop to eventual delivery, not just
    at the _post() boundary.
    """

    async def test_event_survives_headers_failure_through_worker_to_delivery(self) -> None:
        from context_intelligence.auth import EntraTokenAuth

        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            _DestinationDispatcher,
        )

        # Credential raises on the first get_token (expired az login), then
        # returns a valid token (simulating a mid-session `az login`).
        cred = _RaisingCredential(fail_times=1)
        strategy = EntraTokenAuth(cred, "api://53aa4ffd")

        with patch("context_intelligence.auth.build_auth_strategy", return_value=strategy):
            d = _DestinationDispatcher(
                name="t",
                url="http://h:8000",
                api_key="",
                workspace="ws",
                dispatch_timeout=10.0,
                failure_threshold=3,
                queue_capacity=256,
                close_drain_timeout=0.5,
                auth_mode="entra",
                auth_resource="api://53aa4ffd",
                # Zero, jitter-free backoff -> deterministic and instant retry.
                backoff_initial=0.0,
                backoff_jitter=False,
            )

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(status_code=200)
        d._client = mock_client  # type: ignore[attr-defined]

        # Patch the module logger so we can assert the poisoned-drop path
        # (logger.exception("worker_unclassified_exception...")) was NEVER hit.
        with patch(
            "amplifier_module_hook_context_intelligence.handlers.logging_handler.logger"
        ) as mock_logger:
            d.enqueue("session:start", {"session_id": "s1"})
            # Condition-based wait (NOT a fixed sleep): queue.join() returns once
            # the worker has called task_done() -- i.e. the event left the queue
            # via the DELIVERED path.
            await asyncio.wait_for(d._queue.join(), timeout=2.0)
            await d.close()

        # Delivered exactly once, after the credential recovered.
        assert mock_client.post.await_count == 1
        # get_token called twice: first raised, second succeeded.
        assert cred.calls == 2
        # The event was NOT dropped via the unclassified-exception handler.
        assert mock_logger.exception.call_count == 0
        # Queue fully drained; no in-flight event stranded.
        assert d._queue.qsize() == 0
        assert d._current is None  # type: ignore[attr-defined]

    async def test_auth_path_degraded_warning_does_not_contradict_az_login_guidance(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The worker's generic DEGRADED warning must NOT tell the operator
        'no action needed' on the auth path -- that directly contradicts the
        actionable 'run `az login`' warning _post() emits for the same failure.
        Regression guard for the reworded, cause-agnostic degraded message.
        """
        from context_intelligence.auth import EntraTokenAuth

        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            _DestinationDispatcher,
        )

        # Fail the first auth attempt (expired az login), then recover so the
        # worker emits BOTH the auth warning and the degraded warning, then drains.
        cred = _RaisingCredential(fail_times=1)
        strategy = EntraTokenAuth(cred, "api://53aa4ffd")
        with patch("context_intelligence.auth.build_auth_strategy", return_value=strategy):
            d = _DestinationDispatcher(
                name="azure",
                url="http://h:8000",
                api_key="",
                workspace="ws",
                dispatch_timeout=10.0,
                failure_threshold=3,
                queue_capacity=256,
                close_drain_timeout=0.5,
                auth_mode="entra",
                auth_resource="api://53aa4ffd",
                backoff_initial=0.0,
                backoff_jitter=False,
            )

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(status_code=200)
        d._client = mock_client  # type: ignore[attr-defined]

        # Capture at INFO on the module logger so we see BOTH the actionable
        # WARNING and the now-INFO transient degraded notice. Targeting the
        # logger is required: the module logger's effective level is WARNING, so
        # a bare caplog.at_level(INFO) on root would let the INFO notice be
        # filtered before capture.
        with caplog.at_level(logging.INFO, logger="amplifier_module_hook_context_intelligence"):
            d.enqueue("session:start", {"session_id": "s1"})
            await asyncio.wait_for(d._queue.join(), timeout=2.0)
            await d.close()

        # The actionable auth guidance IS present ...
        assert "az login" in caplog.text
        # ... and the contradictory reassurance is GONE.
        assert "no action needed" not in caplog.text
        # The degraded notice is cause-agnostic AND must be a diagnostic INFO,
        # never a user-facing WARNING -- transient, non-actionable, events stay
        # durable. Only "az login" should reach the default WARNING stream here.
        assert "delivery degraded, retrying with backoff" in caplog.text
        degraded_records = [
            r
            for r in caplog.records
            if "delivery degraded, retrying with backoff" in r.getMessage()
        ]
        assert degraded_records, "degraded notice should be emitted"
        assert all(r.levelno == logging.INFO for r in degraded_records), (
            "degraded notice must be INFO (off the default WARNING stream), got "
            f"{[r.levelname for r in degraded_records]}"
        )
        az_login_records = [r for r in caplog.records if "az login" in r.getMessage()]
        assert az_login_records and all(r.levelno == logging.WARNING for r in az_login_records), (
            "the actionable 'az login' guidance must remain a WARNING"
        )
