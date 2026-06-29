"""Tests for ${VAR} expansion of auth_resource in query-tool sources (slice 2-B, root-bundle scope).

Root-bundle scope: only context_intelligence.* is importable here.

  - Hook destination auth_resource: tested in modules/hook-context-intelligence/tests/test_hook_auth.py
  - Upload CLI auth_resource:       tested in modules/tool-context-intelligence-upload/tests/test_auth_wiring.py
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Query-tool sources — _expand() applied to auth_resource
# ---------------------------------------------------------------------------


class TestToolSourceAuthResourceExpansion:
    """ToolConfigResolver.sources applies _expand() to auth_resource."""

    def _resolver(self, config: dict) -> object:
        from context_intelligence.tool_resolver import ToolConfigResolver

        coord = MagicMock()
        coord.config = {}
        return ToolConfigResolver(config, coord)

    def test_auth_resource_placeholder_expanded(self) -> None:
        """${MY_RESOURCE} in source auth_resource is expanded to env value."""
        r = self._resolver(
            {
                "sources": {
                    "team": {
                        "url": "http://ci:8000",
                        "auth_mode": "entra",
                        "auth_resource": "${MY_CI_RESOURCE}",
                    }
                }
            }
        )
        with patch.dict(os.environ, {"MY_CI_RESOURCE": "api://server-id"}, clear=False):
            # Force re-parse
            r._sources = None  # type: ignore[attr-defined]
            sources = r.sources  # type: ignore[attr-defined]
        assert sources["team"].auth_resource == "api://server-id"

    def test_auth_resource_with_default_placeholder_unset(self) -> None:
        """${MY_CI_RESOURCE:api://fallback} uses default when env var unset."""
        r = self._resolver(
            {
                "sources": {
                    "team": {
                        "url": "http://ci:8000",
                        "auth_mode": "entra",
                        "auth_resource": "${_UNSET_CI_RES_XYZZY:api://fallback-id}",
                    }
                }
            }
        )
        env = {k: v for k, v in os.environ.items() if k != "_UNSET_CI_RES_XYZZY"}
        with patch.dict(os.environ, env, clear=True):
            r._sources = None  # type: ignore[attr-defined]
            sources = r.sources  # type: ignore[attr-defined]
        assert sources["team"].auth_resource == "api://fallback-id"

    def test_static_source_api_key_placeholder_expanded(self) -> None:
        """${MY_API_KEY} in source api_key is expanded to env value."""
        r = self._resolver(
            {
                "sources": {
                    "local": {
                        "url": "http://local:8000",
                        "api_key": "${LOCAL_CI_KEY}",
                    }
                }
            }
        )
        with patch.dict(os.environ, {"LOCAL_CI_KEY": "sk-expanded"}, clear=False):
            r._sources = None  # type: ignore[attr-defined]
            sources = r.sources  # type: ignore[attr-defined]
        assert sources["local"].api_key == "sk-expanded"
