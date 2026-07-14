"""Regression tests for issue #338 — mid-run Entra token refresh + bounded retry.

These tests exercise the ACTUAL behaviour the fix introduces (per-request auth
header, transient-vs-permanent classification, bounded exponential backoff,
Retry-After, fail-loud on auth errors, tracker-once semantics) rather than just
asserting mock plumbing. ``time.sleep`` is patched throughout so backoff never
sleeps for real.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import httpx
import pytest

from amplifier_module_tool_context_intelligence_upload.uploader import (
    _is_transient_status,
    run_upload,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_session(
    tmp_path: Path, session_id: str, events: list[dict[str, Any]]
) -> tuple[Path, dict[str, Any]]:
    session_dir = tmp_path / f"session-{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    metadata = {"session_id": session_id, "format": "context-intelligence"}
    (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (session_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8"
    )
    return session_dir, metadata


def _make_events(n: int) -> list[dict[str, Any]]:
    return [{"event": f"e-{i}", "workspace": "ws", "data": {"index": i}} for i in range(n)]


class _Resp:
    """Minimal stand-in for httpx.Response (status + text + headers)."""

    def __init__(
        self, status_code: int, text: str = "", headers: dict[str, str] | None = None
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class ConstAuth:
    """Auth strategy returning a constant bearer header."""

    def __init__(self, token: str = "tok") -> None:
        self._t = token
        self.calls = 0

    def headers(self) -> dict[str, str]:
        self.calls += 1
        return {"Authorization": f"Bearer {self._t}"}


class RaisingAuth:
    """Auth strategy whose headers() always raises (unusable key / cred failure)."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def headers(self) -> dict[str, str]:
        raise self._exc


class _FakeToken:
    def __init__(self, token: str, expires_on: float) -> None:
        self.token = token
        self.expires_on = expires_on


class _ExpiringCredential:
    """Credential returning a fresh, near-expiry token on every get_token().

    ``expires_on`` sits inside EntraTokenAuth's 300s safety margin, so every
    ``headers()`` call is forced down the refresh path — exactly the mid-run
    token-rotation scenario from the bug report.
    """

    def __init__(self) -> None:
        self.n = 0

    def get_token(self, *scopes: str, **kwargs: Any) -> _FakeToken:
        self.n += 1
        return _FakeToken(f"tok-{self.n}", expires_on=time.time() + 100.0)


# ---------------------------------------------------------------------------
# Bug 1 — per-request token refresh actually happens mid-run
# ---------------------------------------------------------------------------


class TestPerRequestTokenRefresh:
    def test_expired_token_is_refreshed_between_posts(self, tmp_path: Path) -> None:
        """A near-expiry Entra token is re-fetched per POST; later POST carries a NEW bearer."""
        from context_intelligence.auth import EntraTokenAuth

        cred = _ExpiringCredential()
        strategy = EntraTokenAuth(cred, "api://resource")

        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(2))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _Resp(200)

            run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=strategy,
            )

        # Credential was re-invoked per request (genuine refresh, not a cached header).
        assert cred.n == 2
        first_hdr = mock_client.post.call_args_list[0].kwargs["headers"]
        second_hdr = mock_client.post.call_args_list[1].kwargs["headers"]
        assert first_hdr == {"Authorization": "Bearer tok-1"}
        assert second_hdr == {"Authorization": "Bearer tok-2"}

    def test_header_not_baked_into_client(self, tmp_path: Path) -> None:
        """The httpx.Client is constructed WITHOUT a frozen Authorization header."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _Resp(200)

            run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth("abc"),
            )

        _, init_kwargs = mock_cls.call_args
        assert "headers" not in init_kwargs or init_kwargs.get("headers") is None


# ---------------------------------------------------------------------------
# Bug 2 — bounded retry with exponential backoff
# ---------------------------------------------------------------------------


class TestRetryBackoff:
    def test_transient_sequence_retried_then_succeeds(self, tmp_path: Path) -> None:
        """A ReadTimeout, then 503, then 200 → event succeeds; tracker fires ONCE."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        outcomes = [httpx.ReadTimeout("read timed out"), _Resp(503), _Resp(200)]

        def side_effect(url: str, **kwargs: Any) -> Any:
            item = outcomes.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch("httpx.Client") as mock_cls, patch("time.sleep") as mock_sleep:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = side_effect

            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
            )

        assert result.success is True
        assert result.events_uploaded == 1
        assert mock_client.post.call_count == 3
        # tracker.event_sent fires exactly once (terminal success), never per attempt.
        assert tracker.event_sent.call_count == 1
        tracker.mark_failed.assert_not_called()
        # Two transient failures → two backoff sleeps.
        assert mock_sleep.call_count == 2

    def test_persistent_transient_fails_after_exactly_max_retries(self, tmp_path: Path) -> None:
        """Unrelenting 503 → fail after max_retries+1 attempts; mark_failed once."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls, patch("time.sleep"):
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _Resp(503, text="down")

            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
                max_retries=3,
            )

        assert result.success is False
        assert result.failed_at is not None
        assert result.failed_at["http_status"] == 503
        # 3 retries + 1 initial attempt = 4 POSTs.
        assert mock_client.post.call_count == 4
        assert tracker.mark_failed.call_count == 1
        tracker.event_sent.assert_not_called()

    def test_permanent_4xx_fails_immediately_zero_retries(self, tmp_path: Path) -> None:
        """A 403 is permanent → single attempt, no backoff sleep."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls, patch("time.sleep") as mock_sleep:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _Resp(403, text="forbidden")

            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
                max_retries=5,
            )

        assert result.success is False
        assert result.failed_at is not None
        assert result.failed_at["http_status"] == 403
        assert mock_client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_401_is_permanent_not_retried(self, tmp_path: Path) -> None:
        """401 is deliberately PERMANENT for the uploader (refresh owns token expiry)."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls, patch("time.sleep") as mock_sleep:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _Resp(401, text="Unauthorized")

            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
                max_retries=5,
            )

        assert result.success is False
        assert result.failed_at is not None
        assert result.failed_at["http_status"] == 401
        assert mock_client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_429_honors_retry_after(self, tmp_path: Path) -> None:
        """A 429 with numeric Retry-After sleeps that many seconds (clamped), then succeeds."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        outcomes = [_Resp(429, headers={"Retry-After": "2"}), _Resp(200)]

        with patch("httpx.Client") as mock_cls, patch("time.sleep") as mock_sleep:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = lambda url, **kw: outcomes.pop(0)

            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
            )

        assert result.success is True
        assert mock_client.post.call_count == 2
        # Retry-After: 2 honored verbatim (min(2, 30) == 2.0).
        assert call(2.0) in mock_sleep.call_args_list

    def test_max_retries_zero_still_posts_once(self, tmp_path: Path) -> None:
        """max_retries=0 → exactly ONE POST attempt (no silent skip of the event)."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls, patch("time.sleep"):
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _Resp(200)

            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
                max_retries=0,
            )

        assert result.success is True
        assert mock_client.post.call_count == 1
        assert result.events_uploaded == 1


# ---------------------------------------------------------------------------
# Auth-error guard — headers() raising must fail LOUD, never crash the run
# ---------------------------------------------------------------------------


class TestAuthHeaderErrorGuard:
    def test_valueerror_from_headers_fails_loud_not_crash(self, tmp_path: Path) -> None:
        """ApiKeyAuth-style ValueError from headers() → UploadResult(success=False), no crash."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls, patch("time.sleep"):
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _Resp(200)

            # Must return a result, not raise.
            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=RaisingAuth(ValueError("api key is unusable")),
            )

        assert result.success is False
        assert result.error is not None and "auth header error" in result.error
        assert result.failed_at is not None
        assert result.failed_at["http_status"] == 0
        # No POST was ever attempted; failure recorded once; run did not crash.
        mock_client.post.assert_not_called()
        assert tracker.mark_failed.call_count == 1
        tracker.event_sent.assert_not_called()

    def test_arbitrary_exception_from_headers_is_caught(self, tmp_path: Path) -> None:
        """A non-ValueError credential failure (e.g. azure error) is also caught, not raised."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        class _AzureError(Exception):
            pass

        with patch("httpx.Client") as mock_cls, patch("time.sleep"):
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _Resp(200)

            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=RaisingAuth(_AzureError("credential unavailable")),
            )

        assert result.success is False
        assert "auth header error" in (result.error or "")


# ---------------------------------------------------------------------------
# Classifier unit — the adapted (uploader-specific) transient rule
# ---------------------------------------------------------------------------


class TestClassifier:
    @pytest.mark.parametrize("status", [500, 502, 503, 504, 429])
    def test_transient_statuses(self, status: int) -> None:
        assert _is_transient_status(status) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 409, 413, 422, 301, 302])
    def test_permanent_statuses(self, status: int) -> None:
        assert _is_transient_status(status) is False


# ---------------------------------------------------------------------------
# Follow-up #1 — configurable read/write timeout (--timeout)
# ---------------------------------------------------------------------------


class TestConfigurableTimeout:
    def test_default_timeout_is_30s(self, tmp_path: Path) -> None:
        """With no timeout_s, the httpx.Client read/write timeout stays at 30s."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _Resp(200)

            run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
            )

        timeout = mock_cls.call_args.kwargs["timeout"]
        assert timeout.read == 30.0
        assert timeout.write == 30.0
        # connect stays short regardless.
        assert timeout.connect == 5.0

    def test_timeout_s_overrides_read_and_write(self, tmp_path: Path) -> None:
        """timeout_s=120 sets read+write to 120s; connect is left short."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = _Resp(200)

            run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
                timeout_s=120.0,
            )

        timeout = mock_cls.call_args.kwargs["timeout"]
        assert timeout.read == 120.0
        assert timeout.write == 120.0
        assert timeout.connect == 5.0


# ---------------------------------------------------------------------------
# Follow-up #2 — cause-classify ConnectError (DNS/TLS fatal = fail fast)
# ---------------------------------------------------------------------------


class TestFatalTransportClassification:
    def test_dns_failure_is_not_retried(self, tmp_path: Path) -> None:
        """A ConnectError caused by DNS resolution (gaierror) fails fast — one attempt."""
        import socket

        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        def raise_dns(url: str, **kwargs: Any) -> Any:
            exc = httpx.ConnectError("name resolution failed")
            exc.__cause__ = socket.gaierror(-2, "Name or service not known")
            raise exc

        with patch("httpx.Client") as mock_cls, patch("time.sleep") as mock_sleep:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = raise_dns

            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://nonexistent.invalid",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
                max_retries=5,
            )

        assert result.success is False
        assert result.failed_at is not None
        assert result.failed_at["http_status"] == 0
        # Fatal transport error → NO retries, NO backoff.
        assert mock_client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_tls_failure_is_not_retried(self, tmp_path: Path) -> None:
        """A ConnectError caused by a TLS/cert error (ssl.SSLError) fails fast."""
        import ssl

        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        def raise_tls(url: str, **kwargs: Any) -> Any:
            exc = httpx.ConnectError("certificate verify failed")
            exc.__cause__ = ssl.SSLError("CERTIFICATE_VERIFY_FAILED")
            raise exc

        with patch("httpx.Client") as mock_cls, patch("time.sleep") as mock_sleep:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = raise_tls

            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
                max_retries=5,
            )

        assert result.success is False
        assert mock_client.post.call_count == 1
        mock_sleep.assert_not_called()

    def test_plain_connection_error_is_still_retried(self, tmp_path: Path) -> None:
        """A ConnectError with NO DNS/TLS cause (e.g. reset) remains transient — retried then succeeds."""
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        outcomes = [httpx.ConnectError("connection reset by peer"), _Resp(200)]

        def side_effect(url: str, **kwargs: Any) -> Any:
            item = outcomes.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        with patch("httpx.Client") as mock_cls, patch("time.sleep") as mock_sleep:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.side_effect = side_effect

            result = run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=ConstAuth(),
            )

        assert result.success is True
        assert mock_client.post.call_count == 2
        assert mock_sleep.call_count == 1


# ---------------------------------------------------------------------------
# CLI flag wiring — --max-retries and --timeout
# ---------------------------------------------------------------------------


class TestCliRobustnessFlags:
    def test_max_retries_default_is_5(self) -> None:
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            ["--path", "/tmp", "--server-url", "http://s", "--api-key", "k"]
        )
        assert args.max_retries == 5

    def test_max_retries_accepts_value(self) -> None:
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            ["--path", "/tmp", "--server-url", "http://s", "--api-key", "k", "--max-retries", "0"]
        )
        assert args.max_retries == 0

    def test_timeout_default_is_none(self) -> None:
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            ["--path", "/tmp", "--server-url", "http://s", "--api-key", "k"]
        )
        assert args.timeout_s is None

    def test_timeout_accepts_value(self) -> None:
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            ["--path", "/tmp", "--server-url", "http://s", "--api-key", "k", "--timeout", "120"]
        )
        assert args.timeout_s == 120.0
