"""Tests for workspace filtering in context-intelligence-upload CLI."""

from __future__ import annotations

from amplifier_module_tool_context_intelligence_upload.cli import _session_passes_filter


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
