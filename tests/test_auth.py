"""Tests for context_intelligence/auth.py — TDD RED phase.

Covers:
- ApiKeyAuth.headers() shape
- EntraTokenAuth.headers() with a fake credential
- build_auth_strategy guards: entra+no resource, static+no key, unknown mode
- Static mode does NOT import azure.identity
"""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# ApiKeyAuth
# ---------------------------------------------------------------------------


class TestApiKeyAuth:
    """ApiKeyAuth produces the correct Authorization header."""

    def test_headers_shape(self) -> None:
        """headers() returns exactly {'Authorization': 'Bearer <key>'}."""
        from context_intelligence.auth import ApiKeyAuth

        auth = ApiKeyAuth("sk-test-key")
        result = auth.headers()
        assert result == {"Authorization": "Bearer sk-test-key"}

    def test_headers_is_dict_str_str(self) -> None:
        """headers() returns a dict[str, str]."""
        from context_intelligence.auth import ApiKeyAuth

        auth = ApiKeyAuth("any-key")
        result = auth.headers()
        assert isinstance(result, dict)
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in result.items())

    def test_empty_key_still_works(self) -> None:
        """ApiKeyAuth with empty string returns Bearer with empty token (guard is in builder)."""
        from context_intelligence.auth import ApiKeyAuth

        auth = ApiKeyAuth("")
        result = auth.headers()
        assert result == {"Authorization": "Bearer "}


# ---------------------------------------------------------------------------
# EntraTokenAuth
# ---------------------------------------------------------------------------


class FakeToken:
    """Minimal fake of azure.core.credentials.AccessToken."""

    # expires_on far in the future so cached tokens are never considered stale
    def __init__(self, token: str, expires_on: float = 9_999_999_999.0) -> None:
        self.token = token
        self.expires_on = expires_on


class FakeCredential:
    """Fake azure-identity TokenCredential that records calls."""

    def __init__(self, token: str = "faketoken") -> None:
        self._token = token
        self.calls: list[tuple[Any, ...]] = []

    def get_token(self, *scopes: str, **kwargs: Any) -> FakeToken:
        self.calls.append(scopes)
        return FakeToken(self._token)


class TestEntraTokenAuth:
    """EntraTokenAuth calls get_token with the right scope and puts token in header."""

    def test_get_token_called_with_default_scope(self) -> None:
        """get_token called with '<resource>/.default'."""
        from context_intelligence.auth import EntraTokenAuth

        cred = FakeCredential("mytoken")
        auth = EntraTokenAuth(cred, "api://my-app-id")
        auth.headers()
        assert len(cred.calls) == 1
        assert cred.calls[0] == ("api://my-app-id/.default",)

    def test_headers_authorization_value(self) -> None:
        """headers() returns {'Authorization': 'Bearer <token>'}."""
        from context_intelligence.auth import EntraTokenAuth

        cred = FakeCredential("faketoken")
        auth = EntraTokenAuth(cred, "api://my-app-id")
        result = auth.headers()
        assert result == {"Authorization": "Bearer faketoken"}

    def test_credential_stored_at_construction(self) -> None:
        """The credential passed at construction is the one used — not re-instantiated."""
        from context_intelligence.auth import EntraTokenAuth

        cred1 = FakeCredential("token-one")
        cred2 = FakeCredential("token-two")

        auth = EntraTokenAuth(cred1, "api://app")
        auth.headers()  # uses cred1

        _ = EntraTokenAuth(cred2, "api://app")  # cred2 untouched

        assert len(cred1.calls) == 1
        assert len(cred2.calls) == 0

    def test_different_resources_produce_different_scopes(self) -> None:
        """Each EntraTokenAuth passes its own resource to get_token."""
        from context_intelligence.auth import EntraTokenAuth

        cred_a = FakeCredential("tok-a")
        cred_b = FakeCredential("tok-b")
        auth_a = EntraTokenAuth(cred_a, "api://resource-a")
        auth_b = EntraTokenAuth(cred_b, "api://resource-b")
        auth_a.headers()
        auth_b.headers()
        assert cred_a.calls[0] == ("api://resource-a/.default",)
        assert cred_b.calls[0] == ("api://resource-b/.default",)


# ---------------------------------------------------------------------------
# build_auth_strategy — guards
# ---------------------------------------------------------------------------


class TestBuildAuthStrategyGuards:
    """build_auth_strategy raises ValueError on bad inputs; never silently falls back."""

    def test_unknown_mode_raises(self) -> None:
        """Unknown auth_mode raises ValueError with mode name in message."""
        from context_intelligence.auth import build_auth_strategy

        with pytest.raises(ValueError, match="unknown auth_mode"):
            build_auth_strategy(auth_mode="magic")

    def test_static_no_key_raises(self) -> None:
        """static mode without api_key raises ValueError."""
        from context_intelligence.auth import build_auth_strategy

        with pytest.raises(ValueError, match="api_key"):
            build_auth_strategy(auth_mode="static", api_key="")

    def test_static_whitespace_key_raises(self) -> None:
        """static mode with whitespace-only api_key raises ValueError."""
        from context_intelligence.auth import build_auth_strategy

        with pytest.raises(ValueError, match="api_key"):
            build_auth_strategy(auth_mode="static", api_key="   ")

    def test_entra_no_resource_raises(self) -> None:
        """entra mode without auth_resource raises ValueError."""
        from context_intelligence.auth import build_auth_strategy

        fake_cred = MagicMock()
        with pytest.raises(ValueError, match="auth_resource"):
            build_auth_strategy(auth_mode="entra", auth_resource="", credential=fake_cred)

    def test_entra_whitespace_resource_raises(self) -> None:
        """entra mode with whitespace-only auth_resource raises ValueError."""
        from context_intelligence.auth import build_auth_strategy

        fake_cred = MagicMock()
        with pytest.raises(ValueError, match="auth_resource"):
            build_auth_strategy(auth_mode="entra", auth_resource="   ", credential=fake_cred)


# ---------------------------------------------------------------------------
# build_auth_strategy — happy paths
# ---------------------------------------------------------------------------


class TestBuildAuthStrategyHappy:
    """build_auth_strategy returns the right strategy type on valid inputs."""

    def test_static_returns_api_key_auth(self) -> None:
        """static mode returns ApiKeyAuth."""
        from context_intelligence.auth import ApiKeyAuth, build_auth_strategy

        strategy = build_auth_strategy(auth_mode="static", api_key="sk-valid")
        assert isinstance(strategy, ApiKeyAuth)

    def test_static_strategy_produces_correct_header(self) -> None:
        """Static strategy's header contains the key."""
        from context_intelligence.auth import build_auth_strategy

        strategy = build_auth_strategy(auth_mode="static", api_key="sk-valid")
        assert strategy.headers() == {"Authorization": "Bearer sk-valid"}

    def test_entra_with_injected_credential(self) -> None:
        """entra mode with injected credential returns EntraTokenAuth."""
        from context_intelligence.auth import EntraTokenAuth, build_auth_strategy

        fake_cred = FakeCredential("injected-token")
        strategy = build_auth_strategy(
            auth_mode="entra",
            auth_resource="api://53aa4ffd",
            credential=fake_cred,
        )
        assert isinstance(strategy, EntraTokenAuth)
        result = strategy.headers()
        assert result == {"Authorization": "Bearer injected-token"}
        assert fake_cred.calls[0] == ("api://53aa4ffd/.default",)


# ---------------------------------------------------------------------------
# Static mode MUST NOT import azure.identity
# ---------------------------------------------------------------------------


class TestStaticModeNoAzureIdentityImport:
    """Static mode must work even when azure.identity is not installed."""

    def test_static_build_succeeds_without_azure_identity(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """build_auth_strategy(auth_mode='static') succeeds even if azure.identity fails to import."""
        # Simulate azure.identity being unavailable by blocking the import
        monkeypatch.setitem(sys.modules, "azure", None)  # type: ignore[arg-type]
        monkeypatch.setitem(sys.modules, "azure.identity", None)  # type: ignore[arg-type]

        # Reload auth to pick up the monkeypatched sys.modules state
        import importlib

        import context_intelligence.auth as auth_mod

        importlib.reload(auth_mod)

        # Static build must NOT touch azure.identity
        strategy = auth_mod.build_auth_strategy(auth_mode="static", api_key="sk-key")
        assert strategy.headers() == {"Authorization": "Bearer sk-key"}

        # Restore (reload back)
        monkeypatch.delitem(sys.modules, "azure", raising=False)
        monkeypatch.delitem(sys.modules, "azure.identity", raising=False)
        importlib.reload(auth_mod)
