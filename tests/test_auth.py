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

    def test_empty_key_raises(self) -> None:
        """ApiKeyAuth.headers() refuses to send an unusable (empty) key as a Bearer token.

        This is the point-of-use guard (defense in depth alongside the
        build_auth_strategy() builder guard): a redacted/unexpanded/empty
        api_key must never reach the wire as ``Bearer <value>``.
        """
        from context_intelligence.auth import ApiKeyAuth

        auth = ApiKeyAuth("")
        with pytest.raises(ValueError, match="unusable"):
            auth.headers()

    def test_redacted_sentinel_key_raises(self) -> None:
        """ApiKeyAuth.headers() refuses the '[REDACTED]' sentinel value.

        A resumed sub-session can mount its config from a persisted,
        redacted metadata.json snapshot (amplifier-core's redact_secrets()),
        landing the literal string "[REDACTED]" where a real key belongs.
        """
        from context_intelligence.auth import ApiKeyAuth

        auth = ApiKeyAuth("[REDACTED]")
        with pytest.raises(ValueError, match="unusable"):
            auth.headers()

    def test_unexpanded_placeholder_key_raises(self) -> None:
        """ApiKeyAuth.headers() refuses an unexpanded ${VAR} placeholder."""
        from context_intelligence.auth import ApiKeyAuth

        auth = ApiKeyAuth("${AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY}")
        with pytest.raises(ValueError, match="unusable"):
            auth.headers()

    def test_unusable_key_warns_once_not_per_call(self, caplog: pytest.LogCaptureFixture) -> None:
        """The unusable-key WARNING is logged once per instance, not once per call."""
        import logging

        from context_intelligence.auth import ApiKeyAuth

        auth = ApiKeyAuth("[REDACTED]")
        with caplog.at_level(logging.WARNING, logger="context_intelligence.auth"):
            for _ in range(3):
                with pytest.raises(ValueError):
                    auth.headers()

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1


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


# ---------------------------------------------------------------------------
# Non-interactive credential seam (DefaultAzureCredential)
# ---------------------------------------------------------------------------


class TestNonInteractiveCredentialSeam:
    """The lazy credential seam builds ``DefaultAzureCredential``.

    ``DefaultAzureCredential`` walks azure-identity's own chain (env-var service
    principal -> managed identity -> workload identity -> shared cache -> az CLI),
    so the same ``auth_mode='entra'`` works BOTH interactively for a developer
    (falls through to ``az login``) AND non-interactively for a hosted app like
    Resolve (managed identity / workload identity) with no code change. That is
    the whole point of app-to-app (M2M) auth: no human in the loop.
    """

    def _install_fake_azure_identity(
        self, monkeypatch: pytest.MonkeyPatch, cred_attr: str
    ) -> dict[str, Any]:
        """Install a fake ``azure.identity`` exposing only *cred_attr*.

        Returns a dict that records whether the fake credential was constructed.
        Because the fake module exposes ONLY *cred_attr*, importing any other
        credential name raises ImportError — which is exactly how we prove the
        seam imports the intended symbol.
        """
        import types

        constructed: dict[str, Any] = {"built": False}

        class _FakeCred:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                constructed["built"] = True

        fake_azure = types.ModuleType("azure")
        fake_identity = types.ModuleType("azure.identity")
        setattr(fake_identity, cred_attr, _FakeCred)
        fake_azure.identity = fake_identity  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "azure", fake_azure)
        monkeypatch.setitem(sys.modules, "azure.identity", fake_identity)
        constructed["cls"] = _FakeCred
        return constructed

    def test_seam_constructs_default_azure_credential(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_make_cli_credential() imports+builds DefaultAzureCredential (not AzureCliCredential)."""
        from context_intelligence.auth import _make_cli_credential

        constructed = self._install_fake_azure_identity(monkeypatch, "DefaultAzureCredential")
        cred = _make_cli_credential()
        assert isinstance(cred, constructed["cls"])
        assert constructed["built"] is True

    def test_seam_does_not_use_azure_cli_credential(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """If only AzureCliCredential is available, the seam FAILS.

        This locks in the switch away from the interactive-only credential: a
        fake azure.identity that exposes ONLY AzureCliCredential must make the
        seam raise ImportError, proving it no longer imports that symbol.
        """
        from context_intelligence.auth import _make_cli_credential

        self._install_fake_azure_identity(monkeypatch, "AzureCliCredential")
        with pytest.raises(ImportError):
            _make_cli_credential()
