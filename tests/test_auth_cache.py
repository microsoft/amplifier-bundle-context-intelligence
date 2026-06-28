"""Tests for the Entra token cache in auth.py — TDD RED → GREEN.

TB's five edges + supporting tests:
1. EXPIRY BOUNDARY       — strict < comparison; at-boundary → refresh, inside → serve cached.
2. CLOCK-SKEW / MARGIN   — safety margin prevents near-expiry serving; env override changes behavior.
3. CONCURRENCY           — double-checked lock; threading.Lock works across asyncio event loops.
4. EXCEPTION NOT CACHED  — get_token failure propagates; nothing stored; next call retries.
5. reset()               — clears module cache; does not break injected-credential isolation.

Plus:
- Repeated cached calls: get_token called exactly once.
- Scope keying: different resources → independent cache entries.
- build_auth_strategy wiring: injected credential → fresh cache; production → module singleton.
- ApiKeyAuth path: unchanged.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — rich FakeCredential/FakeToken for cache tests
# ---------------------------------------------------------------------------

_FAR_FUTURE = 9_999_999_999.0  # far future Unix timestamp


class FakeToken:
    """Minimal AccessToken substitute with both .token and .expires_on."""

    def __init__(self, token: str, expires_on: float = _FAR_FUTURE) -> None:
        self.token = token
        self.expires_on = expires_on


class FakeCredential:
    """Controllable fake TokenCredential for cache tests.

    Parameters
    ----------
    token:
        Token string returned by get_token().
    expires_on:
        expires_on epoch for returned token.
    delay:
        Seconds to sleep inside get_token() — simulates slow az subprocess.
    """

    def __init__(
        self,
        token: str = "faketoken",
        expires_on: float = _FAR_FUTURE,
        delay: float = 0.0,
    ) -> None:
        self._token = token
        self._expires_on = expires_on
        self._delay = delay
        self.calls: list[tuple[str, ...]] = []
        self._exc: Exception | None = None

    def fail_once(self, exc: Exception) -> None:
        """Configure the NEXT get_token() call to raise exc (only once)."""
        self._exc = exc

    def get_token(self, *scopes: str, **kwargs: Any) -> FakeToken:
        self.calls.append(scopes)
        if self._delay:
            time.sleep(self._delay)
        if self._exc is not None:
            exc, self._exc = self._exc, None
            raise exc
        return FakeToken(self._token, self._expires_on)


@pytest.fixture(autouse=True)
def _reset_module_singleton() -> Any:
    """Isolate each test: clear the auth module singleton and its cache before/after."""
    from context_intelligence import auth as _auth_mod

    _auth_mod.reset()
    yield
    _auth_mod.reset()


# ---------------------------------------------------------------------------
# Test 1: EXPIRY BOUNDARY
# ---------------------------------------------------------------------------


class TestExpiryBoundary:
    """The comparison used is ``time.time() < expires_on - margin`` (strict <).

    - INSIDE the valid window (time < expires_on - margin) → serve cached.
    - EXACTLY at the boundary (time == expires_on - margin) → False → refresh.
    - OUTSIDE the window → also refresh.

    This documents the chosen semantics: on the exact boundary the token is
    considered stale and refreshed, which is the conservative safe choice.
    """

    def test_strictly_before_boundary_serves_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """time.time() < expires_on - margin → cached token served (no get_token)."""
        import time as _time_mod

        import context_intelligence.auth as _auth

        fixed_now = 1_000_000.0
        monkeypatch.setattr(_time_mod, "time", lambda: fixed_now)
        monkeypatch.setattr(_auth, "_SAFETY_MARGIN_S", 300.0)

        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        # expires_on - margin = fixed_now + 301 - 300 = fixed_now + 1
        # fixed_now < fixed_now + 1 → True → serve cached
        expires_on = fixed_now + 301.0
        cache = _TokenCache()
        cache.store("api://app/.default", "cached-tok", expires_on)
        cred = FakeCredential("new-tok")

        auth = EntraTokenAuth(cred, "api://app", _cache=cache)
        result = auth.headers()

        assert result == {"Authorization": "Bearer cached-tok"}
        assert len(cred.calls) == 0  # no get_token invocation

    def test_exactly_at_boundary_refreshes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """time.time() == expires_on - margin: strict < is False → refresh.

        Documents that the chosen comparison is strict (<) not (<=), so the
        boundary itself is treated as stale.
        """
        import time as _time_mod

        import context_intelligence.auth as _auth

        fixed_now = 1_000_000.0
        monkeypatch.setattr(_time_mod, "time", lambda: fixed_now)
        monkeypatch.setattr(_auth, "_SAFETY_MARGIN_S", 300.0)

        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        # expires_on - margin = fixed_now + 300 - 300 = fixed_now
        # fixed_now < fixed_now → False → refresh
        expires_on = fixed_now + 300.0
        cache = _TokenCache()
        cache.store("api://app/.default", "old-tok", expires_on)
        cred = FakeCredential("fresh-tok")

        auth = EntraTokenAuth(cred, "api://app", _cache=cache)
        result = auth.headers()

        assert result == {"Authorization": "Bearer fresh-tok"}
        assert len(cred.calls) == 1  # refreshed

    def test_outside_window_refreshes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """time.time() > expires_on - margin → also refresh."""
        import time as _time_mod

        import context_intelligence.auth as _auth

        fixed_now = 1_000_000.0
        monkeypatch.setattr(_time_mod, "time", lambda: fixed_now)
        monkeypatch.setattr(_auth, "_SAFETY_MARGIN_S", 300.0)

        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        # expires_on - margin = fixed_now + 100 - 300 = fixed_now - 200
        # fixed_now < fixed_now - 200 → False → refresh
        expires_on = fixed_now + 100.0
        cache = _TokenCache()
        cache.store("api://app/.default", "expired-tok", expires_on)
        cred = FakeCredential("refreshed-tok")

        auth = EntraTokenAuth(cred, "api://app", _cache=cache)
        result = auth.headers()

        assert result == {"Authorization": "Bearer refreshed-tok"}
        assert len(cred.calls) == 1


# ---------------------------------------------------------------------------
# Test 2: CLOCK-SKEW / MARGIN
# ---------------------------------------------------------------------------


class TestSafetyMargin:
    """``_SAFETY_MARGIN_S`` is the sole guard against serving near-expired tokens.

    Default is 300 s.  It can be overridden via the
    ``AMPLIFIER_CONTEXT_INTELLIGENCE_TOKEN_REFRESH_MARGIN_S`` env variable
    (read at module import time) or by directly setting ``_SAFETY_MARGIN_S``
    on the module (for tests).
    """

    def test_near_expiry_token_is_refreshed_with_default_margin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Token expiring 50 s from now with margin=300 s → stale → refresh."""
        import time as _time_mod

        import context_intelligence.auth as _auth

        fixed_now = 1_000_000.0
        monkeypatch.setattr(_time_mod, "time", lambda: fixed_now)
        monkeypatch.setattr(_auth, "_SAFETY_MARGIN_S", 300.0)

        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        expires_on = fixed_now + 50.0  # only 50 s ahead; margin = 300 s → stale
        cache = _TokenCache()
        cache.store("api://app/.default", "stale-tok", expires_on)
        cred = FakeCredential("fresh-tok")

        auth = EntraTokenAuth(cred, "api://app", _cache=cache)
        result = auth.headers()

        assert result == {"Authorization": "Bearer fresh-tok"}
        assert len(cred.calls) == 1  # refreshed

    def test_reduced_margin_makes_same_token_valid(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """With margin reduced to 10 s, a token 50 s from now is NOT stale → serve cached."""
        import time as _time_mod

        import context_intelligence.auth as _auth

        fixed_now = 1_000_000.0
        monkeypatch.setattr(_time_mod, "time", lambda: fixed_now)
        # Override margin to 10 s (simulating env AMPLIFIER_CI_TOKEN_REFRESH_MARGIN_S=10)
        monkeypatch.setattr(_auth, "_SAFETY_MARGIN_S", 10.0)

        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        # expires_on - margin = fixed_now + 50 - 10 = fixed_now + 40
        # fixed_now < fixed_now + 40 → True → serve cached
        expires_on = fixed_now + 50.0
        cache = _TokenCache()
        cache.store("api://app/.default", "cached-tok", expires_on)
        cred = FakeCredential("new-tok")

        auth = EntraTokenAuth(cred, "api://app", _cache=cache)
        result = auth.headers()

        assert result == {"Authorization": "Bearer cached-tok"}
        assert len(cred.calls) == 0  # NOT refreshed — margin is smaller


# ---------------------------------------------------------------------------
# Test 3: CONCURRENCY
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_cold_cache_n_threads_calls_get_token_exactly_once(self) -> None:
        """N threads hit a cold cache simultaneously; double-checked lock → one get_token call."""
        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        # Slow credential (50 ms): first thread that gets the lock blocks others
        cred = FakeCredential("tok", expires_on=time.time() + 7200, delay=0.05)
        cache = _TokenCache()
        auth = EntraTokenAuth(cred, "api://app", _cache=cache)

        results: list[dict[str, str]] = []
        errors: list[BaseException] = []
        n = 10
        barrier = threading.Barrier(n)

        def call_headers() -> None:
            try:
                barrier.wait()  # all threads start simultaneously
                results.append(auth.headers())
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=call_headers) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors, f"Thread errors: {errors}"
        assert len(results) == n
        assert all(r == {"Authorization": "Bearer tok"} for r in results)
        assert len(cred.calls) == 1  # exactly once — double-checked lock serialised correctly

    def test_threading_lock_works_across_asyncio_event_loops(self) -> None:
        """threading.Lock does not bind to an asyncio event loop.

        A module-level asyncio.Lock binds to the loop that created it and raises
        'attached to a different event loop' when used from a second loop.
        threading.Lock has no such constraint.
        """
        import asyncio

        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        cache = _TokenCache()
        cred = FakeCredential("tok", expires_on=time.time() + 7200)
        auth = EntraTokenAuth(cred, "api://app", _cache=cache)

        async def call_headers() -> dict[str, str]:
            return auth.headers()

        # asyncio.run() always creates a NEW event loop; two calls → two different loops.
        # threading.Lock must survive this without raising.
        result1 = asyncio.run(call_headers())
        result2 = asyncio.run(call_headers())

        assert result1 == {"Authorization": "Bearer tok"}
        assert result2 == {"Authorization": "Bearer tok"}  # served from cache on loop 2
        assert len(cred.calls) == 1  # get_token only called from loop 1


# ---------------------------------------------------------------------------
# Test 4: EXCEPTION NOT CACHED
# ---------------------------------------------------------------------------


class TestExceptionNotCached:
    def test_get_token_exception_propagates(self) -> None:
        """When get_token raises, the exception propagates to the caller."""
        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        cred = FakeCredential()
        cred.fail_once(RuntimeError("az: ERROR: Please run 'az login' to authenticate"))

        cache = _TokenCache()
        auth = EntraTokenAuth(cred, "api://app", _cache=cache)

        with pytest.raises(RuntimeError, match="az login"):
            auth.headers()

    def test_failed_get_token_leaves_cache_empty(self) -> None:
        """After get_token raises, nothing is stored in the cache."""
        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        cred = FakeCredential()
        cred.fail_once(RuntimeError("transient"))

        cache = _TokenCache()
        auth = EntraTokenAuth(cred, "api://app", _cache=cache)

        with pytest.raises(RuntimeError):
            auth.headers()

        assert cache.get("api://app/.default") is None

    def test_next_call_after_failure_retries_and_succeeds(self) -> None:
        """After a failure, the next headers() call retries (no stale cached exception)."""
        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        cred = FakeCredential("good-tok")
        cred.fail_once(RuntimeError("transient failure"))

        cache = _TokenCache()
        auth = EntraTokenAuth(cred, "api://app", _cache=cache)

        with pytest.raises(RuntimeError):
            auth.headers()

        result = auth.headers()  # second call: cred now succeeds
        assert result == {"Authorization": "Bearer good-tok"}
        assert len(cred.calls) == 2  # tried twice


# ---------------------------------------------------------------------------
# Test 5: reset()
# ---------------------------------------------------------------------------


class TestReset:
    def test_reset_clears_module_singleton_cache(self) -> None:
        """reset() empties _MODULE_CACHE; next call using it re-fetches the token."""
        from context_intelligence.auth import EntraTokenAuth, _MODULE_CACHE, reset

        future = time.time() + 7200
        _MODULE_CACHE.store("api://app/.default", "cached-tok", future)

        cred = FakeCredential("fresh-tok")
        auth = EntraTokenAuth(cred, "api://app", _cache=_MODULE_CACHE)

        # First call: served from pre-populated cache
        result1 = auth.headers()
        assert result1 == {"Authorization": "Bearer cached-tok"}
        assert len(cred.calls) == 0

        reset()  # clears _MODULE_CACHE

        # Second call: cache miss → re-fetches
        result2 = auth.headers()
        assert result2 == {"Authorization": "Bearer fresh-tok"}
        assert len(cred.calls) == 1

    def test_reset_does_not_affect_injected_cache(self) -> None:
        """reset() only clears the module singleton; injected-cache instances keep their state."""
        from context_intelligence.auth import EntraTokenAuth, _TokenCache, reset

        cred = FakeCredential("tok")
        own_cache = _TokenCache()
        auth = EntraTokenAuth(cred, "api://app", _cache=own_cache)

        auth.headers()  # populates own_cache
        assert len(cred.calls) == 1

        reset()  # clears _MODULE_CACHE only

        auth.headers()  # hits own_cache, NOT re-fetched
        assert len(cred.calls) == 1  # still 1 — own_cache intact

    def test_reset_sets_singleton_credential_to_none(self) -> None:
        """reset() sets _singleton_credential back to None."""
        import context_intelligence.auth as _auth_mod

        _auth_mod._singleton_credential = object()  # pretend it's set
        _auth_mod.reset()
        assert _auth_mod._singleton_credential is None


# ---------------------------------------------------------------------------
# Additional: cache-hit count, scope keying, wiring
# ---------------------------------------------------------------------------


class TestCacheHit:
    def test_repeated_headers_calls_invoke_get_token_once(self) -> None:
        """After the first cache miss, many headers() calls keep get_token call-count at 1."""
        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        cred = FakeCredential("tok")
        auth = EntraTokenAuth(cred, "api://app", _cache=_TokenCache())

        for _ in range(50):
            result = auth.headers()
            assert result == {"Authorization": "Bearer tok"}

        assert len(cred.calls) == 1  # get_token called exactly once

    def test_different_resources_use_independent_cache_entries(self) -> None:
        """Two EntraTokenAuth instances with different resources do NOT share cached tokens."""
        from context_intelligence.auth import EntraTokenAuth, _TokenCache

        shared_cache = _TokenCache()
        cred_a = FakeCredential("tok-a")
        cred_b = FakeCredential("tok-b")
        auth_a = EntraTokenAuth(cred_a, "api://resource-a", _cache=shared_cache)
        auth_b = EntraTokenAuth(cred_b, "api://resource-b", _cache=shared_cache)

        # First calls: both are cache misses
        assert auth_a.headers() == {"Authorization": "Bearer tok-a"}
        assert auth_b.headers() == {"Authorization": "Bearer tok-b"}
        assert len(cred_a.calls) == 1
        assert len(cred_b.calls) == 1

        # Second calls: both served from cache
        assert auth_a.headers() == {"Authorization": "Bearer tok-a"}
        assert auth_b.headers() == {"Authorization": "Bearer tok-b"}
        assert len(cred_a.calls) == 1
        assert len(cred_b.calls) == 1


class TestBuildAuthStrategyWiring:
    """build_auth_strategy wires injected credential → fresh cache; production → module cache."""

    def test_injected_credential_gets_fresh_cache_not_module_singleton(self) -> None:
        """build_auth_strategy(credential=fake) must NOT share _MODULE_CACHE."""
        from context_intelligence.auth import EntraTokenAuth, _MODULE_CACHE, build_auth_strategy

        cred = FakeCredential("tok")
        strategy = build_auth_strategy(
            auth_mode="entra", auth_resource="api://app", credential=cred
        )
        assert isinstance(strategy, EntraTokenAuth)
        assert strategy._cache is not _MODULE_CACHE  # type: ignore[union-attr]

    def test_production_path_credential_none_uses_module_cache(self) -> None:
        """build_auth_strategy(credential=None) wires to _MODULE_CACHE."""
        from context_intelligence.auth import EntraTokenAuth, _MODULE_CACHE, build_auth_strategy

        fake_cred = FakeCredential("tok")
        with patch("context_intelligence.auth._get_singleton_credential", return_value=fake_cred):
            strategy = build_auth_strategy(auth_mode="entra", auth_resource="api://app")

        assert isinstance(strategy, EntraTokenAuth)
        assert strategy._cache is _MODULE_CACHE  # type: ignore[union-attr]


class TestApiKeyAuthUnchanged:
    """ApiKeyAuth is a pure dict — no cache, no threading."""

    def test_api_key_auth_unaffected_by_cache_changes(self) -> None:
        from context_intelligence.auth import ApiKeyAuth

        auth = ApiKeyAuth("sk-test")
        for _ in range(20):
            assert auth.headers() == {"Authorization": "Bearer sk-test"}
