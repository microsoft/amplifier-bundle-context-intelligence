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
- CACHING: ``AzureCliCredential`` has NO in-process token cache — every ``get_token()``
  call shells out to ``az`` (~487–553 ms, measured).  This module caches the returned
  ``AccessToken`` until ``expires_on − _SAFETY_MARGIN_S`` so the ``az`` subprocess runs
  at most once per token lifetime (~67 min) rather than on every request.
- SINGLETON: A single ``AzureCliCredential`` instance is shared across all in-process
  sessions via ``_get_singleton_credential()``.  ``build_auth_strategy``/mount performs
  ~zero expensive work: no credential construction, no token acquisition.
- CONCURRENCY: the refresh path serialises via a ``threading.Lock``.  The hot path is
  lock-free (one dict read + one float compare + one f-string).  A ``threading.Lock``
  (not ``asyncio.Lock``) is used because a module-level ``asyncio.Lock`` binds to the
  event loop that created it; in-process subsessions running on different event loops
  would raise "attached to a different event loop".
- ``az account`` switch mid-process is NOT auto-detected per call (probing would defeat
  the cache and violate the no-expensive-retrieval rule).  Recovery: call ``reset()``
  and let the server's 403 on an unmapped OID serve as the loud signal.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Protocol


# ---------------------------------------------------------------------------
# Safety margin for token refresh (process-level constant)
# ---------------------------------------------------------------------------

try:
    _SAFETY_MARGIN_S: float = float(
        os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_TOKEN_REFRESH_MARGIN_S", "300")
    )
except ValueError:
    _SAFETY_MARGIN_S = 300.0

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


class _TokenCache:
    """In-process token cache keyed by scope string.

    Hot path (cache hit): one dict read + one float compare — lock-free.
    Refresh path (miss / near-expiry): ``threading.Lock`` + double-check + ``get_token()``.

    Threading note
    --------------
    ``threading.Lock`` is used (not ``asyncio.Lock``) because a module-level
    ``asyncio.Lock`` binds to the loop that created it; in-process subsessions that run
    on different loops raise "attached to a different event loop".  ``threading.Lock``
    is loop-agnostic and correct here.
    """

    __slots__ = ("_cache", "_lock")

    def __init__(self) -> None:
        self._cache: dict[str, tuple[str, float]] = {}  # {scope: (token, expires_on)}
        self._lock = threading.Lock()

    def get(self, scope: str) -> tuple[str, float] | None:
        """Return ``(token_str, expires_on)`` for *scope*, or ``None`` if absent."""
        return self._cache.get(scope)

    def store(self, scope: str, token: str, expires_on: float) -> None:
        """Store ``(token_str, expires_on)`` for *scope*."""
        self._cache[scope] = (token, expires_on)

    def clear(self) -> None:
        """Remove all cached entries."""
        self._cache.clear()


class EntraTokenAuth:
    """Bearer-token auth backed by an Entra ID (azure-identity) credential.

    CACHING RATIONALE
    -----------------
    ``AzureCliCredential`` has no in-process cache — every ``get_token()`` call shells
    out to ``az`` (~487–553 ms, measured).  This class caches the ``AccessToken`` until
    ``expires_on − _SAFETY_MARGIN_S`` so the ``az`` subprocess runs at most once per
    token lifetime (~67 min).

    HOT PATH (cache hit)
    --------------------
    One dict read + one float compare + one f-string — lock-free, no subprocess,
    no await.  Cost: microseconds.

    REFRESH PATH (miss or near-expiry)
    -----------------------------------
    Acquire ``threading.Lock``, double-check (another thread may have refreshed while
    waiting), call ``get_token()`` (blocking subprocess, ~once per token lifetime),
    store result, release.  Exceptions propagate — nothing is cached on failure so the
    next call retries.

    ``headers()`` stays sync; ``get_token()`` is a blocking subprocess that briefly
    blocks the caller (and an async event loop) ONLY during the rare refresh
    (~once per ~67 min token lifetime) — acceptable; no async machinery is added.

    AZ ACCOUNT SWITCH
    -----------------
    Mid-session ``az account`` switch is NOT auto-detected per call (probing would
    defeat the cache and violate the no-expensive-retrieval rule).  Recovery: call
    ``reset()`` and let the server's 403 on an unmapped OID serve as the loud signal.
    """

    def __init__(
        self,
        credential: Any,
        resource: str,
        *,
        _cache: _TokenCache | None = None,
    ) -> None:
        """
        Parameters
        ----------
        credential:
            Any azure-identity ``TokenCredential`` (e.g. ``AzureCliCredential``).
        resource:
            The Entra resource URI (e.g. ``api://<client_id>``).  The scope
            ``<resource>/.default`` is passed to ``get_token()``.
        _cache:
            ``_TokenCache`` instance.  When ``None`` (default), a fresh per-instance
            cache is created — this is the correct default for direct construction in
            tests, ensuring full isolation between test cases.  ``build_auth_strategy``
            wires the module-level singleton cache for production and a fresh cache for
            injected credentials (tests via the ``credential`` parameter).
        """
        self._credential = credential
        self._resource = resource
        self._cache: _TokenCache = _cache if _cache is not None else _TokenCache()

    def headers(self) -> dict[str, str]:
        scope = f"{self._resource}/.default"
        margin = _SAFETY_MARGIN_S  # module-level attribute read — fast, no subprocess

        # ------------------------------------------------------------------ #
        # HOT PATH — lock-free                                                #
        # One dict read + one float compare + one f-string = microseconds.   #
        # No lock, no subprocess, no await on this path.                     #
        # ------------------------------------------------------------------ #
        entry = self._cache.get(scope)
        if entry is not None:
            token_str, expires_on = entry
            if time.time() < expires_on - margin:
                return {"Authorization": f"Bearer {token_str}"}

        # ------------------------------------------------------------------ #
        # REFRESH PATH — serialise, double-check, fetch                      #
        # ------------------------------------------------------------------ #
        with self._cache._lock:
            # Double-check: another thread may have refreshed while we waited.
            entry = self._cache.get(scope)
            if entry is not None:
                token_str, expires_on = entry
                if time.time() < expires_on - margin:
                    return {"Authorization": f"Bearer {token_str}"}

            # get_token() is a blocking subprocess (~once per token lifetime).
            # Exceptions propagate — we never cache a failure.
            token = self._credential.get_token(scope)
            self._cache.store(scope, token.token, float(token.expires_on))
            return {"Authorization": f"Bearer {token.token}"}


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_singleton_credential: Any = None
_MODULE_CACHE: _TokenCache = _TokenCache()


def reset() -> None:
    """Clear the module-level token cache and drop the singleton credential.

    Call after ``az account`` switch mid-process so the next ``headers()`` call
    re-authenticates and picks up the new identity.

    Tradeoff: per-call identity probing would defeat the cache and violate the
    no-expensive-retrieval rule.  A mid-session ``az account`` switch is therefore
    NOT auto-detected per call.  Recovery: call ``reset()``, then let the server's
    403 on an unmapped OID serve as the loud signal.

    Only affects the module singleton.  ``EntraTokenAuth`` instances constructed with
    an injected credential (tests, or direct construction) use their own ``_TokenCache``
    and are NOT affected by ``reset()``.
    """
    global _singleton_credential
    _MODULE_CACHE.clear()
    _singleton_credential = None


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


def _get_singleton_credential() -> Any:
    """Return the process-level singleton ``AzureCliCredential``, creating it once.

    All in-process sessions and subsessions share this single instance so that
    ``build_auth_strategy()`` (the mount equivalent) performs ~zero work: no new
    credential construction, no token acquisition.
    """
    global _singleton_credential
    if _singleton_credential is None:
        _singleton_credential = _make_cli_credential()
    return _singleton_credential


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
        Optional pre-built ``TokenCredential``.

        - When ``None`` and ``auth_mode == "entra"``: the process-level singleton
          ``AzureCliCredential`` is used (one instance shared by all sessions; this
          call performs ~zero expensive work) and the module-level ``_MODULE_CACHE``
          is shared across all callers — this is the production path.
        - When non-``None`` (e.g. a fake in tests): the injected credential is used
          with a FRESH per-instance ``_TokenCache`` so tests are fully isolated and
          never share state with each other or with the module singleton.

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
            # Production path: singleton credential + shared module cache.
            # build_auth_strategy() / mount() performs ~zero expensive work here.
            return EntraTokenAuth(_get_singleton_credential(), auth_resource, _cache=_MODULE_CACHE)
        # Test/injection path: fresh per-instance cache → full isolation.
        return EntraTokenAuth(credential, auth_resource, _cache=_TokenCache())

    raise ValueError(f"unknown auth_mode {auth_mode!r}. Valid values: 'static', 'entra'.")
