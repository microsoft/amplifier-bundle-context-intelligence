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
import socket
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from context_intelligence.auth import AuthStrategy

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

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

_CI_BLOB_SCHEME = "ci-blob://"


class CIClientError(Exception):
    """A context-intelligence HTTP request genuinely failed (not an empty result).

    Raised by AsyncCIClient.cypher()/fetch_blob() so a down / slow / rejecting
    SELECTED source can never masquerade as an empty success.
    """

    def __init__(
        self,
        message: str,
        *,
        error_type: str,
        url: str,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        #: One of "connection_error" | "timeout" | "http_status" | "decode_error".
        self.error_type = error_type
        self.url = url
        self.status_code = status_code


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


def _http_get_strict(url: str, headers: dict[str, str]) -> Any:
    """GET *url* with *headers*, classifying-and-RAISING ``CIClientError`` on failure.

    The fail-loud counterpart of ``_http_get`` (which swallows every error to
    ``None``). A genuine transport/HTTP failure must never masquerade as an empty
    result, consistent with ``AsyncCIClient.cypher()``/``fetch_blob()`` (Phase 0).

    Library preference mirrors ``_http_get`` (requests -> httpx -> urllib.request)
    so classification is correct regardless of which backend is installed. Returns
    the parsed JSON body on a 2xx response; a well-formed empty body (e.g. ``[]``)
    is returned as-is -- an empty SUCCESS, not an error.

    Raises
    ------
    CIClientError
        error_type one of: ``connection_error`` (refused/DNS/reset),
        ``timeout``, ``http_status`` (non-2xx; ``status_code`` set), or
        ``decode_error`` (body is not valid JSON).
    """
    if _requests is not None:
        try:
            resp = _requests.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except _requests.exceptions.Timeout as exc:
            raise CIClientError(f"timeout listing {url}", error_type="timeout", url=url) from exc
        except _requests.exceptions.HTTPError as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            raise CIClientError(
                f"HTTP {status} from {url}",
                error_type="http_status",
                url=url,
                status_code=status,
            ) from exc
        except (ValueError, json.JSONDecodeError) as exc:  # resp.json() failed
            raise CIClientError(
                f"malformed JSON from {url}", error_type="decode_error", url=url
            ) from exc
        except _requests.exceptions.RequestException as exc:  # ConnectionError, etc.
            raise CIClientError(
                f"connection error to {url}: {exc}", error_type="connection_error", url=url
            ) from exc

    if _httpx is not None:
        try:
            with _httpx.Client(timeout=30) as client:
                resp = client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except _httpx.TimeoutException as exc:
            raise CIClientError(f"timeout listing {url}", error_type="timeout", url=url) from exc
        except _httpx.HTTPStatusError as exc:
            raise CIClientError(
                f"HTTP {exc.response.status_code} from {url}",
                error_type="http_status",
                url=url,
                status_code=exc.response.status_code,
            ) from exc
        except (ValueError, json.JSONDecodeError) as exc:  # resp.json() failed
            raise CIClientError(
                f"malformed JSON from {url}", error_type="decode_error", url=url
            ) from exc
        except _httpx.HTTPError as exc:  # ConnectError, transport, etc.
            raise CIClientError(
                f"connection error to {url}: {exc}", error_type="connection_error", url=url
            ) from exc

    # stdlib fallback
    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:  # subclass of URLError -- catch FIRST
        raise CIClientError(
            f"HTTP {exc.code} from {url}",
            error_type="http_status",
            url=url,
            status_code=exc.code,
        ) from exc
    except (TimeoutError, socket.timeout) as exc:  # read timeout
        raise CIClientError(f"timeout listing {url}", error_type="timeout", url=url) from exc
    except urllib.error.URLError as exc:
        # A URLError may wrap a socket timeout in .reason -- classify that as timeout.
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise CIClientError(f"timeout listing {url}", error_type="timeout", url=url) from exc
        raise CIClientError(
            f"connection error to {url}: {exc}", error_type="connection_error", url=url
        ) from exc
    try:
        return json.loads(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        raise CIClientError(
            f"malformed JSON from {url}", error_type="decode_error", url=url
        ) from exc


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
# Auth header helper
# ---------------------------------------------------------------------------


def _build_headers(api_key: str) -> dict[str, str]:
    """Return the standard Authorization header dict for *api_key*."""
    return {"Authorization": f"Bearer {api_key}"}


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
        Used only when *auth_strategy* is not provided (backward compat).
    auth_strategy:
        Optional pre-built ``AuthStrategy``.  When provided, ``headers()`` is
        called PER REQUEST so that Entra tokens are refreshed automatically.
        When ``None``, an ``ApiKeyAuth(api_key)`` is built implicitly (backward compat).
    """

    def __init__(
        self,
        server_url: str,
        api_key: str = "",
        auth_strategy: "AuthStrategy | None" = None,
    ) -> None:
        from context_intelligence.auth import ApiKeyAuth  # noqa: PLC0415

        self._server_url: str = server_url.rstrip("/")
        self._api_key: str = api_key
        self._strategy: AuthStrategy = (  # type: ignore[assignment]
            auth_strategy if auth_strategy is not None else ApiKeyAuth(api_key)
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Return the ``Authorization`` header dict, computed per-request via strategy."""
        return self._strategy.headers()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def cypher(
        self,
        query: str,
        workspace: str = "*",
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a Cypher query against the graph store.

        Parameters
        ----------
        query:
            Cypher query string.
        workspace:
            Workspace to scope the query. Defaults to ``"*"`` (all workspaces).
        params:
            Named query parameters passed to the Cypher engine. Defaults to
            an empty dict when ``None``.

        Returns
        -------
        list[dict]
            Rows returned by the server, or an empty list on failure.
        """
        url = f"{self._server_url}/cypher"
        body: dict[str, Any] = {
            "query": query,
            "params": params if params is not None else {},
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
        ``ci-blob://`` URIs.

        A genuine 200 response with no blob URIs (empty list, or a body that is
        not a list) returns an empty set -- that is an intentional empty SUCCESS,
        NOT an error, mirroring ``cypher()``'s empty-200 handling.

        Parameters
        ----------
        session_id:
            The session whose blob keys to list.

        Returns
        -------
        set[str]
            Set of ``ci-blob://`` URI strings (possibly empty).

        Raises
        ------
        CIClientError
            The request genuinely failed: connection error/refused, timeout,
            non-2xx HTTP status, or a malformed (non-JSON) body. A down / slow /
            rejecting server can never masquerade as "no blobs" -- see error_type
            for the classification.
        """
        url = f"{self._server_url}/blobs/{session_id}"
        result = _http_get_strict(url, self._auth_headers())
        # Genuine empty / non-list 200 -> empty success (NOT an error).
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
        """Check server health by running a simple count query.

        Uses ``POST /cypher`` with a lightweight session-count query
        instead of a dedicated ``/health`` endpoint (which does not exist).

        Returns
        -------
        dict
            ``{"status": "ok", "session_count": N}`` on success, or
            ``{"status": "unavailable", "error": "..."}`` on failure.
        """
        try:
            results = self.cypher("MATCH (s:Session) RETURN count(s) as session_count")
            count = results[0]["session_count"] if results else 0
            return {"status": "ok", "session_count": count}
        except Exception as exc:
            return {"status": "unavailable", "error": str(exc)}


# ---------------------------------------------------------------------------
# AsyncCIClient
# ---------------------------------------------------------------------------


class AsyncCIClient:
    """Asynchronous client for the context-intelligence server.

    Requires the ``httpx`` library (``pip install httpx``).

    Parameters
    ----------
    server_url:
        Base URL of the context-intelligence server (trailing slash is stripped).
    api_key:
        API key sent as ``Authorization: Bearer <api_key>`` on every request.
        Used only when *auth_strategy* is not provided (backward compat).
    auth_strategy:
        Optional pre-built ``AuthStrategy``.  When provided, ``headers()`` is
        called PER REQUEST so that Entra tokens are refreshed automatically.
        When ``None``, an ``ApiKeyAuth(api_key)`` is built implicitly (backward compat).
    timeout:
        Per-request HTTP timeout (seconds) applied to every ``httpx.AsyncClient``
        constructed by this instance (cypher, fetch_blob, list_blob_keys). Defaults
        to 30.0, matching the sync helpers' existing ``timeout=30``. See
        ``ToolConfigResolver.request_timeout`` for how callers resolve this value
        from config/env.
    """

    def __init__(
        self,
        server_url: str,
        api_key: str = "",
        auth_strategy: "AuthStrategy | None" = None,
        timeout: float = 30.0,
    ) -> None:
        from context_intelligence.auth import ApiKeyAuth  # noqa: PLC0415

        if httpx is None:
            raise ImportError(
                "httpx is required for AsyncCIClient. Install it with: pip install httpx"
            )
        self._server_url: str = server_url.rstrip("/")
        self._api_key: str = api_key
        self._strategy: AuthStrategy = (  # type: ignore[assignment]
            auth_strategy if auth_strategy is not None else ApiKeyAuth(api_key)
        )
        self._timeout: float = timeout

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def cypher(
        self,
        query: str,
        workspace: str = "*",
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Execute a Cypher query against the graph store (async).

        Parameters
        ----------
        query:
            Cypher query string.
        workspace:
            Workspace to scope the query. Defaults to ``"*"`` (all workspaces).
        params:
            Named query parameters passed to the Cypher engine. Defaults to
            an empty dict when ``None``.

        Returns
        -------
        list[dict]
            Rows returned by the server. A genuine 200 response with no rows
            (body ``None``, not a list, or a dict without a ``results`` list)
            returns an empty list -- that is an intentional empty SUCCESS, not
            an error.

        Raises
        ------
        CIClientError
            The request genuinely failed: connection error/refused, timeout,
            non-2xx HTTP status, or a malformed (non-JSON) response body. A
            down, slow, or rejecting SELECTED source can never masquerade as
            an empty success -- see error_type for the classification.
        """
        url = f"{self._server_url}/cypher"
        body: dict[str, Any] = {
            "query": query,
            "params": params if params is not None else {},
            "workspace": workspace,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:  # type: ignore[union-attr]
                resp = await client.post(url, json=body, headers=self._strategy.headers())
                resp.raise_for_status()
                result = resp.json()
        except httpx.TimeoutException as exc:  # type: ignore[union-attr]
            raise CIClientError(f"timeout querying {url}", error_type="timeout", url=url) from exc
        except httpx.HTTPStatusError as exc:  # type: ignore[union-attr]
            raise CIClientError(
                f"HTTP {exc.response.status_code} from {url}",
                error_type="http_status",
                url=url,
                status_code=exc.response.status_code,
            ) from exc
        except (ValueError, json.JSONDecodeError) as exc:  # resp.json() failed
            raise CIClientError(
                f"malformed JSON from {url}", error_type="decode_error", url=url
            ) from exc
        except httpx.HTTPError as exc:  # type: ignore[union-attr]  # ConnectError, transport, etc.
            raise CIClientError(
                f"connection error to {url}: {exc}", error_type="connection_error", url=url
            ) from exc

        # --- GRACEFUL EMPTY (intentional, unchanged semantics) ---
        # 200 OK, well-formed, simply no rows -> empty success, NOT an error.
        if result is None:
            return []
        if isinstance(result, list):
            return result
        # Some servers wrap in {"results": [...]}
        if isinstance(result, dict) and "results" in result:
            return result["results"]
        return []

    async def fetch_blob(self, session_id: str, key: str) -> Any | None:
        """Fetch a blob from the server (async).

        Calls ``GET /blobs/{session_id}/{key}`` and returns the parsed JSON
        response.

        Parameters
        ----------
        session_id:
            The session the blob belongs to.
        key:
            The blob key.

        Returns
        -------
        Any or None
            Parsed JSON content. A genuine 200 response with a JSON ``null``
            body returns ``None`` -- that is the caller's problem, not a
            transport error.

        Raises
        ------
        CIClientError
            The request genuinely failed: connection error/refused, timeout,
            non-2xx HTTP status, or a malformed (non-JSON) response body.
        """
        url = f"{self._server_url}/blobs/{session_id}/{key}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:  # type: ignore[union-attr]
                resp = await client.get(url, headers=self._strategy.headers())
                resp.raise_for_status()
                return resp.json()
        except httpx.TimeoutException as exc:  # type: ignore[union-attr]
            raise CIClientError(f"timeout fetching {url}", error_type="timeout", url=url) from exc
        except httpx.HTTPStatusError as exc:  # type: ignore[union-attr]
            raise CIClientError(
                f"HTTP {exc.response.status_code} from {url}",
                error_type="http_status",
                url=url,
                status_code=exc.response.status_code,
            ) from exc
        except (ValueError, json.JSONDecodeError) as exc:  # resp.json() failed
            raise CIClientError(
                f"malformed JSON from {url}", error_type="decode_error", url=url
            ) from exc
        except httpx.HTTPError as exc:  # type: ignore[union-attr]  # ConnectError, transport, etc.
            raise CIClientError(
                f"connection error to {url}: {exc}", error_type="connection_error", url=url
            ) from exc

    async def list_blob_keys(self, session_id: str) -> set[str]:
        """Return the set of ``ci-blob://`` URI keys for *session_id* (async).

        Calls ``GET /blobs/{session_id}`` and parses the response list of
        ``ci-blob://`` URIs.

        A genuine 200 response with no blob URIs (empty list, or a body that is
        not a list) returns an empty set -- an intentional empty SUCCESS, NOT an
        error, mirroring ``cypher()``'s empty-200 handling.

        Parameters
        ----------
        session_id:
            The session whose blob keys to list.

        Returns
        -------
        set[str]
            Set of ``ci-blob://`` URI strings (possibly empty).

        Raises
        ------
        CIClientError
            The request genuinely failed: connection error/refused, timeout,
            non-2xx HTTP status, or a malformed (non-JSON) body. A down / slow /
            rejecting server can never masquerade as "no blobs" -- see error_type
            for the classification. Honors ``self._timeout`` like ``cypher()`` /
            ``fetch_blob()``.
        """
        url = f"{self._server_url}/blobs/{session_id}"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:  # type: ignore[union-attr]
                resp = await client.get(url, headers=self._strategy.headers())
                resp.raise_for_status()
                result = resp.json()
        except httpx.TimeoutException as exc:  # type: ignore[union-attr]
            raise CIClientError(f"timeout listing {url}", error_type="timeout", url=url) from exc
        except httpx.HTTPStatusError as exc:  # type: ignore[union-attr]
            raise CIClientError(
                f"HTTP {exc.response.status_code} from {url}",
                error_type="http_status",
                url=url,
                status_code=exc.response.status_code,
            ) from exc
        except (ValueError, json.JSONDecodeError) as exc:  # resp.json() failed
            raise CIClientError(
                f"malformed JSON from {url}", error_type="decode_error", url=url
            ) from exc
        except httpx.HTTPError as exc:  # type: ignore[union-attr]  # ConnectError, transport, etc.
            raise CIClientError(
                f"connection error to {url}: {exc}", error_type="connection_error", url=url
            ) from exc

        # Genuine empty / non-list 200 -> empty success (NOT an error).
        if not isinstance(result, list):
            return set()
        return {
            item for item in result if isinstance(item, str) and item.startswith(_CI_BLOB_SCHEME)
        }

    async def health_check(self) -> dict[str, Any]:
        """Check server health by running a simple count query (async).

        Uses ``cypher()`` with a lightweight session-count query.

        Returns
        -------
        dict
            ``{"status": "ok", "session_count": N}`` on success, or
            ``{"status": "unavailable", "error": "..."}`` on failure.
        """
        try:
            results = await self.cypher("MATCH (s:Session) RETURN count(s) as session_count")
            count = results[0]["session_count"] if results else 0
            return {"status": "ok", "session_count": count}
        except Exception as exc:
            return {"status": "unavailable", "error": str(exc)}
