"""Tests for BlobReadTool — ported from tool-blob-read, updated for merged module.

Constructor change: BlobReadTool(coordinator, resolver=None).
Tests that previously passed config= now inject a ToolConfigResolver directly.
Patch path updated to amplifier_module_tool_context_intelligence_query.blob_read_tool.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplifier_core import ToolResult

# ---------------------------------------------------------------------------
# Module-level helper functions
# ---------------------------------------------------------------------------


def _make_coordinator(resolver: Any) -> MagicMock:
    """Return a MagicMock coordinator whose get_capability returns *resolver*."""
    coordinator = MagicMock()
    coordinator.config = {}
    coordinator.get_capability.return_value = resolver
    return coordinator


def _make_hook_resolver(server_url: str | None, api_key: str | None = None) -> MagicMock:
    """Return a MagicMock hook resolver (returned by get_capability).

    api_key defaults to None so tests that don't exercise auth get a clean mock.
    Also sets destinations so _first_destination() can iterate it safely.
    """
    resolver = MagicMock()
    resolver.context_intelligence_server_url = server_url
    resolver.context_intelligence_api_key = api_key
    # destinations must be a real dict so _first_destination() can iterate it safely
    if server_url:
        resolver.destinations = {
            "default": SimpleNamespace(name="default", url=server_url, api_key=api_key or ""),
        }
    else:
        resolver.destinations = {}
    return resolver


def _make_tool_resolver(config: dict | None = None) -> Any:
    """Build a real ToolConfigResolver from config for injection."""
    from context_intelligence.tool_resolver import ToolConfigResolver

    coord = MagicMock()
    coord.config = {}
    return ToolConfigResolver(config or {}, coord)


@contextmanager
def _patch_async_client(
    fetch_blob_return: Any = None,
) -> Generator[tuple[MagicMock, MagicMock], None, None]:
    """Context manager that patches AsyncCIClient in the blob_read_tool module.

    Yields ``(mock_cls, mock_instance)`` so callers can assert constructor
    arguments (via ``mock_cls``) and ``fetch_blob`` call arguments (via
    ``mock_instance``).
    """
    mock_instance = MagicMock()
    mock_instance.fetch_blob = AsyncMock(return_value=fetch_blob_return)
    mock_cls = MagicMock(return_value=mock_instance)
    with patch(
        "amplifier_module_tool_context_intelligence_query.blob_read_tool.AsyncCIClient",
        mock_cls,
    ):
        yield mock_cls, mock_instance


# ---------------------------------------------------------------------------
# Module-level fixture
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_blob_dir():  # type: ignore[no-untyped-def]
    """Remove /tmp/ci-blobs/ before each test to prevent cross-test pollution."""
    blob_dir = pathlib.Path("/tmp/ci-blobs")
    if blob_dir.exists():
        shutil.rmtree(blob_dir)
    yield
    # Leave files after test for debugging; next test's setup cleans up


# ---------------------------------------------------------------------------
# (1) Protocol surface
# ---------------------------------------------------------------------------


class TestBlobReadToolProtocol:
    """Tool protocol surface: name, description, schema, execute return type."""

    def test_name_is_blob_read(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert tool.name == "blob_read"

    def test_description_mentions_ci_blob_scheme(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert "ci-blob://" in tool.description

    def test_description_mentions_file_path(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert "file path" in tool.description.lower()
        assert "disk path" not in tool.description.lower()

    def test_schema_type_is_object(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert tool.input_schema["type"] == "object"

    def test_schema_has_uri_required(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert "uri" in tool.input_schema["required"]

    def test_schema_uri_is_string(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        assert tool.input_schema["properties"]["uri"]["type"] == "string"

    async def test_execute_returns_tool_result(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        result = await tool.execute({"uri": "ci-blob://session/key"})
        assert isinstance(result, ToolResult)


# ---------------------------------------------------------------------------
# (2) Lazy capability resolution
# ---------------------------------------------------------------------------


class TestLazyCapabilityResolution:
    """execute() must resolve the config capability lazily and cache it."""

    async def test_capability_not_found_returns_configuration_error(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        # get_capability returns None → capability not registered
        tool = BlobReadTool(_make_coordinator(None))
        # Clear CI env vars so tier-3 fallback does not accidentally succeed
        clean = {k: "" for k in os.environ if k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")}
        with patch.dict(os.environ, clean):
            result = await tool.execute({"uri": "ci-blob://session/key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"
        assert "not configured" in result.error["message"].lower()

    async def test_server_url_none_returns_configuration_error_with_url(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver(None)
        tool = BlobReadTool(_make_coordinator(hook_resolver))
        # Clear CI env vars so tier-3 fallback does not accidentally succeed
        clean = {k: "" for k in os.environ if k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")}
        with patch.dict(os.environ, clean):
            result = await tool.execute({"uri": "ci-blob://session/key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"
        assert "url" in result.error["message"].lower()

    async def test_server_url_empty_returns_configuration_error(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("")
        tool = BlobReadTool(_make_coordinator(hook_resolver))
        # Clear CI env vars so tier-3 fallback does not accidentally succeed
        clean = {k: "" for k in os.environ if k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")}
        with patch.dict(os.environ, clean):
            result = await tool.execute({"uri": "ci-blob://session/key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"

    async def test_resolver_cached_after_first_lookup(self) -> None:
        """get_capability should be called exactly once across two execute() calls."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080")
        coordinator = _make_coordinator(hook_resolver)
        tool = BlobReadTool(coordinator)

        with _patch_async_client(fetch_blob_return={"data": "first"}):
            await tool.execute({"uri": "ci-blob://session/key1"})
            await tool.execute({"uri": "ci-blob://session/key2"})

        coordinator.get_capability.assert_called_once()


# ---------------------------------------------------------------------------
# (3) URI parsing
# ---------------------------------------------------------------------------


class TestURIParsing:
    """execute() must validate and parse ci-blob:// URIs before fetching."""

    async def test_missing_scheme_returns_uri_error(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(hook_resolver))
        # No "ci-blob://" prefix at all
        result = await tool.execute({"uri": "session/key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "uri_error"
        assert "ci-blob://" in result.error["message"]

    async def test_no_slash_after_scheme_returns_uri_error(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(hook_resolver))
        # Scheme present but no "/" separating session_id from key
        result = await tool.execute({"uri": "ci-blob://sessiononly"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "uri_error"
        assert "session_id/key" in result.error["message"]

    async def test_valid_uri_extracts_session_and_key(self) -> None:
        """A well-formed URI must call fetch_blob with correct session_id and key."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(hook_resolver))

        with _patch_async_client(fetch_blob_return={"ok": True}) as (_, mock_instance):
            await tool.execute({"uri": "ci-blob://my-session/my-key"})

        mock_instance.fetch_blob.assert_called_once_with("my-session", "my-key")


# ---------------------------------------------------------------------------
# (4) Path sanitization
# ---------------------------------------------------------------------------


class TestPathSanitization:
    """Output file paths must be confined to /tmp/ci-blobs/ regardless of key."""

    async def test_slashes_sanitized(self) -> None:
        """Slashes in the key must not create unexpected subdirectory depth."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(hook_resolver))

        with _patch_async_client(fetch_blob_return={"data": "test"}):
            result = await tool.execute({"uri": "ci-blob://my-session/path/with/slashes"})

        assert result.success is True
        assert result.output is not None
        output = str(result.output["path"])
        assert output.startswith("/tmp/ci-blobs/")
        assert ".." not in output

    async def test_special_chars_sanitized(self) -> None:
        """Path traversal sequences in the key must be neutralized in the output path."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(hook_resolver))

        with _patch_async_client(fetch_blob_return={"data": "test"}):
            result = await tool.execute({"uri": "ci-blob://my-session/../../etc/passwd"})

        assert result.success is True
        assert result.output is not None
        output = str(result.output["path"])
        assert output.startswith("/tmp/ci-blobs/")

    async def test_path_traversal_neutralized(self) -> None:
        """Output path must always stay under /tmp/ci-blobs/ even with traversal key."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(hook_resolver))

        with _patch_async_client(fetch_blob_return={"data": "test"}):
            result = await tool.execute({"uri": "ci-blob://my-session/../../etc/passwd"})

        assert result.success is True
        assert result.output is not None
        output = str(result.output["path"])
        # Resolved path must be strictly under /tmp/ci-blobs/
        assert output.startswith("/tmp/ci-blobs/")
        # Must not have escaped to /etc/
        assert "/etc/" not in output


# ---------------------------------------------------------------------------
# (5) Successful blob read
# ---------------------------------------------------------------------------


class TestBlobReadSuccess:
    """Happy-path behaviour: file written to disk and path returned."""

    async def test_successful_fetch_writes_file_and_returns_path(self) -> None:
        """Dict blob must be written as json.dumps(data); output is the file path."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        blob_data = {"session": "test", "data": [1, 2, 3]}
        hook_resolver = _make_hook_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(hook_resolver))

        with _patch_async_client(fetch_blob_return=blob_data):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        assert result.output is not None
        written = pathlib.Path(result.output["path"])
        assert written.exists()
        assert json.loads(written.read_text()) == blob_data

    async def test_output_path_structure(self) -> None:
        """Parent directory name == session_id, filename == key.json."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(hook_resolver))

        with _patch_async_client(fetch_blob_return={"ok": True}):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        assert result.output is not None
        p = pathlib.Path(result.output["path"])
        assert p.parent.name == "my-session"
        assert p.name == "my-key.json"

    async def test_string_blob_written_directly(self) -> None:
        """String blob must be written as raw text, NOT wrapped in json.dumps."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(hook_resolver))

        raw_string = "hello world, this is raw text"

        with _patch_async_client(fetch_blob_return=raw_string):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        assert result.output is not None
        written = pathlib.Path(result.output["path"])
        assert written.exists()
        # Must be written verbatim — not json.dumps'd (which would add quotes)
        assert written.read_text() == raw_string


# ---------------------------------------------------------------------------
# (6) Error handling
# ---------------------------------------------------------------------------


class TestBlobReadErrors:
    """execute() maps fetch_blob returning None to a typed http_error."""

    async def test_fetch_blob_none_returns_http_error(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080")
        tool = BlobReadTool(_make_coordinator(hook_resolver))

        with _patch_async_client(fetch_blob_return=None):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "http_error"


# ---------------------------------------------------------------------------
# (7) Authorization / constructor arguments
# ---------------------------------------------------------------------------


class TestAuthHeader:
    """AsyncCIClient must receive the correct api_key from the resolver."""

    async def test_api_key_passed_to_async_ci_client(self) -> None:
        """When api_key is set the AsyncCIClient must be constructed with it."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080", api_key="my-secret")
        tool = BlobReadTool(_make_coordinator(hook_resolver))

        with _patch_async_client(fetch_blob_return={"ok": True}) as (mock_cls, _):
            await tool.execute({"uri": "ci-blob://my-session/my-key"})

        _, call_kwargs = mock_cls.call_args
        assert call_kwargs["server_url"] == "http://localhost:8080"
        assert call_kwargs["api_key"] == "my-secret"
        assert "auth_strategy" in call_kwargs  # strategy is now always passed

    async def test_none_api_key_passes_empty_string(self) -> None:
        """When api_key is None the AsyncCIClient must receive an empty string."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver("http://localhost:8080", api_key=None)
        tool = BlobReadTool(_make_coordinator(hook_resolver))

        with _patch_async_client(fetch_blob_return={"ok": True}) as (mock_cls, _):
            await tool.execute({"uri": "ci-blob://my-session/my-key"})

        _, call_kwargs = mock_cls.call_args
        assert call_kwargs["server_url"] == "http://localhost:8080"
        assert call_kwargs["api_key"] == ""
        assert "auth_strategy" in call_kwargs  # strategy is now always passed


# ---------------------------------------------------------------------------
# Helpers for config-fallback tests (§7 case #8: blob-read parity)
# ---------------------------------------------------------------------------


def _make_hook_resolver_br(destinations: dict) -> MagicMock:
    """Hook resolver mock with specific destinations dict for blob-read tests."""
    resolver = MagicMock()
    resolver.destinations = destinations
    return resolver


def _make_dest_br(url: str, api_key: str) -> SimpleNamespace:
    """Quick Destination-like SimpleNamespace for blob-read test doubles."""
    return SimpleNamespace(name="default", url=url, api_key=api_key)


def _tool_resolver_br(config: dict | None = None) -> Any:
    """Build a ToolConfigResolver for blob-read config-fallback tests."""
    from context_intelligence.tool_resolver import ToolConfigResolver

    coord = MagicMock()
    coord.config = {}
    return ToolConfigResolver(config or {}, coord)


# ---------------------------------------------------------------------------
# TestBlobReadConfigFallback — §7 case #8 parity tests
# ---------------------------------------------------------------------------


class TestBlobReadConfigFallback:
    """Case #8: blob_read uses the same resolve_query_endpoint as graph_query (parity).

    Re-runs cases #1, #2, #5, #6 against BlobReadTool to prove identical resolution.
    """

    # Case #8/#1 — sources wins over destination
    async def test_read_config_wins_over_destination(self) -> None:
        """BlobReadTool case #8/#1: explicit sources wins over hook destinations."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver_br(
            destinations={"default": _make_dest_br("http://upload.example.com", "upload-key")}
        )
        coordinator = _make_coordinator(hook_resolver)
        config = {
            "sources": {
                "default": {"url": "http://read.example.com", "api_key": "read-key"},
            }
        }
        resolver = _tool_resolver_br(config)
        tool = BlobReadTool(coordinator, resolver)

        with _patch_async_client(fetch_blob_return={"ok": True}) as (mock_cls, _):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://read.example.com"
        assert call_kwargs["api_key"] == "read-key"

    # Case #8/#2 — destinations-only falls through to tier 2
    async def test_destinations_only_falls_through_to_tier2(self) -> None:
        """BlobReadTool case #8/#2: no sources key → uses hook destination."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver_br(
            destinations={"default": _make_dest_br("http://dest.example.com", "dest-key")}
        )
        coordinator = _make_coordinator(hook_resolver)
        resolver = _tool_resolver_br({})
        tool = BlobReadTool(coordinator, resolver)

        with _patch_async_client(fetch_blob_return={"ok": True}) as (mock_cls, _):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://dest.example.com"
        assert call_kwargs["api_key"] == "dest-key"

    # Case #8/#5 — env hit
    async def test_env_hit(self) -> None:
        """BlobReadTool case #8/#5: canonical env vars work as tier-3 fallback."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver_br(destinations={})
        coordinator = _make_coordinator(hook_resolver)
        resolver = _tool_resolver_br({})
        tool = BlobReadTool(coordinator, resolver)

        env_patch = {
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env.example.com",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY": "env-key",
        }
        with (
            patch.dict(os.environ, env_patch),
            _patch_async_client(fetch_blob_return={"ok": True}) as (mock_cls, _),
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://env.example.com"
        assert call_kwargs["api_key"] == "env-key"

    # Regression: env is below hook destination (tier 2 beats tier 3)
    async def test_env_does_not_override_hook_destination(self) -> None:
        """BlobReadTool regression: canonical env + hook destination → destination wins."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver_br(
            destinations={"default": _make_dest_br("http://dest.example.com", "dest-key")}
        )
        coordinator = _make_coordinator(hook_resolver)
        resolver = _tool_resolver_br({})
        tool = BlobReadTool(coordinator, resolver)

        # Canonical env set — must NOT override the hook destination
        env_patch = {
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://env-override.example.com",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY": "env-override-key",
        }
        with (
            patch.dict(os.environ, env_patch),
            _patch_async_client(fetch_blob_return={"ok": True}) as (mock_cls, _),
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        # Hook destination (tier 2) wins over env (tier 3)
        assert call_kwargs["server_url"] == "http://dest.example.com"
        assert call_kwargs["api_key"] == "dest-key"

    # Case #8/#6 — all miss → configuration_error
    async def test_all_miss_returns_configuration_error(self) -> None:
        """BlobReadTool case #8/#6: no config, no destinations, no env → configuration_error."""
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        hook_resolver = _make_hook_resolver_br(destinations={})
        coordinator = _make_coordinator(hook_resolver)
        resolver = _tool_resolver_br({})
        tool = BlobReadTool(coordinator, resolver)

        # Exclude ALL CI env vars (including canonical SERVER_URL / API_KEY)
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith("AMPLIFIER_CONTEXT_INTELLIGENCE_")
        }
        with (
            patch.dict(os.environ, clean_env, clear=True),
            _patch_async_client(fetch_blob_return={"ok": True}) as (mock_cls, _),
        ):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "configuration_error"


# ---------------------------------------------------------------------------
# TestBlobReadSourceSelection — workstream-1-multi-source-query-tools.md §6
# ---------------------------------------------------------------------------


class TestBlobReadSourceSelection:
    """execute() with an explicit `source` — same matrix as GraphQueryTool (parity)."""

    def _two_source_config(self) -> dict:
        return {
            "sources": {
                "alpha": {"url": "http://alpha.example.com", "api_key": "alpha-key"},
                "beta": {"url": "http://beta.example.com", "api_key": "beta-key"},
            }
        }

    def test_input_schema_has_optional_source_property(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        tool = BlobReadTool(_make_coordinator(None))
        props = tool.input_schema["properties"]
        assert "source" in props
        assert props["source"]["type"] == "string"
        assert "source" not in tool.input_schema["required"]

    async def test_source_matching_name_selects_that_source(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        resolver = _tool_resolver_br(self._two_source_config())
        coordinator = _make_coordinator(_make_hook_resolver_br(destinations={}))
        tool = BlobReadTool(coordinator, resolver)

        with _patch_async_client(fetch_blob_return={"ok": True}) as (mock_cls, _):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key", "source": "beta"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://beta.example.com"
        assert call_kwargs["api_key"] == "beta-key"

    async def test_source_not_matching_returns_unknown_source_error(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        resolver = _tool_resolver_br(self._two_source_config())
        coordinator = _make_coordinator(_make_hook_resolver_br(destinations={}))
        tool = BlobReadTool(coordinator, resolver)

        result = await tool.execute({"uri": "ci-blob://my-session/my-key", "source": "gamma"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "unknown_source"
        assert result.error["valid_sources"] == ["alpha", "beta"]

    async def test_source_omitted_with_two_configured_returns_ambiguous_error(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        resolver = _tool_resolver_br(self._two_source_config())
        coordinator = _make_coordinator(_make_hook_resolver_br(destinations={}))
        tool = BlobReadTool(coordinator, resolver)

        result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "ambiguous_source_selection"
        assert result.error["valid_sources"] == ["alpha", "beta"]

    async def test_source_omitted_with_one_configured_still_succeeds(self) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        config = {
            "sources": {
                "default": {"url": "http://only.example.com", "api_key": "only-key"},
            }
        }
        resolver = _tool_resolver_br(config)
        coordinator = _make_coordinator(_make_hook_resolver_br(destinations={}))
        tool = BlobReadTool(coordinator, resolver)

        with _patch_async_client(fetch_blob_return={"ok": True}) as (mock_cls, _):
            result = await tool.execute({"uri": "ci-blob://my-session/my-key"})

        assert result.success is True
        call_kwargs = mock_cls.call_args.kwargs
        assert call_kwargs["server_url"] == "http://only.example.com"

    async def test_selected_source_misconfigured_returns_source_misconfigured_error(
        self,
    ) -> None:
        from amplifier_module_tool_context_intelligence_query.blob_read_tool import BlobReadTool

        config = {
            "sources": {
                "good": {"url": "http://good.example.com", "api_key": "good-key"},
                "bad": {"url": "", "api_key": ""},
            }
        }
        resolver = _tool_resolver_br(config)
        coordinator = _make_coordinator(_make_hook_resolver_br(destinations={}))
        tool = BlobReadTool(coordinator, resolver)

        result = await tool.execute({"uri": "ci-blob://my-session/my-key", "source": "bad"})

        assert result.success is False
        assert result.error is not None
        assert result.error["type"] == "source_misconfigured"
        assert "bad" in result.error["message"]

        with _patch_async_client(fetch_blob_return={"ok": True}) as (mock_cls, _):
            good_result = await tool.execute(
                {"uri": "ci-blob://my-session/my-key", "source": "good"}
            )
        assert good_result.success is True
