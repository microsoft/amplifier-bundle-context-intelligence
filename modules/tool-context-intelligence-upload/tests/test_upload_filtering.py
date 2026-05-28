"""Tests for workspace filtering in context-intelligence-upload CLI."""

from __future__ import annotations

from amplifier_module_tool_context_intelligence_upload.cli import _session_passes_filter

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass
class _FakeSession:
    session_id: str
    workspace: str
    path: Path


@dataclass
class _FakeUploadResult:
    success: bool

    def to_dict(self) -> dict:
        return {"status": "completed", "success": self.success}


class TestSessionPassesFilter:
    """Unit tests for the per-session fnmatch filter predicate.

    Real workspace slugs are used so test names self-document the supported
    pattern syntax (suffix wildcard, prefix wildcard, multi-segment, etc.).
    """

    def test_deny_all_no_include(self):
        assert (
            _session_passes_filter(
                "-home-dicolomb-amplifier-context-intelligence-graph-query",
                [],
                [],
            )
            is False
        )

    def test_exact_match(self):
        assert (
            _session_passes_filter(
                "-home-dicolomb-amplifier-context-intelligence-graph-query",
                ["-home-dicolomb-amplifier-context-intelligence-graph-query"],
                [],
            )
            is True
        )

    def test_suffix_wildcard_matches(self):
        assert (
            _session_passes_filter(
                "-home-dicolomb-amplifier-bundle-context-intelligence-design-mode",
                ["-home-dicolomb-amplifier-bundle-*"],
                [],
            )
            is True
        )

    def test_prefix_wildcard_matches(self):
        assert (
            _session_passes_filter(
                "-home-dicolomb-workspaces-cotnext-intelligence-configuration-secrets",
                ["*-secrets"],
                [],
            )
            is True
        )

    def test_multi_segment_wildcard(self):
        assert (
            _session_passes_filter(
                "-home-dicolomb-amplifier-bundle-context-intelligence-design-mode",
                ["-home-dicolomb-amplifier-*-context-intelligence*"],
                [],
            )
            is True
        )

    def test_wildcard_all(self):
        assert _session_passes_filter("default", ["*"], []) is True

    def test_no_pattern_match(self):
        assert (
            _session_passes_filter(
                "-home-dicolomb-personal-projects-ecoflow-library",
                ["-home-dicolomb-amplifier-*"],
                [],
            )
            is False
        )

    def test_exclude_trims_include(self):
        assert (
            _session_passes_filter(
                "-home-dicolomb-workspaces-cotnext-intelligence-configuration-secrets",
                ["-home-dicolomb-workspaces-*"],
                ["*-secrets"],
            )
            is False
        )

    def test_include_with_non_matching_exclude(self):
        assert (
            _session_passes_filter(
                "-home-dicolomb-workspaces-team-pulse-bundle",
                ["-home-dicolomb-workspaces-*"],
                ["*-secrets"],
            )
            is True
        )


class TestUploadFilterArgs:
    """Tests for --include and --exclude argparse flags on the upload CLI."""

    @staticmethod
    def _parse(extra_args: list[str]) -> object:
        from amplifier_module_tool_context_intelligence_upload.cli import _build_parser

        parser = _build_parser()
        return parser.parse_args(["--path", "/tmp"] + extra_args)

    def test_include_defaults_to_empty(self):
        args = self._parse([])
        assert args.include == []

    def test_include_flag_single(self):
        args = self._parse(["--include", "-home-dicolomb-amplifier-*"])
        assert args.include == ["-home-dicolomb-amplifier-*"]

    def test_include_flag_multiple(self):
        args = self._parse(
            ["--include", "-home-dicolomb-amplifier-*", "--include", "-home-dicolomb-workspaces-*"]
        )
        assert args.include == ["-home-dicolomb-amplifier-*", "-home-dicolomb-workspaces-*"]

    def test_exclude_defaults_to_empty(self):
        args = self._parse([])
        assert args.exclude == []

    def test_exclude_flag_single(self):
        args = self._parse(["--exclude", "*-secrets"])
        assert args.exclude == ["*-secrets"]


class TestEffectivePatterns:
    """Tests for env-var + flag union helper _effective_patterns.

    _effective_patterns(flag_values, env_var_name) will be added to cli.py in
    Task 6.  These tests are written first (RED phase of TDD).  Each test
    imports the function inline so that the entire class fails with
        ImportError: cannot import name '_effective_patterns' …
    rather than a collection-time error, which lets pytest report all 7 tests
    as failures rather than a single import crash.
    """

    ENV = "AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_INCLUDE"

    def test_no_flag_no_env(self, monkeypatch):
        from amplifier_module_tool_context_intelligence_upload.cli import _effective_patterns  # noqa: PLC0415

        monkeypatch.delenv(self.ENV, raising=False)
        assert _effective_patterns([], self.ENV) == []

    def test_flag_only(self, monkeypatch):
        from amplifier_module_tool_context_intelligence_upload.cli import _effective_patterns  # noqa: PLC0415

        monkeypatch.delenv(self.ENV, raising=False)
        assert _effective_patterns(["-home-dicolomb-amplifier-*"], self.ENV) == [
            "-home-dicolomb-amplifier-*"
        ]

    def test_env_var_only_comma_separated(self, monkeypatch):
        from amplifier_module_tool_context_intelligence_upload.cli import _effective_patterns  # noqa: PLC0415

        monkeypatch.setenv(self.ENV, "-home-dicolomb-amplifier-*,-home-dicolomb-workspaces-*")
        assert _effective_patterns([], self.ENV) == [
            "-home-dicolomb-amplifier-*",
            "-home-dicolomb-workspaces-*",
        ]

    def test_flag_and_env_var_unioned(self, monkeypatch):
        from amplifier_module_tool_context_intelligence_upload.cli import _effective_patterns  # noqa: PLC0415

        monkeypatch.setenv(self.ENV, "-home-dicolomb-workspaces-*")
        assert _effective_patterns(["-home-dicolomb-amplifier-*"], self.ENV) == [
            "-home-dicolomb-amplifier-*",
            "-home-dicolomb-workspaces-*",
        ]

    def test_empty_env_var_contributes_nothing(self, monkeypatch):
        from amplifier_module_tool_context_intelligence_upload.cli import _effective_patterns  # noqa: PLC0415

        monkeypatch.setenv(self.ENV, "")
        assert _effective_patterns(["-home-dicolomb-amplifier-*"], self.ENV) == [
            "-home-dicolomb-amplifier-*"
        ]

    def test_whitespace_env_entries_ignored(self, monkeypatch):
        from amplifier_module_tool_context_intelligence_upload.cli import _effective_patterns  # noqa: PLC0415

        monkeypatch.setenv(self.ENV, "  ,  ")
        assert _effective_patterns([], self.ENV) == []

    def test_duplicate_pattern_deduplicated_order_preserved(self, monkeypatch):
        from amplifier_module_tool_context_intelligence_upload.cli import _effective_patterns  # noqa: PLC0415

        monkeypatch.setenv(self.ENV, "-home-dicolomb-amplifier-*")
        # flag list: same pattern first, then a second unique pattern
        # env var adds the same pattern again → deduplicate, preserving first-seen order
        assert _effective_patterns(
            ["-home-dicolomb-amplifier-*", "-home-dicolomb-workspaces-*"], self.ENV
        ) == ["-home-dicolomb-amplifier-*", "-home-dicolomb-workspaces-*"]


class TestUploadFilterIntegration:
    """Integration tests that drive filtering in main().

    These tests FAIL until Task 8 wires the include/exclude filter into
    main() — that is the intentional RED state.
    """

    def test_deny_all_warning_when_no_include(self, monkeypatch, capsys, tmp_path) -> None:
        """When no --include patterns are configured, main() warns to stderr and exits 0.

        Verifies that:
        - stderr contains 'no --include configured'
        - run_upload is never invoked
        - exit code is 0
        """
        import amplifier_module_tool_context_intelligence_upload.cli as cli
        import context_intelligence.config as ci_config

        # Remove env vars that could add include/exclude patterns
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_INCLUDE", raising=False)
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_EXCLUDE", raising=False)

        # Stub resolve_config to avoid real server/key lookup
        monkeypatch.setattr(ci_config, "resolve_config", lambda **kwargs: ("http://x", "k"))

        # Set sys.argv — no --include flag provided
        monkeypatch.setattr(sys, "argv", ["context-intelligence-upload", "--path", str(tmp_path)])

        # Stub discover_and_sort to count calls and return empty list
        called: dict[str, int] = {"discover": 0, "upload": 0}

        def fake_discover(path: Path) -> list:
            called["discover"] += 1
            return []

        monkeypatch.setattr(cli, "discover_and_sort", fake_discover)

        # Stub run_upload to raise AssertionError if invoked (should not be called)
        def fake_run_upload(**kwargs: object) -> object:
            called["upload"] += 1
            raise AssertionError(
                "run_upload should not be called when no --include patterns are configured"
            )

        monkeypatch.setattr(cli, "run_upload", fake_run_upload)

        # Call main() — expect clean exit 0
        with pytest.raises(SystemExit) as exc_info:
            cli.main()

        assert exc_info.value.code == 0

        captured = capsys.readouterr()
        assert "no --include configured" in captured.err.lower()
        assert called["upload"] == 0

    def test_per_session_skip_logged_at_info(self, monkeypatch, caplog, tmp_path) -> None:
        """Sessions not matching include patterns are skipped; skip is logged at INFO.

        Verifies that:
        - Only the session whose workspace matches --include is uploaded
        - The skipped session generates an INFO log containing session_id and workspace
        """
        import amplifier_module_tool_context_intelligence_upload.cli as cli
        import context_intelligence.config as ci_config

        # Remove env vars
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_INCLUDE", raising=False)
        monkeypatch.delenv("AMPLIFIER_CONTEXT_INTELLIGENCE_SERVER_EXCLUDE", raising=False)

        # Stub resolve_config
        monkeypatch.setattr(ci_config, "resolve_config", lambda **kwargs: ("http://x", "k"))

        # Create two fake sessions: one that matches, one that doesn't
        s_pass = _FakeSession(
            session_id="s-pass",
            workspace="-home-dicolomb-amplifier-bundle-context-intelligence-design-mode",
            path=tmp_path / "s-pass",
        )
        s_skip = _FakeSession(
            session_id="s-skip",
            workspace="-home-dicolomb-personal-projects-ecoflow-library",
            path=tmp_path / "s-skip",
        )

        # Stub discover_and_sort to return both sessions
        monkeypatch.setattr(cli, "discover_and_sort", lambda path: [s_pass, s_skip])

        # Fake run_upload that records which sessions were uploaded
        uploaded: list[_FakeSession] = []

        def fake_run_upload(
            sessions: list,
            server_url: str,
            api_key: str,
            tracker: object,
            event_delay_s: float,
        ) -> _FakeUploadResult:
            uploaded.extend(sessions)
            return _FakeUploadResult(success=True)

        monkeypatch.setattr(cli, "run_upload", fake_run_upload)

        # Set sys.argv with --include pattern that matches s_pass but not s_skip
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "context-intelligence-upload",
                "--path",
                str(tmp_path),
                "--include",
                "-home-dicolomb-amplifier-*",
            ],
        )

        # Capture INFO logs from the CLI module
        caplog.set_level(
            logging.INFO,
            logger="amplifier_module_tool_context_intelligence_upload.cli",
        )

        # Call main() — expect clean exit 0
        with pytest.raises(SystemExit) as exc_info:
            cli.main()

        assert exc_info.value.code == 0

        # Only the matching session should have been uploaded
        assert [s.session_id for s in uploaded] == ["s-pass"]

        # The skipped session should generate at least one INFO log with both
        # the session_id and workspace slug visible
        skip_logs = [
            r
            for r in caplog.records
            if r.levelno == logging.INFO
            and "s-skip" in r.message
            and "-home-dicolomb-personal-projects-ecoflow-library" in r.message
        ]
        assert len(skip_logs) >= 1
