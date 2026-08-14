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

from amplifier_module_tool_context_intelligence_upload.session_graph import UploadScope


def _fake_scope(sessions: list) -> UploadScope:
    """Build an UploadScope fixture for mocking resolve_upload_sessions in CLI tests."""
    root_ids = [meta["session_id"] for _, meta in sessions]
    return UploadScope(
        sessions=sessions,
        mode="whole",
        selected_root_ids=root_ids,
        total_discovered=len(sessions),
        selected_count=len(sessions),
        dangling_parent_ids=[],
    )


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

        # Issue #338: headers are sent PER REQUEST (so token refresh can fire
        # mid-run), not baked into the httpx.Client constructor.
        post_kwargs = mock_client.post.call_args.kwargs
        assert post_kwargs["headers"] == {"Authorization": "Bearer my-entra-token"}

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

        # Issue #338: header is sent per request, not baked into the client.
        post_kwargs = mock_client.post.call_args.kwargs
        assert post_kwargs["headers"] == {"Authorization": "Bearer legacy-key"}

    def test_strategy_headers_fetched_per_request(self, tmp_path: Path) -> None:
        """Issue #338: the strategy's headers() is fetched PER request, not baked once.

        The Authorization header must be attached to each ``client.post`` call (so a
        long run can pick up a refreshed token mid-flight), NOT passed to the
        ``httpx.Client`` constructor where it would freeze for the whole run.
        """
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

        # The header is NOT baked into the client constructor...
        _, init_kwargs = mock_cls.call_args
        assert "headers" not in init_kwargs or init_kwargs.get("headers") is None
        # ...it is attached to every POST (3 events -> 3 posts, each carrying it).
        assert mock_client.post.call_count == 3
        for call in mock_client.post.call_args_list:
            assert call.kwargs.get("headers") == {"Authorization": "Bearer test-static"}


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
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                return_value=mock_result,
            ) as mock_upload,
            patch("amplifier_module_tool_context_intelligence_upload.cli.TwoLevelProgressRenderer"),
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
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                side_effect=_capture_run_upload,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.TwoLevelProgressRenderer"),
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
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                side_effect=_capture,
            ),
            patch("amplifier_module_tool_context_intelligence_upload.cli.TwoLevelProgressRenderer"),
            pytest.raises(SystemExit),
        ):
            main()

        assert len(captured_strategy) == 1
        strategy = captured_strategy[0]
        assert isinstance(strategy, ApiKeyAuth)
        assert strategy.headers() == {"Authorization": "Bearer sk-static"}


# ---------------------------------------------------------------------------
# main() -- auth_strategy built from the SELECTED DESTINATION's own auth
# config (destination.auth_mode / destination.auth_resource / destination.api_key),
# with NO --auth-mode / --auth-resource / --server-url CLI flags at all.
#
# This is distinct from TestCliMainEntraMode above, which drives auth purely
# via CLI flags on the explicit-connection path (Tiers 1/2). These tests
# exercise Tier 3 (_resolve_connection's destinations-map branch, cli.py
# ~719-727) where the destination itself is the sole source of auth config.
# ---------------------------------------------------------------------------


def _destination_auth_dest(**overrides: Any) -> Any:
    """Build a hook Destination for destination-configured-auth CLI tests."""
    from amplifier_module_hook_context_intelligence.config_resolver import Destination

    fields: dict[str, Any] = {
        "name": "team",
        "url": "https://ci.team",
        "api_key": "",
        "include": (),
        "exclude": (),
    }
    fields.update(overrides)
    return Destination(**fields)


class TestDestinationConfiguredAuth:
    """main() must build auth_strategy from the selected destination's own
    auth_mode/auth_resource/api_key -- not just from CLI flags -- when the
    user supplies no --auth-mode/--auth-resource/--server-url at all.
    """

    @pytest.fixture
    def isolated_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Isolate a main() run from the developer's real machine.

        Mirrors tests/test_cli.py's isolated_home fixture (not shared via
        conftest.py, so redefined locally here).
        """
        monkeypatch.setenv("HOME", str(tmp_path))
        for var in (
            "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_URL",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_API_KEY",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_MODE",
            "AMPLIFIER_CONTEXT_INTELLIGENCE_AUTH_RESOURCE",
        ):
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(
            "amplifier_module_tool_context_intelligence_upload.cli.progress_file_path",
            lambda job_id, override=None: tmp_path / "progress.json",
        )
        return tmp_path

    def test_destination_with_entra_auth_mode_builds_entra_strategy(
        self, tmp_path: Path, isolated_home: Path
    ) -> None:
        """A single destination configured with auth_mode='entra' must produce
        an EntraTokenAuth targeting that destination's auth_resource -- with
        NO --auth-mode/--auth-resource/--server-url flags on the command line.
        """
        from context_intelligence.auth import EntraTokenAuth

        from amplifier_module_tool_context_intelligence_upload.cli import main

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.events_skipped = 0
        mock_result.events_unmapped = 0
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 1,
        }

        destination = _destination_auth_dest(
            name="team",
            url="https://ci.team",
            api_key="",
            auth_mode="entra",
            auth_resource="api://team-app",
        )
        fake_cred = FakeCredential("dest-entra-token")

        captured_strategy: list[Any] = []

        def _capture(**kwargs: Any) -> Any:
            captured_strategy.append(kwargs.get("auth_strategy"))
            return mock_result

        with (
            patch(
                "sys.argv",
                ["context-intelligence-upload", "--path", str(tmp_path), "-y"],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.read_destinations",
                return_value={"team": destination},
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.load_keys_env_into_environ"
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.filter_sessions",
                return_value=(fake_sessions, 0),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                side_effect=_capture,
            ),
            patch(
                "context_intelligence.auth._make_cli_credential",
                return_value=fake_cred,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        assert len(captured_strategy) == 1
        strategy = captured_strategy[0]
        assert isinstance(strategy, EntraTokenAuth)
        # The strategy must target the DESTINATION's own auth_resource --
        # not some other resource -- and produce a real bearer header from
        # the injected credential.
        headers = strategy.headers()
        assert headers == {"Authorization": "Bearer dest-entra-token"}
        assert fake_cred.calls[0] == ("api://team-app/.default",)

    def test_destination_with_static_auth_builds_api_key_strategy(
        self, tmp_path: Path, isolated_home: Path
    ) -> None:
        """A single destination with default auth_mode='static' and its own
        api_key must produce an ApiKeyAuth carrying that api_key -- with NO
        auth flags on the command line.
        """
        from context_intelligence.auth import ApiKeyAuth

        from amplifier_module_tool_context_intelligence_upload.cli import main

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.events_skipped = 0
        mock_result.events_unmapped = 0
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 1,
        }

        destination = _destination_auth_dest(
            name="team",
            url="https://ci.team",
            api_key="sekret",
            # auth_mode defaults to "static"
        )

        captured_strategy: list[Any] = []

        def _capture(**kwargs: Any) -> Any:
            captured_strategy.append(kwargs.get("auth_strategy"))
            return mock_result

        with (
            patch(
                "sys.argv",
                ["context-intelligence-upload", "--path", str(tmp_path), "-y"],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.read_destinations",
                return_value={"team": destination},
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.load_keys_env_into_environ"
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.filter_sessions",
                return_value=(fake_sessions, 0),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                side_effect=_capture,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        assert len(captured_strategy) == 1
        strategy = captured_strategy[0]
        assert isinstance(strategy, ApiKeyAuth)
        assert strategy.headers() == {"Authorization": "Bearer sekret"}

    def test_destination_auth_mode_is_authoritative_over_absent_flag(
        self, tmp_path: Path, isolated_home: Path
    ) -> None:
        """Precedence proof: with NO --auth-mode flag at all (args.auth_mode is
        None, so the local computed default would be 'static'), a destination
        configured with auth_mode='entra' must still win -- proving the
        destination's own auth_mode decides, not the static default.
        """
        from context_intelligence.auth import EntraTokenAuth

        from amplifier_module_tool_context_intelligence_upload.cli import main

        fake_sessions = [(tmp_path, {"session_id": "s1"})]
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.events_skipped = 0
        mock_result.events_unmapped = 0
        mock_result.to_dict.return_value = {
            "status": "completed",
            "sessions_uploaded": 1,
            "events_uploaded": 1,
        }

        destination = _destination_auth_dest(
            name="team",
            url="https://ci.team",
            api_key="",
            auth_mode="entra",
            auth_resource="api://precedence-check",
        )
        fake_cred = FakeCredential("precedence-token")

        captured_strategy: list[Any] = []

        def _capture(**kwargs: Any) -> Any:
            captured_strategy.append(kwargs.get("auth_strategy"))
            return mock_result

        with (
            patch(
                # Deliberately NO --auth-mode / --auth-resource / --server-url:
                # args.auth_mode is None, so the CLI-flag/env-derived local
                # default ("static") is the ONLY other candidate in play.
                "sys.argv",
                ["context-intelligence-upload", "--path", str(tmp_path), "-y"],
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.read_destinations",
                return_value={"team": destination},
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.load_keys_env_into_environ"
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.filter_sessions",
                return_value=(fake_sessions, 0),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.resolve_upload_sessions",
                return_value=_fake_scope(fake_sessions),
            ),
            patch(
                "amplifier_module_tool_context_intelligence_upload.cli.run_upload",
                side_effect=_capture,
            ),
            patch(
                "context_intelligence.auth._make_cli_credential",
                return_value=fake_cred,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            main()

        assert exc_info.value.code == 0
        assert len(captured_strategy) == 1
        strategy = captured_strategy[0]
        assert isinstance(strategy, EntraTokenAuth), (
            "destination.auth_mode='entra' must win over the static default "
            "when no --auth-mode flag is given"
        )
