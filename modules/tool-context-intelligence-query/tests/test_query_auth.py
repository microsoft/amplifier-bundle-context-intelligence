"""Tests for query-tool dual-auth (slice 2-D).

Covers:
- Source dataclass gains auth_mode / auth_resource fields
- ToolConfigResolver.sources parses and _expand()s auth fields
- ToolConfigResolver.validate_sources() XOR validation
- resolve_query_auth_strategy returns ApiKeyAuth for static, EntraTokenAuth for entra
- AsyncCIClient uses strategy.headers() per-request
- graph_query_tool and blob_read_tool pass auth_strategy to AsyncCIClient
- Per-target XOR (static source coexists with entra source)
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeToken:
    # expires_on far future so cached tokens are never considered stale in tests
    def __init__(self, token: str, expires_on: float = 9_999_999_999.0) -> None:
        self.token = token
        self.expires_on = expires_on


class FakeCredential:
    def __init__(self, token: str = "entra-token") -> None:
        self._token = token
        self.calls: list[tuple[Any, ...]] = []

    def get_token(self, *scopes: str, **kwargs: Any) -> FakeToken:
        self.calls.append(scopes)
        return FakeToken(self._token)


def _tool_resolver(config: dict) -> object:
    from context_intelligence.tool_resolver import ToolConfigResolver

    coord = MagicMock()
    coord.config = {}
    return ToolConfigResolver(config, coord)


# ---------------------------------------------------------------------------
# Source dataclass
# ---------------------------------------------------------------------------


class TestSourceDataclass:
    """Source has auth_mode / auth_resource fields."""

    def test_default_auth_mode_is_static(self) -> None:
        r = _tool_resolver({"sources": {"local": {"url": "http://ci:8000", "api_key": "sk"}}})
        src = r.sources["local"]  # type: ignore[attr-defined]
        assert src.auth_mode == "static"

    def test_default_auth_resource_is_empty(self) -> None:
        r = _tool_resolver({"sources": {"local": {"url": "http://ci:8000", "api_key": "sk"}}})
        src = r.sources["local"]  # type: ignore[attr-defined]
        assert src.auth_resource == ""

    def test_entra_source_stores_auth_resource(self) -> None:
        r = _tool_resolver({
            "sources": {
                "azure": {
                    "url": "http://ci:8000",
                    "auth_mode": "entra",
                    "auth_resource": "api://53aa4ffd",
                }
            }
        })
        src = r.sources["azure"]  # type: ignore[attr-defined]
        assert src.auth_mode == "entra"
        assert src.auth_resource == "api://53aa4ffd"


# ---------------------------------------------------------------------------
# _expand() applied to auth fields in sources
# ---------------------------------------------------------------------------


class TestSourceAuthFieldExpansion:
    """`_expand()` (i.e. _expand_env_placeholders) is applied to auth fields."""

    def test_auth_resource_placeholder_expanded(self) -> None:
        r = _tool_resolver({
            "sources": {
                "azure": {
                    "url": "http://ci:8000",
                    "auth_mode": "entra",
                    "auth_resource": "${MY_CI_RESOURCE_QTEST}",
                }
            }
        })
        with patch.dict(os.environ, {"MY_CI_RESOURCE_QTEST": "api://abc-123"}, clear=False):
            r._sources = None  # type: ignore[attr-defined]
            src = r.sources["azure"]  # type: ignore[attr-defined]
        assert src.auth_resource == "api://abc-123"

    def test_auth_resource_with_default_unset_uses_default(self) -> None:
        r = _tool_resolver({
            "sources": {
                "azure": {
                    "url": "http://ci:8000",
                    "auth_mode": "entra",
                    "auth_resource": "${_UNSET_QTEST:api://fallback}",
                }
            }
        })
        env = {k: v for k, v in os.environ.items() if k != "_UNSET_QTEST"}
        with patch.dict(os.environ, env, clear=True):
            r._sources = None  # type: ignore[attr-defined]
            src = r.sources["azure"]  # type: ignore[attr-defined]
        assert src.auth_resource == "api://fallback"


# ---------------------------------------------------------------------------
# validate_sources() XOR validation
# ---------------------------------------------------------------------------


class TestValidateSourcesXOR:
    """Per-source XOR: entra requires auth_resource, static requires api_key."""

    def test_static_valid_passes(self) -> None:
        r = _tool_resolver({"sources": {"local": {"url": "http://ci:8000", "api_key": "sk"}}})
        result = r.validate_sources()  # type: ignore[attr-defined]
        assert "local" in result

    def test_entra_valid_passes(self) -> None:
        r = _tool_resolver({
            "sources": {
                "azure": {
                    "url": "http://ci:8000",
                    "auth_mode": "entra",
                    "auth_resource": "api://53aa4ffd",
                }
            }
        })
        result = r.validate_sources()  # type: ignore[attr-defined]
        assert "azure" in result

    def test_entra_missing_auth_resource_raises(self) -> None:
        r = _tool_resolver({
            "sources": {
                "azure": {"url": "http://ci:8000", "auth_mode": "entra"},
            }
        })
        with pytest.raises(ValueError, match="azure.*missing auth_resource"):
            r.validate_sources()  # type: ignore[attr-defined]

    def test_entra_does_not_require_api_key(self) -> None:
        r = _tool_resolver({
            "sources": {
                "azure": {
                    "url": "http://ci:8000",
                    "auth_mode": "entra",
                    "auth_resource": "api://53aa4ffd",
                    # no api_key — must be OK
                }
            }
        })
        result = r.validate_sources()  # type: ignore[attr-defined]
        assert "azure" in result

    def test_unknown_auth_mode_raises(self) -> None:
        r = _tool_resolver({
            "sources": {
                "weird": {"url": "http://ci:8000", "auth_mode": "kerberos", "api_key": "k"},
            }
        })
        with pytest.raises(ValueError, match="kerberos"):
            r.validate_sources()  # type: ignore[attr-defined]

    def test_empty_sources_passes_with_no_error(self) -> None:
        """Empty sources (no explicit read-config) is valid — fallback to hook/env."""
        r = _tool_resolver({})
        result = r.validate_sources()  # type: ignore[attr-defined]
        assert result == {}

    def test_mixed_sources_validate_independently(self) -> None:
        r = _tool_resolver({
            "sources": {
                "local": {"url": "http://local:8000", "api_key": "sk"},
                "azure": {
                    "url": "http://azure:8000",
                    "auth_mode": "entra",
                    "auth_resource": "api://53aa4ffd",
                },
            }
        })
        result = r.validate_sources()  # type: ignore[attr-defined]
        assert set(result.keys()) == {"local", "azure"}


# ---------------------------------------------------------------------------
# resolve_query_auth_strategy
# ---------------------------------------------------------------------------


class TestResolveQueryAuthStrategy:
    """resolve_query_auth_strategy returns the right strategy."""

    def test_static_source_returns_api_key_auth(self) -> None:
        from context_intelligence.auth import ApiKeyAuth
        from context_intelligence.tool_resolver import resolve_query_auth_strategy

        r = _tool_resolver({"sources": {"local": {"url": "http://ci:8000", "api_key": "sk"}}})
        strategy = resolve_query_auth_strategy(None, r, api_key="my-key")  # type: ignore[arg-type]
        assert isinstance(strategy, ApiKeyAuth)
        assert strategy.headers() == {"Authorization": "Bearer my-key"}

    def test_entra_source_returns_entra_token_auth(self) -> None:
        from context_intelligence.auth import EntraTokenAuth
        from context_intelligence.tool_resolver import resolve_query_auth_strategy

        fake_cred = FakeCredential("entra-query-token")
        r = _tool_resolver({
            "sources": {
                "azure": {
                    "url": "http://ci:8000",
                    "auth_mode": "entra",
                    "auth_resource": "api://53aa4ffd",
                }
            }
        })
        with patch("context_intelligence.auth._make_cli_credential", return_value=fake_cred):
            strategy = resolve_query_auth_strategy(None, r, api_key="")  # type: ignore[arg-type]

        assert isinstance(strategy, EntraTokenAuth)
        headers = strategy.headers()
        assert headers == {"Authorization": "Bearer entra-query-token"}
        assert fake_cred.calls[0] == ("api://53aa4ffd/.default",)

    def test_no_source_falls_back_to_api_key_auth(self) -> None:
        """When no sources configured, returns ApiKeyAuth from the api_key param."""
        from context_intelligence.auth import ApiKeyAuth
        from context_intelligence.tool_resolver import resolve_query_auth_strategy

        r = _tool_resolver({})  # no sources
        strategy = resolve_query_auth_strategy(None, r, api_key="fallback-key")  # type: ignore[arg-type]
        assert isinstance(strategy, ApiKeyAuth)
        assert strategy.headers() == {"Authorization": "Bearer fallback-key"}

    def test_hook_dest_entra_used_when_no_source(self) -> None:
        """When tool has no sources, falls back to hook destination auth_mode."""
        from context_intelligence.auth import EntraTokenAuth
        from context_intelligence.tool_resolver import resolve_query_auth_strategy

        # Simulate a hook resolver with an entra destination
        fake_cred = FakeCredential("hook-entra-token")
        mock_dest = MagicMock()
        mock_dest.auth_mode = "entra"
        mock_dest.auth_resource = "api://hook-resource"
        mock_dest.url = "http://hook:8000"
        mock_dest.api_key = ""

        mock_hook = MagicMock()
        mock_hook.destinations = {"azure": mock_dest}

        r = _tool_resolver({})  # no sources
        with patch("context_intelligence.auth._make_cli_credential", return_value=fake_cred):
            strategy = resolve_query_auth_strategy(mock_hook, r, api_key="")  # type: ignore[arg-type]

        assert isinstance(strategy, EntraTokenAuth)
        headers = strategy.headers()
        assert headers["Authorization"].startswith("Bearer hook-entra-token")


# ---------------------------------------------------------------------------
# AsyncCIClient uses strategy per-request
# ---------------------------------------------------------------------------


class TestAsyncCIClientAuthStrategy:
    """AsyncCIClient.cypher / fetch_blob / list_blob_keys use strategy.headers() per-call."""

    async def test_entra_strategy_headers_per_cypher_call(self) -> None:
        from context_intelligence.auth import EntraTokenAuth
        from context_intelligence.client import AsyncCIClient

        fake_cred = FakeCredential("cypher-token")
        strategy = EntraTokenAuth(fake_cred, "api://53aa4ffd")

        client = AsyncCIClient(server_url="http://ci:8000", auth_strategy=strategy)

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = [{"n": 1}]

        with patch("httpx.AsyncClient") as mock_cls:
            mock_httpx = AsyncMock()
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.post.return_value = mock_response
            mock_cls.return_value = mock_httpx

            await client.cypher("MATCH (n) RETURN n")

        _, call_kwargs = mock_httpx.post.call_args
        assert call_kwargs["headers"] == {"Authorization": "Bearer cypher-token"}

    async def test_static_strategy_backward_compat(self) -> None:
        """AsyncCIClient(api_key=...) without auth_strategy still works."""
        from context_intelligence.client import AsyncCIClient

        client = AsyncCIClient(server_url="http://ci:8000", api_key="static-key")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = []

        with patch("httpx.AsyncClient") as mock_cls:
            mock_httpx = AsyncMock()
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.post.return_value = mock_response
            mock_cls.return_value = mock_httpx

            await client.cypher("MATCH (n) RETURN n")

        _, call_kwargs = mock_httpx.post.call_args
        assert call_kwargs["headers"] == {"Authorization": "Bearer static-key"}
