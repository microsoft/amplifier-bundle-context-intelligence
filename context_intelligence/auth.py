"""Authentication strategies for context-intelligence HTTP clients.

Provides a small, composable auth layer:

    strategy = build_auth_strategy(auth_mode="static", api_key="sk-my-key")
    # or
    strategy = build_auth_strategy(auth_mode="entra", auth_resource="api://...")
    headers = strategy.headers()   # {"Authorization": "Bearer <token>"}

Design notes
------------
- ``AuthStrategy`` is a typing.Protocol — any object with ``headers() -> dict[str, str]``
  satisfies it without inheritance.
- ``build_auth_strategy`` imports ``AzureCliCredential`` LAZILY (inside the entra branch)
  so static-mode callers never need ``azure-identity`` installed.
- The bundle never caches tokens itself: the azure-identity SDK handles refresh internally.
"""

from __future__ import annotations

from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class AuthStrategy(Protocol):
    """Minimal auth strategy: produce HTTP headers for a single request batch."""

    def headers(self) -> dict[str, str]:
        """Return the HTTP headers dict to attach to client requests."""
        ...


# ---------------------------------------------------------------------------
# Implementations
# ---------------------------------------------------------------------------


class ApiKeyAuth:
    """Bearer-token auth backed by a static API key."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}


class EntraTokenAuth:
    """Bearer-token auth backed by an Entra ID (azure-identity) credential.

    The credential is constructed ONCE by the caller and reused across calls.
    Token refresh is handled transparently by the azure-identity SDK.
    """

    def __init__(self, credential: Any, resource: str) -> None:
        """
        Parameters
        ----------
        credential:
            Any azure-identity ``TokenCredential`` (e.g. ``AzureCliCredential``).
        resource:
            The Entra resource URI (e.g. ``api://<client_id>``).  The scope
            ``<resource>/.default`` is passed to ``get_token()``.
        """
        self._credential = credential
        self._resource = resource

    def headers(self) -> dict[str, str]:
        token = self._credential.get_token(f"{self._resource}/.default")
        return {"Authorization": f"Bearer {token.token}"}


# ---------------------------------------------------------------------------
# Lazy credential factory — isolated here for testability
# ---------------------------------------------------------------------------


def _make_cli_credential() -> Any:
    """Lazily import and instantiate ``AzureCliCredential``.

    Isolated in its own function so unit tests can patch
    ``context_intelligence.auth._make_cli_credential`` without requiring the
    ``azure-identity`` package at import time.
    """
    from azure.identity import AzureCliCredential  # noqa: PLC0415

    return AzureCliCredential()


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_auth_strategy(
    *,
    auth_mode: str,
    api_key: str = "",
    auth_resource: str = "",
    credential: Any = None,
) -> AuthStrategy:
    """Build and return the appropriate ``AuthStrategy`` for *auth_mode*.

    Parameters
    ----------
    auth_mode:
        ``"static"`` — use a pre-issued API key.
        ``"entra"``  — acquire a delegated token via ``az login`` (V1: AzureCliCredential).
    api_key:
        Required when ``auth_mode == "static"``.
    auth_resource:
        Required when ``auth_mode == "entra"``.  Typically ``api://<client_id>``.
    credential:
        Optional pre-built ``TokenCredential``.  When ``None`` and
        ``auth_mode == "entra"``, an ``AzureCliCredential`` is constructed via
        :func:`_make_cli_credential`.  Always inject a fake in tests.

    Raises
    ------
    ValueError
        On invalid *auth_mode*, missing *api_key* (static), or missing
        *auth_resource* (entra).  **No silent fallbacks.**
    """
    if auth_mode == "static":
        if not api_key.strip():
            raise ValueError(
                "auth_mode=static requires a non-empty api_key. "
                "Pass --api-key or set AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY."
            )
        return ApiKeyAuth(api_key)

    if auth_mode == "entra":
        if not auth_resource.strip():
            raise ValueError(
                "auth_mode=entra requires a non-empty auth_resource. "
                "Pass --auth-resource or set AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_RESOURCE."
            )
        if credential is None:
            credential = _make_cli_credential()
        return EntraTokenAuth(credential, auth_resource)

    raise ValueError(f"unknown auth_mode {auth_mode!r}. Valid values: 'static', 'entra'.")
