"""context_intelligence.client — HTTP transport and CIClient.

Level 2 — Network I/O.

Provides a thin synchronous HTTP client for the context-intelligence server.
The HTTP helpers prefer the ``requests`` library when available, then
``httpx`` (synchronous API), and fall back to the stdlib
``urllib.request`` so the module works without any third-party install.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from typing import Any

logger = logging.getLogger("context_intelligence.client")

# ---------------------------------------------------------------------------
# Optional library imports (requests preferred, httpx second, stdlib fallback)
# ---------------------------------------------------------------------------

_requests: Any = None
_httpx: Any = None

try:
    import requests as _requests_lib

    _requests = _requests_lib
except ImportError:
    pass

try:
    import httpx as _httpx_lib

    _httpx = _httpx_lib
except ImportError:
    pass

_CI_BLOB_SCHEME = "ci-blob://"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _http_post(url: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
    """POST *body* as JSON to *url* with *headers*.

    Returns the parsed JSON response, or ``None`` on any error.

    Library preference: requests → httpx → urllib.request.
    """
    if _requests is not None:
        try:
            resp = _requests.post(url, json=body, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("_http_post requests error: %s", exc)
            return None

    if _httpx is not None:
        try:
            with _httpx.Client(timeout=30) as client:
                resp = client.post(url, json=body, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.debug("_http_post httpx error: %s", exc)
            return None

    # stdlib fallback
    data = json.dumps(body).encode("utf-8")
    content_headers = {**headers, "Content-Type": "application/json"}
    try:
        req = urllib.request.Request(url, data=data, headers=content_headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except Exception as exc:
        logger.debug("_http_post urllib error: %s", exc)
        return None


def _http_get(url: str, headers: dict[str, str]) -> Any:
    """GET *url* with *headers*.

    Returns the parsed JSON response, or ``None`` on any error.

    Library preference: requests → httpx → urllib.request.
    """
    if _requests is not None:
        try:
            resp = _requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.debug("_http_get requests error: %s", exc)
            return None

    if _httpx is not None:
        try:
            with _httpx.Client(timeout=30) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.debug("_http_get httpx error: %s", exc)
            return None

    # stdlib fallback
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except Exception as exc:
        logger.debug("_http_get urllib error: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Safe JSON parse
# ---------------------------------------------------------------------------


def _safe_json_loads(raw: Any) -> Any:
    """Return *raw* parsed as JSON when it is a string, otherwise return it as-is.

    >>> _safe_json_loads('{"a": 1}')
    {'a': 1}
    >>> _safe_json_loads({"a": 1})
    {'a': 1}
    >>> _safe_json_loads(None) is None
    True
    """
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


# ---------------------------------------------------------------------------
# CIClient
# ---------------------------------------------------------------------------


class CIClient:
    """Synchronous client for the context-intelligence server.

    Parameters
    ----------
    server_url:
        Base URL of the context-intelligence server (trailing slash is stripped).
    api_key:
        API key sent as ``Authorization: Bearer <api_key>`` on every request.
    """

    def __init__(self, server_url: str, api_key: str) -> None:
        self._server_url: str = server_url.rstrip("/")
        self._api_key: str = api_key

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Return the ``Authorization`` header dict."""
        return {"Authorization": f"Bearer {self._api_key}"}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cypher(self, query: str, workspace: str = "*") -> list[dict[str, Any]]:
        """Execute a Cypher query against the graph store.

        Parameters
        ----------
        query:
            Cypher query string.
        workspace:
            Workspace to scope the query. Defaults to ``"*"`` (all workspaces).

        Returns
        -------
        list[dict]
            Rows returned by the server, or an empty list on failure.
        """
        url = f"{self._server_url}/cypher"
        body: dict[str, Any] = {
            "query": query,
            "params": {},
            "workspace": workspace,
        }
        result = _http_post(url, body, self._auth_headers())
        if result is None:
            return []
        if isinstance(result, list):
            return result
        # Some servers wrap in {"results": [...]}
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return []

    def list_blob_keys(self, session_id: str) -> set[str]:
        """Return the set of ``ci-blob://`` URI keys for *session_id*.

        Calls ``GET /blobs/{session_id}`` and parses the response list of
        ``ci-blob://`` URIs.  Returns an empty set on any error.

        Parameters
        ----------
        session_id:
            The session whose blob keys to list.

        Returns
        -------
        set[str]
            Set of ``ci-blob://`` URI strings.
        """
        url = f"{self._server_url}/blobs/{session_id}"
        result = _http_get(url, self._auth_headers())
        if result is None:
            return set()
        if not isinstance(result, list):
            return set()
        return {
            item for item in result if isinstance(item, str) and item.startswith(_CI_BLOB_SCHEME)
        }

    def fetch_blob(self, session_id: str, key: str) -> Any | None:
        """Fetch a blob from the server.

        Calls ``GET /blobs/{session_id}/{key}`` and returns the parsed JSON
        response, or ``None`` on failure.

        Parameters
        ----------
        session_id:
            The session the blob belongs to.
        key:
            The blob key.

        Returns
        -------
        Any or None
            Parsed JSON content, or ``None`` when the request fails.
        """
        url = f"{self._server_url}/blobs/{session_id}/{key}"
        return _http_get(url, self._auth_headers())

    def health_check(self) -> dict[str, Any]:
        """Check the server health.

        Calls ``GET /health`` and returns the server's response dict.
        Always returns a dict with at least ``status`` and ``session_count``
        keys, even when the server is unreachable.

        Returns
        -------
        dict
            Server health response with ``status`` and ``session_count``.
        """
        url = f"{self._server_url}/health"
        result = _http_get(url, self._auth_headers())

        if isinstance(result, dict):
            # Ensure required keys are present
            result.setdefault("status", "unknown")
            result.setdefault("session_count", 0)
            return result

        return {"status": "unavailable", "session_count": 0}
