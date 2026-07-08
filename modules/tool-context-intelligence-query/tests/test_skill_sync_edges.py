"""Adversarial edge tests for skill-sync — tester-breaker list.

These tests cover cases not included in the ported spike suite.  Every test
asserts the REAL behaviour; where a FINDING is surfaced it is marked clearly.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Shared stubs (mirrored from test_skill_sync.py to keep this file standalone)
# ---------------------------------------------------------------------------

_STUB_BODY = (
    "---\nname: context-intelligence-graph-query\nversion: 2.0.0\n---\n\n"
    "# Context Intelligence Graph Query — Server Unavailable\n\n"
    "The context intelligence server is not reachable.\n"
    "Delegate immediately to `session-navigator`. Do not attempt Cypher queries.\n"
)


def _write_stub(skill_path: Path) -> str:
    skill_path.write_text(_STUB_BODY)
    return hashlib.sha256(skill_path.read_bytes()).hexdigest()


def _make_tool(server_url: str, api_key: str = "k", workspace: str = "ws") -> MagicMock:
    tool = MagicMock()
    tool._resolve_server_config = MagicMock(return_value=(server_url, api_key, workspace, None))
    return tool


def _make_ready_coordinator(
    skill_path: Path,
    tool: MagicMock | None,
    *,
    discovery_present: bool = True,
    find_returns_meta: bool = True,
) -> MagicMock:
    discovery: MagicMock | None = None
    if discovery_present:
        discovery = MagicMock()
        meta = MagicMock()
        meta.path = skill_path
        discovery.find = MagicMock(return_value=meta if find_returns_meta else None)

    caps: dict[str, object] = {
        "skills_discovery": discovery,
        "context_intelligence._graph_query_tool": tool,
    }

    coord = MagicMock()
    coord.get_capability = MagicMock(side_effect=lambda name: caps.get(name))
    coord.hooks = MagicMock()
    coord.hooks.register = MagicMock(return_value=MagicMock())
    return coord


# ===========================================================================
# 1. _coerce_bool tier fall-through
#    Unrecognized / empty / whitespace / non-standard values must be treated
#    as ABSENT (None), never as True or False, so they fall through to the
#    next resolution tier.  The final default is FALSE (opt-in).
# ===========================================================================


class TestCoerceBoolTierFallThrough:
    """Direct unit coverage of _coerce_bool — the gatekeeper for boolean config knobs."""

    def _coerce(self, value: object) -> bool | None:
        from context_intelligence.tool_resolver import _coerce_bool

        return _coerce_bool(value)

    # --- Unrecognised / ambiguous values → None (absent, fall through) ---

    def test_unrecognised_string_maybe_returns_none(self) -> None:
        assert self._coerce("maybe") is None, "'maybe' is not a boolean token — must be None"

    def test_unrecognised_string_none_returns_none(self) -> None:
        """'none' is a common YAML accident; it is not a boolean token."""
        assert self._coerce("none") is None

    def test_integer_2_returns_none(self) -> None:
        """Only the string '1' and bool True are affirmative; int 2 is unrecognised."""
        assert self._coerce(2) is None

    def test_whitespace_only_returns_none(self) -> None:
        """Unexpanded YAML placeholder collapses to whitespace → absent."""
        assert self._coerce("   ") is None

    def test_empty_string_returns_none(self) -> None:
        """Empty string from an unexpanded ${VAR:} placeholder → absent."""
        assert self._coerce("") is None

    def test_none_python_returns_none(self) -> None:
        """Python None (absent key in config dict) → absent."""
        assert self._coerce(None) is None

    # --- Recognised TRUE tokens ---

    def test_string_true_lowercase_returns_true(self) -> None:
        assert self._coerce("true") is True

    def test_string_true_mixed_case_returns_true(self) -> None:
        assert self._coerce("True") is True

    def test_string_1_returns_true(self) -> None:
        assert self._coerce("1") is True

    def test_string_yes_returns_true(self) -> None:
        assert self._coerce("yes") is True

    def test_string_on_returns_true(self) -> None:
        assert self._coerce("on") is True

    def test_bool_true_returns_true(self) -> None:
        assert self._coerce(True) is True

    # --- Recognised FALSE tokens ---

    def test_string_false_lowercase_returns_false(self) -> None:
        assert self._coerce("false") is False

    def test_string_False_mixed_case_returns_false(self) -> None:
        """'False' (capital F) from YAML must resolve to False, not None."""
        assert self._coerce("False") is False

    def test_string_0_returns_false(self) -> None:
        assert self._coerce("0") is False

    def test_string_no_returns_false(self) -> None:
        assert self._coerce("no") is False

    def test_string_off_returns_false(self) -> None:
        assert self._coerce("off") is False

    def test_bool_false_returns_false(self) -> None:
        assert self._coerce(False) is False

    # --- Whitespace normalisation ---

    def test_string_true_with_surrounding_whitespace_returns_true(self) -> None:
        """' true ' (extra spaces) must be coerced to True, not None."""
        assert self._coerce(" true ") is True

    def test_string_false_with_surrounding_whitespace_returns_false(self) -> None:
        assert self._coerce("  false  ") is False


class TestSkillSyncEnabledTierFallThrough:
    """Integration: ToolConfigResolver.skill_sync_enabled resolution-tier fall-through."""

    def _resolver(self, config: dict, coord_config: dict | None = None) -> object:
        from context_intelligence.tool_resolver import ToolConfigResolver

        coord = MagicMock()
        coord.config = coord_config or {}
        return ToolConfigResolver(config, coord)

    def test_unrecognised_mount_config_falls_through_to_coord_config_false(self) -> None:
        """mount config 'maybe' (unrecognised) → absent → coord.config False → returns False."""
        resolver = self._resolver(
            config={"skill_sync_enabled": "maybe"},
            coord_config={"skill_sync_enabled": False},
        )
        assert resolver.skill_sync_enabled is False

    def test_unrecognised_mount_config_falls_through_to_coord_config_true(self) -> None:
        resolver = self._resolver(
            config={"skill_sync_enabled": "maybe"},
            coord_config={"skill_sync_enabled": True},
        )
        assert resolver.skill_sync_enabled is True

    def test_absent_at_all_tiers_returns_default_false(self) -> None:
        """No config at any tier → default is FALSE (opt-in)."""
        env_clean = {k: v for k, v in os.environ.items() if "SKILL_SYNC_ENABLED" not in k}
        with patch.dict(os.environ, env_clean, clear=True):
            resolver = self._resolver(config={}, coord_config={})
            assert resolver.skill_sync_enabled is False

    def test_empty_string_mount_config_falls_through_to_default_false(self) -> None:
        """An unexpanded YAML placeholder ('') at tier 1 falls through to the default."""
        env_clean = {k: v for k, v in os.environ.items() if "SKILL_SYNC_ENABLED" not in k}
        with patch.dict(os.environ, env_clean, clear=True):
            resolver = self._resolver(config={"skill_sync_enabled": ""}, coord_config={})
            assert resolver.skill_sync_enabled is False

    def test_integer_2_mount_config_falls_through_to_default_false(self) -> None:
        """int(2) at tier 1 is unrecognised → falls through to default (False)."""
        env_clean = {k: v for k, v in os.environ.items() if "SKILL_SYNC_ENABLED" not in k}
        with patch.dict(os.environ, env_clean, clear=True):
            resolver = self._resolver(config={"skill_sync_enabled": 2}, coord_config={})
            assert resolver.skill_sync_enabled is False

    def test_none_mount_config_falls_through_to_coord_config(self) -> None:
        """None at tier 1 is absent → falls through to tier 2."""
        resolver = self._resolver(
            config={"skill_sync_enabled": None},
            coord_config={"skill_sync_enabled": True},
        )
        assert resolver.skill_sync_enabled is True


# ===========================================================================
# 2. SHA-mismatch loud-fail
#    The pin in EXPECTED_BUNDLED_SKILL_SHA256 is the only thing that prevents
#    a silently-wrong vendored body from reaching production.  These tests
#    verify the detection mechanism is real.
# ===========================================================================


class TestBundledSkillSHAMismatch:
    def test_mismatch_is_detectable(self) -> None:
        """Any body that is not the canonical skill produces a different SHA-256.

        This is the property the test_bundled_skill.py pin assertion relies on:
        if the file on disk drifts, the SHA changes and the assertion catches it.
        """
        from amplifier_module_tool_context_intelligence_query.bundled_skill import (
            EXPECTED_BUNDLED_SKILL_SHA256,
        )

        tampered = "tampered — not the real skill body"
        assert (
            hashlib.sha256(tampered.encode("utf-8")).hexdigest() != EXPECTED_BUNDLED_SKILL_SHA256
        ), "SHA-256 of tampered content must differ from the pin (mismatch detection works)"

    def test_pinned_sha_matches_the_real_vendored_file(self) -> None:
        """Re-prove the pin end-to-end: read file → hash → compare to constant.

        This is a redundant cross-check on top of test_bundled_skill.py to
        ensure the mismatch path is exercised explicitly.
        """
        from importlib import resources

        from amplifier_module_tool_context_intelligence_query.bundled_skill import (
            EXPECTED_BUNDLED_SKILL_SHA256,
        )

        pkg = "amplifier_module_tool_context_intelligence_query.bundled_skill"
        data = (
            resources.files(pkg)
            .joinpath("context-intelligence-graph-query.md")
            .read_text(encoding="utf-8")
        )
        actual = hashlib.sha256(data.encode("utf-8")).hexdigest()
        assert actual == EXPECTED_BUNDLED_SKILL_SHA256, (
            f"Vendored body SHA ({actual[:8]}…) != pin ({EXPECTED_BUNDLED_SKILL_SHA256[:8]}…). "
            "This is the loud-fail the mismatch guard produces."
        )

    def test_install_vendored_body_raises_on_sha_mismatch(self, tmp_path: Path) -> None:
        """_install_vendored_body RAISES and writes NOTHING when the vendored body
        does not match the pinned SHA-256.

        A tampered or corrupted wheel body must never reach disk silently.
        The function must raise (ValueError) naming the skill and both hashes,
        and the skill file must remain unchanged (the pessimistic stub content).
        """
        from amplifier_module_tool_context_intelligence_query.bundled_skill import (
            EXPECTED_BUNDLED_SKILL_SHA256,
        )
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            _install_vendored_body,
        )

        skill_path = tmp_path / "SKILL.md"
        _write_stub(skill_path)  # start with the pessimistic stub
        original_content = skill_path.read_text(encoding="utf-8")
        tampered_body = "tampered — not the real skill"

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync._vendored_body",
            return_value=tampered_body,
        ):
            with pytest.raises(ValueError, match="skill_install_sha_mismatch"):
                _install_vendored_body("context-intelligence-graph-query", skill_path)

        # File must be untouched — no partial write on mismatch.
        assert skill_path.read_text(encoding="utf-8") == original_content, (
            "skill_path must remain unchanged after a SHA mismatch — "
            "no corrupted or tampered body must reach disk"
        )
        # The installed SHA must NOT match the pin (it was never written).
        installed_sha = hashlib.sha256(
            skill_path.read_text(encoding="utf-8").encode("utf-8")
        ).hexdigest()
        assert (
            installed_sha != EXPECTED_BUNDLED_SKILL_SHA256 or original_content == tampered_body
        ), "Sanity: the stub content is not the vendored body, so its SHA must differ from the pin"


# ===========================================================================
# 3. Vendored-body install torn-state
#    Simulate os.replace raising AFTER the .etag deletion to verify that:
#    a) the exception surfaces (not swallowed),
#    b) the .etag is already gone (ETag-first property),
#    c) skill_path is unchanged (atomic swap failed before rename).
# ===========================================================================


class TestInstallVendoredBodyTornState:
    def test_os_replace_raises_surfaces_exception(self, tmp_path: Path) -> None:
        """os.replace raising propagates out of _install_vendored_body — fail loud."""
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            _install_vendored_body,
        )

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# original content")
        etag_path = tmp_path / ".etag"
        etag_path.write_text("stale-etag")

        with patch("os.replace", side_effect=OSError("simulated atomic rename failure")):
            with pytest.raises(OSError, match="simulated atomic rename failure"):
                _install_vendored_body("context-intelligence-graph-query", skill_path)

    def test_etag_deleted_before_os_replace_attempt(self, tmp_path: Path) -> None:
        """ETag-first property: .etag is removed BEFORE os.replace is attempted.

        After a torn-state crash the .etag is gone, so a later re-enabled sync
        issues a clean unconditional GET (never a stale-ETag→304 freeze).
        """
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            _install_vendored_body,
        )

        skill_path = tmp_path / "SKILL.md"
        skill_path.write_text("# original content")
        etag_path = tmp_path / ".etag"
        etag_path.write_text("stale-etag")

        with patch("os.replace", side_effect=OSError("simulated crash")):
            with pytest.raises(OSError):
                _install_vendored_body("context-intelligence-graph-query", skill_path)

        assert not etag_path.exists(), (
            ".etag must be deleted BEFORE os.replace so a later sync does "
            "an unconditional GET rather than freezing on the stale ETag"
        )

    def test_skill_path_unchanged_after_torn_state(self, tmp_path: Path) -> None:
        """skill_path retains its original content when os.replace fails.

        The atomic rename never completed, so the old content is still intact.
        """
        from amplifier_module_tool_context_intelligence_query.skill_sync import (
            _install_vendored_body,
        )

        skill_path = tmp_path / "SKILL.md"
        original_content = "# original content"
        skill_path.write_text(original_content)
        (tmp_path / ".etag").write_text("stale-etag")

        with patch("os.replace", side_effect=OSError("simulated crash")):
            with pytest.raises(OSError):
                _install_vendored_body("context-intelligence-graph-query", skill_path)

        assert skill_path.read_text() == original_content, (
            "skill_path must be unchanged after a failed os.replace — "
            "never a half-written state masquerading as success"
        )


# ===========================================================================
# 4. Disabled-path zero-network
#    With skill_sync_enabled=False, on_session_ready must never construct
#    SkillFetcher or call check_server_version / fetch.
#    We use side_effect=AssertionError so the test fails if called.
# ===========================================================================


class TestDisabledPathZeroNetwork:
    async def test_check_server_version_never_called_when_disabled(self, tmp_path: Path) -> None:
        """SkillFetcher is never even instantiated on the disabled path."""
        from amplifier_module_tool_context_intelligence_query.skill_sync import on_session_ready

        skill_path = tmp_path / "SKILL.md"
        _write_stub(skill_path)
        tool = _make_tool("http://up:9000")
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        def _fail_if_instantiated(*args: object, **kwargs: object) -> None:
            raise AssertionError(
                "SkillFetcher must NOT be instantiated when skill_sync_enabled=False"
            )

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync.SkillFetcher",
            side_effect=_fail_if_instantiated,
        ):
            # Must not raise — SkillFetcher is never constructed.
            await on_session_ready(coord)

    async def test_fetch_never_called_when_disabled(self, tmp_path: Path) -> None:
        """Patching SkillFetcher instance methods to fail if called confirms zero-network."""
        from amplifier_module_tool_context_intelligence_query.skill_sync import on_session_ready

        skill_path = tmp_path / "SKILL.md"
        _write_stub(skill_path)
        tool = _make_tool("http://up:9000")
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        mock_instance = AsyncMock()
        mock_instance.check_server_version = AsyncMock(
            side_effect=AssertionError("check_server_version called on disabled path")
        )
        mock_instance.fetch = AsyncMock(side_effect=AssertionError("fetch called on disabled path"))
        mock_fetcher_cls = MagicMock(return_value=mock_instance)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync.SkillFetcher",
            mock_fetcher_cls,
        ):
            await on_session_ready(coord)  # must complete without raising

        mock_fetcher_cls.assert_not_called()

    async def test_disabled_no_server_no_network_call(self, tmp_path: Path) -> None:
        """Even when no server is configured, SkillFetcher is never constructed."""
        from amplifier_module_tool_context_intelligence_query.skill_sync import on_session_ready

        skill_path = tmp_path / "SKILL.md"
        _write_stub(skill_path)
        tool = _make_tool("")  # empty server_url
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        with patch(
            "amplifier_module_tool_context_intelligence_query.skill_sync.SkillFetcher"
        ) as mock_fetcher_cls:
            await on_session_ready(coord)

        mock_fetcher_cls.assert_not_called()

    async def test_no_skill_unloaded_handler_registered_when_disabled(self, tmp_path: Path) -> None:
        """The skill:unloaded reload handler must NOT be registered on the disabled path."""
        from amplifier_module_tool_context_intelligence_query.skill_sync import on_session_ready

        skill_path = tmp_path / "SKILL.md"
        _write_stub(skill_path)
        tool = _make_tool("http://up:9000")
        tool.skill_sync_enabled = False
        coord = _make_ready_coordinator(skill_path, tool)

        await on_session_ready(coord)

        registered = [c.args[0] for c in coord.hooks.register.call_args_list]
        assert "skill:unloaded" not in registered, (
            "disabled path must not register the per-turn skill:unloaded reload handler"
        )


# ===========================================================================
# 5. build_payload coupling guard
#    The hook strip (Brick B) must not have broken the build_payload import.
# ===========================================================================


class TestBuildPayloadCouplingGuard:
    """Guard that the hook strip (Brick B) did not accidentally remove build_payload.

    These tests require the hook module on sys.path.  Run from the repo root with:
        PYTHONPATH=modules/hook-context-intelligence uv run pytest \
            modules/tool-context-intelligence-query/tests/test_skill_sync_edges.py \
            -k build_payload

    When the hook is not on sys.path the tests are SKIPPED (not failed) — a skip
    means "unverified in this venv", not "the code is broken".  Install the hook
    module or use the PYTHONPATH above for a TRUE pass.
    """

    @staticmethod
    def _require_hook() -> None:
        """Skip the test if the hook module is not on sys.path."""
        import importlib.util

        if importlib.util.find_spec("amplifier_module_hook_context_intelligence") is None:
            pytest.skip(
                "amplifier_module_hook_context_intelligence not on sys.path — "
                "run with PYTHONPATH=modules/hook-context-intelligence for a full coupling check"
            )

    def test_build_payload_import_intact(self) -> None:
        """hook-context-intelligence.upload.build_payload must still be importable.

        The hook strip removed skill_fetcher.py and legacy_content/ from the
        hook module.  This guard ensures build_payload — consumed by other
        integrations — was NOT accidentally deleted as part of that strip.
        """
        self._require_hook()

        import importlib

        hook_upload = importlib.import_module("amplifier_module_hook_context_intelligence.upload")
        build_payload = getattr(hook_upload, "build_payload", None)
        assert callable(build_payload), (
            "build_payload must be a callable function; the hook strip must not have removed it"
        )

    def test_build_payload_accepts_expected_signature(self) -> None:
        """build_payload(event, workspace, data) must accept its documented signature."""
        import importlib
        import inspect

        self._require_hook()

        hook_upload = importlib.import_module("amplifier_module_hook_context_intelligence.upload")
        build_payload = getattr(hook_upload, "build_payload")
        sig = inspect.signature(build_payload)
        param_names = list(sig.parameters.keys())
        assert "event" in param_names
        assert "workspace" in param_names
        assert "data" in param_names


# ===========================================================================
# 6. Bonus: GraphQueryTool.skill_sync_enabled pass-through
#    Verifies that the property correctly reflects the resolver's value so
#    on_session_ready's get_capability → .skill_sync_enabled chain works.
# ===========================================================================


class TestGraphQueryToolSkillSyncPassThrough:
    def _make_tool_with_config(self, config: dict) -> object:
        from context_intelligence.tool_resolver import ToolConfigResolver

        from amplifier_module_tool_context_intelligence_query.graph_query_tool import (
            GraphQueryTool,
        )

        coord = MagicMock()
        coord.config = {}
        resolver = ToolConfigResolver(config, coord)
        return GraphQueryTool(coord, resolver)

    def test_default_is_false(self) -> None:
        """With no config, skill_sync_enabled defaults to False (opt-in)."""
        env_clean = {k: v for k, v in os.environ.items() if "SKILL_SYNC_ENABLED" not in k}
        with patch.dict(os.environ, env_clean, clear=True):
            tool = self._make_tool_with_config({})
            assert tool.skill_sync_enabled is False  # type: ignore[union-attr]

    def test_explicit_true_in_config_returns_true(self) -> None:
        tool = self._make_tool_with_config({"skill_sync_enabled": True})
        assert tool.skill_sync_enabled is True  # type: ignore[union-attr]

    def test_explicit_false_in_config_returns_false(self) -> None:
        tool = self._make_tool_with_config({"skill_sync_enabled": False})
        assert tool.skill_sync_enabled is False  # type: ignore[union-attr]

    def test_string_true_in_config_returns_true(self) -> None:
        tool = self._make_tool_with_config({"skill_sync_enabled": "true"})
        assert tool.skill_sync_enabled is True  # type: ignore[union-attr]

    def test_unrecognised_string_in_config_falls_through_to_default_false(self) -> None:
        env_clean = {k: v for k, v in os.environ.items() if "SKILL_SYNC_ENABLED" not in k}
        with patch.dict(os.environ, env_clean, clear=True):
            tool = self._make_tool_with_config({"skill_sync_enabled": "maybe"})
            assert tool.skill_sync_enabled is False  # type: ignore[union-attr]

    def test_getattr_default_semantics_for_on_session_ready_gate(self) -> None:
        """on_session_ready uses getattr(tool, 'skill_sync_enabled', True).

        When the resolver returns False (default), getattr returns False,
        so 'not False = True' and the disabled branch IS taken.  This is the
        correct opt-in semantics.
        """
        env_clean = {k: v for k, v in os.environ.items() if "SKILL_SYNC_ENABLED" not in k}
        with patch.dict(os.environ, env_clean, clear=True):
            tool = self._make_tool_with_config({})
            result = getattr(tool, "skill_sync_enabled", True)
            assert result is False, "default must be False so disabled-path is taken by default"
            assert result is not True, "not False == True → disabled path executes"
