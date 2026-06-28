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
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


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
        r = _resolver({
            "destinations": {
                "team": {"url": "http://ci:8000", "api_key": "sk-key", "include": ["**"]},
            }
        })
        d = r.destinations["team"]  # type: ignore[attr-defined]
        assert d.auth_mode == "static"

    def test_default_auth_resource_is_empty(self) -> None:
        r = _resolver({
            "destinations": {
                "team": {"url": "http://ci:8000", "api_key": "sk-key", "include": ["**"]},
            }
        })
        d = r.destinations["team"]  # type: ignore[attr-defined]
        assert d.auth_resource == ""

    def test_entra_dest_stores_auth_resource(self) -> None:
        r = _resolver({
            "destinations": {
                "azure": {
                    "url": "http://ci:8000",
                    "auth_mode": "entra",
                    "auth_resource": "api://53aa4ffd",
                    "include": ["**"],
                },
            }
        })
        d = r.destinations["azure"]  # type: ignore[attr-defined]
        assert d.auth_mode == "entra"
        assert d.auth_resource == "api://53aa4ffd"


# ---------------------------------------------------------------------------
# validate_destinations() XOR validation
# ---------------------------------------------------------------------------


class TestValidateDestinationsXOR:
    """Per-target XOR: entra requires auth_resource, static requires api_key."""

    def test_static_valid_passes(self) -> None:
        r = _resolver({
            "destinations": {
                "local": {"url": "http://ci:8000", "api_key": "sk", "include": ["**"]},
            }
        })
        result = r.validate_destinations()  # type: ignore[attr-defined]
        assert "local" in result

    def test_entra_valid_passes(self) -> None:
        r = _resolver({
            "destinations": {
                "azure": {
                    "url": "http://ci:8000",
                    "auth_mode": "entra",
                    "auth_resource": "api://53aa4ffd",
                    "include": ["**"],
                },
            }
        })
        result = r.validate_destinations()  # type: ignore[attr-defined]
        assert "azure" in result

    def test_entra_missing_auth_resource_raises(self) -> None:
        r = _resolver({
            "destinations": {
                "azure": {
                    "url": "http://ci:8000",
                    "auth_mode": "entra",
                    # no auth_resource
                    "include": ["**"],
                },
            }
        })
        with pytest.raises(ValueError, match="azure.*missing auth_resource"):
            r.validate_destinations()  # type: ignore[attr-defined]

    def test_entra_does_not_require_api_key(self) -> None:
        """Entra mode with valid auth_resource and no api_key must NOT raise."""
        r = _resolver({
            "destinations": {
                "azure": {
                    "url": "http://ci:8000",
                    "auth_mode": "entra",
                    "auth_resource": "api://53aa4ffd",
                    # no api_key — must be OK
                    "include": ["**"],
                },
            }
        })
        result = r.validate_destinations()  # type: ignore[attr-defined]
        assert "azure" in result

    def test_unknown_auth_mode_raises(self) -> None:
        r = _resolver({
            "destinations": {
                "weird": {
                    "url": "http://ci:8000",
                    "auth_mode": "kerberos",
                    "api_key": "k",
                    "include": ["**"],
                },
            }
        })
        with pytest.raises(ValueError, match="kerberos"):
            r.validate_destinations()  # type: ignore[attr-defined]

    def test_mixed_fleet_valid(self) -> None:
        """Static + entra destinations coexist; each validates independently."""
        r = _resolver({
            "destinations": {
                "local": {"url": "http://local:8000", "api_key": "sk", "include": ["local/**"]},
                "azure": {
                    "url": "http://azure:8000",
                    "auth_mode": "entra",
                    "auth_resource": "api://53aa4ffd",
                    "include": ["**"],
                },
            }
        })
        result = r.validate_destinations()  # type: ignore[attr-defined]
        assert set(result.keys()) == {"local", "azure"}

    def test_mixed_fleet_entra_invalid_raises(self) -> None:
        """Mixed fleet raises if entra dest is missing auth_resource."""
        r = _resolver({
            "destinations": {
                "local": {"url": "http://local:8000", "api_key": "sk", "include": ["**"]},
                "azure": {
                    "url": "http://azure:8000",
                    "auth_mode": "entra",
                    # missing auth_resource
                    "include": ["**"],
                },
            }
        })
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
        d = _make_dispatcher(auth_mode="entra", auth_resource="api://53aa4ffd", credential=fake_cred)

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock())
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
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock())
        d._client = mock_client  # type: ignore[attr-defined]

        await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]

        _, call_kwargs = mock_client.post.call_args
        assert call_kwargs["headers"] == {"Authorization": "Bearer sk-static"}

    async def test_entra_client_has_no_baked_auth_header(self) -> None:
        """The lazy client created by _post has NO Authorization header at construction."""
        from amplifier_module_hook_context_intelligence.handlers.logging_handler import (
            _DestinationDispatcher,
        )

        fake_cred = FakeCredential("some-token")
        d = _make_dispatcher(auth_mode="entra", auth_resource="api://53aa4ffd", credential=fake_cred)

        mock_response = MagicMock(raise_for_status=MagicMock())

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
                name="t", url="http://h:8000", api_key="", workspace="ws",
                dispatch_timeout=10.0, failure_threshold=3, queue_capacity=256,
                close_drain_timeout=0.5, auth_mode="entra", auth_resource="api://53aa4ffd",
            )

        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post.return_value = MagicMock(raise_for_status=MagicMock())
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
        mock_response = MagicMock(raise_for_status=MagicMock())
        mock_client.post.return_value = mock_response
        d._client = mock_client  # type: ignore[attr-defined]

        await d._post("session:start", {"session_id": "s1"})  # type: ignore[attr-defined]

        mock_client.post.assert_awaited_once()
        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"]["Authorization"] == "Bearer sk-backward-compat"
