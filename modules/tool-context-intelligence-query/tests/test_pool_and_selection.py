"""PURE unit tests (no HTTP) for the connectable pool + resolve_query_connection.

Covers docs/multi-source-build-spec-v5.md §6.2 p1-p12:
  - p1-p3:  _connectable_pool ordering, source-wins collision, None-hook tolerance
  - p4-p5:  _select_from_pool explicit selection (unknown name / destination name)
  - p6-p9:  resolve_query_connection DEFAULT (no-pointer) semantics. RATIFIED RULE
            (user override of spec §4.4): 0 sources + N destinations resolves to
            the FIRST destination (does NOT fail loud); only 2+ SOURCES fails loud.
  - p10:    resolve_query_connection origin variants (source / env / None)
  - p11:    request_timeout coercion (re-verified here alongside the rest of the
            connectable-pool/provenance suite; already covered from Phase 0)
  - p12:    CIClientError classification (kept mock-level per spec -- the real
            transport classification is covered by the real-socket scenarios in
            test_multi_source_e2e.py / test_phase0_fail_loud_e2e.py)

No mocks stand in for the REAL-e2e scenarios (a-j) -- those live in
test_multi_source_e2e.py and test_phase0_fail_loud_e2e.py, driven through the
tools' execute() over real sockets.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_coordinator(config: dict | None = None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.config = config if config is not None else {}
    return coordinator


def _tool_resolver(config: dict) -> Any:
    from context_intelligence.tool_resolver import ToolConfigResolver

    return ToolConfigResolver(config, _make_coordinator())


def _dest(
    name: str,
    url: str,
    api_key: str = "",
    auth_mode: str = "static",
    auth_resource: str = "",
) -> SimpleNamespace:
    """Destination-like stand-in -- same attributes as the hook's real Destination
    NamedTuple (name/url/api_key/auth_mode/auth_resource), read-only."""
    return SimpleNamespace(
        name=name, url=url, api_key=api_key, auth_mode=auth_mode, auth_resource=auth_resource
    )


def _hook(destinations: dict, workspace: str = "test-workspace") -> SimpleNamespace:
    return SimpleNamespace(destinations=destinations, workspace=workspace)


# ---------------------------------------------------------------------------
# p1-p3: _connectable_pool
# ---------------------------------------------------------------------------


class TestConnectablePool:
    def test_p1_sources_then_destinations_in_config_order(self) -> None:
        """Pool order: tool sources (config order) THEN hook destinations (config order)."""
        from context_intelligence.tool_resolver import _connectable_pool

        resolver = _tool_resolver(
            {
                "sources": {
                    "b_source": {"url": "http://b-source.example.com", "api_key": "k"},
                    "a_source": {"url": "http://a-source.example.com", "api_key": "k"},
                }
            }
        )
        hook = _hook(
            {
                "z_dest": _dest("z_dest", "http://z-dest.example.com"),
                "y_dest": _dest("y_dest", "http://y-dest.example.com"),
            }
        )
        pool = _connectable_pool(resolver, hook)
        assert list(pool.keys()) == ["b_source", "a_source", "z_dest", "y_dest"]
        assert pool["b_source"].kind == "source"
        assert pool["a_source"].kind == "source"
        assert pool["z_dest"].kind == "destination"
        assert pool["y_dest"].kind == "destination"

    def test_p2_collision_source_wins(self) -> None:
        """Same-named 'default' on both sides -> pool entry is kind='source'."""
        from context_intelligence.tool_resolver import _connectable_pool

        resolver = _tool_resolver(
            {"sources": {"default": {"url": "http://source-default.example.com", "api_key": "sk"}}}
        )
        hook = _hook({"default": _dest("default", "http://dest-default.example.com", api_key="dk")})
        pool = _connectable_pool(resolver, hook)
        assert len(pool) == 1
        assert pool["default"].kind == "source"
        assert pool["default"].url == "http://source-default.example.com"
        assert pool["default"].api_key == "sk"

    def test_p3_none_hook_tolerated_sources_only(self) -> None:
        """hook_resolver is None (pre-hook-mount) -> sources only, no crash."""
        from context_intelligence.tool_resolver import _connectable_pool

        resolver = _tool_resolver(
            {"sources": {"a": {"url": "http://a.example.com", "api_key": "k"}}}
        )
        pool = _connectable_pool(resolver, None)
        assert list(pool.keys()) == ["a"]

    def test_p3_malformed_destinations_attr_tolerated(self) -> None:
        """hook_resolver.destinations is not a dict (e.g. MagicMock auto-attr) -> ignored."""
        from context_intelligence.tool_resolver import _connectable_pool

        resolver = _tool_resolver(
            {"sources": {"a": {"url": "http://a.example.com", "api_key": "k"}}}
        )
        hook = MagicMock()
        hook.destinations = "not-a-dict"
        pool = _connectable_pool(resolver, hook)
        assert list(pool.keys()) == ["a"]

    def test_p3_empty_pool_when_nothing_configured(self) -> None:
        from context_intelligence.tool_resolver import _connectable_pool

        resolver = _tool_resolver({})
        assert _connectable_pool(resolver, None) == {}


# ---------------------------------------------------------------------------
# p4-p5: _select_from_pool
# ---------------------------------------------------------------------------


class TestSelectFromPool:
    def test_p4_explicit_unknown_raises_lists_whole_pool(self) -> None:
        from context_intelligence.tool_resolver import (
            SourceSelectionError,
            _connectable_pool,
            _select_from_pool,
        )

        resolver = _tool_resolver(
            {"sources": {"a": {"url": "http://a.example.com", "api_key": "k"}}}
        )
        hook = _hook({"b": _dest("b", "http://b.example.com")})
        pool = _connectable_pool(resolver, hook)

        with pytest.raises(SourceSelectionError) as excinfo:
            _select_from_pool(pool, "nope")
        assert excinfo.value.error_type == "unknown_source"
        assert excinfo.value.valid_names == ["a", "b"]  # WHOLE pool, not just sources

    def test_p4_explicit_unknown_on_empty_pool(self) -> None:
        from context_intelligence.tool_resolver import (
            SourceSelectionError,
            _connectable_pool,
            _select_from_pool,
        )

        resolver = _tool_resolver({})
        pool = _connectable_pool(resolver, None)
        with pytest.raises(SourceSelectionError) as excinfo:
            _select_from_pool(pool, "nope")
        assert excinfo.value.valid_names == []

    def test_p5_explicit_destination_name_returns_destination_entry(self) -> None:
        from context_intelligence.tool_resolver import _connectable_pool, _select_from_pool

        resolver = _tool_resolver(
            {"sources": {"a": {"url": "http://a.example.com", "api_key": "k"}}}
        )
        hook = _hook(
            {"warehouse": _dest("warehouse", "http://warehouse.example.com", api_key="wk")}
        )
        pool = _connectable_pool(resolver, hook)

        entry = _select_from_pool(pool, "warehouse")
        assert entry is not None
        assert entry.kind == "destination"
        assert entry.url == "http://warehouse.example.com"
        assert entry.api_key == "wk"

    def test_no_name_defers_to_default_semantics(self) -> None:
        from context_intelligence.tool_resolver import _connectable_pool, _select_from_pool

        resolver = _tool_resolver(
            {"sources": {"a": {"url": "http://a.example.com", "api_key": "k"}}}
        )
        pool = _connectable_pool(resolver, None)
        assert _select_from_pool(pool, None) is None


# ---------------------------------------------------------------------------
# p6-p9: resolve_query_connection DEFAULT (no source_name) semantics
# ---------------------------------------------------------------------------


class TestDefaultResolution:
    def test_p6_one_source_returns_it(self) -> None:
        from context_intelligence.tool_resolver import resolve_query_connection

        resolver = _tool_resolver(
            {"sources": {"only": {"url": "http://only.example.com", "api_key": "k"}}}
        )
        conn = resolve_query_connection(None, resolver)
        assert conn.url == "http://only.example.com"
        assert conn.api_key == "k"
        assert conn.origin is not None
        assert conn.origin.kind == "source"
        assert conn.origin.name == "only"

    def test_p7_two_plus_sources_raises_ambiguous(self) -> None:
        from context_intelligence.tool_resolver import (
            SourceSelectionError,
            resolve_query_connection,
        )

        resolver = _tool_resolver(
            {
                "sources": {
                    "a": {"url": "http://a.example.com", "api_key": "k"},
                    "b": {"url": "http://b.example.com", "api_key": "k"},
                }
            }
        )
        with pytest.raises(SourceSelectionError) as excinfo:
            resolve_query_connection(None, resolver)
        assert excinfo.value.error_type == "ambiguous_source_selection"
        assert excinfo.value.valid_names == ["a", "b"]

    def test_p8_zero_sources_two_plus_destinations_returns_first(self) -> None:
        """RATIFIED RULE (user override of spec §4.4): 0 sources + N destinations +
        no pointer -> FIRST destination in config order wins (destinations are the
        established read-fallback pool). Does NOT fail loud. Provenance names it.
        Read-only: only reads hook destinations."""
        from context_intelligence.tool_resolver import resolve_query_connection

        resolver = _tool_resolver({})
        hook = _hook(
            {
                "d1": _dest("d1", "http://d1.example.com", api_key="d1k"),
                "d2": _dest("d2", "http://d2.example.com", api_key="d2k"),
            }
        )
        conn = resolve_query_connection(hook, resolver)
        assert conn.url == "http://d1.example.com"
        assert conn.api_key == "d1k"
        assert conn.origin is not None
        assert conn.origin.kind == "destination"
        assert conn.origin.name == "d1"

    def test_p9_zero_sources_one_destination_returns_it(self) -> None:
        """Unchanged from #67 (first-destination fallback), now read from the pool."""
        from context_intelligence.tool_resolver import resolve_query_connection

        resolver = _tool_resolver({})
        hook = _hook(
            {"only_dest": _dest("only_dest", "http://only-dest.example.com", api_key="dk")}
        )
        conn = resolve_query_connection(hook, resolver)
        assert conn.url == "http://only-dest.example.com"
        assert conn.api_key == "dk"
        assert conn.origin is not None
        assert conn.origin.kind == "destination"
        assert conn.origin.name == "only_dest"

    def test_zero_sources_zero_destinations_falls_through_to_env(self) -> None:
        from context_intelligence.tool_resolver import resolve_query_connection

        resolver = _tool_resolver({})
        clean = {k: "" for k in os.environ if k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")}
        with patch.dict(
            os.environ, {**clean, "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env.x"}
        ):
            conn = resolve_query_connection(None, resolver)
        assert conn.url == "http://env.x"
        assert conn.origin is not None
        assert conn.origin.kind == "env"
        assert conn.origin.name == ""


# ---------------------------------------------------------------------------
# p10: resolve_query_connection origin variants
# ---------------------------------------------------------------------------


class TestResolvedOrigin:
    def test_p10_source_kind_origin(self) -> None:
        from context_intelligence.tool_resolver import resolve_query_connection

        resolver = _tool_resolver(
            {"sources": {"only": {"url": "http://only.example.com", "api_key": "k"}}}
        )
        conn = resolve_query_connection(None, resolver)
        assert conn.origin is not None
        assert conn.origin.kind == "source"
        assert conn.origin.url == "http://only.example.com"

    def test_p10_env_only_origin(self) -> None:
        from context_intelligence.tool_resolver import resolve_query_connection

        resolver = _tool_resolver({})
        env = {
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env.example.com",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY": "env-key",
        }
        with patch.dict(os.environ, env):
            conn = resolve_query_connection(None, resolver)
        assert conn.url == "http://env.example.com"
        assert conn.api_key == "env-key"
        assert conn.origin is not None
        assert conn.origin.kind == "env"
        assert conn.origin.name == ""

    def test_p10_url_none_origin_none(self) -> None:
        from context_intelligence.tool_resolver import resolve_query_connection

        resolver = _tool_resolver({})
        clean = {k: "" for k in os.environ if k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")}
        with patch.dict(os.environ, clean):
            conn = resolve_query_connection(None, resolver)
        assert conn.url is None
        assert conn.origin is None


# ---------------------------------------------------------------------------
# p11: request_timeout coercion
# ---------------------------------------------------------------------------


class TestRequestTimeout:
    def test_p11_default_when_absent(self) -> None:
        assert _tool_resolver({}).request_timeout == 30.0

    def test_p11_bad_value_falls_back_to_default(self) -> None:
        assert _tool_resolver({"request_timeout": "not-a-number"}).request_timeout == 30.0

    def test_p11_non_positive_clamped_to_minimum(self) -> None:
        assert _tool_resolver({"request_timeout": -5}).request_timeout == 0.1

    def test_p11_valid_value_parsed(self) -> None:
        assert _tool_resolver({"request_timeout": 5.5}).request_timeout == 5.5


# ---------------------------------------------------------------------------
# p12: CIClientError classification (mock-level -- real transport classification
# is covered by the real-socket scenarios in test_multi_source_e2e.py /
# test_phase0_fail_loud_e2e.py, per spec §6.2 p12).
# ---------------------------------------------------------------------------


class TestCIClientErrorClassification:
    async def test_p12_timeout_classified(self) -> None:
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        client = AsyncCIClient(server_url="http://x.example.com", api_key="k")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_httpx = AsyncMock()
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.post = AsyncMock(side_effect=httpx.TimeoutException("boom"))
            mock_cls.return_value = mock_httpx
            with pytest.raises(CIClientError) as excinfo:
                await client.cypher("MATCH (n) RETURN n")
        assert excinfo.value.error_type == "timeout"

    async def test_p12_connection_error_classified(self) -> None:
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        client = AsyncCIClient(server_url="http://x.example.com", api_key="k")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_httpx = AsyncMock()
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
            mock_cls.return_value = mock_httpx
            with pytest.raises(CIClientError) as excinfo:
                await client.cypher("MATCH (n) RETURN n")
        assert excinfo.value.error_type == "connection_error"

    async def test_p12_http_status_classified(self) -> None:
        import httpx

        from context_intelligence.client import AsyncCIClient, CIClientError

        client = AsyncCIClient(server_url="http://x.example.com", api_key="k")
        mock_response = MagicMock()
        mock_response.status_code = 500

        def _raise() -> None:
            raise httpx.HTTPStatusError("500", request=MagicMock(), response=mock_response)

        mock_response.raise_for_status = MagicMock(side_effect=_raise)
        with patch("httpx.AsyncClient") as mock_cls:
            mock_httpx = AsyncMock()
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.post = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_httpx
            with pytest.raises(CIClientError) as excinfo:
                await client.cypher("MATCH (n) RETURN n")
        assert excinfo.value.error_type == "http_status"
        assert excinfo.value.status_code == 500

    async def test_p12_decode_error_classified(self) -> None:
        from context_intelligence.client import AsyncCIClient, CIClientError

        client = AsyncCIClient(server_url="http://x.example.com", api_key="k")
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.side_effect = ValueError("bad json")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_httpx = AsyncMock()
            mock_httpx.__aenter__ = AsyncMock(return_value=mock_httpx)
            mock_httpx.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.post = AsyncMock(return_value=mock_response)
            mock_cls.return_value = mock_httpx
            with pytest.raises(CIClientError) as excinfo:
                await client.cypher("MATCH (n) RETURN n")
        assert excinfo.value.error_type == "decode_error"
