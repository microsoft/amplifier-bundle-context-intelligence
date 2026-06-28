"""Tests for dual-auth wiring in the upload CLI (entra + static paths).

TDD RED phase — these tests define the expected behaviour of:
- run_upload() with an injected auth_strategy
- cli.py --auth-mode / --auth-resource flags
- resolve_config() in entra mode (no api_key required)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers (reused from test_uploader pattern)
# ---------------------------------------------------------------------------


def _write_session(
    tmp_path: Path,
    session_id: str,
    events: list[dict[str, Any]],
) -> tuple[Path, dict[str, Any]]:
    session_dir = tmp_path / f"session-{session_id}"
    session_dir.mkdir(parents=True, exist_ok=True)
    metadata = {"session_id": session_id, "format": "context-intelligence"}
    (session_dir / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
    (session_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events),
        encoding="utf-8",
    )
    return session_dir, metadata


def _make_events(n: int) -> list[dict[str, Any]]:
    return [{"event": f"e-{i}", "workspace": "ws", "data": {"index": i}} for i in range(n)]


class FakeToken:
    # expires_on far future so cached tokens are never considered stale in tests
    def __init__(self, token: str, expires_on: float = 9_999_999_999.0) -> None:
        self.token = token
        self.expires_on = expires_on


class FakeCredential:
    def __init__(self, token: str = "faketoken") -> None:
        self._token = token
        self.calls: list[tuple[Any, ...]] = []

    def get_token(self, *scopes: str, **kwargs: Any) -> FakeToken:
        self.calls.append(scopes)
        return FakeToken(self._token)


# ---------------------------------------------------------------------------
# run_upload() with injected auth_strategy
# ---------------------------------------------------------------------------


class TestRunUploadWithAuthStrategy:
    """run_upload accepts an auth_strategy kwarg and uses its headers."""

    def test_injected_strategy_headers_used_not_api_key(self, tmp_path: Path) -> None:
        """When auth_strategy is provided, its headers() are used — NOT api_key."""
        from context_intelligence.auth import EntraTokenAuth

        from amplifier_module_tool_context_intelligence_upload.uploader import run_upload

        fake_cred = FakeCredential("my-entra-token")
        strategy = EntraTokenAuth(fake_cred, "api://resource")

        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = MagicMock(status_code=200)

            run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=strategy,
            )

        _, init_kwargs = mock_cls.call_args
        assert init_kwargs["headers"] == {"Authorization": "Bearer my-entra-token"}

    def test_no_strategy_derives_api_key_auth(self, tmp_path: Path) -> None:
        """When auth_strategy is None, ApiKeyAuth(api_key) is derived — backward compat."""
        from amplifier_module_tool_context_intelligence_upload.uploader import run_upload

        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(1))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = MagicMock(status_code=200)

            run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="legacy-key",
                tracker=tracker,
            )

        _, init_kwargs = mock_cls.call_args
        assert init_kwargs["headers"] == {"Authorization": "Bearer legacy-key"}

    def test_strategy_headers_called_once_at_client_construction(self, tmp_path: Path) -> None:
        """The strategy's headers() is called once, at httpx.Client construction."""
        from context_intelligence.auth import ApiKeyAuth

        from amplifier_module_tool_context_intelligence_upload.uploader import run_upload

        strategy = ApiKeyAuth("test-static")
        session_dir, metadata = _write_session(tmp_path, "s1", _make_events(3))
        tracker = MagicMock()

        with patch("httpx.Client") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value.__enter__.return_value = mock_client
            mock_client.post.return_value = MagicMock(status_code=200)

            run_upload(
                sessions=[(session_dir, metadata)],
                server_url="https://server",
                api_key="",
                tracker=tracker,
                auth_strategy=strategy,
            )

        # httpx.Client is constructed once; headers kwarg should contain strategy headers
        assert mock_cls.call_count == 1
        _, kwargs = mock_cls.call_args
        assert kwargs.get("headers") == {"Authorization": "Bearer test-static"}


# ---------------------------------------------------------------------------
# resolve_config() — entra mode must NOT require api_key
# ---------------------------------------------------------------------------


class TestResolveConfigEntraMode:
    """resolve_config(auth_mode='entra') must not SystemExit on missing api_key."""

    def test_entra_mode_no_api_key_does_not_exit(self, tmp_path: Any) -> None:
        """In entra mode, empty api_key is legitimate — no SystemExit."""
        from context_intelligence.config import resolve_config

        # No env vars, no settings file, no api_key arg
        with patch("context_intelligence.config.SETTINGS_PATH", tmp_path / "nosettings.yaml"):
            with patch.dict("os.environ", {}, clear=True):
                url, key = resolve_config(
                    server_url="http://localhost:8000",
                    auth_mode="entra",
                )
        assert url == "http://localhost:8000"
        assert key == ""  # empty is fine in entra mode

    def test_static_mode_still_requires_api_key(self, tmp_path: Any) -> None:
        """Static mode (default) still exits when api_key is absent."""
        from context_intelligence.config import resolve_config

        with patch("context_intelligence.config.SETTINGS_PATH", tmp_path / "nosettings.yaml"):
            with patch.dict(
                "os.environ",
                {"AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL": "http://localhost"},
                clear=True,
            ):
                with pytest.raises(SystemExit):
                    resolve_config(server_url="http://localhost")


# ---------------------------------------------------------------------------
# CLI --auth-mode / --auth-resource flags
# ---------------------------------------------------------------------------


class TestCliAuthFlags:
    """_build_parser() must expose --auth-mode and --auth-resource flags."""

    def test_auth_mode_default_is_static(self) -> None:
        """When --auth-mode is not passed, it defaults to 'static'."""
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            ["--path", "/tmp", "--server-url", "http://s", "--api-key", "k"]
        )
        assert args.auth_mode == "static"

    def test_auth_mode_entra_accepted(self) -> None:
        """--auth-mode entra is a valid choice."""
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            ["--path", "/tmp", "--server-url", "http://s", "--auth-mode", "entra"]
        )
        assert args.auth_mode == "entra"

    def test_auth_resource_default_is_none(self) -> None:
        """When --auth-resource is not passed, it defaults to None."""
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            ["--path", "/tmp", "--server-url", "http://s", "--api-key", "k"]
        )
        assert args.auth_resource is None

    def test_auth_resource_accepts_value(self) -> None:
        """--auth-resource is accepted and stored."""
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        args = _build_parser().parse_args(
            [
                "--path",
                "/tmp",
                "--auth-mode",
                "entra",
                "--auth-resource",
                "api://53aa4ffd",
            ]
        )
        assert args.auth_resource == "api://53aa4ffd"

    def test_invalid_auth_mode_rejected(self) -> None:
        """--auth-mode with invalid value causes parse error (exit 2)."""
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        with pytest.raises(SystemExit) as exc_info:
            _build_parser().parse_args(["--path", "/tmp", "--auth-mode", "kerberos"])
        assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# CLI main() entra mode — end-to-end via mocked run_upload
# ---------------------------------------------------------------------------


class TestCliMainEntraMode:
    """main() in entra mode builds an EntraTokenAuth and passes it to run_upload."""

    def test_entra_mode_calls_run_upload_with_auth_strategy(
        self, tmp_path: Path, capsys: Any
    ) -> None:
        """main() with --auth-mode entra injects an auth_strategy into run_upload."""
        from amplifier_module_tool_context_intelligence_upload.cli import main

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 1,
        }

        fake_cred = FakeCredential("entra-token")

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost:38000",
                    "--auth-mode",
                    "entra",
                    "--auth-resource",
                    "api://53aa4ffd",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=fake_sessions,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ) as mock_upload,
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            patch(
                "context_intelligence.auth._make_cli_credential",
                return_value=fake_cred,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        call_kwargs = mock_upload.call_args.kwargs
        # auth_strategy must be present and produce the correct header
        strategy = call_kwargs.get("auth_strategy")
        assert strategy is not None
        # server_url still threaded through
        assert call_kwargs["server_url"] == "http://localhost:38000"

    def test_entra_mode_produces_bearer_from_credential(self, tmp_path: Path, capsys: Any) -> None:
        """In entra mode the auth_strategy.headers() yields 'Bearer <token from cred>'."""
        from context_intelligence.auth import EntraTokenAuth

        from amplifier_module_tool_context_intelligence_upload.cli import main

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 1,
        }

        captured_strategy: list[Any] = []
        fake_cred = FakeCredential("my-real-entra-token")

        def _capture_run_upload(**kwargs: Any) -> Any:
            captured_strategy.append(kwargs.get("auth_strategy"))
            return mock_result

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost:38000",
                    "--auth-mode",
                    "entra",
                    "--auth-resource",
                    "api://53aa4ffd",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=fake_sessions,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                side_effect=_capture_run_upload,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            patch(
                "context_intelligence.auth._make_cli_credential",
                return_value=fake_cred,
            ),
            pytest.raises(SystemExit),
        ):
            main()

        assert len(captured_strategy) == 1
        strategy = captured_strategy[0]
        assert isinstance(strategy, EntraTokenAuth)
        # Ask the strategy for its header — should use the fake credential
        hdrs = strategy.headers()
        assert hdrs == {"Authorization": "Bearer my-real-entra-token"}

    def test_static_mode_unchanged(self, tmp_path: Path, capsys: Any) -> None:
        """In static mode, auth_strategy is ApiKeyAuth derived from api_key."""
        from context_intelligence.auth import ApiKeyAuth

        from amplifier_module_tool_context_intelligence_upload.cli import main

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 1,
        }

        captured_strategy: list[Any] = []

        def _capture(**kwargs: Any) -> Any:
            captured_strategy.append(kwargs.get("auth_strategy"))
            return mock_result

        with (
            patch(
                "sys.argv",
                [
                    "context-intelligence-upload",
                    "--path",
                    str(tmp_path),
                    "--server-url",
                    "http://localhost",
                    "--api-key",
                    "sk-static",
                ],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.discover_and_sort",
                return_value=fake_sessions,
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                side_effect=_capture,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.ProgressTracker"),
            pytest.raises(SystemExit),
        ):
            main()

        assert len(captured_strategy) == 1
        strategy = captured_strategy[0]
        assert isinstance(strategy, ApiKeyAuth)
        assert strategy.headers() == {"Authorization": "Bearer sk-static"}
